import logging

import httpx
import pytest

from app import wizcloud
from app.store import Saved, StoreError
from app.wizcloud import READ_PATHS, SESSION_TTL_S, WizClient, WizError, parse_session_body
from tests.conftest import KEY, SESSION, FakeStore

# Every path the vendor documents, as the vendor writes it, read 2026-11-19 (backlog 0301, slice S0) by three
# independent sweeps and a completeness critic:
#   docs  - docs.wizcloud.co.il, both versions (2.0.0: 35 pages, 1.0.0: 34), the site's 115 JS files and its
#           screenshots, and a page withdrawn from the sitemap that is still served (/docs/matrix/)
#   oas   - the five specs on SwaggerHub (Wizcloud/Api 1.0.0 and 1.0.1, and three drafts)
#   npm   - the vendor's client wizcloud-api, all 16 published versions
# The guard against a write to the books is the code alone (spec §2.5: the token is Chava's user and
# a view-only user is not possible), so this list is the test of that guard. When the vendor documents
# a new path, it goes here; it is refused until a decision (an ADR) adds it to READ_PATHS.
DOCUMENTED_PATHS = {
    # documents
    "invApi/createDoc": "docs, oas, npm",
    "invApi/v2/createdoc": "docs",
    "invApi/issueDocument": "docs, oas, npm",
    "invApi/delDocument": "docs, oas, npm",
    "invApi/addLineDoc": "docs",
    "invApi/delLine": "docs",
    "invApi/invoiceDecision": "docs",
    "invApi/getDoc": "docs, oas, npm",
    "InvApi/createDoc": "docs (a getting-started screenshot, capital I)",
    "restoreDoc": "docs (documents, REST row)",
    "inv_issue/restoreDoc": "docs (documents, REST row)",
    # receipts, tax receipts, credit cards
    "docsApi/createRecipt": "docs, oas",
    "docsApi/createInvRecipt": "docs, oas",
    "docsApi/v2/createReceipt": "docs",
    "docsApi/v2/createInvReceipt": "docs",
    "docsApi/createRefund": "docs",
    "docsApi/createToken": "docs",
    "docsApi/responseCG_confirmNum": "docs",
    "docsapi/getReceipt/": "docs (receipts, REST row, as written)",
    "docsapi/delReceipt/": "docs (receipts, REST row, as written)",
    "docsapi/restoreDoc/": "docs (receipts, REST row, as written)",
    "cancelKabala/cancel/": "docs (receipts, REST row)",
    "cancelKabala/cancel": "docs (credit card refunds, REST row)",
    # journal batches
    "jtransApi/tmpBatch": "docs, oas, npm",
    "jtransApi/chkBatch": "docs, oas, npm",
    "jtransApi/newBatch": "docs, oas, npm",
    "jtransApi/issueBatch": "docs, oas, npm",
    # cards, codes, bank pages, extra data, files
    "IndexApi/importIndex": "docs, oas, npm",
    "IndexApi/deleteIndex": "npm",
    "SortCodeApi/importSortCodes": "docs, oas",
    "TransTypesApi/importTransTypes": "docs, oas",
    "BankPagesApi/importBankPage": "docs, oas, npm",
    "ExtraDataApi/importExtraData": "docs",
    "ExtraDataNameApi/importExtraData": "docs",
    "FilesApi/uploadfile": "docs",
    "FilesApi/attachFiles": "docs",
    "compDefApi/getCompDef": "docs",
    "compDefApi/UpdCompDef": "docs",
    "matrixItemApi/importMatrixItem": "docs (/docs/matrix/, withdrawn from the sitemap, still served)",
    "matrixItemApi/deleteMatrixItem": "docs (/docs/matrix/, withdrawn from the sitemap, still served)",
    "matrixItemApi/getMatrixItem": "docs (/docs/matrix/, withdrawn from the sitemap, still served)",
    # CRM
    "crmApi/getRecords": "docs",
    "crmApi/insertRecord": "docs",
    "crmApi/updateRecord": "docs",
    "crmApi/deleteRecord": "docs",
    "crmApi/runProcess": "docs",
    "crmActivitiesApi/createSchema": "npm",
    "crmActivitiesTest/createSchema": "npm",
    "api/napi": "npm",
    # billing (standing orders)
    "billingApi/getAllConid": "docs",
    "billingApi/getLateConid": "docs",
    "billingApi/getNowConid": "docs",
    # sessions, reports, companies, webhooks
    "createSession/{WizcloudApiPrivateKey}/{WizcloudApiDBName}": "docs, oas, npm (the client's own GET, never through call())",
    "ExportDataApi/exportData": "docs, oas, npm",
    "ExportDataApi/exportData?target=pdf": "docs (reportdata: the PDF form)",
    "CompanyListToTokenApi/TokenCompanies": "docs, oas, npm",
    "TriggersApi/getURL": "docs, oas",
    "TriggersApi/setURL": "docs, oas",
    "TriggersApi/updateURL": "docs, oas",
    "TriggersApi/deleteURL": "docs, oas",
}
# Reads the vendor documents that this service does not use. Refused all the same: READ_PATHS is the
# list of what the service reads, not of what is harmless.
UNUSED_READS = {
    "billingApi/getAllConid",
    "billingApi/getLateConid",
    "billingApi/getNowConid",
    "compDefApi/getCompDef",
    "crmApi/getRecords",
    "docsapi/getReceipt/",
    "matrixItemApi/getMatrixItem",
    "ExportDataApi/exportData?target=pdf",
}
SESSION_PATHS = {"createSession/{WizcloudApiPrivateKey}/{WizcloudApiDBName}"}
# Everything else writes, or its effect is not stated (api/napi, jtransApi/chkBatch): refused all the same.
WRITE_PATHS = sorted(set(DOCUMENTED_PATHS) - READ_PATHS - UNUSED_READS - SESSION_PATHS)
# Spellings that would slip past a prefix or case-insensitive check.
TRICKS = [
    "docsapi/delReceipt",  # the vendor writes it with a trailing slash; a client may not
    "docsApi/delReceipt",
    "docsapi/restoreDoc",
    "invApi/createDoc/{data}",  # the rest-api table's placeholder notation
    "invApi/getDoc/123",
    "/invApi/getDoc",
    "invApi/getDoc/../createDoc",
    "invapi/getdoc",
    "invApi/getDoc?x=1",
    "invApi/getDoc ",
    "ExportDataApi/exportData/../../invApi/delDocument",
]


