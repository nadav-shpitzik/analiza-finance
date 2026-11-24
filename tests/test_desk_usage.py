"""How the desk is used (D2): app/desk/usage.py's closed vocabulary, POST /api/usage, /health's `desk.usage`, and
`desk_events` (migrations/005) - analiza-metrics tests/test_usage.py at `da671b2`, ported onto this service's gate, store
and /health.

Nadav asked for Chava's usage of the finance desk, to improve it over time. A row holds a monday user id and closed words
and counts - never a name, an amount, the search's text or an ID number - and this is where that is held: every value
outside the lists is refused, by the module that writes, before the database is reached. The page's side here is static
(every kind and detail key the page names is in the lists); tests/test_desk_page.py drives the page in node and passes
what it sends through clean_batch."""
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

from app import main, migrate
from app.desk import routes as dr
from app.desk import store, usage
from tests.conftest import TEST_DATABASE_URL, drop_all, needs_pg
from tests.test_desk import SECRET, desk, session_token  # noqa: F401 - `desk` is the fixture

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "frontend" / "finance.js"
SESSION_ID = "k3j4h5g6f7d8s9a0"
READER = 7301
DUMMY_URL = "postgresql://nobody@127.0.0.1:1/never"  # never connected to: the writer is replaced in these tests
STORE_HEALTH = main.store_health  # the real one: the `use` fixture stubs it, so /health reads no database there


def batch(*events, desk="finance", session_id=SESSION_ID) -> dict:
    return {"desk": desk, "session": session_id, "events": list(events)}


def ev(k_="state", **kw) -> dict:
    """An event as the page sends it; keyword arguments other than t, tab and st are its details."""
    return {"k": k_, "t": kw.pop("t", 1200), "tab": kw.pop("tab", "b2c"), "st": kw.pop("st", "tasks"), **({"d": kw} if kw else {})}


def page_code() -> str:
    """finance.js without its comments: a comment may tell the history, the code may not hold it."""
    return re.sub(r"/\*.*?\*/", "", PAGE.read_text(encoding="utf-8"), flags=re.S)


# --- the vocabulary -----------------------------------------------------------------------------------------------------

def test_a_well_formed_batch_becomes_rows_of_the_readers_own_id():
    rows, refused = usage.clean_batch(batch(ev("open", ms=812, board="sc", refreshing=True), ev("state"),
                                            ev("search", len=9, kind="digits", results=0, unfiltered=2),
                                            ev("period", period="2026-09+2026-10"), ev("hide")), 7)
    assert refused == 0 and [r["kind"] for r in rows] == ["open", "state", "search", "period", "hide"]
    assert rows[0] == {"kind": "open", "page_ms": 1200, "tab": "b2c", "state": "tasks", "user_id": 7,
                       "session": SESSION_ID, "desk": "finance", "detail": {"ms": 812, "board": "sc", "refreshing": True}}
    assert rows[-1]["detail"] == {}


@pytest.mark.parametrize("event", [
    ev("search", len=9, kind="דנה", results=0, unfiltered=0),        # a Hebrew word: a name could pass as a "kind"
    ev("search", len=9, kind="012345678"),                          # an ID number as a word
    ev("search", len=9, kind="digits", results=123456789),          # an ID number as a count
    ev("search", len=9, kind="digits", q="דנה כהן"),                 # the search's text, under any key
    ev("id_copied", code="", number="012345678"),
    ev("row", state="done", open=True, item="5101234567"),          # an item id
    ev("act", action="refund_done", phase="done", amount="4000"),
    ev("open", ms=1.5), ev("open", ms=-1), ev("open", ms=True), ev("open", ms=600_001),   # a float, below, a bool, above
    ev("legend", open="yes"), ev("period", period="אוקטובר"), ev("period", period="2026-10; drop"),
    ev("teleport"), ev("state", st="Tasks"), ev("state", st="123"), ev("state", tab="b2x"),
    {"k": "state", "t": -5}, {"k": "state", "t": "5"}, {"k": "state", "extra": 1}, "state", None,
])
def test_an_event_outside_the_lists_is_refused_and_the_rest_of_its_batch_is_kept(event):
    rows, refused = usage.clean_batch(batch(ev("state"), event, ev("hide")), 7)
    assert refused == 1 and [r["kind"] for r in rows] == ["state", "hide"]


