"""The books copy (backlog 0301 B1): parsing, the run, the store, the routes."""

import json
from decimal import Decimal

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient

from app import main
from app.books import store as books_store
from app.books import sync
from app.books.parse import NEVER_STORED, ParseError, document_rows
from app.books.store import BOOKS_TABLES, BooksStore, summarize_series
from app.wizcloud import WizClient
from tests.conftest import KEY, SESSION, TEST_DATABASE_URL, drop_all, dump, needs_pg

DEMO = "wizdb2245n4"
SYNC_KEY = "SYNCKEY-0123456789abcdef0123"


def document(stock_id, number, doc_type=1, status=1, total=650.0, **extra):
    header = {
        "StockID": stock_id, "DocumentID": doc_type, "DocNumber": number, "status": status,
        "ValueDate": "2026-08-16", "issuedate": "2026-08-16", "duedate": "2026-09-15", "paydate": "1990-01-01",
        "AccountKey": "10093", "accountname": "ישראל ישראלי ת.ז 123456789",
        "Address": "רחוב הרצל 1", "City": "תל אביב", "Phone": "050-1234567", "email": "israel@example.co.il",
        "Tftal": total, "tftalvat": round(total / 1.18, 2), "tftalvatfree": 0, "vatprc": 18, "currency": 'ש"ח',
        "reference": "50347", "remarks": "עח: 50887-50891, טל 050-7654321", "Details": "קורס", "TaxApiNumber": "",
        "Agent": "", "batch": "17",
    }
    header.update(extra)
    lines = [{"itemkey": "C1", "itemname": "קורס פייתון", "Quantity": 1, "Price": round(total / 1.18, 2),
              "Tftal": round(total / 1.18, 2), "moveid": stock_id * 10, "BaseMoveID": 0, "Details": "תשלום 1 מתוך 3",
              "CurrencyCode": 'ש"ח', "status": status}]
    return {"DocumentDetails": [[header], lines]}


class Vendor:
    """createSession and getDoc. `docs`: stockID -> body; `fail`: stockID -> response."""

    def __init__(self, docs=None, fail=None):
        self.docs = dict(docs or {})
        self.fail = dict(fail or {})
        self.reads = []

    def handler(self, request):
        if request.url.path.startswith("/createSession/"):
            return httpx.Response(200, text=SESSION)
        sid = int(json.loads(request.content)["stockID"])
        self.reads.append(sid)
        if sid in self.fail:
            return self.fail[sid]
        if sid in self.docs:
            body = self.docs[sid]
            return httpx.Response(200, content=(body if isinstance(body, str) else json.dumps(body)).encode())
        return httpx.Response(500, json={"statusCode": 500, "status": {"errors": "Stock not found"}})

    def client(self, clock):
        return WizClient("lb19.wizcloud.co.il", DEMO, KEY, transport=httpx.MockTransport(self.handler), clock=clock)


# ---- parsing ---------------------------------------------------------------------------------------


def test_a_document_is_read_through_the_allowlist_and_cleaned():
    doc, lines, stats = document_rows(json.loads(json.dumps(document(2200, 50820)), parse_float=Decimal))
    assert (doc["stock_id"], doc["doc_type"], doc["doc_number"], doc["status"]) == (2200, 1, "50820", 1)
    assert doc["total_incl"] == Decimal("650.0") and isinstance(doc["total_excl"], Decimal)
    assert str(doc["value_date"]) == "2026-08-16" and doc["pay_date"] is None  # 1990-01-01 is "no date"
    assert doc["account_name"] == "ישראל ישראלי ת.ז #"
    assert doc["remarks"] == "עח: 50887-50891, טל #" and doc["refs_in_text"] == ["50347", "50887-50891"]
    assert doc["email_domain"] == "example.co.il" and doc["tax_api_number_present"] is False
    assert lines[0]["move_id"] == 22000 and lines[0]["base_move_id"] is None and lines[0]["item_name"] == "קורס פייתון"
    assert stats["masked"] == 2  # the ID in the name and the phone in the remarks
    assert "Address" in doc["fields_seen"] and "Phone" in doc["fields_seen"]  # seen by name, never by value
    text = dump({k: str(v) for k, v in doc.items()}) + dump([{k: str(v) for k, v in x.items()} for x in lines])
    for leak in ("123456789", "050-1234567", "1234567", "7654321", "רחוב הרצל", "תל אביב", "israel@", "israel"):
        assert leak not in text, leak


def test_no_column_for_an_address_a_phone_a_full_email_or_a_tax_id_exists():
    columns = {c for cols in BOOKS_TABLES.values() for c in cols}
    for forbidden in NEVER_STORED + ("tax_id", "vat_number", "id_number", "tax_api_number", "agent"):
        assert forbidden not in columns, forbidden
    migration = (main.ROOT / "migrations" / "002_books.sql").read_text(encoding="utf-8")
    statements = migration.split("CREATE TABLE")[1:]
    for statement in statements:
        for line in statement.splitlines()[1:]:
            name = line.strip().split(" ")[0]
            assert name not in NEVER_STORED + ("tax_id", "vat_number", "id_number", "tax_api_number")


@pytest.mark.parametrize("body", [{}, {"DocumentDetails": []}, {"DocumentDetails": [["x"], []]}, {"DocumentDetails": [[{"x": 1}], ["y"]]},
                                  {"DocumentDetails": [[{"DocumentID": 1}], []]}])
def test_an_answer_that_is_not_a_document_is_a_parse_error(body):
    with pytest.raises(ParseError):
        document_rows(body)


def test_series_count_valid_numbers_and_find_the_holes():
    rows = [(1, str(n), 1) for n in (50001, 50002, 50004, 50005, 50005, 50008)] + [(1, "50003", 0), (3, "70001", 1), (99, "x", 1)]
    by_type = {s["doc_type"]: s for s in summarize_series(rows)}
    invoices = by_type[1]
    assert (invoices["valid"], invoices["first"], invoices["last"]) == (6, 50001, 50008)
    assert (invoices["holes"], invoices["holes_sample"], invoices["duplicates"], invoices["drafts"]) == (3, [50003, 50006, 50007], 1, 1)
    assert by_type[3]["holes"] == 0 and by_type[99]["not_numeric"] == 1 and by_type[99]["role"] == "unknown"


# ---- the run, against Postgres -------------------------------------------------------------------


@pytest.fixture
def pg():
    drop_all(TEST_DATABASE_URL)
    from app.migrate import apply_migrations

    apply_migrations(TEST_DATABASE_URL)
    yield TEST_DATABASE_URL
    drop_all(TEST_DATABASE_URL)


def run(vendor, url, clock, box_s=600, monotonic=None, tick=False):
    """One run. tick=False is the copy alone (B1's phases), so a test of the frontier counts only its reads."""
    store = BooksStore(url)
    with store.session(DEMO) as s:
        return sync.run_once(vendor.client(clock), s, DEMO, trigger="test", box_s=box_s, tick=tick,
                             **({"clock": monotonic} if monotonic else {}))


def demo_books():
    # 120-121: deleted drafts; 396-400: a jump of 256 past 140. Numbered as the books number them: no number is skipped
    sids = [sid for sid in range(100, 141) if sid not in (120, 121)] + list(range(396, 401))
    return {sid: document(sid, 50100 + i) for i, sid in enumerate(sids)}


@needs_pg
def test_a_run_stores_the_documents_and_a_second_adds_nothing(pg, clock, monkeypatch):
    monkeypatch.setitem(sync.FIRST_ID, DEMO, 90)
    vendor = Vendor(demo_books())
    first = run(vendor, pg, clock)
    assert (first["added"], first["changed"], first["ok"], first["complete"]) == (44, 0, True, True)
    # the first run's wide frontier read up to 340; the probe at +256 hit 396, and the 55 ids it jumped over
    # (341-395) were read in the same run, so nothing waits and the run is complete
    assert first["far_hits"] == [396] and first["queued"] == 55 and "55 ids it jumped over are read" in first["note"]
    assert first["failed"] == {} and first["masked"] > 0
    second = run(vendor, pg, clock)
    assert (second["added"], second["changed"], second["found"], second["complete"]) == (0, 0, 0, True)
    assert second["from_id"] == 401 and second["probed"] == sync.MISSES_IN_A_ROW + len(sync.FAR_PROBES)
    with psycopg.connect(pg) as conn:
        held = conn.execute("SELECT count(*), count(DISTINCT company) FROM books_documents").fetchone()
        events = conn.execute("SELECT kind, count(*) FROM books_document_events GROUP BY kind").fetchall()
        texts = conn.execute("SELECT account_name || remarks FROM books_documents").fetchall()
    assert held == (44, 1) and events == [("first_seen", 44)]
    assert all("123456789" not in t[0] and "7654321" not in t[0] for t in texts)