def client(fake, clock):
    return WizClient("lb19.wizcloud.co.il", "wizdb2245n4", KEY, transport=fake.transport(), clock=clock)


@pytest.mark.parametrize("path", WRITE_PATHS + sorted(UNUSED_READS) + sorted(SESSION_PATHS) + TRICKS + ["createSession"])
def test_anything_outside_the_read_list_is_refused_before_any_request(fake, clock, path):
    c = client(fake, clock)
    with pytest.raises(WizError) as err:
        c.call(path, {"x": 1})
    assert err.value.kind == "forbidden_path"
    assert fake.requests == []


def test_every_documented_write_path_is_in_the_refused_list():
    """The acceptance of slice S0: no documented path that writes slips out of the test above."""
    assert (len(DOCUMENTED_PATHS), len(WRITE_PATHS), len(UNUSED_READS)) == (60, 47, 8)
    for path in ("invApi/createDoc", "docsApi/v2/createReceipt", "crmApi/deleteRecord", "FilesApi/uploadfile",
                 "TriggersApi/setURL", "TriggersApi/updateURL", "TriggersApi/deleteURL", "IndexApi/importIndex",
                 "docsapi/delReceipt/", "cancelKabala/cancel", "inv_issue/restoreDoc", "matrixItemApi/deleteMatrixItem"):
        assert path in WRITE_PATHS


def test_the_read_list_is_exactly_four_documented_paths():
    assert READ_PATHS == {
        "CompanyListToTokenApi/TokenCompanies",
        "TriggersApi/getURL",
        "ExportDataApi/exportData",
        "invApi/getDoc",
    }
    assert READ_PATHS <= set(DOCUMENTED_PATHS)
    assert not set(WRITE_PATHS) & READ_PATHS


def test_one_session_serves_many_calls(fake, clock):
    c = client(fake, clock)
    c.companies()
    c.triggers()
    c.companies()
    assert fake.issued == 1
    assert all(r.headers["authorization"] == f"Bearer {SESSION}" for r in fake.requests[1:])


