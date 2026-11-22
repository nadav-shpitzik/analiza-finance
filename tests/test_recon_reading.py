"""The reconciliation's reading of monday (Analiza-Monday backlog 0301 R0): the read-only reader, the profile read whole
and counted twice, the store, the routes, and the guard that keeps a monday-side rule off Demo and off a short reading."""

import json

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient

from app import main
from app.monday import client as monday
from app.monday.client import MondayError, MondayReader, flatten
from app.recon import reading as rr
from app.recon import store as rs
from tests.conftest import KEY, TEST_DATABASE_URL, drop_all, needs_pg

TOKEN = "MONDAYTOKEN-0123456789abcdef"  # distinctive, so a leak is easy to grep
SYNC_KEY = "SYNCKEY-0123456789abcdef0123"
STATUS_COLUMNS = {"status", "deal_stage"}


def cell(col: str, item_id: int) -> dict:
    """A cell shaped like monday's answer: a status with its index and label, a relation with its ids, else text."""
    if col.startswith("color_") or col in STATUS_COLUMNS:
        return {"id": col, "text": "שולם", "index": item_id % 3, "label": "שולם"}
    if col.startswith("board_relation"):
        return {"id": col, "text": "", "linked_item_ids": [str(item_id + 1)]}
    return {"id": col, "text": f"{col}-{item_id}"}