@needs_pg
def test_a_failed_read_is_not_an_absence_and_three_end_the_run(pg, clock, monkeypatch):
    monkeypatch.setitem(sync.FIRST_ID, DEMO, 100)
    bad = httpx.Response(502, text="Bad Gateway")
    vendor = Vendor({sid: document(sid, 50000 + sid) for sid in range(100, 110)}, fail={103: bad, 106: bad, 107: bad, 108: bad})
    summary = run(vendor, pg, clock)
    assert summary["ok"] is False and summary["complete"] is False
    assert summary["failed"] == {"http_error": 4} and summary["absent"] == 0
    assert summary["added"] == 5  # 100-102, 104-105; 109 not reached
    assert "3 failed reads in a row" in summary["note"]


@needs_pg
def test_the_time_box_cuts_a_run_and_the_next_one_goes_on(pg, clock, monkeypatch):
    monkeypatch.setitem(sync.FIRST_ID, DEMO, 100)
    ticks = iter(range(10_000))
    vendor = Vendor({sid: document(sid, 50000 + sid) for sid in range(100, 130)})
    cut = run(vendor, pg, clock, box_s=10, monotonic=lambda: next(ticks))
    assert cut["complete"] is False and "time box reached" in cut["note"] and 0 < cut["added"] < 30
    rest = run(vendor, pg, clock)
    assert cut["added"] + rest["added"] == 30 and rest["complete"] is True and rest["from_id"] == 100 + cut["added"]


@needs_pg
def test_a_document_answering_for_another_id_is_not_stored(pg, clock, monkeypatch):
    monkeypatch.setitem(sync.FIRST_ID, DEMO, 100)
    vendor = Vendor({100: document(100, 50100), 101: document(555, 50555)})
    summary = run(vendor, pg, clock)
    assert summary["added"] == 1 and summary["failed"] == {"bad_response": 1}


@needs_pg
def test_saving_the_same_document_again_changes_nothing_and_an_edit_is_an_event(pg):
    store = BooksStore(pg)
    with store.session(DEMO) as s:
        run_id = s.start_run(DEMO, "test", 1)
        doc, lines, _ = document_rows(json.loads(json.dumps(document(7, 50007)), parse_float=Decimal))
        assert s.save(DEMO, run_id, doc, lines) == "added"
        assert s.save(DEMO, run_id, doc, lines) == "unchanged"
        edited, lines2, _ = document_rows(json.loads(json.dumps(document(7, 50007, total=700.0)), parse_float=Decimal))
        assert s.save(DEMO, run_id, edited, lines2) == "changed"
        issued, lines3, _ = document_rows(json.loads(json.dumps(document(7, 50007, total=700.0, status=0)), parse_float=Decimal))
        assert s.save(DEMO, run_id, issued, lines3) == "changed"
    with psycopg.connect(pg) as conn:
        events = conn.execute("SELECT kind, fields FROM books_document_events ORDER BY event_id").fetchall()
        total = conn.execute("SELECT total_incl FROM books_documents WHERE stock_id = 7").fetchone()[0]
    assert [e[0] for e in events] == ["first_seen", "changed", "status_changed"]
    assert "total_incl" in events[1][1] and total == Decimal("700.0")


@needs_pg
def test_a_second_run_of_the_same_company_is_skipped(pg):
    store = BooksStore(pg)
    with store.session(DEMO) as first:
        assert first is not None
        with BooksStore(pg).session(DEMO) as second:
            assert second is None
        with BooksStore(pg).session("wizdb2245n5") as other_company:
            assert other_company is not None


# ---- the routes ------------------------------------------------------------------------------------


@pytest.fixture
def books_app(pg, fake, clock, monkeypatch):
    monkeypatch.setenv("WIZ_PRIVATE_KEY", KEY)
    monkeypatch.setenv("DATABASE_URL", pg)
    monkeypatch.setenv("BOOKS_SYNC_KEY", SYNC_KEY)
    monkeypatch.delenv("WIZ_COMPANY", raising=False)
    monkeypatch.setitem(sync.FIRST_ID, DEMO, 100)
    vendor = Vendor({sid: document(sid, 50000 + sid) for sid in range(100, 110)})
    main._cache.update(at=0.0, result=None)
    main._store_cache.update(at=0.0, key=None, value=None)
    main.app.state.transport = httpx.MockTransport(vendor.handler)
    main.app.state.clock = clock
    main.app.state.client = None
    yield TestClient(main.app), vendor
    main.app.state.transport = None
    main.app.state.clock = __import__("time").time
    main.app.state.client = None
    main._store_cache.update(at=0.0, key=None, value=None)