def test_session_is_renewed_before_24_hours(fake, clock):
    c = client(fake, clock)
    c.companies()
    clock.t += SESSION_TTL_S - 1
    c.companies()
    assert fake.issued == 1
    clock.t += 2
    c.companies()
    assert fake.issued == 2


def test_a_rejected_session_gets_one_fresh_try(fake, clock):
    c = client(fake, clock)
    c.companies()
    fake.valid_sessions = {"NEWSESSION-1234567890"}
    fake.session_body = "NEWSESSION-1234567890"
    assert c.companies() == fake.companies
    assert fake.issued == 2


def test_a_session_rejected_twice_is_reported_not_looped(fake, clock):
    fake.valid_sessions = set()
    c = client(fake, clock)
    with pytest.raises(WizError) as err:
        c.companies()
    assert err.value.kind == "session_rejected"
    assert err.value.message == "Token is not valid"
    assert fake.issued == 2


def test_bad_private_key(fake, clock):
    c = WizClient("lb19.wizcloud.co.il", "wizdb2245n4", "WRONGKEY-999999", transport=fake.transport(), clock=clock)
    with pytest.raises(WizError) as err:
        c.companies()
    assert (err.value.kind, err.value.status, err.value.message) == ("bad_key", 400, "iligal token")


def test_module_inactive_is_named(fake, clock):
    fake.session_status = 500
    fake.session_body = "Api module not active, contact customer service"
    with pytest.raises(WizError) as err:
        client(fake, clock).companies()
    assert err.value.kind == "module_inactive"


def test_missing_config_makes_no_request(fake, clock):
    c = WizClient("lb19.wizcloud.co.il", "wizdb2245n4", "", transport=fake.transport(), clock=clock)
    with pytest.raises(WizError) as err:
        c.companies()
    assert err.value.kind == "config_missing"
    assert fake.requests == []


def test_network_errors_never_carry_the_url_with_the_key(clock):
    def boom(request):
        raise httpx.ConnectError(f"cannot reach {request.url}", request=request)

    c = WizClient("lb19.wizcloud.co.il", "wizdb2245n4", KEY, transport=httpx.MockTransport(boom), clock=clock)
    with pytest.raises(WizError) as err:
        c.companies()
    assert err.value.kind == "network"
    assert KEY not in str(err.value) and KEY not in repr(err.value.__dict__)
    assert err.value.__cause__ is None and err.value.__suppress_context__


def test_an_echoed_key_is_redacted(fake, clock):
    def echo(request):
        fake.requests.append(request)
        return httpx.Response(400, text=f"bad token {KEY} for wizdb2245n4")

    c = WizClient("lb19.wizcloud.co.il", "wizdb2245n4", KEY, transport=httpx.MockTransport(echo), clock=clock)
    with pytest.raises(WizError) as err:
        c.companies()
    assert KEY not in str(err.value)
    assert "***" in err.value.message


def test_httpx_request_lines_are_not_logged(fake, clock, caplog):
    caplog.set_level(logging.DEBUG)
    client(fake, clock).companies()
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
    assert KEY not in caplog.text and SESSION not in caplog.text


@pytest.mark.parametrize(
    "body,token,shape",
    [
        ("abc123", "abc123", "text"),
        ("  abc123\n", "abc123", "text"),
        ('"abc123"', "abc123", "json-string"),
        ('{"wizAuthToken": "abc123"}', "abc123", "json-object:wizAuthToken"),
        ('{"token": "abc123"}', "abc123", "json-object:token"),
        ('{"other": 1}', None, "json-object:keys=other"),
        ("", None, "empty"),
        ("iligal token", None, "text-with-spaces"),
        ("{not json", None, "json-unparseable"),
    ],
)
def test_session_body_shapes(body, token, shape):
    assert parse_session_body(body) == (token, shape)


def test_unreadable_session_body_is_bad_response(fake, clock):
    fake.session_body = '{"other": 1}'
    with pytest.raises(WizError) as err:
        client(fake, clock).companies()
    assert err.value.kind == "bad_response"