@pytest.mark.parametrize("body", [
    batch(ev(), desk="sales"), batch(ev(), session_id="short"), batch(ev(), session_id="UPPER-and-dashes-123"),
    batch(), {"desk": "finance", "session": SESSION_ID, "events": "state"}, batch(*[ev()] * (usage.MAX_EVENTS + 1)),
    [ev()], None,
])
def test_a_batch_of_another_shape_is_refused_whole(body):
    with pytest.raises(usage.Refused) as e:
        usage.clean_batch(body, 7)
    assert e.value.code == "bad_usage"


def test_a_page_left_open_for_weeks_keeps_its_age_capped():
    rows, _ = usage.clean_batch(batch(ev(t=10 ** 12)), 7)
    assert rows[0]["page_ms"] == usage.PAGE_MS_MAX


# --- the page against the vocabulary (static; the page driven in node is tests/test_desk_page.py) ----------------------

# The one kind the server keeps and the ported page no longer sends: the billing details are read only on the page until
# their edit comes back from the browser (finance.js, its change 4).
NOT_SENT = {"bill"}


def _object_body(code: str, start: int) -> str:
    """The text between the braces that open at `start`, string literals skipped while the braces are counted."""
    depth, i = 0, start
    while True:
        ch = code[i]
        if ch in "\"'":
            j = i + 1
            while code[j] != ch:
                j += 2 if code[j] == "\\" else 1
            i = j
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return code[start + 1:i]
        i += 1


def page_events(code: str) -> list:
    """Each `use("kind", {...})` call of the page: (kind, {detail key: the string literals of its value}). A value's
    strings are the ones that can reach the server: those inside a call or an index (`contains("fin-name")`) are not."""
    out = []
    for m in re.finditer(r'\buse\("([a-z_]+)"\s*(,\s*\{)?', code):
        details: dict = {}
        if m.group(2):
            literals: list = []

            def hold(s, literals=literals):
                literals.append(s.group(1))
                return "\x00%d\x00" % (len(literals) - 1)

            body = re.sub(r'"((?:[^"\\]|\\.)*)"', hold, _object_body(code, m.end() - 1))
            while True:  # what sits inside a call, an index or a nested object is not a value of the detail
                flat = re.sub(r"\([^()]*\)|\[[^\[\]]*\]|\{[^{}]*\}", "", body)
                if flat == body:
                    break
                body = flat
            for part in body.split(","):
                key = re.match(r"\s*([A-Za-z_$][\w$]*)\s*:", part)
                assert key, (m.group(1), part)
                details[key.group(1)] = [literals[int(n)] for n in re.findall(r"\x00(\d+)\x00", part)]
        out.append((m.group(1), details))
    return out


def test_the_page_sends_only_the_kinds_the_server_keeps_and_the_server_keeps_none_the_page_never_sends_but_bill():
    code = page_code()
    sent = set(re.findall(r'\buse\("([a-z_]+)"', code))
    # the ID number's two outcomes when it was read are named in one expression (e.value ? "id_shown" : "id_empty")
    sent |= set(re.findall(r'"(id_[a-z]+)"', code))
    assert sent == set(usage.KINDS) - NOT_SENT, sent ^ (set(usage.KINDS) - NOT_SENT)
    assert not sent & NOT_SENT and NOT_SENT <= set(usage.KINDS)
    # and the page's word for an error is the server's alphabet (useWord)
    assert "useWord" in code and usage.WORD.pattern.startswith("(?:[a-z]")


def test_every_detail_the_page_names_is_its_kinds_own_and_every_literal_value_passes_the_server():
    calls = page_events(page_code())
    assert len(calls) >= 25 and {k for k, _ in calls} == set(usage.KINDS) - NOT_SENT - {"id_shown", "id_empty"}
    for kind, details in calls:
        assert set(details) <= set(usage.KINDS[kind]), (kind, sorted(details))
        for key, values in details.items():
            for value in values:
                assert usage._value_ok(key, value), (kind, key, value)
    # each kind with details sends every one of its keys somewhere (none of the list is dead but bill's)
    named = {}
    for kind, details in calls:
        named.setdefault(kind, set()).update(details)
    assert all(named.get(k, set()) == set(keys) for k, keys in usage.KINDS.items() if k not in NOT_SENT | {"id_shown", "id_empty"})


