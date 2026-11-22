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


def run(vendor, url, clock, box_s=600, monotonic=None):
    store = BooksStore(url)
    with store.session(DEMO) as s:
        return sync.run_once(vendor.client(clock), s, DEMO, trigger="test", box_s=box_s, **({"clock": monotonic} if monotonic else {}))


def demo_books():
    docs = {sid: document(sid, 50000 + sid) for sid in range(100, 141) if sid not in (120, 121)}  # 120-121: deleted drafts
    docs.update({sid: document(sid, 50000 + sid) for sid in range(396, 401)})  # a jump of 256 past 140
    return docs


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
    assert first["ok"] is True and first["complete"] is False and first["failed"] == {"http_error": 3}
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

    def refuse_102(self, company, run_id, doc, lines):
        if doc["stock_id"] == 102:
            raise books_store.BooksStoreError("22021")
        return real_save(self, company, run_id, doc, lines)

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
        again = sync.run_once(vendor.client(clock), s, DEMO, trigger="test", box_s=600, reread_held=True)
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
        sync.run_once(client, s, DEMO, trigger="test", box_s=20)
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
    assert summary["failed"] == {"bad_response": 2} and summary["added"] == 3 and summary["ok"] is True
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
    assert second["retried"] == 3 and second["found"] == 3 and second["from_id"] == 109
    assert all(vendor.reads.count(sid) == 1 for sid in (106, 107, 108)) and second["complete"] is True


@needs_pg
def test_what_masking_cost_is_counted_by_field_and_reason(pg, clock, monkeypatch):
    monkeypatch.setitem(sync.FIRST_ID, DEMO, 100)
    summary = run(Vendor({100: document(100, 50100, remarks="עח 50347 2026")}), pg, clock)
    assert summary["masked_fields"] == {"account_name": 1, "remarks": 1}
    assert summary["clean"] == {"numbers_masked": 2, "refs_lost": {"plain": 1}}


def test_a_number_out_of_range_or_not_finite_is_not_a_document():
    for value in ('"Infinity"', "1E+999999", "1E-99"):
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