def test_error_inside_a_200_envelope_is_still_an_error(clock):
    def handler(request):
        if request.url.path.startswith("/createSession/"):
            return httpx.Response(200, text=SESSION)
        return httpx.Response(200, json={"statusCode": 500, "status": {"errors": "Data not found"}})

    c = WizClient("lb19.wizcloud.co.il", "wizdb2245n4", KEY, transport=httpx.MockTransport(handler), clock=clock)
    with pytest.raises(WizError) as err:
        c.export_report("datafile")
    assert (err.value.kind, err.value.message) == ("http_error", "Data not found")


def test_export_and_get_doc_send_json_bodies(clock):
    seen = []

    def handler(request):
        seen.append(request)
        if request.url.path.startswith("/createSession/"):
            return httpx.Response(200, text=SESSION)
        return httpx.Response(200, json={"statusCode": 200, "repdata": []})

    c = WizClient("lb19.wizcloud.co.il", "wizdb2245n4", KEY, transport=httpx.MockTransport(handler), clock=clock)
    c.export_report("DF", [{"p_name": "__MUSTACH_P0__", "defVal": "105"}])
    c.get_doc("17")
    assert seen[1].url.path == "/ExportDataApi/exportData"
    assert seen[1].read() == b'{"datafile":"DF","parameters":[{"p_name":"__MUSTACH_P0__","defVal":"105"}]}'
    assert seen[2].read() == b'{"stockID":17}'


def test_kinds_are_a_closed_list():
    with pytest.raises(AssertionError):
        WizError("something_new")
    assert len(set(wizcloud.KINDS)) == len(wizcloud.KINDS)


# ---- the saved session (slice S0): the store's contract, faked; tests/test_store.py runs the SQL ----


def stored_client(fake, clock, store):
    return WizClient("lb19.wizcloud.co.il", "wizdb2245n4", KEY, transport=fake.transport(), clock=clock, store=store)


def test_a_saved_session_is_used_without_opening_one(fake, clock):
    store = FakeStore()
    store.rows[("lb19.wizcloud.co.il", "wizdb2245n4")] = Saved(token=SESSION, shape="text", issued_at=clock.t - 3600)
    c = stored_client(fake, clock, store)
    assert c.companies() == fake.companies
    assert fake.issued == 0
    assert fake.requests[0].headers["authorization"] == f"Bearer {SESSION}"
    assert c.session().origin == "saved" and store.renewals == 0


def test_a_new_session_is_saved_for_the_next_process(fake, clock):
    store = FakeStore()
    first = stored_client(fake, clock, store)
    first.companies()
    assert fake.issued == 1 and store.saves == 1
    assert first.session().origin == "new" and first.session().saved is True
    second = stored_client(fake, clock, store)  # a cold start: a new process, the same database
    second.companies()
    assert fake.issued == 1
    assert second.session().origin == "saved"


def test_a_saved_session_past_its_day_is_renewed(fake, clock):
    store = FakeStore()
    store.rows[("lb19.wizcloud.co.il", "wizdb2245n4")] = Saved(token="OLDSESSION-123", shape="text", issued_at=clock.t - SESSION_TTL_S)
    c = stored_client(fake, clock, store)
    c.companies()
    assert fake.issued == 1
    assert store.rows[("lb19.wizcloud.co.il", "wizdb2245n4")].token == SESSION


def test_a_rejected_saved_session_is_replaced_once(fake, clock):
    store = FakeStore()
    store.rows[("lb19.wizcloud.co.il", "wizdb2245n4")] = Saved(token="REVOKED-123456", shape="text", issued_at=clock.t - 60)
    c = stored_client(fake, clock, store)
    assert c.companies() == fake.companies
    assert fake.issued == 1
    assert store.rows[("lb19.wizcloud.co.il", "wizdb2245n4")].token == SESSION


def test_a_renewal_by_another_instance_is_taken_not_repeated(fake, clock):
    """If a new session revokes the old one (unverified, spec §10), the instance that finds its session
    rejected takes the one another instance saved, instead of opening a third."""
    store = FakeStore()
    c = stored_client(fake, clock, store)
    c.companies()
    assert fake.issued == 1
    clock.t += 5  # the other instance renewed after this one opened its session
    store.rows[("lb19.wizcloud.co.il", "wizdb2245n4")] = Saved(token="OTHERINSTANCE-77", shape="text", issued_at=clock.t)
    fake.valid_sessions = {"OTHERINSTANCE-77"}
    assert c.companies() == fake.companies
    assert fake.issued == 1
    assert fake.requests[-1].headers["authorization"] == "Bearer OTHERINSTANCE-77"