@needs_pg
def test_the_tick_needs_the_sync_key(books_app, monkeypatch):
    client, vendor = books_app
    assert client.post("/api/books/tick").status_code == 401
    assert client.post("/api/books/tick", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/api/books/status", headers={"Authorization": f"Bearer {SYNC_KEY}x"}).status_code == 401
    assert vendor.reads == []
    monkeypatch.delenv("BOOKS_SYNC_KEY")
    r = client.post("/api/books/tick", headers={"Authorization": f"Bearer {SYNC_KEY}"})
    assert r.status_code == 503 and r.json()["error"] == "BOOKS_SYNC_KEY is not set"


@needs_pg
def test_a_tick_copies_and_says_counts_only(books_app, clock):
    client, _ = books_app
    auth = {"Authorization": f"Bearer {SYNC_KEY}"}
    first = client.post("/api/books/tick", headers=auth)
    assert first.status_code == 200 and first.json()["added"] == 10 and first.json()["complete"] is True
    second = client.post("/api/books/tick", headers=auth).json()
    assert second["added"] == 0 and second["changed"] == 0
    for leak in ("ישראל", "50105", "650", "קורס"):
        assert leak not in first.text
    status = client.get("/api/books/status", headers=auth).json()
    invoices = [s for s in status["series"] if s["doc_type"] == 1][0]
    assert (invoices["valid"], invoices["first"], invoices["last"], invoices["holes"]) == (10, 50100, 50109, 0)
    clock.t += 20
    health = client.get("/health")
    books = health.json()["books"]
    assert books["sync_key"] == "ok" and books["holes"] == 0 and books["series"] == 1
    assert books["last_run"]["ok"] is True and books["last_run"]["complete"] is True
    assert "50100" not in health.text and "50109" not in health.text and SYNC_KEY not in health.text


# ---- the review of 22.11 -----------------------------------------------------------------------------


@needs_pg
def test_a_read_that_failed_is_read_again_and_the_run_is_not_complete_until_then(pg, clock, monkeypatch):
    """Finding 2: one 502 each at 101, 103, 105 used to be skipped for good, with ok and complete true."""
    monkeypatch.setitem(sync.FIRST_ID, DEMO, 100)
    bad = httpx.Response(502, text="Bad Gateway")
    vendor = Vendor({sid: document(sid, 50000 + sid) for sid in range(100, 111)}, fail={101: bad, 103: bad, 105: bad})
    first = run(vendor, pg, clock)
    assert first["failing"] is False and first["complete"] is False and first["failed"] == {"http_error": 3}
    assert first["added"] == 8 and "3 ids wait in books_retry" in first["note"]
    vendor.fail.clear()
    second = run(vendor, pg, clock)
    assert (second["retried"], second["added"], second["complete"], second["failed"]) == (3, 3, True, {})
    with psycopg.connect(pg) as conn:
        assert conn.execute("SELECT count(*) FROM books_documents").fetchone()[0] == 11
        assert conn.execute("SELECT count(*) FROM books_retry").fetchone()[0] == 0


@needs_pg
def test_a_far_probe_that_failed_is_not_a_miss(pg, clock, monkeypatch):
    """Finding 2: the +64 probe failed over seven documents and the run said complete."""
    monkeypatch.setitem(sync.FIRST_ID, DEMO, 90)
    monkeypatch.setattr(sync, "FIRST_RUN_MISSES", 50)  # so 164 lies past the frontier, where only a probe reaches
    docs = {100: document(100, 50100)}
    docs.update({sid: document(sid, 50000 + sid) for sid in range(164, 171)})
    vendor = Vendor(docs, fail={164: httpx.Response(502, text="Bad Gateway")})
    first = run(vendor, pg, clock)
    assert first["complete"] is False and "a far probe failed" in first["note"] and first["added"] == 1
    vendor.fail.clear()
    second = run(vendor, pg, clock)
    assert second["added"] == 7 and second["complete"] is True


@needs_pg
def test_a_document_the_database_refuses_is_retried_and_the_run_goes_on(pg, clock, monkeypatch):
    """Finding 4: one refused document blocked the copy for good and left the run row unfinished."""
    monkeypatch.setitem(sync.FIRST_ID, DEMO, 100)
    vendor = Vendor({sid: document(sid, 50000 + sid) for sid in range(100, 106)})
    from app.books import store as books_store

    real_save = books_store.BooksStore.save

    def refuse_102(self, company, run_id, doc, lines, **kw):
        if doc["stock_id"] == 102:
            raise books_store.BooksStoreError("22021")
        return real_save(self, company, run_id, doc, lines, **kw)

    monkeypatch.setattr(books_store.BooksStore, "save", refuse_102)
    first = run(vendor, pg, clock)
    assert first["added"] == 5 and first["failed"] == {"store_refused": 1} and first["complete"] is False
    assert "the database refused a document (22021)" in first["note"]
    monkeypatch.setattr(books_store.BooksStore, "save", real_save)
    assert run(vendor, pg, clock)["added"] == 1


@needs_pg
def test_a_run_that_crashes_is_still_finished_in_the_log(pg, clock, monkeypatch):
    monkeypatch.setitem(sync.FIRST_ID, DEMO, 100)
    vendor = Vendor({100: document(100, 50100)})

    def boom(self, company):  # a bug outside the reads: an answer the parser cannot read is a failed read instead
        raise RuntimeError("a bug")

    monkeypatch.setattr(books_store.BooksStore, "high_water", boom)
    with pytest.raises(RuntimeError):
        run(vendor, pg, clock)
    with psycopg.connect(pg) as conn:
        row = conn.execute("SELECT finished_at IS NOT NULL, ok, complete, note FROM books_sync_runs").fetchone()
    assert row[:3] == (True, False, False) and "the run stopped on RuntimeError" in row[3]


@needs_pg
def test_the_second_run_re_reads_every_document_and_finds_it_unchanged(pg, clock, monkeypatch):
    """Finding 2: 'a second run adds zero' held because it read nothing. The acceptance re-reads all."""
    monkeypatch.setitem(sync.FIRST_ID, DEMO, 100)
    vendor = Vendor({sid: document(sid, 50000 + sid) for sid in range(100, 110)})
    run(vendor, pg, clock)
    store = BooksStore(pg)
    with store.session(DEMO) as s:
        again = sync.run_once(vendor.client(clock), s, DEMO, trigger="test", box_s=600, reread_held=True, tick=False)
    assert (again["reread"], again["unchanged"], again["changed"], again["added"], again["complete"]) == (10, 10, 0, 0, True)


@needs_pg
def test_one_amount_written_three_ways_is_one_document(pg):
    """Finding 7: 650.0, 650.00 and 650 hashed differently, and each save said changed ['lines']."""
    store = BooksStore(pg)
    with store.session(DEMO) as s:
        run_id = s.start_run(DEMO, "test", 1)
        results = []
        for spelling in ("650.0", "650.00", "650"):
            text = json.dumps(document(7, 50007)).replace('"Tftal": 650.0', f'"Tftal": {spelling}')
            doc, lines, _ = document_rows(json.loads(text, parse_float=Decimal))
            results.append(s.save(DEMO, run_id, doc, lines))
    assert results == ["added", "unchanged", "unchanged"]


@needs_pg
def test_an_empty_company_reads_the_wide_frontier_once(pg, clock):
    """Demo holds nothing: once a run reached the end, the next ones read the plain 50 and the far probes."""
    vendor = Vendor({})
    first = run(vendor, pg, clock)
    second = run(vendor, pg, clock)
    beyond = [j for j in sync.FAR_PROBES if j > sync.FIRST_RUN_MISSES]  # a probe inside the window is not read twice
    assert first["probed"] == sync.FIRST_RUN_MISSES + len(beyond) and first["complete"] is True
    assert second["probed"] == sync.MISSES_IN_A_ROW + len(sync.FAR_PROBES)


@needs_pg
def test_every_read_is_bounded_by_what_is_left_of_the_box(pg, clock, monkeypatch):
    """Finding 6: a read that started at 44 s of a 45 s box could take 30 s more."""
    import time as _time

    monkeypatch.setitem(sync.FIRST_ID, DEMO, 100)
    seen = []
    vendor = Vendor({sid: document(sid, 50000 + sid) for sid in range(100, 103)})
    client = vendor.client(clock)
    real_get = client.get_doc

    def spy(sid, deadline=None):
        seen.append(deadline - _time.monotonic())
        return real_get(sid, deadline=deadline)

    client.get_doc = spy
    store = BooksStore(pg)
    with store.session(DEMO) as s:
        sync.run_once(client, s, DEMO, trigger="test", box_s=20, tick=False)
    assert seen and max(seen) <= 20 - sync.MARGIN_S + 0.5


def test_booleans_and_field_names_are_read_strictly():
    body = document(7, 50007)
    body["DocumentDetails"][1][0]["VatExempt"] = "0"
    body["DocumentDetails"][0][0]["SESSION-LIKE KEY 123456789"] = 1
    doc, lines, stats = document_rows(json.loads(json.dumps(body), parse_float=Decimal))
    assert lines[0]["vat_exempt"] is False  # bool("0") was True
    assert stats["fields_unnamed"] == 1 and not any("123456789" in f for f in doc["fields_seen"])


@needs_pg
def test_a_short_sync_key_is_refused(books_app, monkeypatch):
    client, _ = books_app
    monkeypatch.setenv("BOOKS_SYNC_KEY", "short")
    r = client.post("/api/books/tick", headers={"Authorization": "Bearer short"})
    assert r.status_code == 503 and "too short" in r.json()["error"]


# ---- the second review of 22.11 ----------------------------------------------------------------------------


class TimedVendor(Vendor):
    """A vendor that honours the timeout httpx hands it, as a real connection does (a mock transport alone
    ignores it): an answer slower than the read timeout waits that long and times out."""

    def __init__(self, docs=None, slow=None, latency=0.0):
        super().__init__(docs)
        self.slow = dict(slow or {})  # 'session' or a stockID -> seconds that answer takes
        self.latency = latency

    def handler(self, request):
        key = "session" if request.url.path.startswith("/createSession/") else int(json.loads(request.content)["stockID"])
        took = self.slow.get(key, self.latency)
        limit = (request.extensions.get("timeout") or {}).get("read")
        if limit is not None and took > limit:
            import time as _time

            _time.sleep(limit)
            raise httpx.ReadTimeout("timed out", request=request)
        if took:
            import time as _time

            _time.sleep(took)
        return super().handler(request)


def retry_rows(url):
    with psycopg.connect(url) as conn:
        return conn.execute("SELECT stock_id, kind FROM books_retry ORDER BY stock_id").fetchall()


@needs_pg
def test_a_healthy_run_that_reaches_its_box_fails_nothing(pg, clock, monkeypatch):
    """Review 22.11: reads that began with 1.0-1.5 s left failed at once as network, and three of them said
    'WizCloud is failing' and filled books_retry."""
    import time as _time

    monkeypatch.setitem(sync.FIRST_ID, DEMO, 100)
    vendor = TimedVendor({sid: document(sid, 50000 + sid) for sid in range(100, 140)}, latency=0.25)
    summary = run(vendor, pg, clock, box_s=3, monotonic=_time.monotonic)
    assert summary["ok"] is True and summary["complete"] is False and summary["failed"] == {}
    assert summary["note"] == "time box reached" and retry_rows(pg) == []


@needs_pg
@pytest.mark.parametrize("slow", ["session", 100])
def test_a_read_or_a_session_cut_by_the_box_is_time_not_a_failure(pg, clock, monkeypatch, slow):
    """Review 22.11: a slow createSession near the end of a 2 s box made the run take 4 s. Every wait is
    bounded by what is left of the box, and a request it cuts stops the run for time."""
    import time as _time

    monkeypatch.setitem(sync.FIRST_ID, DEMO, 100)
    vendor = TimedVendor({100: document(100, 50100)}, slow={slow: 30.0})
    started = _time.monotonic()
    summary = run(vendor, pg, clock, box_s=3, monotonic=_time.monotonic)
    assert _time.monotonic() - started < 3.5
    assert summary["ok"] is True and summary["failed"] == {} and summary["probed"] == 0
    assert summary["note"] == "time box reached" and retry_rows(pg) == []


@needs_pg
def test_a_number_no_document_holds_is_a_failed_read_not_a_crash(pg, clock, monkeypatch):
    """Review 22.11: status Infinity raised OverflowError out of the parser and stalled the copy for good."""
    monkeypatch.setitem(sync.FIRST_ID, DEMO, 100)
    docs = {sid: document(sid, 50000 + sid) for sid in range(100, 105)}
    docs[102] = document(102, 50102, status=float("inf"))
    docs[103] = json.dumps(document(103, 50103)).replace('"Tftal": 650.0', '"Tftal": 1E+999999')
    summary = run(Vendor(docs), pg, clock)
    assert summary["failed"] == {"bad_response": 2} and summary["added"] == 3 and summary["failing"] is False
    assert summary["complete"] is False and retry_rows(pg) == [(102, "bad_response"), (103, "bad_response")]


@needs_pg
def test_an_answer_the_parser_cannot_read_is_a_failed_read(pg, clock, monkeypatch):
    monkeypatch.setitem(sync.FIRST_ID, DEMO, 100)
    real = sync.document_rows

    def fragile(body):
        from app.books.parse import split

        if split(body)[0].get("StockID") == 101:
            raise RuntimeError("a bug in the parser")
        return real(body)

    monkeypatch.setattr(sync, "document_rows", fragile)
    summary = run(Vendor({sid: document(sid, 50000 + sid) for sid in range(100, 103)}), pg, clock)
    assert summary["failed"] == {"bad_response": 1} and summary["added"] == 2
    assert "an answer the parser could not read (RuntimeError)" in summary["note"] and retry_rows(pg) == [(101, "bad_response")]


@needs_pg
def test_documents_after_a_gap_are_copied_once_a_far_probe_lands_on_them(pg, clock, monkeypatch):
    """Review 22.11: with 111-160 empty, 161-170 were never read, and once 171-180 came the probe at 174 jumped
    over 161-173 for good. Now every id a probe jumps over is read. What a run cannot see before a probe lands
    (the second run here) is the documented limit of a frontier; the weekly sweep (B2) reads every id."""
    monkeypatch.setitem(sync.FIRST_ID, DEMO, 100)
    vendor = Vendor({sid: document(sid, 50000 + sid) for sid in range(100, 111)})
    assert run(vendor, pg, clock)["complete"] is True
    vendor.docs.update({sid: document(sid, 50000 + sid) for sid in range(161, 171)})
    second = run(vendor, pg, clock)
    assert second["found"] == 0 and second["complete"] is True
    vendor.docs.update({sid: document(sid, 50000 + sid) for sid in range(171, 181)})
    third = run(vendor, pg, clock)
    assert third["far_hits"] == [174] and third["queued"] == 13 and third["added"] == 20 and third["complete"] is True
    with psycopg.connect(pg) as conn:
        held = [r[0] for r in conn.execute("SELECT stock_id FROM books_documents ORDER BY stock_id").fetchall()]
    assert held == list(range(100, 111)) + list(range(161, 181)) and retry_rows(pg) == []


@needs_pg
def test_a_jumped_range_cut_by_the_box_waits_in_books_retry_and_is_read_first(pg, clock, monkeypatch):
    monkeypatch.setitem(sync.FIRST_ID, DEMO, 100)
    vendor = Vendor({sid: document(sid, 50000 + sid) for sid in range(100, 111)})
    assert run(vendor, pg, clock)["complete"] is True
    vendor.docs.update({sid: document(sid, 50000 + sid) for sid in range(170, 201)})
    vendor.reads.clear()
    ticks = lambda: float(len(vendor.reads))  # noqa: E731 - one second per read
    # 50 misses (111-160), the probe at 174, then 5 of the 13 jumped ids (161-173) before the box closes
    cut = run(vendor, pg, clock, box_s=57.0, monotonic=ticks)
    assert cut["far_hits"] == [174] and cut["queued"] == 13 and cut["complete"] is False and cut["failed"] == {}
    assert [r[0] for r in retry_rows(pg)] == list(range(166, 174)) and {r[1] for r in retry_rows(pg)} == {"jumped"}
    vendor.reads.clear()
    after = run(vendor, pg, clock)
    assert vendor.reads[:8] == list(range(166, 174)) and after["retried"] == 8 and after["complete"] is True
    assert retry_rows(pg) == []


@needs_pg
def test_a_retried_id_above_the_high_water_mark_is_read_once(pg, clock, monkeypatch):
    """Review 22.11: the frontier's start was taken before the retries, so a retried id was read twice."""
    monkeypatch.setitem(sync.FIRST_ID, DEMO, 100)
    bad = httpx.Response(502, text="Bad Gateway")
    vendor = Vendor({sid: document(sid, 50000 + sid) for sid in range(100, 109)}, fail={106: bad, 107: bad, 108: bad})
    first = run(vendor, pg, clock)
    assert first["ok"] is False and first["added"] == 6
    vendor.fail.clear()
    vendor.reads.clear()
    second = run(vendor, pg, clock)
    # the frontier starts at the high-water mark (106) and steps past the ids that failed before; they are read
    # once, after it (third review 22.11)
    assert second["retried"] == 3 and second["found"] == 3 and second["from_id"] == 106
    assert all(vendor.reads.count(sid) == 1 for sid in (106, 107, 108)) and second["complete"] is True


@needs_pg
def test_what_masking_cost_is_counted_by_field_and_reason(pg, clock, monkeypatch):
    monkeypatch.setitem(sync.FIRST_ID, DEMO, 100)
    summary = run(Vendor({100: document(100, 50100, remarks="עח 50347 2026")}), pg, clock)
    assert summary["masked_fields"] == {"account_name": 1, "remarks": 1}
    assert summary["clean"] == {"numbers_masked": 2, "refs_lost": {"plain": 1}}


def test_a_number_out_of_range_or_not_finite_is_not_a_document():
    for value in ('"Infinity"', "1E+999999"):  # 1E-99 is zero since the third review (a float residue)
        text = json.dumps(document(7, 50007)).replace('"Tftal": 650.0', f'"Tftal": {value}')
        with pytest.raises(ParseError):
            document_rows(json.loads(text, parse_float=Decimal))


def test_vat_exempt_reads_one_point_zero_and_nothing_else_odd():
    from app.books.parse import _read

    assert [_read(v, "bool", {}) for v in (Decimal("1.0"), "0.00", 1, "true", "2", "yes", None)] == [
        True, False, True, True, None, None, None]


def test_canonical_never_rounds_two_values_together():
    """Review 22.11: Decimal's 28-digit context made two 29-digit values hash alike."""
    from app.books.parse import canonical

    a, b = Decimal("1234567890123456789.0123456789"), Decimal("1234567890123456789.0123456788")
    assert canonical(a) != canonical(b) and canonical(Decimal("650.00")) == canonical(650) == "650"


def test_a_vendor_field_name_with_digits_in_it_is_counted_not_kept():
    body = document(7, 50007, ID_123456789="x", MulFactor1="1")
    doc, _, stats = document_rows(body)
    assert "MulFactor1" in doc["fields_seen"] and not any("123456789" in f for f in doc["fields_seen"])
    assert stats["fields_unnamed"] == 1


def test_health_says_when_the_sync_key_is_too_short(monkeypatch):
    from app import config

    monkeypatch.setenv("BOOKS_SYNC_KEY", "short")
    assert main.sync_key_state(config.load()) == "too_short"
    monkeypatch.setenv("BOOKS_SYNC_KEY", SYNC_KEY)
    assert main.sync_key_state(config.load()) == "ok"
    monkeypatch.delenv("BOOKS_SYNC_KEY")
    assert main.sync_key_state(config.load()) == "missing"


# ---- the third review of 22.11: the gate before Analiza ---------------------------------------------------

# Three answers no retry fixes, as a document the vendor cannot render, an answer that is not a document, and a
# value no document holds would give: each is a failed read every time it is read.
STUCK = {
    "vendor_error": httpx.Response(500, json={"statusCode": 500, "status": {"errors": "Object reference not set to an instance of an object."}}),
    "not_a_document": httpx.Response(200, json={"DocumentDetails": []}),
    "no_such_value": httpx.Response(200, content=json.dumps(document(0, 0)).replace('"status": 1', '"status": "Infinity"').encode()),
}


def stuck_at(*ids):
    return dict(zip(ids, STUCK.values()))


def retry_attempts(url):
    with psycopg.connect(url) as conn:
        return conn.execute("SELECT stock_id, attempts FROM books_retry ORDER BY stock_id").fetchall()


def held_ids(url):
    with psycopg.connect(url) as conn:
        return [r[0] for r in conn.execute("SELECT stock_id FROM books_documents ORDER BY stock_id").fetchall()]


@needs_pg
def test_ids_that_always_fail_never_freeze_the_copy(pg, clock, monkeypatch):
    """Third review 22.11, finding 1: books_retry was read first, and three ids that fail on every read made
    'three failed reads in a row' stop each run before the frontier: no new document was read again."""
    monkeypatch.setitem(sync.FIRST_ID, DEMO, 100)
    vendor = Vendor({sid: document(sid, 50000 + sid) for sid in range(100, 111)}, fail=stuck_at(102, 104, 106))
    first = run(vendor, pg, clock)
    assert first["failing"] is False and first["added"] == 8 and first["complete"] is False
    vendor.docs.update({sid: document(sid, 50000 + sid) for sid in range(111, 116)})
    second = run(vendor, pg, clock)
    assert second["failing"] is False and second["added"] == 5, second["note"]
    third = run(vendor, pg, clock)
    assert third["failing"] is False and third["added"] == 0 and third["complete"] is False
    assert held_ids(pg) == [sid for sid in range(100, 116) if sid not in (102, 104, 106)]
    assert retry_attempts(pg) == [(102, 3), (104, 3), (106, 3)]


@needs_pg
def test_three_stuck_ids_at_the_top_stop_one_run_only(pg, clock, monkeypatch):
    """Finding 1 at the frontier: three fresh failures in a row may be WizCloud failing, so the first run stops;
    the next one knows them, reads past them, and reads them once more after the frontier."""
    monkeypatch.setitem(sync.FIRST_ID, DEMO, 100)
    vendor = Vendor({sid: document(sid, 50000 + sid) for sid in range(100, 112)}, fail=stuck_at(106, 107, 108))
    first = run(vendor, pg, clock)
    assert first["ok"] is False and first["added"] == 6
    vendor.reads.clear()
    second = run(vendor, pg, clock)
    assert second["failing"] is False and second["added"] == 3, second["note"]
    assert all(vendor.reads.count(sid) == 1 for sid in (106, 107, 108))
    assert held_ids(pg) == [sid for sid in range(100, 112) if sid not in (106, 107, 108)]


def test_a_float_residue_below_the_bound_is_zero_not_a_failed_read():
    """Finding 1's likely trigger: 2^-42, the residue of subtracting two doubles, was 'a number out of range'."""
    for value in ("2.2737367544323206e-13", "-2.2737367544323206e-13", "1E-99"):
        text = json.dumps(document(7, 50007)).replace('"tftalvatfree": 0', f'"tftalvatfree": {value}')
        doc, _, _ = document_rows(json.loads(text, parse_float=Decimal))
        assert doc["total_vat_free"] == 0


@needs_pg
def test_the_status_route_names_the_ids_that_keep_failing(books_app):
    client, vendor = books_app
    vendor.fail.update(stuck_at(103))
    auth = {"Authorization": f"Bearer {SYNC_KEY}"}
    client.post("/api/books/tick", headers=auth)
    client.post("/api/books/tick", headers=auth)
    retry = client.get("/api/books/status", headers=auth).json()["retry"]
    assert retry["waiting"] == 1 and retry["by_kind"] == {"http_error": 1}
    assert [(s["stock_id"], s["kind"], s["attempts"]) for s in retry["stuck"]] == [(103, "http_error", 2)]


@needs_pg
def test_a_far_probe_that_failed_and_then_answers_brings_the_gap_before_it(pg, clock, monkeypatch):
    """Finding 3: the probe at +64 failed; the next run read it first, it held a document, the high-water mark
    jumped to it, and the ids between the frontier's end and it were never read."""
    monkeypatch.setitem(sync.FIRST_ID, DEMO, 100)
    vendor = Vendor({sid: document(sid, 50000 + sid) for sid in range(100, 111)})
    assert run(vendor, pg, clock)["complete"] is True
    vendor.docs.update({sid: document(sid, 50000 + sid) for sid in range(165, 176)})  # past a gap of 54 empty ids
    vendor.fail[174] = httpx.Response(502, text="Bad Gateway")  # 110 + 64
    second = run(vendor, pg, clock)
    assert second["complete"] is False and "a far probe failed" in second["note"]
    vendor.fail.clear()
    third = run(vendor, pg, clock)
    assert held_ids(pg) == list(range(100, 111)) + list(range(165, 176)) and third["complete"] is True


@needs_pg
def test_a_retried_id_found_above_the_frontier_brings_the_ids_below_it(pg, clock, monkeypatch):
    """Finding 3, whatever order ids arrive in: a retried id read after the frontier that holds a document above the
    highest id held puts the ids between that no read reached in books_retry, and reads them."""
    monkeypatch.setitem(sync.FIRST_ID, DEMO, 100)
    vendor = Vendor({sid: document(sid, 50000 + sid) for sid in range(100, 111)})
    assert run(vendor, pg, clock)["complete"] is True
    vendor.docs.update({sid: document(sid, 50000 + sid) for sid in range(170, 175)})
    vendor.fail[174] = httpx.Response(502, text="Bad Gateway")
    assert run(vendor, pg, clock)["complete"] is False
    vendor.fail.clear()
    vendor.docs.update({sid: document(sid, 50000 + sid) for sid in range(111, 116)})  # so the probes move past 174
    third = run(vendor, pg, clock)
    assert held_ids(pg) == list(range(100, 116)) + list(range(170, 175)) and third["queued"] == 8  # 166-173
    # 174 lies past where the walk ended (165): documents may follow it, so the run is not complete (fourth review)
    assert third["complete"] is False and "past the frontier" in third["note"]
    assert run(vendor, pg, clock)["complete"] is True


@needs_pg
def test_a_crash_after_a_far_probe_hit_leaves_the_jumped_ids_waiting(pg, clock, monkeypatch):
    """Finding 4: the probe's document was committed before the range it jumped over was written to books_retry,
    so a crash between the two lost the range for good. Now both are one transaction."""
    monkeypatch.setitem(sync.FIRST_ID, DEMO, 100)
    vendor = Vendor({sid: document(sid, 50000 + sid) for sid in range(100, 111)})
    assert run(vendor, pg, clock)["complete"] is True
    vendor.docs.update({sid: document(sid, 50000 + sid) for sid in range(165, 176)})
    real_save = books_store.BooksStore.save

    def crash_after_174(self, company, run_id, doc, lines, **kw):
        result = real_save(self, company, run_id, doc, lines, **kw)
        if doc["stock_id"] == 174:
            raise RuntimeError("the process died here")
        return result

    monkeypatch.setattr(books_store.BooksStore, "save", crash_after_174)
    with pytest.raises(RuntimeError):
        run(vendor, pg, clock)
    monkeypatch.setattr(books_store.BooksStore, "save", real_save)
    after = run(vendor, pg, clock)
    assert held_ids(pg) == list(range(100, 111)) + list(range(165, 176)) and after["complete"] is True


@needs_pg
def test_a_retried_id_that_fails_again_or_is_absent_is_read_once(pg, clock, monkeypatch):
    """Finding 5: a retried id above the high-water mark that failed again, or was absent, was read by the retry
    pass and then again by the frontier."""
    monkeypatch.setitem(sync.FIRST_ID, DEMO, 100)
    bad = httpx.Response(502, text="Bad Gateway")
    vendor = Vendor({sid: document(sid, 50000 + sid) for sid in range(100, 108)}, fail={106: bad, 107: bad})
    first = run(vendor, pg, clock)
    assert first["failed"] == {"http_error": 2} and first["added"] == 6
    del vendor.docs[107], vendor.fail[107]  # 107 is gone now (a deleted draft); 106 still fails
    vendor.reads.clear()
    run(vendor, pg, clock)
    assert vendor.reads.count(106) == 1 and vendor.reads.count(107) == 1


# ---- B2: the tick ------------------------------------------------------------------------------------------


def tick(vendor, url, clock, **kw):
    return run(vendor, url, clock, tick=True, **kw)


def books(*specs):
    """Documents numbered as the books number them: valid ones in one unbroken series by id, drafts apart.
    `specs`: (stock_id, status) pairs."""
    out, n = {}, 50100
    for sid, status in specs:
        if status == 0:
            out[sid] = document(sid, 90000 + sid, status=0, total=0.0)
        else:
            out[sid] = document(sid, n)
            n += 1
    return out


def age_misses(url, hours=2):
    with psycopg.connect(url) as conn:
        conn.execute("UPDATE books_documents SET missing_since = missing_since - make_interval(hours => %s), "
                     "missing_seen_at = missing_seen_at - make_interval(hours => %s) WHERE missing_since IS NOT NULL",
                     (hours, hours))


@needs_pg
def test_a_tick_reads_the_canary_first_and_every_draft(pg, clock, monkeypatch):
    monkeypatch.setitem(sync.FIRST_ID, DEMO, 100)
    vendor = Vendor(books(*[(sid, 1) for sid in range(100, 110)], (110, 0), (111, 1)))
    first = tick(vendor, pg, clock)
    assert first["canary"] == "none" and first["added"] == 12 and first["ok"] is True
    vendor.reads.clear()
    second = tick(vendor, pg, clock)
    assert vendor.reads[0] == 100 and second["canary"] == "ok"  # the oldest valid document held
    assert second["drafts"] == 1 and 110 in vendor.reads and second["ok"] is True and second["alert"] is False


@needs_pg
def test_a_draft_missed_twice_an_hour_apart_is_gone_and_a_valid_document_waits_for_a_person(pg, clock, monkeypatch):
    monkeypatch.setitem(sync.FIRST_ID, DEMO, 100)
    vendor = Vendor(books((100, 1), (101, 1), (102, 1), (103, 0), (104, 1)))
    assert tick(vendor, pg, clock)["ok"] is True
    del vendor.docs[103], vendor.docs[104]  # a draft deleted, and a valid document WizCloud no longer finds
    first_miss = tick(vendor, pg, clock)
    assert first_miss["missing"] == 2 and first_miss["gone"] == 0 and first_miss["ok"] is True
    age_misses(pg)  # the second read comes more than an hour after the first
    second_miss = tick(vendor, pg, clock)
    assert second_miss["gone"] == 1 and second_miss["missing_valid"] == 1
    assert second_miss["reasons"] == ["missing_valid"] and second_miss["ok"] is False and second_miss["alert"] is True
    with psycopg.connect(pg) as conn:
        gone = conn.execute("SELECT stock_id, gone_at IS NOT NULL FROM books_documents WHERE stock_id IN (103, 104) "
                            "ORDER BY stock_id").fetchall()
        events = conn.execute("SELECT stock_id, kind FROM books_document_events WHERE kind <> 'first_seen'").fetchall()
    assert gone == [(103, True), (104, False)] and events == [(103, "gone")]  # the valid one stays in every figure
    vendor.docs[104] = books((100, 1), (101, 1), (102, 1), (103, 0), (104, 1))[104]  # it answers again
    back = tick(vendor, pg, clock)
    assert back["missing_valid"] == 0 and back["ok"] is True and back["alert"] is True  # the way back to ok


@needs_pg
def test_a_canary_that_does_not_answer_trusts_no_absence(pg, clock, monkeypatch):
    """If WizCloud answered 'not found' for everything (the wrong company), every document would look gone."""
    monkeypatch.setitem(sync.FIRST_ID, DEMO, 100)
    vendor = Vendor(books(*[(sid, 1) for sid in range(100, 106)]))
    tick(vendor, pg, clock)
    with psycopg.connect(pg) as conn:
        conn.execute("INSERT INTO books_retry (company, stock_id, kind, attempts) VALUES (%s, 150, 'jumped', 0)", (DEMO,))
    vendor.docs.clear()
    blind = tick(vendor, pg, clock)
    assert blind["canary"] == "absent" and blind["reasons"] == ["canary"] and blind["ok"] is False
    assert blind["missing"] == 0 and retry_rows(pg) == [(150, "jumped")]  # nothing marked, nothing cleared
    with psycopg.connect(pg) as conn:
        assert conn.execute("SELECT count(*) FROM books_documents WHERE missing_since IS NOT NULL").fetchone()[0] == 0


@needs_pg
def test_at_most_five_drafts_go_in_one_tick(pg, clock, monkeypatch):
    monkeypatch.setitem(sync.FIRST_ID, DEMO, 100)
    vendor = Vendor(books((100, 1), *[(sid, 0) for sid in range(101, 109)]))
    tick(vendor, pg, clock)
    for sid in range(101, 109):
        del vendor.docs[sid]
    assert tick(vendor, pg, clock)["missing"] == 8
    age_misses(pg)
    five = tick(vendor, pg, clock)
    assert five["gone"] == 5 and "more than 5 drafts gone in one tick" in five["note"]
    assert tick(vendor, pg, clock)["gone"] == 3


@needs_pg
def test_the_weekly_sweep_keeps_its_place_and_waits_a_week(pg, clock, monkeypatch):
    monkeypatch.setitem(sync.FIRST_ID, DEMO, 100)
    monkeypatch.setattr(sync, "RECENT_PER_TICK", 0)
    vendor = Vendor(books(*[(sid, 1) for sid in range(100, 140)]))
    first = tick(vendor, pg, clock)
    assert first["sweep"] == {"next_id": 140, "ended": True, "new_pass": True}  # the frontier read every id already
    assert tick(vendor, pg, clock)["sweep"] is None  # this week's pass is done
    with psycopg.connect(pg) as conn:
        conn.execute("UPDATE books_sweep SET pass_started_at = now() - interval '8 days'")
    vendor.reads.clear()
    # one second a read: the canary, 50 misses and 5 probes, then the sweep until the box closes
    cut = tick(vendor, pg, clock, box_s=66.0, monotonic=lambda: float(len(vendor.reads)))
    sweep = cut["sweep"]
    assert sweep["new_pass"] is True and sweep["ended"] is False and 101 < sweep["next_id"] < 140
    vendor.reads.clear()
    rest = tick(vendor, pg, clock)
    assert rest["sweep"] == {"next_id": 140, "ended": True, "new_pass": False}
    assert sweep["next_id"] in vendor.reads and sweep["next_id"] - 1 not in vendor.reads  # it went on from its place
    with psycopg.connect(pg) as conn:
        assert conn.execute("SELECT passes FROM books_sweep").fetchone()[0] == 2


@needs_pg
def test_the_recent_documents_are_read_a_slice_a_tick(pg, clock, monkeypatch):
    monkeypatch.setitem(sync.FIRST_ID, DEMO, 100)
    monkeypatch.setattr(sync, "RECENT_HELD", 6)
    monkeypatch.setattr(sync, "RECENT_PER_TICK", 3)
    vendor = Vendor(books(*[(sid, 1) for sid in range(100, 110)]))
    tick(vendor, pg, clock)
    slices = []
    for _ in range(3):
        vendor.reads.clear()
        tick(vendor, pg, clock)
        slices.append(sorted(sid for sid in vendor.reads if 104 <= sid <= 109))
    assert slices == [[104, 105, 106], [107, 108, 109], [104, 105, 106]]


@needs_pg
def test_a_hole_is_judged_only_once_the_frontier_reached_the_end(pg, clock, monkeypatch):
    monkeypatch.setitem(sync.FIRST_ID, DEMO, 100)
    docs = books(*[(sid, 1) for sid in range(100, 140)])
    docs[110] = document(110, 50300)  # a number far ahead: 50110-50299 are missing while 110 is held
    vendor = Vendor(docs)
    catching = tick(vendor, pg, clock, box_s=15.0, monotonic=lambda: float(len(vendor.reads)))
    assert catching["catching_up"] is True and catching["holes"] > 0 and catching["ok"] is True  # counted, not judged
    caught = tick(vendor, pg, clock)
    assert caught["holes"] > 0 and caught["reasons"] == ["holes"] and caught["ok"] is False


@needs_pg
def test_an_id_failing_for_a_day_is_a_reason(pg, clock, monkeypatch):
    monkeypatch.setitem(sync.FIRST_ID, DEMO, 100)
    vendor = Vendor(books(*[(sid, 1) for sid in range(100, 106)]), fail=stuck_at(103))
    tick(vendor, pg, clock)
    assert tick(vendor, pg, clock)["stuck_day"] == 0  # failed twice, within the day
    with psycopg.connect(pg) as conn:
        conn.execute("UPDATE books_retry SET first_failed_at = now() - interval '25 hours'")
    late = tick(vendor, pg, clock)
    assert late["stuck_day"] == 1 and "stuck" in late["reasons"]


@needs_pg
def test_the_tick_route_alerts_on_a_change_only_and_says_it_in_hebrew(books_app):
    client, vendor = books_app
    auth = {"Authorization": f"Bearer {SYNC_KEY}"}
    first = client.post("/api/books/tick", headers=auth).json()
    assert first["ok"] is True and first["alert"] is False and first["request_s"] < main.REQUEST_BOX_S
    bad = httpx.Response(502, text="Bad Gateway")
    vendor.fail.update({sid: bad for sid in range(0, 2000)})
    down = client.post("/api/books/tick", headers=auth).json()
    assert down["ok"] is False and down["reasons"] == ["wizcloud_failing"] and down["alert"] is True
    assert down["canary"] == "failed"  # its reads failed: WizCloud failing, not a canary that says 'not found'
    assert down["message"] == "ספרי חשבשבת (demo): WizCloud לא עונה: שלוש קריאות נכשלו ברצף."
    assert client.post("/api/books/tick", headers=auth).json()["alert"] is False  # still down: said once
    vendor.fail.clear()
    back = client.post("/api/books/tick", headers=auth).json()
    assert back["ok"] is True and back["alert"] is True and back["message"] == "ספרי חשבשבת (demo): חזר לתקין."
    status = client.get("/api/books/status", headers=auth).json()
    assert status["sweep"]["passes"] >= 1 and status["missing"]["missing"] == 0
    health = client.get("/health").json()["books"]
    assert health["last_run"]["reasons"] == []


@needs_pg
def test_the_whole_request_is_boxed(pg, clock, monkeypatch):
    """Spec §2.3: 60 seconds for the whole request, the session included. Here 3, with reads of 0.25 s."""
    import time as _time

    monkeypatch.setenv("WIZ_PRIVATE_KEY", KEY)
    monkeypatch.setenv("DATABASE_URL", pg)
    monkeypatch.setenv("BOOKS_SYNC_KEY", SYNC_KEY)
    monkeypatch.delenv("WIZ_COMPANY", raising=False)
    monkeypatch.setitem(sync.FIRST_ID, DEMO, 100)
    monkeypatch.setattr(main, "REQUEST_BOX_S", 3)
    monkeypatch.setattr(main, "FINISH_RESERVE_S", 0.5)
    vendor = TimedVendor({sid: document(sid, 50000 + sid) for sid in range(100, 200)}, latency=0.25)
    main.app.state.transport = httpx.MockTransport(vendor.handler)
    main.app.state.client = None
    try:
        started = _time.monotonic()
        body = TestClient(main.app).post("/api/books/tick", headers={"Authorization": f"Bearer {SYNC_KEY}"}).json()
        took = _time.monotonic() - started
    finally:
        main.app.state.transport = None
        main.app.state.client = None
    assert took < 3.5 and body["catching_up"] is True and body["ok"] is True and 0 < body["added"] < 100


def test_every_migration_only_creates_or_adds():
    """A publish aligns production to development, DROP included: a migration never drops, renames or retypes."""
    import re as _re

    for path in sorted((main.ROOT / "migrations").glob("*.sql")):
        sql = "\n".join(line.split("--")[0] for line in path.read_text(encoding="utf-8").splitlines()).upper()
        assert not _re.search(r"\bDROP\b|\bRENAME\b|\bALTER\s+COLUMN\b|\bTRUNCATE\b|\bDELETE\b", sql), path.name


@needs_pg
def test_a_canary_that_went_missing_is_missed_once_the_next_one_answers(pg, clock, monkeypatch):
    """Self-review 22.11: the oldest valid document is read first in every tick, before anything is trusted - so
    if it were deleted and the next one answered, its own miss was never recorded and it never went missing."""
    monkeypatch.setitem(sync.FIRST_ID, DEMO, 100)
    vendor = Vendor(books(*[(sid, 1) for sid in range(100, 106)]))
    tick(vendor, pg, clock)
    del vendor.docs[100]
    second = tick(vendor, pg, clock)
    assert second["canary"] == "ok" and second["missing"] == 1
    age_misses(pg)
    assert tick(vendor, pg, clock)["missing_valid"] == 1


# ---- the fourth review of 22.11 (on ea952e0): each test failed there -------------------------------------------

STUCK_500 = STUCK["vendor_error"]


def sweep_row(url):
    with psycopg.connect(url) as conn:
        return conn.execute("SELECT next_id, passes FROM books_sweep").fetchone()


@needs_pg
def test_two_deleted_canaries_do_not_blind_every_tick(pg, clock, monkeypatch):
    """Finding 1: the canaries were always the two oldest valid documents, and only a canary that answered could have
    them marked missing - so two deleted ones left every later tick blind: no miss, no draft gone, for good."""
    monkeypatch.setitem(sync.FIRST_ID, DEMO, 100)
    vendor = Vendor(books(*[(sid, 1) for sid in range(100, 110)], (110, 0)))
    tick(vendor, pg, clock)
    del vendor.docs[100], vendor.docs[101], vendor.docs[110]  # the two oldest valid documents, and a draft
    first = tick(vendor, pg, clock)
    assert first["canary"] == "ok" and first["missing"] == 3
    age_misses(pg)
    second = tick(vendor, pg, clock)
    assert second["gone"] == 1 and second["missing_valid"] == 2 and second["reasons"] == ["missing_valid"]


@needs_pg
def test_canaries_that_fail_on_every_read_are_not_read_as_canaries(pg, clock, monkeypatch):
    """Finding 1: two canaries the vendor cannot render made every tick trust no absence, while it said ok."""
    monkeypatch.setitem(sync.FIRST_ID, DEMO, 100)
    vendor = Vendor(books((100, 1), (101, 1), (102, 1), (103, 0), (104, 1)))
    tick(vendor, pg, clock)
    vendor.fail.update({100: STUCK_500, 101: STUCK_500})
    del vendor.docs[103]
    tick(vendor, pg, clock)  # 100 and 101 fail and wait in books_retry
    second = tick(vendor, pg, clock)
    assert second["canary"] == "ok" and second["missing"] == 1  # the draft's miss is recorded
    age_misses(pg)
    assert tick(vendor, pg, clock)["gone"] == 1


@needs_pg
def test_a_tick_that_does_not_reach_the_end_keeps_the_hole_it_knew(pg, clock, monkeypatch):
    """Finding 2: holes were judged only when the frontier reached the end, so one failed far probe dropped the
    reason - ok, alert, and 'back to ok' posted while the hole was still there."""
    monkeypatch.setitem(sync.FIRST_ID, DEMO, 100)
    docs = books(*[(sid, 1) for sid in range(100, 140)])
    docs[110] = document(110, 50300)
    vendor = Vendor(docs)
    assert tick(vendor, pg, clock)["reasons"] == ["holes"]
    vendor.fail[139 + 64] = httpx.Response(502, text="Bad Gateway")
    second = tick(vendor, pg, clock)
    assert second["reasons"] == ["holes"] and second["ok"] is False and second["alert"] is False
    assert second["holes"] > 0


@needs_pg
def test_a_new_document_is_not_a_new_pass_of_the_sweep(pg, clock, monkeypatch):
    """Finding 3: after a pass ended, each new document made the sweep 'go on' over ids it had read, end again and
    count a pass."""
    monkeypatch.setitem(sync.FIRST_ID, DEMO, 100)
    monkeypatch.setattr(sync, "RECENT_PER_TICK", 0)
    all_docs = books(*[(sid, 1) for sid in range(100, 150)])
    vendor = Vendor({sid: all_docs[sid] for sid in range(100, 140)})
    tick(vendor, pg, clock)
    for sid in (140, 141, 142):
        vendor.docs[sid] = all_docs[sid]
        assert tick(vendor, pg, clock)["sweep"] is None  # this week's pass is done
    assert sweep_row(pg)[1] == 1


@needs_pg
def test_a_week_later_the_sweep_begins_again_while_documents_keep_coming(pg, clock, monkeypatch):
    """Finding 3: a new document in every tick held the next pass back for good."""
    monkeypatch.setitem(sync.FIRST_ID, DEMO, 100)
    monkeypatch.setattr(sync, "RECENT_PER_TICK", 0)
    all_docs = books(*[(sid, 1) for sid in range(100, 150)])
    vendor = Vendor({sid: all_docs[sid] for sid in range(100, 140)})
    tick(vendor, pg, clock)
    with psycopg.connect(pg) as conn:
        conn.execute("UPDATE books_sweep SET pass_started_at = now() - interval '8 days'")
    vendor.docs[140] = all_docs[140]
    assert tick(vendor, pg, clock)["sweep"]["new_pass"] is True


@needs_pg
def test_a_crash_that_repeats_alerts_once(books_app, monkeypatch):
    """Finding 4: every crash answered alert true, so one that repeats posted to Discord every hour."""
    client, _ = books_app
    auth = {"Authorization": f"Bearer {SYNC_KEY}"}

    def boom(self, company, n):
        raise RuntimeError("a bug in a phase")

    monkeypatch.setattr(books_store.BooksStore, "canaries", boom)
    first = client.post("/api/books/tick", headers=auth).json()
    second = client.post("/api/books/tick", headers=auth).json()
    assert first["alert"] is True and second["alert"] is False and second["reasons"] == ["crashed"]


@needs_pg
def test_the_way_back_from_a_crash_before_the_run_is_said(books_app, monkeypatch):
    """Finding 4: a crash before the run's row was written left no row, so the next good tick compared with the run
    before the crash and said nothing - Discord's last word stayed 'it crashed'."""
    client, _ = books_app
    auth = {"Authorization": f"Bearer {SYNC_KEY}"}
    assert client.post("/api/books/tick", headers=auth).json()["alert"] is False
    real = books_store.BooksStore.start_run

    def broken(self, company, trigger, from_id):
        raise books_store.BooksStoreError("57P01")

    monkeypatch.setattr(books_store.BooksStore, "start_run", broken)
    down = client.post("/api/books/tick", headers=auth).json()
    monkeypatch.setattr(books_store.BooksStore, "start_run", real)
    back = client.post("/api/books/tick", headers=auth).json()
    assert down["alert"] is True and back["alert"] is True and back["message"].endswith("חזר לתקין.")


@needs_pg
def test_an_id_queued_as_jumped_is_stuck_from_its_first_failure_not_from_its_queueing(pg, clock, monkeypatch):
    """The review's minor note: first_failed_at of a jumped id was the time it was queued."""
    monkeypatch.setitem(sync.FIRST_ID, DEMO, 100)
    vendor = Vendor(books(*[(sid, 1) for sid in range(100, 106)]))
    tick(vendor, pg, clock)
    with psycopg.connect(pg) as conn:
        conn.execute("INSERT INTO books_retry (company, stock_id, kind, attempts, first_failed_at) "
                     "VALUES (%s, 103, 'jumped', 0, now() - interval '3 days')", (DEMO,))
    vendor.fail[103] = STUCK_500
    tick(vendor, pg, clock)
    assert tick(vendor, pg, clock)["stuck_day"] == 0  # failed twice today: not a day yet


def test_the_message_says_back_to_ok_only_on_the_way_back():
    """Seen on Demo 22.11 (run 2): a tick with nothing wrong before or now said 'חזר לתקין' - harmless, since Make posts
    only on an alert, but a false sentence in the answer."""
    quiet = {"reasons": [], "alert": False}
    back = {"reasons": [], "alert": True}
    assert main.tick_message("demo", quiet) == "ספרי חשבשבת (demo): תקין."
    assert main.tick_message("demo", back) == "ספרי חשבשבת (demo): חזר לתקין."


# ---- the alert goes by email (Nadav 22.11: the Discord server is out of use; ADR-0178's shape) ---------------------


@needs_pg
def test_an_alert_carries_the_email_the_service_composed(books_app):
    """The service decides and composes the email, Make only sends it (ADR-0178): a subject and an RTL body, counts
    and a closed vocabulary only, never a value from the books."""
    client, vendor = books_app
    auth = {"Authorization": f"Bearer {SYNC_KEY}"}
    vendor.fail.update({sid: httpx.Response(502, text="Bad Gateway") for sid in range(0, 2000)})
    down = client.post("/api/books/tick", headers=auth).json()
    email = down["email"]
    assert down["alert"] is True and email["subject"] == "ספרי חשבשבת (demo): WizCloud לא עונה"
    assert 'dir="rtl"' in email["html"] and down["message"] in email["html"] and "/health" in email["html"]
    for leak in ("ישראל", "50105", "650", "קורס", SYNC_KEY):
        assert leak not in json.dumps(down, ensure_ascii=False)
    vendor.fail.clear()
    back = client.post("/api/books/tick", headers=auth).json()
    assert back["alert"] is True and back["email"]["subject"] == "ספרי חשבשבת (demo): חזר לתקין"


@needs_pg
def test_an_alert_test_reads_nothing_and_writes_nothing(books_app):
    """?alert_test=1, behind the key: an alert with its email and nothing else, so the email path is seen live before
    the scenario is switched on."""
    client, vendor = books_app
    auth = {"Authorization": f"Bearer {SYNC_KEY}"}
    assert client.post("/api/books/tick?alert_test=1").status_code == 401
    body = client.post("/api/books/tick?alert_test=1", headers=auth).json()
    assert body["test"] is True and body["alert"] is True and body["ok"] is True
    assert body["email"]["subject"] == "ספרי חשבשבת (demo): בדיקת התראה" and vendor.reads == []
    with psycopg.connect(main.config.load().database_url) as conn:
        assert conn.execute("SELECT count(*) FROM books_sync_runs").fetchone()[0] == 0


# ---- the weekly sweep on the deployment (22.11: probed 161, sweep null) ------------------------------------------
class Counting:
    """A connection that counts what a save sends: statements and transactions."""

    def __init__(self, conn):
        self.conn, self.statements, self.transactions = conn, 0, 0

    def execute(self, *a, **k):
        self.statements += 1
        return self.conn.execute(*a, **k)

    def transaction(self, *a, **k):
        self.transactions += 1
        return self.conn.transaction(*a, **k)

    def cursor(self, *a, **k):
        outer, cur = self, self.conn.cursor(*a, **k)

        class Cursor:
            def execute(self, *a, **k):
                outer.statements += 1
                return cur.execute(*a, **k)

            def __getattr__(self, name):
                return getattr(cur, name)

        return Cursor()

    def __getattr__(self, name):
        return getattr(self.conn, name)


def saved_twice(s, sid=7, **edits):
    """A document saved, then the same one again on a counting connection: the second save's result and counts."""
    run_id = s.start_run(DEMO, "test", 1)
    doc, lines, _ = document_rows(json.loads(json.dumps(document(sid, 50000 + sid, **edits)), parse_float=Decimal))
    assert s.save(DEMO, run_id, doc, lines) == "added"
    return run_id, doc, lines


@needs_pg
def test_an_unchanged_document_is_one_statement_that_does_what_the_full_save_did(pg):
    """On the deployment a save of an unchanged document was six round trips to the database (BEGIN, SET LOCAL,
    DELETE, SELECT FOR UPDATE, UPDATE, COMMIT), about 0.24 s - more than the read itself - so the steady tick's fixed
    phases filled the box and the weekly sweep never began (22.11, probed 161, sweep null)."""
    store = BooksStore(pg)
    with store.session(DEMO) as s:
        run_id, doc, lines = saved_twice(s)
        s.mark_failed(DEMO, 7, "http_error")  # it failed once before: the read that works clears it
        with psycopg.connect(pg) as conn:
            conn.execute("UPDATE books_documents SET last_read_at = now() - interval '1 day'")
        counting = Counting(s._conn)
        s._conn = counting
        try:
            assert s.save(DEMO, run_id, doc, lines) == "unchanged"
        finally:
            s._conn = counting.conn
    assert (counting.statements, counting.transactions) == (1, 0)
    with psycopg.connect(pg) as conn:
        assert conn.execute("SELECT extract(epoch FROM now() - last_read_at) < 60 FROM books_documents").fetchone()[0]
        assert conn.execute("SELECT count(*) FROM books_retry").fetchone()[0] == 0
        assert [e[0] for e in conn.execute("SELECT kind FROM books_document_events").fetchall()] == ["first_seen"]


@needs_pg
def test_a_missing_or_gone_document_read_again_unchanged_still_takes_the_full_save(pg):
    """The one statement is only for a row that is neither missing nor gone: those are cleared, and a gone one says
    it reappeared, in the full save's transaction."""
    store = BooksStore(pg)
    with store.session(DEMO) as s:
        run_id, doc, lines = saved_twice(s)
        with psycopg.connect(pg) as conn:
            conn.execute("UPDATE books_documents SET missing_since = now(), missing_seen_at = now()")
        assert s.save(DEMO, run_id, doc, lines) == "unchanged"
        with psycopg.connect(pg) as conn:
            conn.execute("UPDATE books_documents SET gone_at = now()")
        assert s.save(DEMO, run_id, doc, lines) == "unchanged"
    with psycopg.connect(pg) as conn:
        assert conn.execute("SELECT gone_at, missing_since FROM books_documents").fetchone() == (None, None)
        assert [e[0] for e in conn.execute("SELECT kind FROM books_document_events ORDER BY event_id")] == [
            "first_seen", "reappeared"]


@needs_pg
def test_an_unchanged_document_whose_row_is_held_does_not_wait_outside_the_timeout(pg):
    """The one statement takes its row's lock with NOWAIT: a row held elsewhere falls to the full save, which waits
    within its statement timeout, so no lock can hold the box with no bound."""
    import threading

    store = BooksStore(pg)
    with store.session(DEMO) as s:
        run_id, doc, lines = saved_twice(s)
        holder = psycopg.connect(pg)
        holder.execute("SELECT 1 FROM books_documents WHERE stock_id = 7 FOR UPDATE")
        release = threading.Timer(0.5, holder.rollback)
        release.start()
        counting = Counting(s._conn)
        s._conn = counting
        try:
            assert s.save(DEMO, run_id, doc, lines) == "unchanged"
        finally:
            s._conn = counting.conn
            release.join()
            holder.close()
    assert counting.transactions == 1  # the one statement gave way at once; the full save waited for the lock


@needs_pg
def test_a_tick_with_no_time_left_for_the_sweep_still_begins_its_pass(pg, clock, monkeypatch):
    """When the box closed before phase 8 no row was written, so a pass that never began had no age and could never be
    late (22.11 on the deployment: sweep null after every tick, and no reason said so)."""
    monkeypatch.setitem(sync.FIRST_ID, DEMO, 100)
    vendor = Vendor(books(*[(sid, 1) for sid in range(100, 140)]))
    run(vendor, pg, clock)  # the copy alone: no sweep
    vendor.reads.clear()
    # one second a read: the canary, 50 misses and 5 probes, then the recent documents until the box closes
    cut = tick(vendor, pg, clock, box_s=80.0, monotonic=lambda: float(len(vendor.reads)))
    assert cut["swept"] == 0 and cut["recent"] < 39
    unread = min(sid for sid in range(100, 140) if sid not in vendor.reads)  # what this tick read counts for the pass
    assert cut["sweep"] == {"next_id": unread, "ended": False, "new_pass": True}
    assert sweep_row(pg) == (unread, 0) and cut["reasons"] == []


@needs_pg
def test_a_pass_of_the_sweep_open_for_more_than_a_week_is_a_reason(pg, clock, monkeypatch):
    monkeypatch.setitem(sync.FIRST_ID, DEMO, 100)
    vendor = Vendor(books(*[(sid, 1) for sid in range(100, 140)]))
    run(vendor, pg, clock)

    def one_second_a_read():
        return float(len(vendor.reads))

    begun = tick(vendor, pg, clock, box_s=3.0, monotonic=one_second_a_read)  # the canary and one miss: no time to sweep
    assert begun["sweep"]["new_pass"] is True and begun["swept"] == 0 and begun["reasons"] == []
    with psycopg.connect(pg) as conn:
        conn.execute("UPDATE books_sweep SET pass_started_at = now() - interval '7 days 1 hour'")
    late = tick(vendor, pg, clock, box_s=3.0, monotonic=one_second_a_read)
    assert late["reasons"] == ["sweep_late"] and late["ok"] is False and late["alert"] is True
    assert sync.REASONS == tuple(main.REASON_TEXT) == tuple(main.SUBJECT_TEXT)  # every reason is said in Hebrew
    done = tick(vendor, pg, clock)  # time enough: the pass ends, and the tick says it is back
    assert done["sweep"]["ended"] is True and done["reasons"] == [] and done["alert"] is True
    with psycopg.connect(pg) as conn:
        conn.execute("UPDATE books_sweep SET pass_started_at = now() - interval '8 days'")
    again = tick(vendor, pg, clock, box_s=3.0, monotonic=one_second_a_read)
    assert again["sweep"]["new_pass"] is True and again["reasons"] == []  # an ended pass is never late: one begins