def test_the_reader_of_the_page_is_not_fooled_by_a_value_with_commas_or_braces():
    """The parser above, on a made-up call: a value with a call, an index and a nested object in it is one value."""
    code = 'use("open", { ms: Math.min(600000, Math.max(0, x)), board: a[b, c] ? "sc" : "", refreshing: !!{ a: 1, b: "x" }.a })'
    assert page_events(code) == [("open", {"ms": [], "board": ["sc", ""], "refreshing": []})]


# --- POST /api/usage ----------------------------------------------------------------------------------------------------

class Writer:
    """app/desk/store.py append_events as the route calls it, in memory: the rows as they would be written, `detail` as
    its JSON text. `fail` makes every call raise it."""

    def __init__(self, fail: Exception | None = None):
        self.rows: list = []
        self.calls = 0
        self.fail = fail

    def __call__(self, url, rows):
        self.calls += 1
        if self.fail is not None:
            raise self.fail
        for r in rows:
            if set(r) != store.ROW_FIELDS:
                raise store.UnknownRow(",".join(sorted(set(r) ^ store.ROW_FIELDS)))
        self.rows += [dict(r, detail=json.dumps(r["detail"], ensure_ascii=True)) for r in rows]
        return len(rows)


@pytest.fixture
def use(monkeypatch):
    """The app with the client secret and a database URL that is never connected to: the writer is `use.writer`."""
    monkeypatch.setenv("MONDAY_CLIENT_SECRET", SECRET)
    monkeypatch.setenv("DATABASE_URL", DUMMY_URL)
    monkeypatch.setattr(main, "store_health", lambda cfg, now: {"configured": True})  # /health reads no database here
    writer = Writer()
    monkeypatch.setattr(dr, "append_events", writer)
    main.app.state.usage = None

    class U:
        pass

    u = U()
    u.writer, u.client = writer, TestClient(main.app)
    u.post = lambda body, user=READER, token=None, **dat: u.client.post(
        "/api/usage", json=body, headers={"Authorization": "Bearer " + (token or session_token(user, **dat))})
    u.health = lambda: u.client.get("/health").json()["desk"]["usage"]
    yield u
    main.app.state.usage = None


def test_a_signed_in_reader_s_batch_is_kept_under_their_own_id_and_said_in_health(use):
    r = use.post(batch(ev("open", ms=900, board="b2"), ev("teleport"), ev("hide")))
    assert r.status_code == 200 and r.json() == {"stored": 2, "refused": 1}
    assert r.headers["cache-control"] == "no-store"
    assert [(x["user_id"], x["kind"], x["desk"], x["session"]) for x in use.writer.rows] == [
        (READER, "open", "finance", SESSION_ID), (READER, "hide", "finance", SESSION_ID)]
    assert json.loads(use.writer.rows[0]["detail"]) == {"ms": 900, "board": "b2"}
    h = use.health()
    assert h["batches"] == 1 and h["stored"] == 2 and h["refused"] == 1 and h["failed"] == 0 and h["last_error"] is None
    # /health is open to anyone: it never names a person, a kind or a detail. The time is left out of the look: a reader's
    # digits can be in a date (analiza-metrics, 17.11)
    said = json.dumps({k: v for k, v in h.items() if k != "last_at"})
    assert "open" not in said and str(READER) not in said


def test_any_signed_in_reader_of_the_account_may_send_their_own_and_another_account_may_not(use):
    """Usage is the sessionToken only: a reader the desk refuses (on neither board, a guest, a viewer) still sends their own
    `load_failed`, under the id the token names. Another account, a forged token or none is 401, and nothing is kept."""
    for dat in ({}, {"is_guest": True}, {"is_view_only": True}):
        assert use.post(batch(ev("load_failed", code="not_a_board_member")), user=9, **dat).status_code == 200
    assert {x["user_id"] for x in use.writer.rows} == {9}
    assert use.post(batch(ev()), token=session_token(READER, account_id=999)).json() == {"error": "unauthorized"}
    assert use.post(batch(ev()), token=session_token(READER, secret="another")).status_code == 401
    assert use.client.post("/api/usage", json=batch(ev())).status_code == 401
    assert use.post(batch(ev()), token=session_token(READER, exp=time.time() - 3600)).json() == {"error": "token_expired"}
    assert len(use.writer.rows) == 3 and use.health()["batches"] == 3