def test_a_failing_store_costs_a_session_not_the_read(fake, clock):
    store = FakeStore()
    store.fail = StoreError("store_unreachable", "OperationalError")
    c = stored_client(fake, clock, store)
    assert c.companies() == fake.companies
    sess = c.session()
    assert (sess.origin, sess.saved, sess.store_error) == (
        "new", False, {"ok": False, "kind": "store_unreachable", "detail": "OperationalError"})
    c.triggers()
    assert fake.issued == 1 and store.loads == 1  # held in memory: the store is not asked again


def test_a_failed_save_does_not_open_a_second_session(fake, clock):
    store = FakeStore()
    store.fail_on_save = StoreError("store_error", "42501")
    c = stored_client(fake, clock, store)
    c.companies()
    assert fake.issued == 1
    assert c.session().saved is False and c.session().store_error["kind"] == "store_error"


def test_a_session_sealed_under_another_key_is_replaced_and_said(fake, clock):
    store = FakeStore()
    store.rows[("lb19.wizcloud.co.il", "wizdb2245n4")] = Saved(token="", shape="text", issued_at=clock.t - 60)
    c = stored_client(fake, clock, store)
    c.companies()
    assert fake.issued == 1
    assert c.session().note == "the saved session was sealed under another key and was replaced"


def test_without_retry_a_rejected_session_is_reported(fake, clock):
    fake.valid_sessions = set()
    c = client(fake, clock)
    with pytest.raises(WizError) as err:
        c.call("CompanyListToTokenApi/TokenCompanies", retry=False)
    assert err.value.kind == "session_rejected" and fake.issued == 1


def test_no_repr_carries_the_key_or_the_session(fake, clock):
    c = stored_client(fake, clock, FakeStore())
    c.companies()
    text = repr(c) + repr(c.session())
    assert KEY not in text and SESSION not in text



def test_an_older_saved_session_is_not_a_renewal(fake, clock):
    """The row may hold a session older than the one just refused (a save that failed): not taken."""
    store = FakeStore()
    c = stored_client(fake, clock, store)
    c.companies()
    store.rows[("lb19.wizcloud.co.il", "wizdb2245n4")] = Saved(token="OLDER-SESSION-1", shape="text", issued_at=clock.t - 60)
    fake.valid_sessions = {"NEWSESSION-1234567890"}
    fake.session_body = "NEWSESSION-1234567890"
    assert c.companies() == fake.companies
    assert fake.issued == 2 and c.session().token == "NEWSESSION-1234567890"


@pytest.mark.parametrize("server", [
    "lb19.wizcloud.co.il/invApi/delDocument#",
    "lb19.wizcloud.co.il/invApi/delDocument?x=",
    "lb19.wizcloud.co.il.evil.example",
    "evil.example/lb19.wizcloud.co.il",
    "LB19.wizcloud.co.il",
])
def test_the_client_refuses_a_server_that_is_not_a_balancer(fake, clock, server):
    """The review's trick (19.11): a server string that turns getDoc into delDocument. The client checks
    the host itself, before any request, and whatever config.load() let through."""
    c = WizClient(server, "wizdb2245n4", KEY, transport=fake.transport(), clock=clock)
    with pytest.raises(WizError) as err:
        c.get_doc(626)
    assert err.value.kind == "config_missing"
    assert fake.requests == []


@pytest.mark.parametrize("key", ["KEY?x=/wizdb2245n5", "KEY#frag", "KEY/../x", "KEY WITH SPACE"])
def test_a_key_that_would_reshape_the_url_is_refused_before_any_request(fake, clock, key):
    c = WizClient("lb19.wizcloud.co.il", "wizdb2245n4", key, transport=fake.transport(), clock=clock)
    with pytest.raises(WizError) as err:
        c.companies()
    assert err.value.kind == "config_missing" and key not in str(err.value)
    assert fake.requests == []