class Monday:
    """monday's GraphQL, for the five boards: the columns check, the ids paged, the items by id. `sizes`: board -> how many
    items; `count_off`: board -> what items_count adds; `deleted`: ids that answer as deleted; `columns_missing`: board ->
    columns it lacks; `hidden`: boards the token cannot see; `fail`: a list of responses served first."""

    def __init__(self, sizes=None):
        self.sizes = dict(sizes or {b.board_id: n for b, n in zip(rr.PROFILE, (620, 60, 607, 118, 15))})
        self.count_off, self.deleted, self.columns_missing, self.hidden = {}, set(), {}, set()
        self.fail: list = []
        self.queries: list = []

    def ids(self, board_id: str) -> list:
        base = int(board_id[-4:]) * 10000
        return [base + i for i in range(self.sizes.get(board_id, 0))]

    @staticmethod
    def board_of(item_id: int) -> str:
        return next((b.board_id for b in rr.PROFILE if int(b.board_id[-4:]) == int(item_id) // 10000), "1")

    @staticmethod
    def own_columns(board_id: str) -> set:
        """A board has its own columns only, as monday does: one asked of another board is not there."""
        return set(next((b.columns for b in rr.PROFILE if b.board_id == board_id), ()))

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == TOKEN and request.headers["API-Version"] == monday.MONDAY_API_VERSION
        body = json.loads(request.content)
        query, var = body["query"], body["variables"]
        self.queries.append(query)
        if self.fail:
            return self.fail.pop(0)
        if "columns(ids:" in query:
            data = {}
            for k in [k for k in var if k.startswith("b")]:
                board = var[k][0]
                cols = var["c" + k[1:]]
                data[k] = [] if board in self.hidden else [{"columns": [
                    {"id": c} for c in cols if c in self.own_columns(board) and c not in self.columns_missing.get(board, ())]}]
            return httpx.Response(200, json={"data": data})
        if "next_items_page" in query:
            board, start = var["c"].split(":")
            ids = self.ids(board)[int(start):int(start) + var["limit"]]
            nxt = int(start) + var["limit"]
            cursor = f"{board}:{nxt}" if nxt < len(self.ids(board)) else None
            return httpx.Response(200, json={"data": {"next_items_page": {"cursor": cursor, "items": [{"id": str(i)} for i in ids]}}})
        if "items_page" in query:
            board = var["b"][0]
            if board in self.hidden:
                return httpx.Response(200, json={"data": {"boards": []}})
            ids = self.ids(board)
            cursor = f"{board}:{var['limit']}" if len(ids) > var["limit"] else None
            return httpx.Response(200, json={"data": {"boards": [{
                "items_count": len(ids) + self.count_off.get(board, 0),
                "items_page": {"cursor": cursor, "items": [{"id": str(i)} for i in ids[:var["limit"]]]}}]}})
        if "items(ids:" in query:
            # by id monday answers an item of any board, and drops the columns its board lacks (backlog 0234)
            items = [{"id": str(i), "name": f"שם {i}", "state": "deleted" if int(i) in self.deleted else "active",
                      "created_at": "2026-11-01T00:00:00Z",
                      "column_values": [cell(c, int(i)) for c in var["cols"] if c in self.own_columns(self.board_of(int(i)))]}
                     for i in var["ids"]]
            if "board { id }" in query:  # only what is asked is answered
                for item in items:
                    item["board"] = {"id": self.board_of(int(item["id"]))}
            return httpx.Response(200, json={"data": {"items": items}})
        raise AssertionError("an unexpected query: " + query[:80])


def reader(fake: Monday, **kw) -> MondayReader:
    return MondayReader(TOKEN, client=httpx.Client(transport=httpx.MockTransport(fake.handler)), sleep=lambda s: None, **kw)


# ---- the reader ---------------------------------------------------------------------------------------------------
def test_a_reading_reads_every_board_whole_and_counts_it_twice():
    fake = Monday()
    got = rr.read_profile(reader(fake))
    assert got["complete"] is True and got["error"] is None
    for board in rr.PROFILE:
        check = got["checks"][board.key]
        assert check["items_count"] == check["rows"] == check["unique"] == fake.sizes[board.board_id] and check["ok"]
    # one columns check, then per board its ids in pages of 500 (Student Charge's 620 and Accounts' 607 in two each),
    # then the rows in chunks of 100
    pages = sum(max(1, -(-n // monday.PAGE_LIMIT)) for n in fake.sizes.values())
    chunks = sum(-(-n // monday.ITEMS_BY_ID_CHUNK) for n in fake.sizes.values())
    assert pages == len(rr.PROFILE) + 2 and got["calls"] == 1 + pages + chunks
    assert all(q.lstrip().startswith("query") for q in fake.queries)


def test_a_board_that_reads_short_of_its_items_count_makes_the_reading_incomplete():
    fake = Monday()
    fake.count_off[rr.ACCOUNTS] = 1  # an item that items_count counts and the pages did not bring
    got = rr.read_profile(reader(fake))
    assert got["complete"] is False and got["checks"]["accounts"]["ok"] is False
    assert all(c["ok"] for k, c in got["checks"].items() if k != "accounts")


def test_an_item_deleted_between_its_id_and_its_row_is_a_short_read_not_a_quiet_gap():
    fake = Monday()
    fake.deleted.add(fake.ids(rr.STUDENT_CHARGE)[5])
    got = rr.read_profile(reader(fake))
    check = got["checks"]["student_charge"]
    assert got["complete"] is False and check["rows"] == check["items_count"] - 1


def test_a_column_a_board_lacks_ends_the_reading_instead_of_reading_empty():
    """monday drops an unknown column id from column_values with no error (Analiza-Monday backlog 0234)."""
    fake = Monday()
    fake.columns_missing[rr.B2B_CHARGE] = {rr.B2["invoice"]}
    got = rr.read_profile(reader(fake))
    assert got["complete"] is False and got["error"] == "unknown_column" and got["boards"] == {}
    assert rr.B2["invoice"] in got["detail"] and not any("items(ids:" in q for q in fake.queries)


def test_a_board_the_token_cannot_see_is_not_visible():
    fake = Monday()
    fake.hidden.add(rr.VOUCHERS)
    assert rr.read_profile(reader(fake))["error"] == "not_visible"


def test_the_reader_never_sends_a_mutation():
    fake = Monday()
    with pytest.raises(MondayError) as err:
        reader(fake).post("mutation { change_simple_column_value(item_id: 1, board_id: 2, column_id: \"x\", value: \"y\") { id } }", {})
    assert err.value.code == "graphql" and fake.queries == []


@pytest.mark.parametrize("answer, code", [
    (httpx.Response(401), "auth"),
    (httpx.Response(200, json={"errors": [{"message": "Not Authenticated", "extensions": {"code": "UNAUTHENTICATED"}}]}), "auth"),
    (httpx.Response(200, json={"errors": [{"message": "Column not found", "extensions": ["odd"]}]}), "graphql"),
    (httpx.Response(200, json={"error_code": "InvalidTokenException", "error_message": "invalid token"}), "auth"),
])
def test_errors_have_a_closed_code_and_never_the_token(answer, code):
    fake = Monday()
    fake.fail = [answer]
    got = rr.read_profile(reader(fake))
    assert got["error"] == code and TOKEN not in json.dumps(got) and len(fake.queries) == 1


def test_a_5xx_or_a_rate_limit_is_retried_once():
    fake = Monday()
    fake.fail = [httpx.Response(500)]
    assert rr.read_profile(reader(fake))["complete"] is True
    fake = Monday()
    fake.fail = [httpx.Response(429, headers={"Retry-After": "1"}), httpx.Response(503)]
    got = rr.read_profile(reader(fake))
    assert got["error"] == "http" and got["calls"] == 2


def test_no_request_starts_after_the_deadline():
    fake = Monday()
    now = [100.0]
    r = reader(fake, clock=lambda: now[0])
    r.deadline = 99.0
    got = rr.read_profile(r, clock=lambda: now[0])
    assert got["error"] == "deadline" and fake.queries == []


def test_a_status_is_kept_by_its_index_and_a_relation_by_its_ids():
    row = flatten({"id": 5, "name": "x\x00", "state": "archived", "board": {"id": 42}, "column_values": [
        {"id": "color_a", "text": "שולם", "index": 1, "label": "שולם"},
        {"id": "board_relation_b", "text": "", "linked_item_ids": [7, "8"]},
        {"id": "text_c", "text": "631\x0009"}]})
    assert row == {"id": "5", "name": "x", "created_at": "", "state": "archived", "board_id": "42", "color_a": "שולם",
                   "color_a#": "1", "board_relation_b": ["7", "8"], "text_c": "63109"}  # a NUL never reaches jsonb


# ---- what is never read -------------------------------------------------------------------------------------------
def test_no_column_of_the_profile_is_one_that_is_never_read():
    """ADR-0173 §1: an ID number is never stored server side - Leads' and the vouchers board's ת.ז, Accounts' business
    number (a sole proprietor's ID number) - and neither is a refund's bank account."""
    read = {c for b in rr.PROFILE for c in b.columns}
    assert not read & rr.NEVER_READ
    assert {"text_mkzvem4", "text_mm0kgsx9", "text_mm3y3gm7"} <= rr.NEVER_READ


def test_the_store_refuses_a_body_that_carries_a_never_read_column():
    with pytest.raises(rs.UnsafeBody):
        rs.refuse_unsafe({"vouchers": [{"id": "1", "text_mm0kgsx9": "123456789"}]})
    with pytest.raises(rs.UnsafeBody):
        rs.refuse_unsafe({"accounts": [{"id": "1", "text_mm3y3gm7#": ""}]})
    rs.refuse_unsafe({"accounts": [{"id": "1", rr.AC["hash_key"]: "10091"}]})


# ---- the guard: the acceptance test of R0 ------------------------------------------------------------------------
def test_a_monday_side_rule_refuses_to_run_on_demo_and_on_an_incomplete_reading():
    """The rules' contract §0.6: a monday-side rule runs only on a complete reading, and never on Demo."""
    ran = []

    @rr.monday_side
    def rule(company, reading):
        ran.append(company)
        return "matched"

    complete = rr.read_profile(reader(Monday()))
    short_fake = Monday()
    short_fake.count_off[rr.DEALS] = 1
    short = rr.read_profile(reader(short_fake))
    cases = [("demo", complete, "demo"), ("analiza", None, "no_reading"),
             ("analiza", {**complete, "complete": False, "error": "deadline"}, "failed"),
             ("analiza", {**complete, "error": "crashed"}, "failed"),  # complete, and yet it says it failed
             ("analiza", short, "incomplete"), ("other", complete, "company"),
             ("analiza", {"complete": True}, "incomplete"),  # a flag is not a reading
             ("analiza", {**complete, "read_at": "2026-01-01T00:00:00+00:00"}, "stale")]
    for company, reading, reason in cases:
        with pytest.raises(rr.NotReady) as err:
            rule(company, reading)
        assert err.value.reason == reason
    assert ran == [] and rule("analiza", complete) == "matched" and ran == ["analiza"] and rule.monday_side is True


# ---- the store and the routes -------------------------------------------------------------------------------------
@pytest.fixture
def pg():
    drop_all(TEST_DATABASE_URL)
    from app.migrate import apply_migrations

    apply_migrations(TEST_DATABASE_URL)
    yield TEST_DATABASE_URL
    drop_all(TEST_DATABASE_URL)


@needs_pg
def test_a_reading_is_kept_and_a_failed_one_replaces_it(pg):
    good = rr.read_profile(reader(Monday()))
    rs.save_reading(pg, good)
    kept = rs.load_reading(pg)
    assert kept["complete"] is True and len(kept["boards"]["student_charge"]) == 620
    assert kept["checks"]["deals"]["items_count"] == 118
    assert rr.why_not_ready("analiza", kept) is None
    fake = Monday()
    fake.fail = [httpx.Response(401)]
    rs.save_reading(pg, rr.read_profile(reader(fake)))
    after = rs.load_reading(pg)
    assert after["error"] == "auth" and after["boards"] == {} and rr.why_not_ready("analiza", after) == "failed"


@needs_pg
def test_only_one_reading_of_the_profile_runs_at_a_time(pg):
    with rs.reading_lock(pg) as first:
        with rs.reading_lock(pg) as second:
            assert (first, second) == (True, False)


@pytest.fixture
def recon_app(pg, monkeypatch):
    monkeypatch.setenv("WIZ_PRIVATE_KEY", KEY)
    monkeypatch.setenv("DATABASE_URL", pg)
    monkeypatch.setenv("BOOKS_SYNC_KEY", SYNC_KEY)
    monkeypatch.setenv("MONDAY_TOKEN", TOKEN)
    fake = Monday()
    main._store_cache.update(at=0.0, key=None, value=None)
    main.app.state.monday_transport = httpx.MockTransport(fake.handler)
    yield TestClient(main.app), fake
    main.app.state.monday_transport = None
    main._store_cache.update(at=0.0, key=None, value=None)


@needs_pg
def test_the_run_route_reads_keeps_and_answers_counts_only(recon_app):
    client, fake = recon_app
    auth = {"Authorization": f"Bearer {SYNC_KEY}"}
    assert client.post("/api/recon/run").status_code == 401
    body = client.post("/api/recon/run", headers=auth).json()
    assert body["ok"] is True and body["complete"] is True and body["rules"] is None
    assert body["checks"]["student_charge"] == {"board_id": rr.STUDENT_CHARGE, "items_count": 620, "rows": 620,
                                                "unique": 620, "strangers": 0, "short_rows": 0, "ok": True}
    text = json.dumps(body, ensure_ascii=False)
    for leak in ("שם ", TOKEN, "text_mm6d6s97-"):
        assert leak not in text
    status = client.get("/api/recon/status", headers=auth).json()
    assert status["reading"]["checks"] == body["checks"] and "boards" not in json.dumps(status)
    health = client.get("/health").json()
    assert health["recon"]["monday_token_set"] is True and health["store"]["missing_columns"] == []
    last = health["recon"]["last_reading"]
    assert set(last) == {"read_at", "complete", "error"} and last["complete"] is True  # no count on the public page


@needs_pg
def test_the_run_route_says_what_is_missing(recon_app, monkeypatch):
    client, _fake = recon_app
    monkeypatch.delenv("MONDAY_TOKEN")
    body = client.post("/api/recon/run", headers={"Authorization": f"Bearer {SYNC_KEY}"}).json()
    assert body == {"ok": False, "skipped": False, "error": "not_configured", "detail": "MONDAY_TOKEN not set"}
    assert client.get("/health").json()["recon"]["monday_token_set"] is False


# ---- what the review's mutants showed no test held (22.11) -----------------------------------------------------
class Duplicated(Monday):
    """The second page of Student Charge's ids repeats the last id of the first, and items_count counts it."""

    def handler(self, request):
        resp = super().handler(request)
        body = json.loads(request.content)
        if "next_items_page" in body["query"] and body["variables"]["c"].startswith(rr.STUDENT_CHARGE):
            data = json.loads(resp.content)
            first_page_last = str(self.ids(rr.STUDENT_CHARGE)[monday.PAGE_LIMIT - 1])
            data["data"]["next_items_page"]["items"].insert(0, {"id": first_page_last})
            return httpx.Response(200, json=data)
        return resp


def test_an_id_that_comes_twice_is_not_a_whole_board_even_when_the_counts_agree():
    fake = Duplicated()
    fake.count_off[rr.STUDENT_CHARGE] = 1  # items_count agrees with the rows read; only the distinct ids tell
    check = rr.read_profile(reader(fake))["checks"]["student_charge"]
    assert check["items_count"] == check["rows"] == 621 and check["unique"] == 620 and check["ok"] is False


@needs_pg
def test_the_save_itself_refuses_a_body_that_carries_a_never_read_column(pg):
    got = rr.read_profile(reader(Monday()))
    got["boards"]["vouchers"][0]["text_mm0kgsx9"] = "123456789"
    with pytest.raises(rs.UnsafeBody):
        rs.save_reading(pg, got)
    assert rs.load_reading(pg) is None


@needs_pg
def test_the_route_is_not_ok_on_a_reading_that_came_short(recon_app):
    client, fake = recon_app
    fake.count_off[rr.ACCOUNTS] = 1
    body = client.post("/api/recon/run", headers={"Authorization": f"Bearer {SYNC_KEY}"}).json()
    assert body["ok"] is False and body["complete"] is False and body["checks"]["accounts"]["ok"] is False


def test_every_request_waits_at_most_what_is_left_and_never_past_twenty_seconds():
    seen, now = [], [0.0]

    def handler(request):
        seen.append(request.extensions["timeout"]["read"])
        return Monday().handler(request)

    r = MondayReader(TOKEN, client=httpx.Client(transport=httpx.MockTransport(handler)), clock=lambda: now[0])
    r.deadline = 7.0
    r.require_columns([(rr.VOUCHERS, rr.PROFILE[4].columns)])
    r.deadline = None
    r.require_columns([(rr.VOUCHERS, rr.PROFILE[4].columns)])
    assert seen == [7.0, monday.READ_TIMEOUT_SECONDS]


def test_a_retry_that_would_sleep_past_the_deadline_is_not_made():
    fake, slept, now = Monday(), [], [0.0]
    fake.fail = [httpx.Response(429, headers={"Retry-After": "30"})]
    r = MondayReader(TOKEN, client=httpx.Client(transport=httpx.MockTransport(fake.handler)), sleep=slept.append,
                     clock=lambda: now[0])
    r.deadline = 5.0
    with pytest.raises(MondayError) as err:
        r.require_columns([(rr.VOUCHERS, rr.PROFILE[4].columns)])
    assert err.value.code == "deadline" and slept == []


def test_a_transport_error_is_retried_once():
    fake, failures = Monday(), [2]

    def handler(request):
        if failures[0]:
            failures[0] -= 1
            raise httpx.ConnectError("refused")
        return fake.handler(request)

    once = MondayReader(TOKEN, client=httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda s: None)
    with pytest.raises(MondayError) as err:
        once.require_columns([(rr.VOUCHERS, rr.PROFILE[4].columns)])
    assert err.value.code == "http" and once.calls == 2
    failures[0] = 1
    again = MondayReader(TOKEN, client=httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda s: None)
    again.require_columns([(rr.VOUCHERS, rr.PROFILE[4].columns)])
    assert again.calls == 2


def test_a_cursor_that_never_ends_is_an_error_not_a_hang(monkeypatch):
    monkeypatch.setattr(monday, "MAX_PAGES", 1)
    assert rr.read_profile(reader(Monday()))["error"] == "too_many_pages"  # Student Charge needs two pages


def test_the_four_bank_columns_are_never_read():
    assert {"text_mm8cxm9y", "text_mm8cv3v3", "text_mm8cgc0d", "text_mm8ckgh8"} <= rr.NEVER_READ


def test_a_request_that_times_out_at_the_deadline_on_its_last_try_says_deadline():
    fake, now, tries = Monday(), [0.0], [0]

    def handler(request):
        tries[0] += 1
        if tries[0] == 1:
            raise httpx.ConnectError("refused")  # with time left: retried
        now[0] = 9.8  # the second try runs into the deadline
        raise httpx.ReadTimeout("read")

    r = MondayReader(TOKEN, client=httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda s: None,
                     clock=lambda: now[0])
    r.deadline = 10.0
    with pytest.raises(MondayError) as err:
        r.require_columns([(rr.VOUCHERS, rr.PROFILE[4].columns)])
    assert err.value.code == "deadline" and tries[0] == 2


@needs_pg
def test_an_incomplete_reading_replaces_a_complete_one_flag_and_all(pg):
    rs.save_reading(pg, rr.read_profile(reader(Monday())))
    fake = Monday()
    fake.count_off[rr.DEALS] = 1
    rs.save_reading(pg, rr.read_profile(reader(fake)))
    kept = rs.load_reading(pg)
    assert kept["complete"] is False and rr.why_not_ready("analiza", kept) == "incomplete"  # the status and /health show the flag


class Vanished(Monday):
    """The pages give one id more than items_count, and that item answers as deleted: the counts then agree (n-1 each),
    no row is a stranger, and only the ids the pages gave tell that one of them never came back."""

    def handler(self, request):
        resp = super().handler(request)
        body = json.loads(request.content)
        if "items_page" in body["query"] and "next_items_page" not in body["query"] and body["variables"]["b"] == [rr.DEALS]:
            data = json.loads(resp.content)
            data["data"]["boards"][0]["items_count"] -= 1
            return httpx.Response(200, json=data)
        return resp


def test_an_id_the_pages_gave_that_never_came_back_is_not_a_whole_board():
    fake = Vanished()
    fake.deleted.add(fake.ids(rr.DEALS)[0])
    check = rr.read_profile(reader(fake))["checks"]["deals"]
    n = fake.sizes[rr.DEALS] - 1
    assert check["items_count"] == check["rows"] == check["unique"] == n and check["strangers"] == 0
    assert check["ok"] is False


class Foreign(Monday):
    """One Deals row answers from another board, with every column asked (two boards can share column ids)."""

    def handler(self, request):
        resp = super().handler(request)
        body = json.loads(request.content)
        if "items(ids:" in body["query"] and str(self.ids(rr.DEALS)[3]) in body["variables"]["ids"]:
            data = json.loads(resp.content)
            for item in data["data"]["items"]:
                if item["id"] == str(self.ids(rr.DEALS)[3]):
                    item["board"] = {"id": "5000000000"}
            return httpx.Response(200, json=data)
        return resp


def test_a_row_from_another_board_is_a_stranger_even_with_every_column():
    check = rr.read_profile(reader(Foreign()))["checks"]["deals"]
    assert check["strangers"] == 1 and check["short_rows"] == 0 and check["ok"] is False


class Malformed(Monday):
    """An item with no id: not a monday error, a crash of the reading."""

    def handler(self, request):
        resp = super().handler(request)
        if "items(ids:" in json.loads(request.content)["query"]:
            data = json.loads(resp.content)
            del data["data"]["items"][0]["id"]
            return httpx.Response(200, json=data)
        return resp


@needs_pg
def test_a_reading_that_crashes_is_kept_as_failed_and_replaces_the_last(pg):
    rs.save_reading(pg, rr.read_profile(reader(Monday())))
    crashed = rr.read_profile(reader(Malformed()))
    assert crashed["error"] == "crashed" and crashed["detail"] == "KeyError" and crashed["boards"] == {}
    rs.save_reading(pg, crashed)
    assert rr.why_not_ready("analiza", rs.load_reading(pg)) == "failed"