def test_no_session_no_usage_and_a_batch_of_another_shape_is_400(use):
    r = use.post(batch(ev(), desk="sales"))
    assert r.status_code == 400 and r.json() == {"error": "bad_usage"}
    # a field the batch does not have (the user id above all: it is the session's, never the body's) - as every desk body
    r = use.post({**batch(ev()), "user_id": 1})
    assert r.status_code == 400 and r.json() == {"error": "invalid_filter"}
    r = use.client.post("/api/usage", content=b"not json", headers={"Authorization": "Bearer " + session_token(READER),
                                                                     "Content-Type": "application/json"})
    assert r.status_code == 400 and r.json() == {"error": "invalid_filter"}
    assert use.writer.calls == 0
    # the batch of another shape is counted as failed, by its word; the bodies that never reached the route are not
    h = use.health()
    assert (h["batches"], h["failed"], h["stored"], h["last_error"]) == (1, 1, 0, "bad_usage")


def test_without_a_database_nothing_is_kept_and_the_page_is_told_so(use, monkeypatch):
    monkeypatch.delenv("DATABASE_URL")
    r = use.post(batch(ev(), ev("teleport")))
    assert r.status_code == 503 and r.json() == {"error": "usage_unavailable"}
    assert use.writer.calls == 0
    h = use.health()
    assert (h["failed"], h["refused"], h["last_error"]) == (1, 1, "no_store")


def test_without_the_client_secret_usage_says_so_and_keeps_nothing(use, monkeypatch):
    monkeypatch.delenv("MONDAY_CLIENT_SECRET")
    r = use.post(batch(ev()))
    assert r.status_code == 503 and r.json() == {"error": "app_not_configured", "missing": ["MONDAY_CLIENT_SECRET"]}
    assert use.writer.calls == 0 and use.health()["batches"] == 0


@pytest.mark.parametrize("fail, word", [
    (RuntimeError("connection to db.internal:5432 failed for user finance"), "RuntimeError"),
    (store.UsageStoreError("store_schema", "42P01"), "store_schema"),
    (store.UsageStoreError("store_unreachable", "OperationalError"), "store_unreachable"),
])
def test_a_write_that_fails_is_said_in_health_by_its_word_and_never_raises(use, caplog, fail, word):
    use.writer.fail = fail
    with caplog.at_level(logging.INFO, logger="analiza_finance.desk"):
        r = use.post(batch(ev()))
    assert r.status_code == 503 and r.json() == {"error": "usage_unavailable"}
    h = use.health()
    assert h["failed"] == 1 and h["last_error"] == word and h["stored"] == 0
    logged = "\n".join(x.getMessage() for x in caplog.records)
    assert "usage not written (%s" % word in logged and "db.internal" not in logged and "user finance" not in logged


def test_the_audit_line_names_counts_only(use, caplog):
    with caplog.at_level(logging.INFO, logger="analiza_finance.desk"):
        use.post(batch(ev("search", len=7, kind="hebrew", results=0, unfiltered=1)))
        use.post(batch(ev("search", len=7, kind="hebrew", results=0, unfiltered=1), ev("search", q="דנה כהן")))
    lines = [x.getMessage() for x in caplog.records if "endpoint=usage" in x.getMessage()]
    assert lines == ["desk user=%d endpoint=usage outcome=ok results=1" % READER,
                     "desk user=%d endpoint=usage outcome=some_refused results=1" % READER]


def test_usage_never_takes_from_the_readers_desk_budget_and_has_a_limit_of_its_own(desk, monkeypatch):  # noqa: F811
    monkeypatch.setattr(dr, "USAGE_BATCHES_PER_USER_HOUR", 3)
    monkeypatch.setattr(dr, "DESK_PER_USER_HOUR", 2)
    monkeypatch.setenv("DATABASE_URL", DUMMY_URL)
    writer = Writer()
    monkeypatch.setattr(dr, "append_events", writer)
    main.app.state.usage = None
    auth = {"Authorization": "Bearer " + session_token(1)}
    try:
        for _ in range(3):
            assert desk.client.post("/api/usage", headers=auth, json=batch(ev())).status_code == 200
        r = desk.client.post("/api/usage", headers=auth, json=batch(ev()))
        assert r.status_code == 429 and r.json()["error"] == "rate_limited" and int(r.headers["Retry-After"]) > 0
        # the reader's own desk budget (2 an hour here) is whole: the desk still answers, and only then is it spent
        assert [desk.client.get("/api/desk", headers=auth).status_code for _ in range(3)] == [200, 200, 429]
        assert len(writer.rows) == 3
    finally:
        main.app.state.usage = None