def test_a_2xx_answer_that_is_not_json_is_never_quoted(clock):
    """It can be a document or a report row (names, ID numbers): only its shape is said (review 19.11)."""
    body = f"<html>ישראל ישראלי 123456789 {SESSION}</html>" + "." * 300

    def handler(request):
        if request.url.path.startswith("/createSession/"):
            return httpx.Response(200, text=SESSION)
        return httpx.Response(200, text=body, headers={"content-type": "text/html; charset=utf-8"})

    c = WizClient("lb19.wizcloud.co.il", "wizdb2245n4", KEY, transport=httpx.MockTransport(handler), clock=clock)
    with pytest.raises(WizError) as err:
        c.get_doc(626)
    assert err.value.kind == "bad_response" and err.value.status == 200
    assert err.value.message == f"a 200 answer that is not JSON ({len(body)} characters, content-type text/html)"


def test_a_short_plain_text_refusal_is_still_retried(fake, clock):
    def handler(request):
        if request.url.path.startswith("/createSession/"):
            fake.issued += 1
            return httpx.Response(200, text=f"SESSION-NUMBER-{fake.issued}-abcdef")
        if request.headers["authorization"].endswith("-1-abcdef"):
            return httpx.Response(200, text="Invalid token")
        return httpx.Response(200, json=[])

    c = WizClient("lb19.wizcloud.co.il", "wizdb2245n4", KEY, transport=httpx.MockTransport(handler), clock=clock)
    assert c.companies() == [] and fake.issued == 2


def test_a_partial_echo_of_a_secret_in_an_error_text_is_masked(clock):
    """A 4xx error text is quoted (it is the vendor's diagnosis), redacted first, then cut: no 12
    characters of the key or of the session survive, whole or cut, and no unseen token either."""
    partial = SESSION[:20]
    unseen = "ZQ" + "9x" * 40  # a session the client never saw, e.g. handed back inside an error

    def handler(request):
        if request.url.path.startswith("/createSession/"):
            return httpx.Response(200, text=SESSION)
        return httpx.Response(400, text=f"bad {SESSION} and {partial}xyz and {KEY[3:18]} and {unseen}" + " ." * 5000)

    c = WizClient("lb19.wizcloud.co.il", "wizdb2245n4", KEY, transport=httpx.MockTransport(handler), clock=clock)
    with pytest.raises(WizError) as err:
        c.companies()
    text = str(err.value) + str(err.value.as_dict())
    for secret in (SESSION, KEY, unseen):
        for i in range(len(secret) - 11):
            assert secret[i:i + 12] not in text
    assert err.value.message.startswith("bad *** and ***") and len(err.value.message) <= 203


def test_a_session_shaped_key_in_a_json_object_is_counted_not_named():
    secretish = "S" * 224
    assert parse_session_body('{"%s": 1, "status": 2}' % secretish) == (None, "json-object:keys=status (+1 other)")


def test_create_session_has_its_own_tighter_timeout(clock):
    seen = {}

    def handler(request):
        seen[request.url.path.split("/")[1]] = request.extensions["timeout"]
        if request.url.path.startswith("/createSession/"):
            return httpx.Response(200, text=SESSION)
        return httpx.Response(200, json=[])

    WizClient("lb19.wizcloud.co.il", "wizdb2245n4", KEY, transport=httpx.MockTransport(handler), clock=clock).companies()
    assert seen["createSession"] == {"connect": 5.0, "read": 10.0, "write": 10.0, "pool": 10.0}
    assert seen["CompanyListToTokenApi"]["read"] == 30.0


@pytest.mark.parametrize("key", ['"PRIVATEKEY-0123456789abcdef"', "﻿PRIVATEKEY-0123", "PRIVATEKEY-0123​",
                                 "PRIVATEKEY\x00-0123", "PRIVATEKEY\x7f-0123", "PRIVATE{KEY}-0123", "PRIVATE%KEY-0123",
                                 "x%2F..%2FinvApi%2FdelDocument"])
def test_a_mis_pasted_key_is_a_config_problem_not_a_code_bug(fake, clock, key):
    """Review 19.11: quotes, a BOM or a zero-width space read as forbidden_path ("a bug, never the
    vendor"), and a control character made /check a 500."""
    from app import config

    assert config.load({"WIZ_PRIVATE_KEY": key}).problems
    c = WizClient("lb19.wizcloud.co.il", "wizdb2245n4", key, transport=fake.transport(), clock=clock)
    with pytest.raises(WizError) as err:
        c.companies()
    assert err.value.kind == "config_missing" and fake.requests == []


