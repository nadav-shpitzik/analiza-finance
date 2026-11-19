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


def test_a_partial_echo_of_the_session_is_masked(clock):
    """A 2xx body that is not JSON and echoes the session, whole or cut: no 12 characters of it survive."""
    partial = SESSION[:20]

    def handler(request):
        if request.url.path.startswith("/createSession/"):
            return httpx.Response(200, text=SESSION)
        return httpx.Response(200, text=f"<html>bad {SESSION} and {partial}xyz and {KEY[3:18]}</html>" + "." * 300)

    c = WizClient("lb19.wizcloud.co.il", "wizdb2245n4", KEY, transport=httpx.MockTransport(handler), clock=clock)
    with pytest.raises(WizError) as err:
        c.companies()
    text = str(err.value) + str(err.value.as_dict())
    for i in range(len(SESSION) - 11):
        assert SESSION[i:i + 12] not in text
    for i in range(len(KEY) - 11):
        assert KEY[i:i + 12] not in text
    assert "<html>bad ***" in err.value.message


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