# --- desk_events in Postgres (skipped without TEST_DATABASE_URL) --------------------------------------------------------

def _sql(url, sql, params=()):
    with psycopg.connect(url, autocommit=True) as conn:
        cur = conn.execute(sql, params)
        return cur.fetchall() if cur.description else cur.rowcount


def _schema(url) -> dict:
    with psycopg.connect(url, autocommit=True) as conn, conn.cursor() as cur:
        return migrate.schema_state(cur)


@pytest.fixture
def pg():
    drop_all(TEST_DATABASE_URL)
    yield TEST_DATABASE_URL
    drop_all(TEST_DATABASE_URL)


@needs_pg
def test_a_batch_is_written_to_desk_events_in_one_transaction(pg):
    migrate.apply_migrations(pg)
    rows, _ = usage.clean_batch(batch(ev("open", ms=812, board="sc"), ev("search", len=9, kind="digits", results=0,
                                                                          unfiltered=2)), 7)
    assert store.append_events(pg, rows) == 2
    got = _sql(pg, "SELECT user_id, session, desk, kind, tab, state, page_ms, detail FROM desk_events ORDER BY id")
    assert got == [(7, SESSION_ID, "finance", "open", "b2c", "tasks", 1200, {"ms": 812, "board": "sc"}),
                   (7, SESSION_ID, "finance", "search", "b2c", "tasks", 1200,
                    {"len": 9, "kind": "digits", "results": 0, "unfiltered": 2})]
    # a row the store does not recognise is refused before anything is written, and names fields, never values
    with pytest.raises(store.UnknownRow) as e:
        store.append_events(pg, [rows[0], dict(rows[0], name="דנה")])
    assert str(e.value) == "name"
    # all or none: a batch whose second row the database refuses writes nothing of the first
    with pytest.raises(store.UsageStoreError) as e:
        store.append_events(pg, [rows[0], dict(rows[1], user_id=2 ** 70)])
    assert (e.value.kind, e.value.detail) == ("store_error", "22003")
    assert _sql(pg, "SELECT count(*) FROM desk_events")[0][0] == 2
    assert store.append_events(pg, []) == 0
    state = _schema(pg)
    assert state["tables"]["desk_events"] == "present" and state["missing_columns"] == []


@needs_pg
def test_a_database_without_the_table_is_said_and_the_table_is_never_made_at_runtime(pg, use, monkeypatch):
    """Rule 8: the deployment never changes its own schema. A batch meets a missing table as `store_schema`, the page is
    told usage_unavailable, /health says it twice (the usage's word, and the store's missing table), and the table is
    still missing after it."""
    monkeypatch.setattr(dr, "append_events", store.append_events)
    monkeypatch.setenv("DATABASE_URL", pg)
    r = use.post(batch(ev("hide")))
    assert r.status_code == 503 and r.json() == {"error": "usage_unavailable"}
    assert use.health()["last_error"] == "store_schema"
    assert _schema(pg)["tables"]["desk_events"] == "absent"
    main._store_cache.update(at=0.0, key=None, value=None)
    try:
        from app import config

        health = STORE_HEALTH(config.load(), time.time())
        assert "desk_events" in health["missing_columns"] and health["ok"] is False
        assert health["tables"]["desk_events"] == "absent"
    finally:
        main._store_cache.update(at=0.0, key=None, value=None)
    # once the Shell's migration ran, the same batch is kept
    migrate.apply_migrations(pg)
    assert use.post(batch(ev("hide"))).json() == {"stored": 1, "refused": 0}
    assert _sql(pg, "SELECT user_id, kind FROM desk_events") == [(READER, "hide")]