@pytest.mark.parametrize("key", ["PRIVATEKEY-0123456789abcdef", "abc.DEF_123~x+y=", "a!b*c'(d);e:f@g&h$i,j"])
def test_key_problem_lets_any_key_through_that_travels_as_is(key):
    from app.wizcloud import key_problem

    assert key_problem(key) is None


def test_a_dead_session_borrowed_from_the_store_gets_one_more_try(fake, clock):
    """Review 19.11: the vendor drops sessions twice; another instance renewed in between, and its
    renewal is dead too. The retry was spent on it; now one more attempt opens a session of its own."""
    store = FakeStore()
    tokens = iter(f"SESSION-{n}-abcdefghij" for n in range(1, 10))

    def handler(request):
        if request.url.path.startswith("/createSession/"):
            fake.session_body = next(tokens)
            fake.valid_sessions = {fake.session_body}  # each drop and each new session: only the newest lives
        return fake.handler(request)

    transport = httpx.MockTransport(handler)
    a = WizClient("lb19.wizcloud.co.il", "wizdb2245n4", KEY, transport=transport, clock=clock, store=store)
    b = WizClient("lb19.wizcloud.co.il", "wizdb2245n4", KEY, transport=transport, clock=clock, store=store)
    a.companies()
    b.companies()  # both hold SESSION-1 (b took it from the row)
    fake.valid_sessions = set()  # the vendor drops every session
    clock.t += 5
    a.companies()  # a renews to SESSION-2 and saves it
    fake.valid_sessions = set()  # dropped again before b's next call
    clock.t += 5
    assert b.companies() == fake.companies  # SESSION-1 refused, SESSION-2 borrowed and refused, SESSION-3 opened
    assert b.session().token == "SESSION-3-abcdefghij" and store.rows[("lb19.wizcloud.co.il", "wizdb2245n4")].token == b.session().token


def test_a_session_this_call_opened_itself_is_not_retried_twice(fake, clock):
    fake.valid_sessions = set()
    c = stored_client(fake, clock, FakeStore())
    with pytest.raises(WizError) as err:
        c.companies()
    assert err.value.kind == "session_rejected" and fake.issued == 2


def test_a_secret_cut_by_the_cap_does_not_slide_into_the_message(clock):
    """Review 19.11: masking a long run before the cap shrinks the text, and what the cap cut through -
    the start of an unseen session, or of a known one - slid inside the 200 characters."""
    import base64

    blob = base64.b64encode(bytes(range(256)) * 2).decode()[:240]  # a long token-like run, masked to ***
    unseen = "UNSEEN" + "q7" * 109  # a 224-character session the client never saw
    cap = 200 + 2 * len(SESSION) + 12  # the cap _short applies on a read: limit + 2 x the longest known secret + 12
    for tail_secret, inside in ((unseen, 20), (SESSION, 11)):
        lead = "err " + blob + " "
        lead = lead[: cap - inside - 1] + " "  # the secret starts `inside` characters before the cap
        body = lead + tail_secret + " end"

        def handler(request, body=body):
            if request.url.path.startswith("/createSession/"):
                return httpx.Response(200, text=SESSION)
            return httpx.Response(400, text=body)

        c = WizClient("lb19.wizcloud.co.il", "wizdb2245n4", KEY, transport=httpx.MockTransport(handler), clock=clock)
        with pytest.raises(WizError) as err:
            c.companies()
        message = err.value.message
        for secret in (unseen, SESSION, KEY):
            for i in range(len(secret) - 7):
                assert secret[i:i + 8] not in message, (tail_secret[:6], secret[i:i + 8])


def test_the_cap_masks_a_cut_through_a_known_secret_with_odd_characters():
    from app.wizcloud import _cap

    assert _cap("abc PRIV!ATE and more", 11, ["PRIV!ATE"]) == "abc ***"  # '!' breaks the token run; the prefix rule catches it
    assert _cap("abc def ghi", 9, ["PRIV!ATE"]) == "abc def ***"  # a cut word is masked too: it may be a token's head
    assert _cap("short", 99, ["x" * 20]) == "short"
