import logging

import httpx
import pytest

from app import wizcloud
from app.wizcloud import READ_PATHS, SESSION_TTL_S, WizClient, WizError, parse_session_body
from tests.conftest import KEY, SESSION

# Every mutating path the vendor documents (OpenAPI 1.0.0 + the vendor's npm client).
WRITE_PATHS = [
    "invApi/createDoc",
    "invApi/issueDocument",
    "invApi/delDocument",
    "docsApi/createRecipt",
    "docsApi/createInvRecipt",
    "jtransApi/tmpBatch",
    "jtransApi/chkBatch",
    "jtransApi/newBatch",
    "jtransApi/issueBatch",
    "IndexApi/importIndex",
    "IndexApi/deleteIndex",
    "SortCodeApi/importSortCodes",
    "TransTypesApi/importTransTypes",
    "BankPagesApi/importBankPage",
    "TriggersApi/setURL",
    "TriggersApi/deleteURL",
    "TriggersApi/updateURL",
    "api/napi",
    "crmActivitiesApi/createSchema",
]
# Spellings that would slip past a prefix or case-insensitive check.
TRICKS = [
    "/invApi/getDoc",
    "invApi/getDoc/../createDoc",
    "invapi/getdoc",
    "invApi/getDoc?x=1",
    "invApi/getDoc ",
    "ExportDataApi/exportData/../../invApi/delDocument",
]


def client(fake, clock):
    return WizClient("lb19.wizcloud.co.il", "wizdb2245n4", KEY, transport=fake.transport(), clock=clock)


@pytest.mark.parametrize("path", WRITE_PATHS + TRICKS)
def test_anything_outside_the_read_list_is_refused_before_any_request(fake, clock, path):
    c = client(fake, clock)
    with pytest.raises(WizError) as err:
        c.call(path, {"x": 1})
    assert err.value.kind == "forbidden_path"
    assert fake.requests == []


def test_the_read_list_is_exactly_four_paths():
    assert READ_PATHS == {
        "CompanyListToTokenApi/TokenCompanies",
        "TriggersApi/getURL",
        "ExportDataApi/exportData",
        "invApi/getDoc",
    }
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