@needs_pg
def test_a_usage_table_missing_behind_its_ledger_row_is_repaired_and_a_missing_column_is_named(pg):
    migrate.apply_migrations(pg)
    _sql(pg, "DROP TABLE desk_events")
    assert _schema(pg)["tables"]["desk_events"] == "absent"
    result = migrate.apply_migrations(pg)
    assert "005_desk_events.sql" in result["skipped"] and result["repaired"] and result["missing"] == []
    _sql(pg, "ALTER TABLE desk_events DROP COLUMN detail")
    assert _schema(pg)["missing_columns"] == ["desk_events.detail"]
    assert migrate.apply_migrations(pg)["missing"] == ["desk_events.detail"]  # IF NOT EXISTS adds no column
    rows, _ = usage.clean_batch(batch(ev()), 7)
    with pytest.raises(store.UsageStoreError) as e:
        store.append_events(pg, rows)
    assert (e.value.kind, e.value.detail) == ("store_schema", "42703")
    _sql(pg, "DROP TABLE desk_events")
    migrate.apply_migrations(pg)
    assert _schema(pg)["missing_columns"] == []


@needs_pg
def test_a_table_someone_holds_costs_one_batch_never_a_hung_request(pg, monkeypatch):
    migrate.apply_migrations(pg)
    monkeypatch.setattr(store, "LOCK_TIMEOUT", "1s")
    rows, _ = usage.clean_batch(batch(ev()), 7)
    with psycopg.connect(pg) as holder:
        holder.execute("LOCK TABLE desk_events IN ACCESS EXCLUSIVE MODE")
        started = time.monotonic()
        with pytest.raises(store.UsageStoreError) as e:
            store.append_events(pg, rows)
        took = time.monotonic() - started
        holder.rollback()
    assert (e.value.kind, e.value.detail) == ("store_lock_timeout", "55P03") and took < 3
    assert store.append_events(pg, rows) == 1


@needs_pg
def test_the_table_has_analiza_metrics_columns_type_for_type(pg):
    """D5 imports analiza-metrics' desk_events row for row: the same columns, types, nullability and defaults."""
    migrate.apply_migrations(pg)
    got = _sql(pg, "SELECT column_name, data_type, is_nullable, column_default FROM information_schema.columns "
                   "WHERE table_schema = current_schema() AND table_name = 'desk_events' ORDER BY ordinal_position")
    assert got == [("id", "bigint", "NO", "nextval('desk_events_id_seq'::regclass)"),
                   ("at", "timestamp with time zone", "NO", "now()"),
                   ("user_id", "bigint", "NO", None), ("session", "text", "NO", None), ("desk", "text", "NO", None),
                   ("kind", "text", "NO", None), ("tab", "text", "NO", "''::text"), ("state", "text", "NO", "''::text"),
                   ("page_ms", "bigint", "NO", "0"), ("detail", "jsonb", "NO", "'{}'::jsonb")]
    indexes = _sql(pg, "SELECT indexname FROM pg_indexes WHERE tablename = 'desk_events' ORDER BY indexname")
    assert [i for (i,) in indexes] == ["desk_events_at", "desk_events_pkey", "desk_events_user_at"]


def test_a_url_that_cannot_be_parsed_is_unreachable_by_its_class_never_its_text():
    rows, _ = usage.clean_batch(batch(ev()), 7)
    for url in ("postgresql://user:pa%zzSECRETPW@127.0.0.1:1/x", "postgresql://user:SECRETPW@127.0.0.1:1/x"):
        with pytest.raises(store.UsageStoreError) as e:
            store.append_events(url, rows)
        assert e.value.kind == "store_unreachable" and e.value.__cause__ is None
        assert "SECRETPW" not in str(e.value) and "127.0.0.1" not in str(e.value) and "user" not in str(e.value)


@pytest.mark.parametrize("raised, kind", [
    (psycopg.errors.UndefinedTable("x"), "store_schema"), (psycopg.errors.UndefinedColumn("x"), "store_schema"),
    (psycopg.errors.LockNotAvailable("x"), "store_lock_timeout"), (psycopg.errors.QueryCanceled("x"), "store_timeout"),
    (psycopg.OperationalError("x"), "store_unreachable"), (psycopg.errors.UniqueViolation("x"), "store_error"),
])
def test_a_database_error_is_a_kind_by_its_class(raised, kind):
    with pytest.raises(store.UsageStoreError) as e:
        with store._guard():
            raise raised
    assert e.value.kind == kind and "x" not in e.value.detail


# --- the migration and the store name the same table --------------------------------------------------------------------

# analiza-metrics migrations/004_desk_events.sql at da671b2, its statements (the comments left out): 005 is this, so D5
# imports that history row for row
METRICS_004 = """CREATE TABLE IF NOT EXISTS desk_events (
  id        bigserial PRIMARY KEY,
  at        timestamptz NOT NULL DEFAULT now(),
  user_id   bigint NOT NULL,
  session   text NOT NULL,
  desk      text NOT NULL,
  kind      text NOT NULL,
  tab       text NOT NULL DEFAULT '',
  state     text NOT NULL DEFAULT '',
  page_ms   bigint NOT NULL DEFAULT 0,
  detail    jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS desk_events_at ON desk_events (at);
CREATE INDEX IF NOT EXISTS desk_events_user_at ON desk_events (user_id, at);
"""


def test_the_store_and_the_migration_name_the_same_usage_columns_and_the_table_is_analiza_metrics_004():
    sql = (migrate.MIGRATIONS_DIR / "005_desk_events.sql").read_text(encoding="utf-8")
    statements = "".join(line + "\n" for line in sql.splitlines() if not line.startswith("--"))
    assert statements == METRICS_004
    body = sql[sql.index("CREATE TABLE IF NOT EXISTS desk_events"):]
    for col in store.DESK_TABLES["desk_events"]:
        assert "\n  %s " % col in body, col
    assert migrate.checked_tables()["desk_events"] == store.DESK_TABLES["desk_events"]
    assert "DROP" not in statements.upper() and "ALTER" not in statements.upper()


# --- docs/usage-data.md: its queries run on this table, and each after the first reads one user ---------------------------

DOC = ROOT / "docs" / "usage-data.md"


def doc_sql(section: str = "") -> list:
    """The ```sql blocks of docs/usage-data.md, of one section ("## 3.") or of the whole page."""
    text = DOC.read_text(encoding="utf-8")
    if section:
        start = text.index(section)
        text = text[start:text.index("\n## ", start + 1)]
    return re.findall(r"```sql\n(.*?)```", text, flags=re.S)


def test_each_numbered_query_after_the_first_reads_one_user():
    """analiza-metrics' copy counted every user in its queries 5-9 (the docs repo's session note of 24.11)."""
    which, *numbered = doc_sql("## 3.")
    assert "desk_events" in which and len(numbered) == 9
    assert "GROUP BY user_id" in numbered[0]
    for n, sql in enumerate(numbered[1:], start=2):
        assert "user_id = 12345678" in sql, n


@needs_pg
def test_the_docs_queries_run_on_the_table_and_count_one_user_where_they_say(pg):
    migrate.apply_migrations(pg)
    for user, sid in ((12345678, "aaaaaaaaaaaaaaaa"), (999, "bbbbbbbbbbbbbbbb")):
        rows, _ = usage.clean_batch(batch(ev("open", ms=800, board="sc"), ev("search", len=9, kind="digits", results=0,
                                                                              unfiltered=0),
                                          ev("act", action="refund_done", phase="done"), ev("open_item", where="name"),
                                          ev("id_shown"), ev("state"), session_id=sid), user)
        store.append_events(pg, rows)
    which, *numbered = doc_sql("## 3.")
    with psycopg.connect(pg) as conn:  # one transaction, rolled back: §5's DELETE runs too, and changes nothing
        assert conn.execute(which).fetchone()[0] == 12
        got = [conn.execute(sql).fetchall() for sql in numbered]
        for sql in doc_sql():
            conn.execute(sql)
        conn.rollback()
    assert sorted(r[0] for r in got[0]) == [999, 12345678]
    assert got[4] == [("digits", 9, 1, 1, 0)]                       # 5: her one search, not both users' two
    assert got[5] == [("refund_done", "done", None, 1)]             # 6
    assert got[6] == [("b2c", "tasks", "name", 1)]                  # 7
    assert got[7] == [("id_shown", None, 1)]                        # 8
    assert [(r[1], r[2], r[3]) for r in got[8]] == [(1, 800.0, 800)]  # 9
