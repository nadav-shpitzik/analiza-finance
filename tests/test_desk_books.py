"""D3 (Analiza-Monday backlog 0301): what the books say that monday does not, in the desk, read only - app/desk/books.py, and
what it needs of the books copy (app/books/store.py `desk_snapshot`, `not_ready`) and of A1 (app/recon/receipt_dates.py).

Each rule with its test (the rules' contract §0.1), each named by its rule: the marks from A1's buckets (FR-C1, FR-3, FR-4),
the documents no charge cites (FR-C4, FR-D1), the side of a card (FR-6), the era (spec §6.4.2, ADR-0168 §5), the law of §5
(a name is only ever a candidate), the gate (spec §6.4.1), and only a whole copy changing what is shown (spec §6.1.5).

Fixtures are synthetic and shaped like the incidents (the rules' contract §0.2): numbers, amounts and dates mirror the
records, names are invented, and no card carries an ID number."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import psycopg
import pytest

from app.books.store import NOT_READY, BooksStore, not_ready
from app.recon import receipt_dates as a1
from tests.conftest import TEST_DATABASE_URL, drop_all, needs_pg

ANALIZA = "wizdb2245n5"


def doc(number, total="4000", day="2026-11-03", doc_type=2, status=1, missing=False, card="20001", name="דנה לוי",
        excl=None) -> dict:
    """A document as BooksStore.desk_snapshot gives it: its card's key and name, and its total before VAT."""
    total_d = Decimal(total) if total is not None else None
    return {"stock_id": int(number), "doc_type": doc_type, "doc_number": str(number), "status": status,
            "value_date": date.fromisoformat(day) if day else None, "total_incl": total_d, "missing": missing,
            "total_excl": Decimal(excl) if excl is not None else (None if total_d is None else (total_d / Decimal("1.18")).quantize(Decimal("0.01"))),
            "account_key": card, "account_name": name}


# ---- the books copy: one snapshot, and the rule A1 and the desk share -------------------------------------------------------
@pytest.fixture
def pg():
    drop_all(TEST_DATABASE_URL)
    from app.migrate import apply_migrations

    apply_migrations(TEST_DATABASE_URL)
    yield TEST_DATABASE_URL
    drop_all(TEST_DATABASE_URL)


def seed(url: str, docs: list, run: tuple = (True, True), reasons=()) -> None:
    """Documents into the copy as a run saves them, and a finished run (complete, ok) unless `run` is None."""
    with psycopg.connect(url, autocommit=True) as conn:
        for d in docs:
            conn.execute(
                "INSERT INTO books_documents (company, stock_id, doc_type, doc_number, status, value_date, total_incl, "
                "total_excl, account_key, account_name, missing_since, content_hash) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CASE WHEN %s THEN now() END, 'h')",
                (ANALIZA, d["stock_id"], d["doc_type"], d["doc_number"], d["status"], d["value_date"], d["total_incl"],
                 d["total_excl"], d["account_key"], d["account_name"], d["missing"]))
        if run is not None:
            conn.execute("INSERT INTO books_sync_runs (company, trigger, finished_at, complete, ok, reasons) "
                         "VALUES (%s, 'test', now(), %s, %s, %s)", (ANALIZA, run[0], run[1], list(reasons)))


@needs_pg
def test_the_desks_snapshot_is_a1s_with_the_card_and_the_total_before_vat(pg):
    """D3: `desk_snapshot` reads what `matching_snapshot` reads, in one repeatable-read transaction on a connection of its
    own (no run's lock), and each document's card and total before VAT, and the last finished run's id and time."""
    seed(pg, [doc(62500, card="20001", name="דנה לוי"), doc(50400, doc_type=1, card="30001", name="חברה בדויה",
                                                            excl="5000", total="5900")])
    store = BooksStore(pg)
    wide = store.desk_snapshot(ANALIZA)
    with store.session(ANALIZA + ":t") as s:
        narrow = s.matching_snapshot(ANALIZA)
    base = ("stock_id", "doc_type", "doc_number", "status", "value_date", "total_incl", "missing")
    assert [{k: d[k] for k in base} for d in wide["documents"]] == narrow["documents"]
    assert {k: wide[k] for k in narrow if k != "documents"} == {k: v for k, v in narrow.items() if k != "documents"}
    first = wide["documents"][0]
    assert (first["account_key"], first["account_name"], first["total_excl"]) == ("30001", "חברה בדויה", Decimal("5000"))
    assert isinstance(wide["last_run_id"], int) and isinstance(wide["last_run_finished_at"], datetime)
    assert store.last_finished_run(ANALIZA) == (wide["last_run_id"], wide["last_run_finished_at"])
    # A1's own answer is unchanged, key for key: no card, no name, no run id
    assert set(narrow) == {"documents", "retry_waiting", "last_run_complete", "last_run_ok", "last_run_reasons",
                           "ever_completed"}
    assert set(narrow["documents"][0]) == set(base)


@needs_pg
def test_the_desks_snapshot_holds_no_run_lock_and_says_none_when_no_run_finished(pg):
    seed(pg, [doc(62500)], run=None)
    store = BooksStore(pg)
    with store.session(ANALIZA) as s:  # a tick holds its run's lock: the desk reads beside it
        assert s is not None
        got = store.desk_snapshot(ANALIZA)
    assert got["last_run_id"] is None and got["last_run_finished_at"] is None and got["ever_completed"] is False
    assert store.last_finished_run(ANALIZA) is None and not_ready(got) == "never_completed"


@needs_pg
def test_last_finished_run_is_bounded_like_the_snapshot(pg):
    """The review of dfa967b, finding 6 (tests/test_review_d3_run_timeout.py, ported): the desk asks it first, inside a
    person's request and inside DeskBooks' one lock; desk_snapshot bounds its statements, and last_finished_run did not, so
    a query waiting behind a lock on books_sync_runs (a publish's schema change takes one) held the request and the lock
    for as long as the lock was held. It is bounded the same way (STATEMENT_TIMEOUT), said by class. Takes about 15 s."""
    import threading

    from app.books.store import STATEMENT_TIMEOUT, BooksStoreError

    seed(pg, [doc(62500)])
    store = BooksStore(pg)
    got: dict = {}

    def ask():
        try:
            got["answer"] = store.last_finished_run(ANALIZA)
        except BooksStoreError as exc:
            got["error"] = exc.detail

    with psycopg.connect(pg) as locker:                                      # one transaction, holding the table
        locker.execute("LOCK TABLE books_sync_runs IN ACCESS EXCLUSIVE MODE")
        t = threading.Thread(target=ask, daemon=True)
        t.start()
        t.join(int(STATEMENT_TIMEOUT.rstrip("s")) + 5)
        hung = t.is_alive()
        locker.rollback()                                                    # let it go, whatever happened
    t.join(10)
    assert not hung, "last_finished_run waited past its bound behind a lock"
    assert got == {"error": "57014"}                                         # query_canceled: by its SQLSTATE, no text


@needs_pg
def test_the_desks_snapshot_leaves_out_a_gone_document_and_a_run_in_progress_is_no_finished_run(pg):
    """S04 and S07 of the review's mutants: a document the books deleted (`gone_at`) is no document of the copy, and a run
    still in progress (no `finished_at`) is not the copy's last finished run - the desk names its copy by that run."""
    seed(pg, [doc(62500), doc(62501)])
    with psycopg.connect(pg, autocommit=True) as conn:
        conn.execute("UPDATE books_documents SET gone_at = now() WHERE company = %s AND stock_id = 62501", (ANALIZA,))
        conn.execute("INSERT INTO books_sync_runs (company, trigger) VALUES (%s, 'tick')", (ANALIZA,))   # in progress
    store = BooksStore(pg)
    snapshot = store.desk_snapshot(ANALIZA)
    assert [d["stock_id"] for d in snapshot["documents"]] == [62500]
    run = store.last_finished_run(ANALIZA)
    assert run is not None and run == (snapshot["last_run_id"], snapshot["last_run_finished_at"])
    assert run[1] is not None


def test_a_database_that_cannot_be_reached_is_a_books_store_error_by_class():
    from app.books.store import BooksStoreError

    store = BooksStore("postgresql://nobody@127.0.0.1:1/none?connect_timeout=1")
    for ask in (store.desk_snapshot, store.last_finished_run):
        with pytest.raises(BooksStoreError) as got:
            ask(ANALIZA, connect_timeout_s=1)
        assert "nobody" not in got.value.detail and "127.0.0.1" not in got.value.detail


def test_a1_and_the_desk_judge_a_copy_by_one_rule():
    """spec §6.1.5: the conditions of A1's plan, in its order - never completed, last run cut, last run not ok, ids waiting,
    a hole - now the books copy's own (`not_ready`), which app/main.py imports under A1's old names."""
    from app import main

    assert main.books_not_ready is not_ready and main.BOOKS_NOT_READY == NOT_READY
    whole = {"ever_completed": True, "last_run_complete": True, "last_run_ok": True, "retry_waiting": 0,
             "documents": [doc(62500), doc(62501)]}
    assert not_ready(whole) is None
    assert not_ready(dict(whole, ever_completed=False, last_run_complete=False)) == "never_completed"
    assert not_ready(dict(whole, last_run_complete=False, last_run_ok=False)) == "last_run_incomplete"
    assert not_ready(dict(whole, last_run_ok=False, retry_waiting=3)) == "last_run_not_ok"
    assert not_ready(dict(whole, retry_waiting=3)) == "retry_waiting"
    assert not_ready(dict(whole, documents=[doc(62500), doc(62502)])) == "holes"


# ---- A1 says what decided a row (nothing it decides changes) ------------------------------------------------------------------
SC_BASE = 50934486770000


def row(n: int, ref: str = "", paid: str = "4000", status: str = "1", name: str = "דנה לוי", day: str = "") -> dict:
    """A Student Charge row as the reading holds it (app/monday/client.flatten)."""
    return {"id": str(SC_BASE + n), "name": name, "state": "active", a1.REF_COLUMN: ref, a1.AMOUNT_COLUMN: paid,
            a1.COLUMN: day, a1.STATUS_COLUMN: "שולם", a1.STATUS_COLUMN + "#": status}


def test_a1_says_which_numbers_failed_and_what_its_groups_summed_without_changing_a_bucket():
    """`refused`: each number of a row that failed on its own, with its bucket, in the order typed; `group`: the rows,
    documents and the two sums the verdict compared (63109: one 4,000 invoice cited by two 4,000 rows, a gap)."""
    docs = [doc(63109, total="4000"), doc(70100, doc_type=3), doc(62510, total="10000"), doc(62600), doc(62600)]
    rows = [row(0, "63109"), row(1, "63109"), row(2, "99999, 70100"), row(3, "62510", paid="4000"),
            row(4, "62510", paid="6000"), row(5, "62600")]
    got = a1.analyse(rows, docs)["rows"]
    e = {int(i) - SC_BASE: x for i, x in got.items()}
    assert [e[i]["bucket"] for i in range(6)] == ["amount_mismatch", "amount_mismatch", "ref_not_in_books", "fill", "fill",
                                                  "ref_ambiguous"]
    assert e[0]["group"] is e[1]["group"] and e[0]["group"] == {
        "rows": [str(SC_BASE), str(SC_BASE + 1)], "documents": ["63109"], "paid": Decimal(8000), "total": Decimal(4000)}
    assert e[2]["refused"] == [("99999", "ref_not_in_books"), ("70100", "ref_not_an_invoice")] and e[2]["group"] is None
    assert e[3]["group"]["paid"] == e[3]["group"]["total"] == Decimal(10000) and e[3]["refused"] == []
    assert e[5]["refused"] == [("62600", "ref_ambiguous")]
    # the plan is what it was: the same actions, the same counts
    plan = a1._plan_body(a1.analyse(rows, docs))
    assert plan["fill"] == 2 and plan["counts"]["amount_mismatch"] == 2 and plan["counts"]["ref_ambiguous"] == 1


# ---- the marks (spec §6.1.1, §6.2): A1's buckets, and nothing else --------------------------------------------------------
from types import SimpleNamespace  # noqa: E402

from app.desk import books as db  # noqa: E402
from app.desk import view as fd  # noqa: E402
from tests.desk_fixtures import TODAY, b2, reading_of, sc, snap  # noqa: E402

AS_OF = datetime(2026, 11, 25, 8, 0, tzinfo=timezone.utc)
NOW = datetime(2026, 11, 25, 8, 30, tzinfo=timezone.utc)   # the desk's clock: half an hour after the copy's last run
CFG = SimpleNamespace(database_url="postgresql://books.test/db", company="analiza", db=ANALIZA)


def copy(docs: list, **over) -> dict:
    """A whole copy as desk_snapshot answers it."""
    out = {"documents": list(docs), "retry_waiting": 0, "last_run_complete": True, "last_run_ok": True,
           "last_run_reasons": [], "ever_completed": True, "last_run_id": 7, "last_run_finished_at": AS_OF}
    out.update(over)
    return out


def pay(id_, ref="", paid="4000", st="1", on="2026-11-03", name=None, receipt="1") -> dict:
    """A Student Charge row of the desk's reading: its reference, what was paid, its status by index, its date typed."""
    r = sc(id_, st=st, paid=paid, invoice=ref, receipt=receipt, paid_date=on, lead=["L%s" % id_])
    if name is not None:
        r["name"] = name
    return r


def run(sc_rows, docs, b2_rows=(), keys=None) -> tuple:
    """(the part, the view, the reading) for these rows and this copy."""
    reading = reading_of(snap(list(sc_rows), list(b2_rows)))
    view = fd.build_view(reading, TODAY)
    return db.compute(reading, view, copy(docs), keys or {}), view, reading


def marks(part) -> dict:
    return {i: [(m["kind"], m["chip"]) for m in ms] for i, ms in part["marks"].items()}


def facts_row(view: dict, reading: dict, item_id: str) -> dict:
    """A row as the marks read it (`compute`): the view's row, and its "סכום ששולם" as the reading holds it."""
    raw = {str(r["id"]): r for r in reading["student_charge"]["rows"]}[item_id]
    return dict({r["id"]: r for r in view["b2c"]["rows"]}[item_id], paid_raw=raw.get(a1.AMOUNT_COLUMN))


def test_every_mark_is_a1s_bucket_and_every_other_bucket_gives_none():
    """The invariant of the marks, for the same rows and the same copy: `books_ref_invalid` only on a row A1 refused for its
    numbers and `books_amount_differs` only on `amount_mismatch` (MARK_OF_BUCKET); on every other row,
    `books_receipt_unpaid` exactly when its own facts say so (`receipt_unpaid_facts`), whatever its bucket - and no other
    mark. A1's buckets are what they were."""
    docs = [doc(62500), doc(62501), doc(62600), doc(62600), doc(70100, doc_type=3), doc(50400, doc_type=1, total="-4000"),
            doc(63109, total="4000"), doc(62800, total="3347.46"), doc(62502), doc(50401, doc_type=1),
            doc(62990, day="2025-12-28"), doc(62700, missing=True), doc(62510, total="10000"),
            doc(62503, day="2026-11-03"), doc(62504, day="2026-11-05"), doc(62505, day="2026-11-03"),
            doc(62506, day="2026-11-05"), doc(62507)]
    rows = [pay(1, "62500"), pay(2, "10500"), pay(3, "62600"), pay(4, "70100"), pay(5, "50400"), pay(6, "63109"),
            pay(7, "63109"), pay(8, "62800", paid="3350"), pay(9, "62502", st="0"), pay(10, "50401", st="0"),
            pay(11, "62990"), pay(12, "62700"), pay(13, ""), pay(14, "62,5O1"), pay(15, "62510", paid="4000"),
            pay(16, "62510", paid="6000"), pay(17, "62501", paid=""), pay(18, "62503, 62504", paid="8000", st="0"),
            pay(19, "62505, 62506", paid="8000"), pay(20, "62507", paid="", st="0")]
    part, view, reading = run(rows, docs)
    buckets = a1.analyse(reading["student_charge"]["rows"], docs)["rows"]
    got = {int(i): [m["kind"] for m in ms] for i, ms in part["marks"].items()}
    assert got == {2: ["books_ref_invalid"], 3: ["books_ref_invalid"], 4: ["books_ref_invalid"], 5: ["books_ref_invalid"],
                   6: ["books_amount_differs"], 7: ["books_amount_differs"], 9: ["books_receipt_unpaid"],
                   18: ["books_receipt_unpaid"], 20: ["books_receipt_unpaid"]}
    idx = db.index_copy(docs)
    for item_id, entry in buckets.items():
        facts = db.receipt_unpaid_facts(entry, facts_row(view, reading, item_id), idx)
        want = db.MARK_OF_BUCKET.get(entry["bucket"]) or ("books_receipt_unpaid" if facts else None)
        assert got.get(int(item_id), [None]) == [want], (item_id, entry["bucket"])
    assert part["buckets"] == {i: e["bucket"] for i, e in buckets.items()}
    assert (buckets["18"]["bucket"], buckets["19"]["bucket"], buckets["20"]["bucket"]) == (
        "dates_differ", "dates_differ", "amount_unreadable")
    # the buckets this set reaches, so the test is not vacuous
    seen = {e["bucket"] for e in buckets.values()}
    assert {"ref_not_in_books", "ref_ambiguous", "ref_not_an_invoice", "amount_mismatch", "rounding_band",
            "status_not_money_in", "invoice_needs_receipt", "out_of_window", "document_missing", "no_ref",
            "ref_unreadable", "filled_same", "amount_unreadable", "dates_differ"} <= seen


def test_books_ref_invalid_names_each_number_and_why():
    """FR-C1, FR-3, FR-4: not found, held by two valid documents, or not an invoice (its type); the chip says each in a
    word, the row's details in a sentence. A draft's number is not a valid document (FR-2), here inside its series: it is
    said as a draft ("טיוטה"), since the copy does hold the number."""
    docs = [doc(62600), doc(62600), doc(70100, doc_type=3), doc(80500, doc_type=31), doc(40505, doc_type=78),
            doc(50400, doc_type=1, total="-4000"), doc(62610, status=0), doc(62611)]
    rows = [pay(1, "40000"), pay(2, "62600"), pay(3, "70100"), pay(4, "80500, 40505"), pay(5, "50400"), pay(6, "62610")]
    part, _v, _r = run(rows, docs)
    head = "מספר החשבונית שבשורה לא נמצא בחשבשבת כמסמך תקף: "
    got = {int(i) % 100: ms[0] for i, ms in part["marks"].items()}
    assert {n: m["chip"] for n, m in got.items()} == {
        1: head + "40000 לא נמצא", 2: head + "62600 בשני מסמכים", 3: head + "70100 חשבונית זיכוי",
        4: head + "80500 קבלה; 40505 חשבון עסקה", 5: head + "50400 חשבונית מס בסכום שלילי", 6: head + "62610 טיוטה"}
    assert part["notes"] == {}
    assert got[2]["text"] == "מספר החשבונית שבשורה לא נמצא בחשבשבת כמסמך תקף: 62600: שני מסמכים תקפים בחשבשבת נושאים את המספר הזה."
    assert "70100: חשבונית זיכוי, ולא חשבונית מס או חשבונית מס/קבלה" in got[3]["text"]
    assert "50400: חשבונית מס בסכום שלילי, כלומר ביטול" in got[5]["text"]
    assert got[6]["text"] == head + "62610: בחשבשבת יש במספר הזה טיוטה, ולא מסמך תקף."
    assert got[4]["documents"] == ["80500", "40505"]


def test_a_negative_invoice_receipt_is_said_as_a_negative_sum_not_as_another_type():
    """The review of dfa967b, finding 8 (tests/test_review_d3_ref_reason.py, ported): A1 refuses an invoice-receipt whose
    total is negative as `ref_not_an_invoice`, exactly as a negative tax invoice, and its reason named the negative case
    for tax invoices only - so the details read "62500: חשבונית מס/קבלה, ולא חשבונית מס או חשבונית מס/קבלה", a sentence
    that contradicts itself (in the Excel file too), and the chip gave no reason. A negative sum is said as one; a credit
    note's reason stays its type, whatever its sign."""
    part, _v, _r = run([pay(1, "62500"), pay(2, "70100")],
                       [doc(62500, total="-4000"), doc(70100, doc_type=3, total="-500")])
    (mark,) = part["marks"]["1"]
    assert mark["kind"] == "books_ref_invalid"
    assert "ולא חשבונית מס או חשבונית מס/קבלה" not in mark["text"], mark["text"]
    assert mark["chip"].endswith(": 62500 חשבונית מס/קבלה בסכום שלילי")
    assert mark["text"].endswith(": 62500: חשבונית מס/קבלה בסכום שלילי.")
    assert part["marks"]["2"][0]["chip"].endswith(": 70100 חשבונית זיכוי")


def test_a_number_just_past_the_end_of_its_series_is_not_yet_in_the_copy_and_gets_no_mark():
    """The copy is read every hour: a document issued and cited on monday since is just past the last number the copy holds
    in its series (status 1, by the rule of the copy's `series()`), and is no gap yet - no `books_ref_invalid` for it, and
    the row's details say "עוד לא בהעתק של הספרים". "Just past": at most NOT_YET_SPAN (20) past the last number of the series
    nearest below it - both sides of each end: the last plus one and plus 20 are not yet in the copy, plus 21 is not found,
    and so is a number not held at or below a series' last, or below every series. Another refused number of the row keeps
    its mark."""
    docs = [doc(62500), doc(62501), doc(62503), doc(50400, doc_type=1), doc(70100, doc_type=3)]
    rows = [pay(1, "62504"), pay(2, "62502"), pay(3, "50401"), pay(4, "40000"), pay(5, "62504, 70100"),
            pay(6, "62503, 62505"), pay(7, "50400, 62504"), pay(8, "62523"), pay(9, "62524"), pay(10, "50420"),
            pay(11, "50421")]
    part, view, _r = run(rows, docs)
    assert db.series_ends(docs) == [(50400, 50400), (62500, 62503)] and db.NOT_YET_SPAN == 20
    head = "מספר החשבונית שבשורה לא נמצא בחשבשבת כמסמך תקף: "
    assert {i: ms[0]["chip"] for i, ms in part["marks"].items()} == {
        "2": head + "62502 לא נמצא", "4": head + "40000 לא נמצא", "5": head + "70100 חשבונית זיכוי",
        "9": head + "62524 לא נמצא", "11": head + "50421 לא נמצא"}
    assert part["notes"] == {"1": "62504: עוד לא בהעתק של הספרים", "3": "50401: עוד לא בהעתק של הספרים",
                             "5": "62504: עוד לא בהעתק של הספרים", "6": "62505: עוד לא בהעתק של הספרים",
                             "7": "62504: עוד לא בהעתק של הספרים", "8": "62523: עוד לא בהעתק של הספרים",
                             "10": "50420: עוד לא בהעתק של הספרים"}
    assert part["marks"]["5"][0]["documents"] == ["70100"] and "62504" not in part["marks"]["5"][0]["text"]
    # the note is in the row's details only: no flag, no chip, and no place in "לטיפול"
    merged = db.merge(view, dict(part, state="ok", reasons=[], as_of=None, key="k"))
    row = {r["id"]: r for r in merged["b2c"]["rows"]}["1"]
    assert row["books_note"] == "62504: עוד לא בהעתק של הספרים" and "books" not in row and "books_task" not in row
    assert not any(f.startswith("books_") for f in row["flags"])
    ends = db.series_ends(docs)
    assert db.not_yet_read("62504", ends) and db.not_yet_read("62523", ends) and not db.not_yet_read("62524", ends)
    assert not db.not_yet_read("62503", ends) and not db.not_yet_read("40000", ends) and not db.not_yet_read("62504", [])


# The review of dfa967b, finding 1 (tests/test_review_d3_not_yet_read.py, ported): with no upper bound, every wrong number
# above the lowest series was "not yet in the copy" - never marked, and its details said so at every later tick. On a whole
# copy (no holes: the only copy the desk computes on), a number no valid document holds is outside every series.
NOT_YET_DOCS = [doc(50400, doc_type=1), doc(50401, doc_type=1), doc(50402, doc_type=1),
                doc(62500), doc(62501), doc(62502), doc(62503), doc(70100, doc_type=3), doc(70101, doc_type=3)]


def test_a_typo_far_above_the_top_series_is_marked_not_found():
    """99999 is 37,496 past the last invoice-receipt: no document issued in the hour since the last tick."""
    assert not_ready(copy(NOT_YET_DOCS)) is None
    part, _v, _r = run([pay(1, "99999")], NOT_YET_DOCS)
    assert [m["kind"] for m in part["marks"].get("1", [])] == ["books_ref_invalid"], (part["marks"], part["notes"])
    assert "1" not in part["notes"]


def test_a_number_thousands_past_its_series_is_marked_not_found():
    """67503 is 5,000 past the last invoice-receipt (62503): a mistyped digit, not a document of the last hour."""
    part, _v, _r = run([pay(1, "67503")], NOT_YET_DOCS)
    assert [m["kind"] for m in part["marks"].get("1", [])] == ["books_ref_invalid"], (part["marks"], part["notes"])


def test_a_number_between_two_series_is_marked_not_found():
    """52894 (a typo of 62894) sits between the tax invoices (last 50402) and the invoice-receipts (first 62500): 2,492
    past the invoices' last. It is no tax invoice issued in the last hour."""
    part, _v, _r = run([pay(1, "52894")], NOT_YET_DOCS)
    assert [m["kind"] for m in part["marks"].get("1", [])] == ["books_ref_invalid"], (part["marks"], part["notes"])


def test_the_details_never_say_not_yet_in_the_copy_of_an_absurd_number():
    part, _v, _r = run([pay(1, "99999"), pay(2, "52894")], NOT_YET_DOCS)
    assert part["notes"] == {}, part["notes"]
    assert not db.not_yet_read("99999", db.series_ends(NOT_YET_DOCS))


def test_a_number_the_copy_holds_as_a_draft_is_a_draft_not_missing_from_the_copy():
    """Spec §4.1: a draft carries its series' next number. A row citing a draft cites no valid document (FR-2: the mark),
    and the copy does hold the number: it is said as a draft, never "עוד לא בהעתק של הספרים"."""
    docs = NOT_YET_DOCS + [doc(62504, status=0)]                             # a draft, the next number of its series
    assert not_ready(copy(docs)) is None
    part, _v, _r = run([pay(1, "62504")], docs)
    assert "1" not in part["notes"], part["notes"]
    assert [m["kind"] for m in part["marks"].get("1", [])] == ["books_ref_invalid"], part["marks"]
    assert part["marks"]["1"][0]["chip"].endswith("62504 טיוטה")


def test_amount_differs_says_the_groups_two_sums_and_the_rounding_band_is_no_mark():
    """FR-C1 and the contract §0.3: past 1 ₪ a document and 0.1% is a mark, "בשורה X, בחשבשבת Y" (students with VAT); one
    document over two rows sums its rows (63109: 4,000 cited by two 4,000 rows), said inside the legend's one term - the
    row's own sum, and in brackets the rows' together; Haemek's 3,350 against 3,347.46 is the rounding band, shown to a
    person by A1 and no mark here."""
    docs = [doc(62700, total="3000"), doc(63109, total="4000"), doc(62800, total="3347.46")]
    rows = [pay(1, "62700", paid="4000"), pay(2, "63109"), pay(3, "63109"), pay(4, "62800", paid="3350")]
    part, _v, _r = run(rows, docs)
    by = {int(i): v for i, v in marks(part).items()}
    assert by[1] == [("books_amount_differs", "בשורה 4,000.00 ₪, בחשבשבת 3,000.00 ₪")]
    assert by[2] == by[3] == [("books_amount_differs", "בשורה 4,000.00 ₪ (ב-2 השורות 8,000.00 ₪), בחשבשבת 4,000.00 ₪")]
    assert 4 not in by
    text = part["marks"]["2"][0]["text"]
    assert "8,000.00 ₪" in text and "4,000.00 ₪" in text and "חשבונית מס/קבלה 63109" in text and "בשורה הזאת 4,000.00 ₪" in text


def test_an_invoice_receipt_on_a_row_whose_status_says_no_money_came_is_marked_the_case_of_23_11():
    """FR-L12: an invoice-receipt pays itself. On "לא שולם", "יש בעיה" or an empty status the books contradict the row:
    "בחשבשבת חשבונית מס/קבלה N, והסטטוס {status}". Not on "מלגת אנליזה" (a scholarship: nothing is due) nor on a label the
    desk does not know (noise, the review of dfa967b). A tax invoice alone is a demand, so "לא שולם" beside it is no
    contradiction (A1: invoice_needs_receipt), and no mark."""
    docs = [doc(62502), doc(62503), doc(62504), doc(50401, doc_type=1), doc(62505), doc(62506)]
    rows = [pay(1, "62502", st="0"), pay(2, "62503", st="2"), pay(3, "62504", st="3"), pay(4, "50401", st="0"),
            pay(5, "62505", st="7"), pay(6, "62506", st="")]
    part, _v, _r = run(rows, docs)
    by = {int(i): v for i, v in marks(part).items()}
    assert by == {1: [("books_receipt_unpaid", 'בחשבשבת חשבונית מס/קבלה 62502, והסטטוס "לא שולם"')],
                  2: [("books_receipt_unpaid", 'בחשבשבת חשבונית מס/קבלה 62503, והסטטוס "יש בעיה"')],
                  6: [("books_receipt_unpaid", "בחשבשבת חשבונית מס/קבלה 62506, והסטטוס ריק")]}
    assert part["marks"]["1"][0]["text"] == ('בחשבשבת חשבונית מס/קבלה 62502 מ-03.11.2026 על 4,000.00 ₪ כולל מע"מ, כלומר הכסף '
                                             'התקבל, וסטטוס התשלום בשורה "לא שולם".')


def test_the_case_of_23_11_is_read_off_the_group_whatever_a1s_order_of_buckets():
    """`books_receipt_unpaid` does not depend on A1's order of buckets: A1 judges a group's days (`dates_differ`) before a
    row's status, so a "לא שולם" row citing two invoice-receipts of different days had no mark. The mark is read off the
    row itself: an unpaid status, every number a valid invoice-receipt, and its group's two sums within the tolerance (or
    its amount empty). A1's buckets do not change; a group out of the window (FR-7), one with a tax invoice, one in the
    rounding band and one whose sums differ are no such case."""
    docs = [doc(62502, day="2026-11-03"), doc(62503, day="2026-11-05"), doc(62504, day="2026-11-03"),
            doc(62505, day="2026-11-06"), doc(62506, day="2026-11-03"), doc(62507, day="2026-11-04"),
            doc(62508, total="3000", day="2026-11-03"), doc(62509, day="2026-11-08"), doc(50401, doc_type=1),
            doc(62510, day="2026-11-03"), doc(62511, day="2025-12-30")]
    rows = [pay(1, "62502, 62503", paid="8000", st="0"),   # "לא שולם", two days: the case - marked
            pay(2, "62504, 62505", paid="8000", st="1"),   # "שולם", the same shape: the money came in, no mark
            pay(3, "62506, 62507", paid="8100", st="0"),   # the sums differ: the amount's mark, not this one
            pay(4, "62508, 62509", paid="7005", st="0"),   # 5 past 7,000 over two documents: the rounding band, no mark
            pay(5, "50401, 62510", paid="8000", st="0"),   # a tax invoice in it: it waits for its receipt, no mark
            pay(6, "62511", st="2")]                       # out of the window (FR-7): never a gap
    part, _v, reading = run(rows, docs)
    buckets = {int(i): e["bucket"] for i, e in a1.analyse(reading["student_charge"]["rows"], docs)["rows"].items()}
    assert buckets == {1: "dates_differ", 2: "dates_differ", 3: "amount_mismatch", 4: "rounding_band",
                       5: "invoice_needs_receipt", 6: "out_of_window"}
    assert {int(i): [(m["kind"], m["chip"]) for m in ms] for i, ms in part["marks"].items()} == {
        1: [("books_receipt_unpaid", 'בחשבשבת חשבונית מס/קבלה 62502, 62503, והסטטוס "לא שולם"')],
        3: [("books_amount_differs", "בשורה 8,100.00 ₪, בחשבשבת 8,000.00 ₪")]}
    assert part["marks"]["1"][0]["text"] == ('בחשבשבת חשבונית מס/קבלה 62502, 62503 מ-03.11.2026, 05.11.2026 על 8,000.00 ₪ '
                                             'כולל מע"מ, כלומר הכסף התקבל, וסטטוס התשלום בשורה "לא שולם".')
    assert part["marks"]["1"][0]["documents"] == ["62502", "62503"]


def test_the_receipts_mark_reads_its_facts_off_the_row_whatever_its_bucket():
    """The facts are the row's (its status, the numbers it cites, its amount) and its group's sums, never A1's bucket or
    its order. Each fact is broken on its own on the same row: the statuses, an amount that is empty or within the
    tolerance, a tax invoice, a document outside the window (FR-7) or answered "not found", a number A1 refused."""
    docs = [doc(62502, day="2026-11-03"), doc(62503, day="2026-11-05"), doc(50401, doc_type=1),
            doc(62511, day="2025-12-30"), doc(62512, missing=True)]
    idx = db.index_copy(docs)

    def entry(numbers=("62502", "62503"), paid="8000", total="8000", group=True, refused=()):
        return {"bucket": "dates_differ" if group else "amount_unreadable", "documents": list(numbers),
                "refused": list(refused), "group": {"rows": ["1"], "documents": list(numbers), "paid": Decimal(paid),
                                                    "total": Decimal(total)} if group else None}

    def facts(e, **row):
        return db.receipt_unpaid_facts(e, dict({"status_idx": "0", "status": "לא שולם", "paid_raw": "8000"}, **row), idx)

    both = ["62502", "62503"]
    assert facts(entry()) == both
    for st, label, want in (("2", "יש בעיה", both), ("", "", both), ("3", "מלגת אנליזה", []), ("1", "שולם", []),
                            ("9", "ביקש החזר", []), ("7", "תווית חדשה", []), ("", "תווית בלי אינדקס", [])):
        assert facts(entry(), status_idx=st, status=label) == want, (st, label)
    # the amount: empty needs no group (A1's amount_unreadable); typed, its group's sums within 1 ₪ a document
    assert facts(entry(group=False), paid_raw="") == both and facts(entry(group=False), paid_raw=None) == both
    assert facts(entry(group=False), paid_raw="\u200f") == both              # an invisible mark alone is empty, as A1 reads
    assert facts(entry(group=False), paid_raw="8000") == []                  # typed, and no group to compare it with
    assert facts(entry(paid="8002")) == both and facts(entry(paid="8003")) == []
    # the documents: each a valid invoice-receipt of the window, never answered "not found"
    assert facts(entry(numbers=("50401", "62502"))) == []
    assert facts(entry(numbers=("62511",), paid="4000", total="4000")) == []
    assert facts(entry(numbers=("62512",), paid="4000", total="4000")) == []
    assert facts(entry(numbers=("62502", "62513"), refused=[("62513", "ref_not_in_books")])) == []
    assert facts(entry(numbers=())) == []


def test_an_unpaid_row_with_no_amount_citing_an_invoice_receipt_is_marked():
    """The review of dfa967b, finding 2 (tests/test_review_d3_receipt_unpaid_no_amount.py, ported): the desk move plan
    §1a.2.1 defines the mark with no amount in it, and an unpaid row's "סכום ששולם" is usually empty - A1's
    `amount_unreadable`, which has no group. It is marked, and so the contradiction of 23.11 is shown on the row (its
    document is cited, so it is on no list)."""
    part, _v, _r = run([pay(1, "62502", st="0", paid="", receipt="0")], [doc(62502), doc(62503)])
    assert part["buckets"]["1"] == "amount_unreadable"                     # A1's bucket, unchanged by the desk
    assert [m["kind"] for m in part["marks"].get("1", [])] == ["books_receipt_unpaid"], part["marks"]
    assert part["marks"]["1"][0]["chip"] == 'בחשבשבת חשבונית מס/קבלה 62502, והסטטוס "לא שולם"'


def test_and_its_document_is_shown_somewhere():
    part, _v, _r = run([pay(1, "62502", st="0", paid="", receipt="0")], [doc(62502), doc(62503)])
    shown_on_row = bool(part["marks"].get("1"))
    shown_in_list = any(e["number"] == "62502" for e in part["students"])
    assert shown_on_row or shown_in_list


def test_a_mark_moves_no_row_changes_no_sum_and_joins_tasks_only_from_the_era():
    """spec §6.1.1 and §6.4.2 (ADR-0168 §5): a marked row stays in its state and no state's count or sum changes; a marked
    row dated from 25.10.2026 on is in "לטיפול" too, and the period's count of "לטיפול" counts it; an older marked row keeps
    its mark and does not join. The date is the desk's: "תאריך תקבול", else the day the row was made."""
    docs = [doc(62502), doc(62503), doc(62504, day="2026-10-01")]
    old = sc(3, st="0", receipt="0", invoice="62504", lead=["L3"], created="2026-10-01T10:00:00Z")   # made 1.10, no date
    new_made = sc(4, st="0", receipt="0", invoice="62503", lead=["L4"], created="2026-11-02T10:00:00Z")  # made 2.11
    rows = [pay(1, "62502", st="0", on="2026-10-25", receipt="0"), old, new_made, pay(5, "", st="1", receipt="0")]
    part, view, _r = run(rows, docs)
    assert set(part["marks"]) == {"1", "3", "4"}
    merged = db.merge(view, dict(part, state="ok", reasons=[], as_of=None, key="k"))
    before = {r["id"]: r for r in view["b2c"]["rows"]}
    after = {r["id"]: r for r in merged["b2c"]["rows"]}
    assert {i: r["state"] for i, r in after.items()} == {i: r["state"] for i, r in before.items()}
    assert [p["states"] for p in merged["b2c"]["periods"]] == [p["states"] for p in view["b2c"]["periods"]]
    assert (after["1"]["books_task"], after["3"]["books_task"], after["4"]["books_task"]) == (True, False, True)
    assert "books_task" not in after["5"] and "books" not in after["5"] and after["5"] is before["5"]
    assert after["1"]["flags"] == before["1"]["flags"] + ["books_receipt_unpaid"]
    tasks = [r["id"] for r in fd.filter_b2c(merged["b2c"], "tasks", "", "")]
    assert sorted(tasks) == ["1", "4", "5"]                       # 5 is "שולם, שליחת קבלה: טרם"; 3 is older
    assert [r["id"] for r in fd.filter_b2c(view["b2c"], "tasks", "", "")] == ["5"]
    whole = merged["b2c"]["periods"][0]
    assert whole["tasks"] == 3 and sum(whole["states"][k]["count"] for k in fd.TASK_STATES) == 1
    assert {p["key"]: p["tasks"] for p in merged["b2c"]["periods"]}["2026-10"] == 1   # 1 (25.10); 3 is older, unmarked there
    # the Excel file's "מה לבדוק" says the mark in its own words, as the page's column does
    assert fd.row_reasons("b2c", after["1"])[0] == 'בחשבשבת חשבונית מס/קבלה 62502, והסטטוס "לא שולם"'


def test_a_marked_row_joins_tasks_and_stays_in_its_own_state_only():
    """V02 of the review's mutants: a row the books mark joins "לטיפול" (`books_task`), and in every other list it is
    where its own state puts it - never in another state's list."""
    part, view, _r = run([pay(1, "62502", st="0", receipt="0"), pay(2, "", st="1", receipt="0")], [doc(62502)])
    merged = db.merge(view, dict(part, state="ok", reasons=[], as_of=None, key="k"))
    marked = {r["id"]: r for r in merged["b2c"]["rows"]}["1"]
    assert marked["books_task"] is True
    assert "1" in [r["id"] for r in fd.filter_b2c(merged["b2c"], "tasks", "", "")]
    for state in fd.B2C_STATES:
        ids = [r["id"] for r in fd.filter_b2c(merged["b2c"], state, "", "")]
        assert ("1" in ids) == (state == marked["state"]), state


def test_a_card_is_a_companys_when_an_account_names_it_else_a_students_by_its_invoice_receipt_fr_6():
    """FR-6 until B3's sort codes (the plan §1a.2.3): an Account's "מפתח חשבשבת" is a company's card, whatever it holds; a card
    holding an invoice-receipt and no proforma is a student's (students are sent no proforma); an invoice-receipt beside a
    proforma, or only tax invoices, is unclear. A draft is no evidence; the keys are compared trimmed and case folded."""
    docs = [doc(62500, card="30001"), doc(62501, card="20001"), doc(62502, card="20002"), doc(40505, doc_type=78, card="20002"),
            doc(50400, doc_type=1, card="20003"), doc(62503, card="20004", status=0), doc(50401, doc_type=1, card="20004"),
            doc(62504, card="ab-7")]
    sides = db.card_sides(docs, db.account_keys_of([{"name": "חברה בדויה", fd.AC["hash_key"]: " 30001 "},
                                                   {"name": "חברה אחרת", fd.AC["hash_key"]: "AB-7"}]))
    assert sides == {"30001": ("company", "account_key"), "20001": ("student", "invoice_receipt"),
                     "20002": ("unclear", "invoice_receipt_and_proforma"), "20003": ("unclear", "no_invoice_receipt"),
                     "20004": ("unclear", "no_invoice_receipt"), "ab-7": ("company", "account_key")}
    # a cell that names two cards names both (MON-43: it should not; the desk does not lose one that does)
    assert db.account_keys_of([{"name": "משרד", fd.AC["hash_key"]: "10091, 10092"}]) == {"10091": ["משרד"],
                                                                                        "10092": ["משרד"]}


def test_a_masked_or_empty_card_key_is_no_card_and_its_side_is_judged_by_the_cards_name():
    """The copy cleans a card's key like any text (app/books/parse.py "key"), so a key of six digits or more - a student's
    card keyed by an ID number, a company's by its ח.פ - is stored "#", and an empty key is "": pooled by key they would be
    two cards of many people's documents, and one proforma on any of them would make every invoice-receipt there unclear.
    A key that is masked or empty names no card: the side is judged by the card's name (NFKC, case folded, spaces
    collapsed), the same on every document of a card - so a proforma beside invoice-receipts of the same name makes them
    unclear, and a name with an invoice-receipt and no proforma is a student's. A document with neither a key nor a name is
    judged alone by its own type. Never an Account's, even if a key the desk read from monday was masked to "#" too."""
    docs = [doc(62406, card="#", name="דנה לוי"), doc(62407, card="#", name="משה כהן"),
            doc(40505, doc_type=78, card="#", name="דנה  לוי"),                 # a proforma of the same name: 62406's card
            doc(62408, card="", name="רות אבן"), doc(62409, card=None, name="יעל גל"),
            doc(40506, doc_type=78, card="", name="נועה בר"),                   # a proforma with no key, alone on its name
            doc(50400, doc_type=1, card="AB-#", name="כרטיס מוסתר"),           # a tax invoice, its name holding no receipt
            doc(50401, doc_type=1, card=None, name=""),                         # neither a key nor a name: alone
            doc(62410, card=None, name=None)]                                   # alone: an invoice-receipt, a student's
    keys = {"#": ["חברה שהמפתח שלה הוסתר"], db.NAME_CARD + "משה כהן": ["חברה בשם"]}   # neither ever matches
    part, _v, _r = run([], docs, keys=keys)
    assert sorted(e["number"] for e in part["students"]) == ["62407", "62408", "62409", "62410"]
    assert [(e["number"], e["why"]) for e in sorted(part["unclear"], key=lambda e: e["number"])] == [
        ("50400", "name_no_invoice_receipt"), ("50401", "key_empty"), ("62406", "name_invoice_receipt_and_proforma")]
    assert part["companies"] == [] and part["counts"] == {"students": 4, "unclear": 3, "outside": 2}
    assert all(e["why_text"] == db.UNCLEAR_WHY[e["why"]] for e in part["unclear"])
    assert db.card_sides(docs, keys) == {
        db.NAME_CARD + "דנה לוי": ("unclear", "name_invoice_receipt_and_proforma"),
        db.NAME_CARD + "משה כהן": ("student", "invoice_receipt"),
        db.NAME_CARD + "רות אבן": ("student", "invoice_receipt"), db.NAME_CARD + "יעל גל": ("student", "invoice_receipt"),
        db.NAME_CARD + "נועה בר": ("unclear", "name_no_invoice_receipt"),
        db.NAME_CARD + "כרטיס מוסתר": ("unclear", "name_no_invoice_receipt")}
    assert db.norm_name(" ＤＡＮＡ\u00a0 Levi ") == "dana levi" == db.norm_name("dana levi")
    # a key the desk read from monday and masked (`mask_ids`) names no Account's card either
    assert db.account_keys_of([{"name": "חברה", fd.AC["hash_key"]: "#"}, {"name": "חברה אחרת", fd.AC["hash_key"]: "10092"}]) \
        == {"10092": ["חברה אחרת"]}
    # beside them, a real card keeps its rule, by its key whatever its name: its invoice-receipt with a proforma on the same
    # card is unclear, and a masked card of the same name is not joined to it
    mixed = docs + [doc(62411, card="20005", name="משה כהן"), doc(40507, doc_type=78, card="20005", name="משה כהן")]
    part, _v, _r = run([], mixed, keys=keys)
    assert ("62411", "invoice_receipt_and_proforma") in [(e["number"], e["why"]) for e in part["unclear"]]
    assert sorted(e["number"] for e in part["students"]) == ["62407", "62408", "62409", "62410"]


def test_an_invoice_receipt_beside_a_proforma_on_a_masked_card_is_not_a_students():
    """The review of dfa967b, finding 3 (tests/test_review_d3_masked_card.py, ported): FR-C4 and FR-6 - a type 2 never makes
    a card a student's when anything marks it B2B. The case that wrote the rule is a company card carrying proforma 40522
    whose invoice-receipts 63149 and 63186 were counted as students (18.11). On a card keyed by its ח.פ, the proforma and
    the invoice-receipts all read "#": judged alone, the invoice-receipts went to the students' list, shown with the
    company's card name to a member of Student Charge only. The card's name ties them (spec §3.4)."""
    name = "שירות תעסוקה בדוי"
    docs = [doc(63149, card="#", name=name, total="52500"), doc(63186, card="#", name=name, total="52500"),
            doc(40522, doc_type=78, card="#", name=name, total="42000")]
    part, _v, _r = run([], docs)
    assert [e["number"] for e in part["students"]] == [], [(e["number"], e["card"]) for e in part["students"]]
    assert sorted((e["number"], e["why"]) for e in part["unclear"]) == [
        ("63149", "name_invoice_receipt_and_proforma"), ("63186", "name_invoice_receipt_and_proforma")]


def test_type2_on_b2b_card_is_not_a_student_without_a_row():
    """FR-C4 (the employment service's 63149 and 63186, counted as students on 18.11; its card carries proforma 40522): an
    invoice-receipt on a company's card, or beside a proforma, is never on the students' list."""
    docs = [doc(63149, card="30002", name="שירות בדוי", total="52500"), doc(63186, card="30002", total="52500"),
            doc(40522, doc_type=78, card="30003"), doc(63150, card="30003", name="שירות שני")]
    part, _v, _r = run([], docs, keys={"30002": ["שירות בדוי"]})
    assert part["students"] == []
    assert [e["number"] for e in part["companies"]] == ["63186", "63149"]
    assert [(e["number"], e["why"]) for e in part["unclear"]] == [("63150", "invoice_receipt_and_proforma")]


def test_voucher_card_counted_apart():
    """FR-D1: card 10091 (Defence's vouchers) is counted apart, one line with its count, never listed - whether or not an
    Account names it."""
    docs = [doc(50500, doc_type=1, card="10091"), doc(50501, doc_type=1, card="10091"), doc(50502, doc_type=1, card="30001")]
    for keys in ({"30001": ["משרד בדוי"]}, {"30001": ["משרד בדוי"], "10091": ["משרד בדוי"]}):
        part, view, _r = run([], docs, keys=keys)
        assert part["vouchers"] == 2 and [e["number"] for e in part["companies"]] == ["50502"]
        merged = db.merge(view, dict(part, state="ok", reasons=[], as_of=None, key="k"))
        assert merged["b2b"]["books"]["vouchers_said"].startswith("בכרטיס 10091 בחשבשבת")
        assert "2 מסמכים" in merged["b2b"]["books"]["vouchers_said"]


def test_every_document_lands_in_exactly_one_place_and_each_list_holds_its_place():
    """spec §6.3.1: every held document lands in one place of PLACES - outside (status, type, a cancellation, before the
    era, a student's outside 2026), cited by a charge row of either board (an unreadable cell holds what it may name), not
    found, the vouchers', or a list. The lists hold exactly their places' documents, so nothing is shown twice or lost."""
    docs = [
        doc(62400, day="2026-10-24"),                       # before the era
        doc(62401, status=0),                               # a draft
        doc(70100, doc_type=3),                             # a credit
        doc(50400, doc_type=1, total="-500", card="30001"),  # a cancellation (FR-4)
        doc(62402),                                         # cited by a Student Charge row
        doc(62403, card="30001"),                           # cited by a B2B Charge row
        doc(62404),                                         # cited by an unreadable cell
        doc(62405, missing=True),                           # answered "not found"
        doc(50401, doc_type=1, card="10091"),               # the vouchers'
        doc(62406),                                         # a student's, uncited
        doc(62407, day="2027-01-03"),                       # a student's of 2027: outside Student Charge's window
        doc(50402, doc_type=1, card="30001", day="2027-01-03"),  # a company's of 2027: listed (B2B has no year's end)
        doc(50403, doc_type=1, card="20009"),               # unclear: only tax invoices
        doc(62408, card="20010"), doc(40510, doc_type=78, card="20010", day="2026-11-01"),  # unclear: with a proforma
    ]
    rows = [pay(1, "62402"), pay(2, "62,404")]
    b2_rows = [b2(1, invoice="62403")]
    part, _v, _r = run(rows, docs, b2_rows, keys={"30001": ["חברה בדויה"]})
    places = part["places"]
    assert set(places) == {d["stock_id"] for d in docs} and set(places.values()) <= set(db.PLACES)
    assert sum(part["counts"].values()) == len(docs)
    assert {k: sorted(s for s, p in places.items() if p == k) for k in db.PLACES if k in part["counts"]} == {
        "outside": [40510, 50400, 62400, 62401, 62407, 70100], "cited": [62402, 62403, 62404], "missing": [62405],
        "vouchers": [50401], "students": [62406], "companies": [50402], "unclear": [50403, 62408]}
    for k in ("students", "companies", "unclear"):
        assert sorted(int(e["number"]) for e in part[k]) == sorted(s for s, p in places.items() if p == k), k
    assert part["vouchers"] == part["counts"]["vouchers"] == 1


def test_a_document_of_the_eras_first_day_is_in_the_era_and_every_number_a_cell_cites_is_cited():
    """B03 and C07 of the review's mutants: a document dated 25.10.2026 itself is of the era (ADR-0168 §5: from that day
    on), the day before is not; and every number of a cell that cites several - on either charge board - is cited, so
    none of them is listed as a document no charge cites."""
    part, _v, _r = run([], [doc(62406, day="2026-10-25"), doc(62407, day="2026-10-24")])
    assert [e["number"] for e in part["students"]] == ["62406"] and part["places"][62407] == "outside"
    docs = [doc(62502), doc(62503), doc(50500, doc_type=1, card="30001"), doc(50501, doc_type=1, card="30001")]
    part, _v, _r = run([pay(1, "62502, 62503", paid="8000")], docs, [b2(1, invoice="50500, 50501")],
                       keys={"30001": ["חברה בדויה"]})
    assert part["students"] == [] and part["companies"] == [] and part["counts"] == {"cited": 4}
    assert db.cited_numbers([{a1.REF_COLUMN: "62502, 62503"}], a1.REF_COLUMN) == {"62502", "62503"}


def test_a_name_is_only_ever_a_candidate():
    """The law of the rules' §5 (trap 9): a document's card named like Student Charge rows shows them as candidates - equal
    as a set of words of two letters or more, no digits, any order - and nothing more: the document stays on the list, no
    row is marked, linked or filled by it, and a similar name is no candidate (FR-C2's similar name needs an eye)."""
    docs = [doc(62406, name="לוי דנה"), doc(62407, name="דנה  לוי-כהן"), doc(62408, name="משה # כהן 2")]
    rows = [pay(1, "", name="דנה לוי"), pay(2, "", st="0", name="Dana Levi"), pay(3, "", name="דנה לוי"),
            pay(4, "", name="דנה לוי כהן"), pay(5, "", name="כהן משה")]
    part, view, _r = run(rows, docs)
    listed = {e["number"]: e for e in part["students"]}
    assert set(listed) == {"62406", "62407", "62408"}                       # never a match: every document stays
    assert [c["id"] for c in listed["62406"]["candidates"]] == ["3", "1"]       # in the list's order, newest first
    assert [c["id"] for c in listed["62407"]["candidates"]] == ["4"]         # "לוי-כהן" is two words
    assert [c["id"] for c in listed["62408"]["candidates"]] == ["5"]         # "#" and "2" are no words
    # (QA 26.11, 9: and why the books propose nothing here - FR-C2's bucket for the row: two rows of the name and amount)
    assert listed["62406"]["candidates"][1] == {"id": "1", "name": "דנה לוי", "state": "done", "status": "שולם",
                                                "date": "2026-11-03", "invoice": "",
                                                "why": db.CANDIDATE_WHY["document_wanted_twice"],
                                                "why_key": "document_wanted_twice"}
    assert part["marks"] == {}                                               # no row is marked by a name
    assert db.name_words("דנה לוי") == db.name_words("לוי   דנה") != db.name_words("דנה לוין")
    assert db.name_words("a 12 b3 #") == frozenset()


def test_a_common_name_shows_ten_candidates_and_how_many_more():
    rows = [pay(i, "", name="דנה לוי") for i in range(1, 14)]
    part, _v, _r = run(rows, [doc(62406)])
    e = part["students"][0]
    assert len(e["candidates"]) == db.CANDIDATES_MAX == 10 and e["candidates_more"] == 3


# ---- only a whole copy on a whole reading changes what is shown (spec §6.1.5) ------------------------------------------------
import threading  # noqa: E402
import time  # noqa: E402

from app.books.store import BooksStoreError  # noqa: E402


class FakeCopy:
    """BooksStore's two reads of D3 over a snapshot the test changes: `runs` and `snapshots` count what was asked, `fail`
    makes both raise as a database that is down does (BooksStoreError with a SQLSTATE or a class)."""

    def __init__(self, snapshot: dict):
        self.snapshot, self.runs, self.snapshots, self.fail = snapshot, 0, 0, None

    def last_finished_run(self, company, connect_timeout_s=None):
        self.runs += 1
        if self.fail:
            raise BooksStoreError(self.fail)
        s = self.snapshot
        return (s["last_run_id"], s["last_run_finished_at"]) if s.get("last_run_id") is not None else None

    def desk_snapshot(self, company, connect_timeout_s=None):
        self.snapshots += 1
        if self.fail:
            raise BooksStoreError(self.fail)
        return self.snapshot


class FakeKeys:
    def __init__(self, keys):
        self.keys, self.asked = keys, 0

    def get(self, may_read=True):
        self.asked += 1
        return self.keys

    def health(self):
        return {"read_at": None, "error": None}


class Tick:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def holder(store, keys=None, clock=None, now=None) -> db.DeskBooks:
    b = db.DeskBooks(lambda _url: store, lambda: None, clock=clock or Tick(), now=now or (lambda: NOW))
    b.keys = FakeKeys({} if keys is None else keys)
    return b


def reading_view(rows, n: int = 1, whole: bool = True) -> tuple:
    reading = reading_of(snap(list(rows)), n)
    if not whole:
        reading["checks"]["student_charge"] = dict(reading["checks"]["student_charge"], ok=False)
    return reading, fd.build_view(reading, TODAY)


ROWS = [pay(1, "62502", st="0", receipt="0"), pay(2, "")]
DOCS = [doc(62502), doc(62503, card="20002")]   # numbered without a gap, as a series is: a gap is a hole


def test_off_without_a_database_or_on_demo_and_nothing_is_asked():
    store = FakeCopy(copy(DOCS))
    b = holder(store)
    reading, view = reading_view(ROWS)
    for cfg, why in ((SimpleNamespace(database_url="", company="analiza", db=ANALIZA), "not_configured"),
                     (SimpleNamespace(database_url="x", company="demo", db="wizdb2245n4"), "demo"),
                     (SimpleNamespace(database_url="x", company="other", db=None), "company")):
        part = b.answer(cfg, reading, view)
        assert (part["state"], part["reasons"], part["marks"], part["students"]) == ("off", [why], {}, [])
        assert db.public(part)["said"] == db.REASONS[why]
    assert store.runs == store.snapshots == 0 and b.keys.asked == 0


def test_a_whole_copy_on_a_whole_reading_is_computed_once_per_reading_and_per_run():
    store, tick = FakeCopy(copy(DOCS)), Tick()
    b = holder(store, clock=tick)
    reading, view = reading_view(ROWS)
    part = b.answer(CFG, reading, view)
    assert part["state"] == "ok" and part["reasons"] == [] and part["as_of"] == "2026-11-25T08:00:00+00:00"
    assert set(part["marks"]) == {"1"} and [e["number"] for e in part["students"]] == ["62503"]
    assert b.answer(CFG, reading, view) is part and (store.runs, store.snapshots) == (1, 1)   # asked a moment ago
    tick.t += db.RUN_CHECK_S + 1
    assert b.answer(CFG, reading, view) is part and (store.runs, store.snapshots) == (2, 1)   # the copy did not move
    store.snapshot = copy(DOCS + [doc(62504, card="20003")], last_run_id=8)
    tick.t += db.RUN_CHECK_S + 1
    moved = b.answer(CFG, reading, view)
    assert moved is not part and [e["number"] for e in moved["students"]] == ["62504", "62503"] and store.snapshots == 2
    other, view2 = reading_view(ROWS[1:], n=2)                                               # a new reading: computed
    again = b.answer(CFG, other, view2)
    assert store.snapshots == 3 and again["marks"] == {} and again["state"] == "ok"
    assert b.current() is again and b.health() == {
        "state": "ok", "as_of": "2026-11-25T08:00:00+00:00", "reasons": [], "computed_at": again["computed_at"],
        "seconds": b.trace["seconds"], "error": None, "accounts": {"read_at": None, "error": None}}


def test_a_copy_that_is_not_whole_shows_the_last_whole_result_with_its_time_and_why():
    """spec §6.1.5: the last whole part, `held`, with its copy's time and the reason (A1's closed codes); with none, `none`
    and no marks. When the copy is whole again, it is computed again."""
    store, tick = FakeCopy(copy(DOCS, ever_completed=False, last_run_complete=False)), Tick()
    b = holder(store, clock=tick)
    reading, view = reading_view(ROWS)
    none = b.answer(CFG, reading, view)
    assert (none["state"], none["reasons"], none["marks"], none["as_of"]) == ("none", ["never_completed"], {}, None)
    assert db.public(none)["said"] == db.REASONS["never_completed"] + " " + db.NONE_SAID
    store.snapshot = copy(DOCS, last_run_id=8)
    tick.t += db.RUN_CHECK_S + 1
    ok = b.answer(CFG, reading, view)
    assert ok["state"] == "ok"
    for reason, over in (("last_run_incomplete", {"last_run_complete": False}), ("last_run_not_ok", {"last_run_ok": False}),
                         ("retry_waiting", {"retry_waiting": 2}), ("holes", {"documents": DOCS + [doc(62605)]})):
        store.snapshot = copy(DOCS, last_run_id=store.snapshot["last_run_id"] + 1, **over)
        tick.t += db.RUN_CHECK_S + 1
        held = b.answer(CFG, reading, view)
        assert (held["state"], held["reasons"]) == ("held", [reason]), reason
        assert held["marks"] == ok["marks"] and held["students"] == ok["students"] and held["as_of"] == ok["as_of"]
        assert db.public(held)["said"] == db.REASONS[reason] + " " + db.HELD_SAID
    store.snapshot = copy(DOCS + [doc(62504, card="20003")], last_run_id=20,
                          last_run_finished_at=datetime(2026, 11, 25, 9, 0, tzinfo=timezone.utc))
    tick.t += db.RUN_CHECK_S + 1
    fresh = b.answer(CFG, reading, view)
    assert fresh["state"] == "ok" and fresh["as_of"] == "2026-11-25T09:00:00+00:00" and len(fresh["students"]) == 2


def test_a_whole_copy_older_than_three_hours_is_held_as_stale():
    """The review of dfa967b, 10(b): a copy whole by A1's rule whose last run finished more than COPY_STALE_S ago (a sync
    that stopped: the books are read every hour) is `held`, `copy_stale` - its marks and lists stay, said as not current,
    never posing as current - and /health says the reason. The boundary: three hours exactly is current. A run that
    finishes again makes it current."""
    store, tick = FakeCopy(copy(DOCS)), Tick()
    now = {"t": AS_OF + timedelta(hours=1)}
    b = holder(store, clock=tick, now=lambda: now["t"])
    reading, view = reading_view(ROWS)
    ok = b.answer(CFG, reading, view)
    assert ok["state"] == "ok" and set(ok["marks"]) == {"1"}
    now["t"] = AS_OF + timedelta(seconds=db.COPY_STALE_S)
    assert b.answer(CFG, reading, view) is ok and b.health()["reasons"] == []
    now["t"] += timedelta(seconds=1)
    stale = b.answer(CFG, reading, view)
    assert (stale["state"], stale["reasons"], stale["as_of"]) == ("held", ["copy_stale"], ok["as_of"])
    assert stale["marks"] == ok["marks"] and stale["students"] == ok["students"] and stale["key"] != ok["key"]
    assert db.public(stale)["said"] == db.REASONS["copy_stale"] + " " + db.HELD_SAID
    assert b.answer(CFG, reading, view)["key"] == stale["key"] and b.current()["reasons"] == ["copy_stale"]
    assert (b.health()["state"], b.health()["reasons"]) == ("held", ["copy_stale"])
    # /health says it even when no request came since the copy grew stale
    later = holder(store, clock=tick, now=lambda: AS_OF + timedelta(hours=1))
    later.answer(CFG, reading, view)
    later.now = lambda: AS_OF + timedelta(hours=4)
    assert later.health()["reasons"] == ["copy_stale"]
    # the sync runs again
    store.snapshot = copy(DOCS, last_run_id=8, last_run_finished_at=now["t"])
    tick.t += db.RUN_CHECK_S + 1
    fresh = b.answer(CFG, reading, view)
    assert (fresh["state"], fresh["reasons"]) == ("ok", [])


def test_past_student_charges_window_the_part_says_it_ended():
    """The review of dfa967b, 10(c): Student Charge is compared to the books within its window (FR-7: 2026), so from the
    next year no student's document is listed or marked. The part says so - a closed reason, a note said after the others
    and by a current part too - never an empty list without a word; /health says it. The year is the desk's (Israel's):
    2026's last evening in UTC is already 2027 there."""
    ran = datetime(2026, 12, 31, 20, 30, tzinfo=timezone.utc)                    # the copy's last run, fresh
    store, tick = FakeCopy(copy(DOCS, last_run_finished_at=ran)), Tick()
    now = {"t": datetime(2026, 12, 31, 21, 0, tzinfo=timezone.utc)}
    b = holder(store, clock=tick, now=lambda: now["t"])
    reading, view = reading_view(ROWS)
    first = b.answer(CFG, reading, view)
    assert (first["state"], first["reasons"]) == ("ok", [])
    now["t"] = datetime(2026, 12, 31, 21, 59, tzinfo=timezone.utc)                # 23:59 in Israel: still 2026
    assert b.answer(CFG, reading, view) is first
    now["t"] = datetime(2026, 12, 31, 22, 0, tzinfo=timezone.utc)                 # midnight in Israel: 2027
    part = b.answer(CFG, reading, view)
    assert (part["state"], part["reasons"], part["marks"]) == ("ok", ["student_window_ended"], first["marks"])
    assert part["key"] != first["key"] and b.answer(CFG, reading, view) is part
    assert db.public(part)["said"] == db.REASONS["student_window_ended"] and "2026" in db.REASONS["student_window_ended"]
    assert b.health()["reasons"] == ["student_window_ended"]
    # with another reason, the note comes after it, and after what is shown instead
    store.snapshot = copy(DOCS, last_run_id=9, last_run_complete=False)
    tick.t += db.RUN_CHECK_S + 1
    held = b.answer(CFG, reading, view)
    assert (held["state"], held["reasons"]) == ("held", ["last_run_incomplete", "student_window_ended"])
    assert db.public(held)["said"] == " ".join([db.REASONS["last_run_incomplete"], db.HELD_SAID,
                                                db.REASONS["student_window_ended"]])


def test_through_the_route_a_stale_copy_is_said_on_the_page_and_in_health(booked):
    booked.client.app.state.books_now = lambda: AS_OF + timedelta(hours=4)
    books = booked.get(1).json()["view"]["books"]
    assert (books["state"], books["reasons"]) == ("held", ["copy_stale"]) and db.REASONS["copy_stale"] in books["said"]
    health = booked.client.get("/health").json()["desk"]["books"]
    assert (health["state"], health["reasons"]) == ("held", ["copy_stale"])


def test_a_reading_read_short_shows_the_last_whole_result_and_never_asks_the_copy():
    store = FakeCopy(copy(DOCS))
    b = holder(store)
    short, view = reading_view(ROWS, whole=False)
    part = b.answer(CFG, short, view)
    assert (part["state"], part["reasons"], store.snapshots) == ("none", ["reading_incomplete"], 0)
    good, view2 = reading_view(ROWS, n=2)
    assert b.answer(CFG, good, view2)["state"] == "ok"
    short3, view3 = reading_view(ROWS, n=3, whole=False)
    held = b.answer(CFG, short3, view3)
    assert (held["state"], held["reasons"], set(held["marks"])) == ("held", ["reading_incomplete"], {"1"})


def test_a_reading_is_whole_only_with_no_error_and_both_charge_boards_read_whole():
    """B73 and B74 of the review's mutants: a reading with an error, or with either charge board read short, is not
    matched to the books (a row missing from a board read short would turn its documents into documents no row cites):
    `reading_incomplete`, and the copy is not asked."""
    reading, view = reading_view(ROWS)
    assert db.reading_whole(reading)
    b2b_short = dict(reading, checks=dict(reading["checks"], b2b_charge=dict(reading["checks"]["b2b_charge"], ok=False)))
    failed = dict(reading, error="read_failed")
    for short in (b2b_short, failed):
        assert not db.reading_whole(short)
        store = FakeCopy(copy(DOCS))
        part = holder(store).answer(CFG, short, view)
        assert (part["state"], part["reasons"], store.snapshots) == ("none", ["reading_incomplete"], 0)


def test_a_database_that_fails_is_said_by_class_and_not_asked_again_for_a_while():
    store, tick = FakeCopy(copy(DOCS)), Tick()
    b = holder(store, clock=tick)
    reading, view = reading_view(ROWS)
    assert b.answer(CFG, reading, view)["state"] == "ok"
    store.fail = "57P01"
    tick.t += db.RUN_CHECK_S + 1
    held = b.answer(CFG, reading, view)
    assert (held["state"], held["reasons"]) == ("held", ["store_error"]) and b.health()["error"] == "57P01"
    asked = store.runs
    tick.t += db.STORE_FAIL_GAP_S - 1
    assert b.answer(CFG, reading, view)["reasons"] == ["store_error"] and store.runs == asked
    store.fail = None
    tick.t += 2
    assert b.answer(CFG, reading, view)["state"] == "ok" and store.runs == asked + 1


class SlowKeys(FakeKeys):
    """The Accounts board read slowly: `get` that may read blocks until released (a read of monday in progress)."""

    def __init__(self, keys):
        super().__init__(keys)
        self.started, self.release = threading.Event(), threading.Event()

    def get(self, may_read=True):
        self.asked += 1
        if may_read:
            self.started.set()
            self.release.wait(10)
        return self.keys


def test_an_answer_at_once_does_not_wait_for_another_requests_computation(monkeypatch):
    """The review of dfa967b, finding 4 (tests/test_review_d3_at_once_waits.py, ported): D2's answer at once (`refreshing`)
    is the fast path of an open, and it waited up to WAIT_S behind another request's computation - which may read the whole
    Accounts board and the copy; on a cold instance, the page's own `?fresh=1` computing while a second open arrives, the
    common case. It takes the lock without waiting, and is answered with what is held: here nothing yet, so `pending`.
    WAIT_S is shortened so the test is quick; the code waits the same way at 30 s."""
    monkeypatch.setattr(db, "WAIT_S", 3)
    store = FakeCopy(copy(DOCS))
    b = holder(store)
    b.keys = SlowKeys({})
    reading, view = reading_view(ROWS, n=1)
    other, view2 = reading_view(ROWS, n=2)
    t = threading.Thread(target=lambda: b.answer(CFG, reading, view, may_read=True), daemon=True)
    t.start()
    assert b.keys.started.wait(5)                                            # the first request computes, holding the lock
    try:
        t0 = time.monotonic()
        got = b.answer(CFG, other, view2, may_read=False)                    # an open answered at once
        waited = time.monotonic() - t0
    finally:
        b.keys.release.set()
        t.join(10)
    assert waited < 1.0, "the answer at once waited %.1f s for another request's computation" % waited
    assert (got["state"], got["reasons"]) == ("none", ["pending"])
    # with a part held, an answer at once while another computes is that part
    b.keys.started.clear()
    b.keys.release.clear()
    third, view3 = reading_view(ROWS, n=3)
    t = threading.Thread(target=lambda: b.answer(CFG, third, view3, may_read=True), daemon=True)   # a new reading
    t.start()
    try:
        assert b.keys.started.wait(5)
        assert b.answer(CFG, other, view2, may_read=False) is b.part and b.part["state"] == "ok"
    finally:
        b.keys.release.set()
        t.join(10)


def test_a_request_that_may_read_waits_for_the_computation_in_progress_and_answers_its_part(monkeypatch):
    """The other side (C04 of the review's mutants): a request that may read and finds the part being computed waits for
    it, up to WAIT_S, and is answered with the part that computation made - never `busy` at once."""
    monkeypatch.setattr(db, "WAIT_S", 5)
    store = FakeCopy(copy(DOCS))
    b = holder(store)
    b.keys = SlowKeys({})
    reading, view = reading_view(ROWS)
    first, second = {}, {}
    t1 = threading.Thread(target=lambda: first.update(part=b.answer(CFG, reading, view)), daemon=True)
    t1.start()
    assert b.keys.started.wait(5)
    t2 = threading.Thread(target=lambda: second.update(part=b.answer(CFG, reading, view)), daemon=True)
    t2.start()
    t2.join(0.3)
    try:
        assert t2.is_alive() and "part" not in second                       # it waits for the computation
    finally:
        b.keys.release.set()
        t1.join(10)
        t2.join(10)
    assert first["part"]["state"] == "ok" and second["part"] is first["part"]
    assert store.snapshots == 1                                              # computed once, by the first


def test_two_held_parts_with_different_reasons_have_different_keys():
    """The review of dfa967b, finding 5 (tests/test_review_d3_part_key.py, ported): the part's key named it by its state,
    its copy's time and when it was computed, so every `held` part made from one last whole part had one key whatever its
    reasons, and routes `_merged` (one merged view per reading and key) answered a part whose reasons changed with the
    merged view of the part before: the page said the old reason while /health said the new one. The key holds the
    reasons; two parts of one copy computed at two times differ in it too."""
    store, tick = FakeCopy(copy(DOCS)), Tick()
    b = holder(store, clock=tick)
    reading, view = reading_view(ROWS)
    ok = b.answer(CFG, reading, view)
    assert ok["state"] == "ok"
    store.fail = "57P01"
    tick.t += db.RUN_CHECK_S + 1
    first = b.answer(CFG, reading, view)
    assert (first["state"], first["reasons"]) == ("held", ["store_error"])
    store.fail = None
    store.snapshot = copy(DOCS + [doc(62605)], last_run_id=8)               # the copy is back, with a hole
    tick.t += db.STORE_FAIL_GAP_S + 1
    second = b.answer(CFG, reading, view)
    assert (second["state"], second["reasons"]) == ("held", ["holes"])
    assert first["key"] != second["key"], (first["key"], second["key"])
    # every reason at once still fits what the totals and the files are asked by
    assert len(b._made("held", list(db.REASONS), ok)["key"]) <= db.BOOKS_KEY_MAX
    # the same copy computed again at another time is another part (B81 of the review's mutants)
    again = db.DeskBooks(lambda _url: store, lambda: None, clock=tick,
                         now=lambda: datetime(2026, 11, 25, 9, 1, tzinfo=timezone.utc))
    again.keys = FakeKeys({})
    store.snapshot = copy(DOCS)
    other = again.answer(CFG, reading, view)
    assert other["state"] == "ok" and other["as_of"] == ok["as_of"] and other["key"] != ok["key"]


def test_two_parts_computed_in_one_second_have_different_keys():
    """A part's key names the computation it comes from, not only its second: a copy whose run moved within the second the
    part before was computed (here its finished time kept, as a fixture keeps it) is another part, with another list."""
    store = FakeCopy(copy(DOCS))
    b = db.DeskBooks(lambda _url: store, lambda: None, clock=Tick(),
                     now=lambda: datetime(2026, 11, 25, 9, 0, tzinfo=timezone.utc))
    b.keys = FakeKeys({})
    reading, view = reading_view(ROWS)
    first = b.answer(CFG, reading, view)
    store.snapshot = copy(DOCS + [doc(62504, card="20003")], last_run_id=8)
    b._checked_at = None
    second = b.answer(CFG, reading, view)
    assert (first["computed_at"], first["as_of"]) == (second["computed_at"], second["as_of"])
    assert len(second["students"]) == len(first["students"]) + 1 and first["key"] != second["key"]


def test_the_merged_view_is_kept_per_reading_and_part():
    """R06 of the review's mutants: one part laid over two readings (an `off` part has one key for every reading) is two
    merged views - each of its own reading's rows - and the same reading and part is the merged view kept."""
    from app.desk import routes

    st = SimpleNamespace(view_lock=threading.Lock(), merged=None)
    off = holder(FakeCopy(copy(DOCS)))._made("off", ["demo"])
    r1, v1 = reading_view(ROWS, n=1)
    r2, v2 = reading_view(ROWS[1:], n=2)
    m1 = routes._merged(st, r1, v1, off)
    m2 = routes._merged(st, r2, v2, off)
    assert [r["id"] for r in m1["b2c"]["rows"]] == [r["id"] for r in v1["b2c"]["rows"]]
    ids2 = [r["id"] for r in m2["b2c"]["rows"]]
    assert ids2 == [r["id"] for r in v2["b2c"]["rows"]] != [r["id"] for r in m1["b2c"]["rows"]]
    assert routes._merged(st, r2, v2, off) is m2


def test_the_merged_view_says_the_reasons_of_the_part_it_is_merged_with():
    from app.desk import routes

    store, tick = FakeCopy(copy(DOCS)), Tick()
    b = holder(store, clock=tick)
    reading, view = reading_view(ROWS)
    st = SimpleNamespace(view_lock=threading.Lock(), merged=None)
    b.answer(CFG, reading, view)
    store.fail = "57P01"
    tick.t += db.RUN_CHECK_S + 1
    shown = routes._merged(st, reading, view, b.answer(CFG, reading, view))
    assert shown["books"]["reasons"] == ["store_error"]
    store.fail = None
    store.snapshot = copy(DOCS + [doc(62605)], last_run_id=8)
    tick.t += db.STORE_FAIL_GAP_S + 1
    part = b.answer(CFG, reading, view)
    shown = routes._merged(st, reading, view, part)
    assert shown["books"]["reasons"] == part["reasons"] == ["holes"], shown["books"]


def test_without_the_accounts_keys_nothing_is_computed():
    """The side of a card needs every Account's key: with none read, no list and no mark (a company's invoice-receipt would
    otherwise be a student's)."""
    store = FakeCopy(copy(DOCS))
    b = holder(store)
    b.keys = FakeKeys(None)
    reading, view = reading_view(ROWS)
    part = b.answer(CFG, reading, view)
    assert (part["state"], part["reasons"], part["marks"]) == ("none", ["accounts_unread"], {})


def test_a_part_that_cannot_be_computed_is_said_by_class_never_by_its_text(caplog):
    import logging

    store = FakeCopy(copy(DOCS))
    b = holder(store)
    reading, view = reading_view(ROWS)
    real = db.compute

    def broken(*_a):
        raise KeyError("62502 דנה לוי")
    db.compute = broken
    try:
        with caplog.at_level(logging.INFO, logger="analiza_finance.desk"):
            part = b.answer(CFG, reading, view)
    finally:
        db.compute = real
    assert (part["state"], part["reasons"]) == ("none", ["store_error"]) and b.health()["error"] == "KeyError"
    assert not any("62502" in r.getMessage() or "דנה" in r.getMessage() for r in caplog.records)


# ---- who sees what (spec §6.4.1): the server never sends what the reader may not see ------------------------------------------
import json  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from app import main  # noqa: E402
from app.desk import reading as rd  # noqa: E402
from tests.test_desk import SECRET, TOKEN, World, session_token, status, world  # noqa: E402
from tests.test_desk_mirror import MemoryStore  # noqa: E402


def books_world() -> World:
    """tests/test_desk.py's monday, its Accounts board read whole too (D3 reads every Account's key), and its rows citing the
    books: 101 cites invoice-receipt 62502 and says "לא שולם" (the case of 23.11), 103 cites nothing and shares its name
    with the card of 62503, and the account 701's key is 77001."""
    w, base = World(), world()
    w.items, w.order = base.items, dict(base.order)
    w.order[rd.ACCOUNTS_BOARD] = ["701", "702"]
    w.set(101, touch=False, invoice="62502", status=status("לא שולם", 0))
    w.set(103, touch=False, invoice="")
    return w


# the copy: a student's invoice-receipt cited by 101; a student's uncited, named as 103; a company's invoice (account 701's
# card); a tax invoice on a card no Account names (unclear); one on the vouchers' card. Distinctive names and numbers, to
# grep the answers for a leak
ROUTE_DOCS = [doc(62502, card="20001", name="שם-בכרטיס-אחד"), doc(62503, card="20002", name="תלמיד-גימל"),
              doc(50500, doc_type=1, card="77001", name="כרטיס-של-חברה", total="11800", excl="10000"),
              doc(50501, doc_type=1, card="20009", name="כרטיס-לא-ברור", total="5900", excl="5000"),
              doc(50502, doc_type=1, card="10091", name="כרטיס-שוברים", total="1180", excl="1000")]
STUDENT_WORDS = ("62502", "שם-בכרטיס-אחד", "62503", "תלמיד-גימל")
COMPANY_WORDS = ("50500", "כרטיס-של-חברה")
UNCLEAR_WORDS = ("50501", "כרטיס-לא-ברור")


@pytest.fixture
def booked(monkeypatch):
    """The app with the desk's secrets, a database (its stored reading in memory, its books copy `booked.copy`), Analiza's
    company, and books_world's monday."""
    w = books_world()
    monkeypatch.setenv("MONDAY_CLIENT_SECRET", SECRET)
    monkeypatch.setenv("MONDAY_TOKEN", TOKEN)
    monkeypatch.setenv("DATABASE_URL", "postgresql://nobody@127.0.0.1:1/none")
    monkeypatch.setenv("WIZ_COMPANY", "analiza")
    fake = FakeCopy(copy(ROUTE_DOCS))
    main.app.state.monday_transport = w.transport()
    main.app.state.desk = None
    main.app.state.usage = None
    main.app.state.frontend_dir = None
    main.app.state.desk_store = MemoryStore()
    main.app.state.books_store = fake
    main.app.state.books_now = lambda: NOW

    class D:
        pass

    d = D()
    d.w, d.copy, d.client = w, fake, TestClient(main.app)
    d.get = lambda user, path="/api/desk": d.client.get(path, headers={"Authorization": "Bearer " + session_token(user)})
    yield d
    main.app.state.monday_transport = None
    main.app.state.desk = None
    main.app.state.usage = None
    main.app.state.desk_store = None
    main.app.state.books_store = None
    main.app.state.books_now = None


def test_a_member_of_both_boards_sees_the_marks_both_lists_and_the_unclear_documents(booked):
    body = booked.get(1).json()
    v = body["view"]
    assert v["books"]["state"] == "ok" and v["books"]["reasons"] == [] and v["books"]["as_of"] == "2026-11-25T08:00:00+00:00"
    rows = {r["id"]: r for r in v["b2c"]["rows"]}
    assert rows["101"]["books"][0]["chip"] == 'בחשבשבת חשבונית מס/קבלה 62502, והסטטוס "לא שולם"'
    assert rows["101"]["books_task"] is True and "books_receipt_unpaid" in rows["101"]["flags"]
    assert rows["101"]["state"] == "no_money"                                 # in its own state, and in "לטיפול" too
    students = v["b2c"]["books"]["documents"]
    assert [(e["number"], [c["id"] for c in e["candidates"]]) for e in students] == [("62503", ["103"])]
    assert students[0]["total_incl"] == "4000"                                # money as a string, never a float
    companies = v["b2b"]["books"]
    assert [(e["number"], e["accounts"], e["total_excl"]) for e in companies["documents"]] == [("50500", ["חברה-משלמת"], "10000")]
    assert companies["vouchers"] == 1 and "10091" in companies["vouchers_said"]
    assert [e["number"] for e in v["books_unclear"]["documents"]] == ["50501"]
    # the Accounts board was read whole, with its one column and never the business number
    reads = [x for x in booked.w.queries if "items(ids:" in x[0] and x[1].get("cols") == [rd.AC["hash_key"]]]
    assert reads and not any(rd.AC["vat_id"] in json.dumps(v_) for _q, v_ in booked.w.queries if "items(ids:" in _q)


def test_a_member_of_student_charge_only_never_receives_a_companys_or_an_unclear_document(booked):
    text = booked.get(2).text
    v = json.loads(text)["view"]
    assert v["b2b"] is None and v["books_unclear"] is None and v["b2c"]["books"]["documents"]
    assert v["books"]["state"] == "ok"
    for word in COMPANY_WORDS + UNCLEAR_WORDS + ("חברה-משלמת", "77001", "10091"):
        assert word not in text, word


def test_a_member_of_b2b_charge_only_never_receives_a_mark_a_students_or_an_unclear_document(booked):
    text = booked.get(3).text
    v = json.loads(text)["view"]
    assert v["b2c"] is None and v["books_unclear"] is None and v["b2b"]["books"]["documents"]
    for word in STUDENT_WORDS + UNCLEAR_WORDS + ("books_receipt_unpaid",):
        assert word not in text, word


def test_health_says_the_books_state_time_and_reasons_never_a_count_or_a_document(booked):
    booked.get(1)
    text = booked.client.get("/health").text
    books = json.loads(text)["desk"]["books"]
    assert (books["state"], books["as_of"], books["reasons"]) == ("ok", "2026-11-25T08:00:00+00:00", [])
    assert set(books) == {"state", "as_of", "reasons", "computed_at", "seconds", "error", "accounts"}
    assert books["accounts"]["error"] is None and books["accounts"]["read_at"]
    for word in STUDENT_WORDS + COMPANY_WORDS + UNCLEAR_WORDS + ("10091", "77001", "חברה-משלמת"):
        assert word not in text, word


def test_nothing_of_the_books_reaches_the_log(booked, caplog):
    import logging

    with caplog.at_level(logging.DEBUG):
        for user in (1, 2, 3):
            booked.get(user)
        booked.copy.fail = "57P01"
        booked.get(1, "/api/desk?fresh=1")
    said = "\n".join(r.getMessage() for r in caplog.records)
    for word in STUDENT_WORDS + COMPANY_WORDS + UNCLEAR_WORDS + ("10091", "77001"):
        assert word not in said, word


def test_the_tasks_total_is_of_the_books_part_the_page_shows(booked):
    """The totals of "לטיפול" hold the rows the books mark, and only of the part on the screen: another part (a newer copy
    since) is `other_reading`, as another reading is."""
    from urllib.parse import quote

    body = booked.get(1).json()
    taken, key = quote(body["taken_at"]), quote(body["view"]["books"]["key"])
    auth = {"Authorization": "Bearer " + session_token(1)}
    t = booked.client.get("/api/desk/totals?tab=b2c&state=tasks&taken=%s&books=%s" % (taken, key), headers=auth).json()
    assert t["count"] == len(fd.filter_b2c(_merged_b2c(), "tasks", "", "")) and t["count"] >= 1
    other = booked.client.get("/api/desk/totals?tab=b2c&state=tasks&taken=%s&books=other" % taken, headers=auth).json()
    assert other["other_reading"] is True
    # a page that sends no key is answered as before D3
    assert booked.client.get("/api/desk/totals?tab=b2c&state=tasks&taken=%s" % taken, headers=auth).json()["count"] == t["count"]


def test_through_the_route_the_page_and_health_say_the_same_reason(booked):
    """The review of dfa967b, finding 5, end to end: GET /api/desk while the database fails, then while the copy has a
    hole - one reading."""
    assert booked.get(1).json()["view"]["books"]["state"] == "ok"
    books = booked.client.app.state.desk.books
    booked.copy.fail = "57P01"
    books._checked_at = None                                                  # past RUN_CHECK_S
    assert booked.get(1).json()["view"]["books"]["reasons"] == ["store_error"]
    booked.copy.fail = None
    booked.copy.snapshot = copy(ROUTE_DOCS + [doc(62605)], last_run_id=8)
    books._checked_at = books._store_failed_at = None                         # past STORE_FAIL_GAP_S
    page = booked.get(1).json()["view"]["books"]
    health = booked.client.get("/health").json()["desk"]["books"]
    assert health["reasons"] == ["holes"]
    assert page["reasons"] == health["reasons"], (page, health)


def _merged_b2c() -> dict:
    st = main.app.state.desk
    return st.merged[2]["b2c"]


# ---- the Excel files: "לטיפול" carries the marks and its tab's list --------------------------------------------------------------
def _link(booked, user: int, tab: str, state: str = "tasks", books=None) -> str:
    body = booked.get(user).json()
    asked = {"tab": tab, "state": state, "taken": body["taken_at"], **({} if books is None else {"books": books})}
    return booked.client.post("/api/desk/export-link", headers={"Authorization": "Bearer " + session_token(user)},
                              json=asked).json()["url"]


def _file(booked, user: int, tab: str, state: str = "tasks", books=None):
    import io

    import openpyxl

    got = booked.client.get(_link(booked, user, tab, state, books))
    assert got.status_code == 200, got.text
    return openpyxl.load_workbook(io.BytesIO(got.content))


def test_the_students_tasks_file_carries_the_marked_rows_and_its_list_and_the_unclear_only_for_both(booked):
    wb = _file(booked, 1, "b2c")
    assert wb.sheetnames == ["תקבולים", "פרטים", "מסמכים בלי חיוב", "לא ברור של מי"]
    ws = wb["תקבולים"]
    heads = [c.value for c in ws[1]]
    rows = {ws.cell(row=i, column=heads.index("מזהה במאנדיי") + 1).value: [c.value for c in ws[i]]
            for i in range(2, ws.max_row + 1) if ws.cell(row=i, column=heads.index("מזהה במאנדיי") + 1).value}
    assert rows["101"][heads.index("מה לבדוק")] == 'בחשבשבת חשבונית מס/קבלה 62502, והסטטוס "לא שולם"'
    assert rows["101"][heads.index("שיבוץ")] == "ללא תקבול"
    lst = wb["מסמכים בלי חיוב"]
    lheads = [c.value for c in lst[1]]
    assert lheads == ["מספר מסמך", "סוג מסמך", "תאריך מסמך", 'סכום בחשבשבת (כולל מע"מ)', "שם בכרטיס", "מועמדת לפי שם"]
    first = [c.value for c in lst[2]]
    assert first[0] == "62503" and first[1] == "חשבונית מס/קבלה" and first[3] == 4000 and first[4] == "תלמיד-גימל"
    assert first[5].startswith("תלמיד-גימל (") and lst[2][0].data_type == "s" and lst.sheet_view.rightToLeft is True
    assert [c.value for c in wb["לא ברור של מי"][2]][0] == "50501"
    info = {r[0].value: r[1].value for r in wb["פרטים"].iter_rows()}
    assert info["לפי הספרים"].startswith("נכון ל-25/11/2026 10:00")
    # a member of Student Charge only: the list, never the unclear documents
    assert _file(booked, 2, "b2c").sheetnames == ["תקבולים", "פרטים", "מסמכים בלי חיוב"]
    # and a list that is not "לטיפול" carries no list of the books
    assert _file(booked, 1, "b2c", state="").sheetnames == ["תקבולים", "פרטים"]


def test_the_file_carries_the_part_the_page_was_answered_and_reads_nothing_of_the_books(booked):
    """R05 of the review's mutants: the Excel file of "לטיפול" is built from the books' part the instance last answered -
    the page's - never computed again for it, even when the copy moved since: it says so (10(a)), and reads nothing."""
    import io

    import openpyxl

    from app.desk.export import BOOKS_OTHER_SAID

    body = booked.get(1).json()
    assert [e["number"] for e in body["view"]["b2c"]["books"]["documents"]] == ["62503"]
    booked.copy.snapshot = copy(ROUTE_DOCS + [doc(62504, card="20003", name="תלמיד-דלת")], last_run_id=8)
    booked.client.app.state.desk.books._checked_at = None
    asked = (booked.copy.runs, booked.copy.snapshots)
    url = booked.client.post("/api/desk/export-link", headers={"Authorization": "Bearer " + session_token(1)},
                             json={"tab": "b2c", "state": "tasks", "taken": body["taken_at"],
                                   "books": body["view"]["books"]["key"]}).json()["url"]
    wb = openpyxl.load_workbook(io.BytesIO(booked.client.get(url).content))
    listed = [c.value for c in wb["מסמכים בלי חיוב"]["A"][1:] if c.value and str(c.value).isdigit()]
    assert listed == ["62503"] and (booked.copy.runs, booked.copy.snapshots) == asked
    assert BOOKS_OTHER_SAID not in [r[1].value for r in wb["פרטים"].iter_rows()]


def test_the_lists_text_is_written_as_text_never_as_a_formula(booked):
    """X01 of the review's mutants: a card's name, a candidate's name and an Account's name come from the books and from
    monday - a name that opens with "=", "+", "-" or "@" is written as text, formatted as text, never as a formula (D2's
    hostile review 10.11: a name became a live HYPERLINK formula)."""
    names = ['=HYPERLINK("https://example.test","פתח")', "+1+2 כרטיס", "-3 כרטיס", "@SUM(1) כרטיס"]
    booked.copy.snapshot = copy([doc(62502, card="20001", name="שם-בכרטיס-אחד"), doc(62503, card="20002", name=names[0]),
                                 doc(50500, doc_type=1, card="77001", name=names[1]),
                                 doc(50501, doc_type=1, card="20009", name=names[2])])
    booked.w.items["103"]["name"] = names[0]                                  # a candidate by name of 62503
    booked.w.items["701"]["name"] = names[3]                                  # the Account that names card 77001
    cells = []
    for user, tab in ((1, "b2c"), (1, "b2b")):
        wb = _file(booked, user, tab)
        for title in ("מסמכים בלי חיוב", "לא ברור של מי"):
            cells += [c for line in wb[title].iter_rows(min_row=2) for c in line if isinstance(c.value, str)]
    hits = [c for c in cells if c.value.startswith(("=", "+", "-", "@"))]
    assert {c.value for c in hits} >= {names[0], names[1], names[2], names[3]} and any(
        c.value.startswith(names[0] + " (") for c in hits)                     # the card, and the candidate
    assert all(c.data_type == "s" and c.number_format == "@" for c in hits), [(c.value, c.data_type) for c in hits]


def test_the_companies_tasks_file_carries_its_list_and_the_vouchers_line(booked):
    wb = _file(booked, 3, "b2b")
    assert wb.sheetnames == ["חיובים", "פרטים", "מסמכים בלי חיוב"]
    lst = wb["מסמכים בלי חיוב"]
    assert [c.value for c in lst[1]] == ["מספר מסמך", "סוג מסמך", "תאריך מסמך", 'סכום בחשבשבת (ללא מע"מ)',
                                         'סכום בחשבשבת (כולל מע"מ)', "שם בכרטיס", "חברה"]
    assert [c.value for c in lst[2]][:2] == ["50500", "חשבונית מס"] and [c.value for c in lst[2]][-1] == "חברה-משלמת"
    said = [r[0].value for r in lst.iter_rows(min_row=3) if r[0].value]
    assert any("10091" in x for x in said) and not any("50502" in x for x in said)
    assert "לא ברור של מי" in _file(booked, 1, "b2b").sheetnames


def test_a_file_built_from_another_books_part_than_the_page_showed_says_so(booked):
    """The review of dfa967b, 10(a): the Excel link carries the books' part the page shows (its key), signed with the rest
    of the link, so a file built after the part changed says so in "פרטים" - as the totals say `other_reading`. A link with
    the part on the screen says nothing; a link without a key is signed as before D3."""
    from urllib.parse import parse_qs, urlencode, urlsplit

    from app.desk.export import BOOKS_OTHER_SAID

    def said(wb) -> list:
        return [r[1].value for r in wb["פרטים"].iter_rows()]

    shown = booked.get(1).json()["view"]["books"]["key"]
    assert BOOKS_OTHER_SAID not in said(_file(booked, 1, "b2c", books=shown))
    assert BOOKS_OTHER_SAID not in said(_file(booked, 1, "b2c"))
    # the copy moves: the page that showed the part before asks for its file
    booked.copy.snapshot = copy(ROUTE_DOCS + [doc(62504, card="20003", name="תלמיד-דלת")], last_run_id=8)
    booked.client.app.state.desk.books._checked_at = None
    assert BOOKS_OTHER_SAID in said(_file(booked, 1, "b2c", books=shown))
    # the key is signed: changed or dropped, the link opens nothing
    url = _link(booked, 1, "b2c", books=shown)
    q = {k: v[0] for k, v in parse_qs(urlsplit(url).query, keep_blank_values=True).items()}
    assert q["books"] == shown
    for forged in (dict(q, books="other"), {k: v for k, v in q.items() if k != "books"}):
        assert booked.client.get("/api/desk/export.xlsx?" + urlencode(forged)).status_code == 403
    assert booked.client.get(url).status_code == 200


# ---- the whole path, on a real Postgres ---------------------------------------------------------------------------------------
@pytest.fixture
def booked_pg(pg, monkeypatch):
    """booked's app on a real database: the books copy and the desk's stored reading in Postgres."""
    w = books_world()
    monkeypatch.setenv("MONDAY_CLIENT_SECRET", SECRET)
    monkeypatch.setenv("MONDAY_TOKEN", TOKEN)
    monkeypatch.setenv("DATABASE_URL", pg)
    monkeypatch.setenv("WIZ_COMPANY", "analiza")
    main.app.state.monday_transport = w.transport()
    for k in ("desk", "usage", "frontend_dir", "desk_store", "books_store"):
        setattr(main.app.state, k, None)
    client = TestClient(main.app)
    yield client, w, pg
    for k in ("monday_transport", "desk", "usage", "desk_store", "books_store"):
        setattr(main.app.state, k, None)


@needs_pg
def test_the_desk_reads_the_copy_whole_and_says_when_it_is_not(booked_pg):
    client, w, pg = booked_pg
    auth = {"Authorization": "Bearer " + session_token(1)}
    seed(pg, ROUTE_DOCS)
    v = client.get("/api/desk", headers=auth).json()["view"]
    assert v["books"]["state"] == "ok" and v["books"]["as_of"]
    assert [e["number"] for e in v["b2c"]["books"]["documents"]] == ["62503"]
    assert [e["number"] for e in v["b2b"]["books"]["documents"]] == ["50500"]
    assert {r["id"] for r in v["b2c"]["rows"] if r.get("books")} == {"101"}
    # a run that is cut short: the last whole result, held, with why
    with psycopg.connect(pg, autocommit=True) as conn:
        conn.execute("INSERT INTO books_sync_runs (company, trigger, finished_at, complete, ok) "
                     "VALUES (%s, 'tick', now(), false, true)", (ANALIZA,))
    main.app.state.desk.books._checked_at = None
    held = client.get("/api/desk", headers=auth).json()["view"]["books"]
    assert (held["state"], held["reasons"], held["as_of"]) == ("held", ["last_run_incomplete"], v["books"]["as_of"])
    assert "הקריאה האחרונה של חשבשבת לא הגיעה לסוף" in held["said"]
    health = client.get("/health").json()["desk"]["books"]
    assert (health["state"], health["reasons"]) == ("held", ["last_run_incomplete"])


@needs_pg
def test_on_demo_the_desk_never_asks_the_copy(booked_pg, monkeypatch):
    client, w, pg = booked_pg
    monkeypatch.setenv("WIZ_COMPANY", "demo")
    seed(pg, ROUTE_DOCS)
    v = client.get("/api/desk", headers={"Authorization": "Bearer " + session_token(1)}).json()["view"]
    assert (v["books"]["state"], v["books"]["reasons"]) == ("off", ["demo"])
    assert v["b2c"]["books"]["documents"] == [] and not any(r.get("books") for r in v["b2c"]["rows"])
    assert not [q for q, _v in w.queries if "items(ids:" in q and _v.get("cols") == [rd.AC["hash_key"]]]


def test_an_answer_at_once_never_reads_monday_for_the_keys_and_the_next_request_computes():
    """D2's answer at once (`refreshing`, a reading past its span) stays at once: with no Account's key held it says
    `pending` and keeps nothing under the reading, and the page's own next request reads the keys and computes."""
    store = FakeCopy(copy(DOCS))
    b = holder(store)

    class Cold(FakeKeys):
        held = None

        def get(self, may_read=True):
            self.asked += 1
            if may_read:
                self.held = self.keys
            return self.held
    b.keys = Cold({})
    reading, view = reading_view(ROWS)
    pending = b.answer(CFG, reading, view, may_read=False)
    assert (pending["state"], pending["reasons"], pending["marks"]) == ("none", ["pending"], {})
    assert db.public(pending)["said"] == db.REASONS["pending"]
    part = b.answer(CFG, reading, view)
    assert part["state"] == "ok" and set(part["marks"]) == {"1"}
    # with the keys held, an answer at once is computed from them
    other, view2 = reading_view(ROWS, n=2)
    assert b.answer(CFG, other, view2, may_read=False)["state"] == "ok"


def test_an_answer_at_once_with_no_key_held_does_not_read_the_copy():
    """The review of dfa967b, finding 7 (tests/test_review_d3_at_once.py, ported): `_compute` read the copy first - every
    document of the company with its card's name, in a repeatable-read transaction - and only then asked for the keys,
    found none, and answered `pending` with nothing kept: every cold open paid a full read of the books for no answer. The
    keys held are asked first."""
    class Cold(FakeKeys):
        def get(self, may_read=True):
            self.asked += 1
            return self.keys if may_read else None

    store = FakeCopy(copy(DOCS))
    b = holder(store)
    b.keys = Cold({})
    reading, view = reading_view(ROWS)
    part = b.answer(CFG, reading, view, may_read=False)
    assert (part["state"], part["reasons"]) == ("none", ["pending"])
    assert store.snapshots == 0, "the whole copy was read for an answer that keeps nothing"
    assert b.answer(CFG, reading, view)["state"] == "ok" and store.snapshots == 1   # the page's next request computes


def test_the_invoice_columns_keep_a_list_of_document_numbers_and_mask_a_long_one():
    """The review of dfa967b, 10(d): the desk masks an ID number typed into free text (`mask_ids`, judged by the books'
    cleaning), which reads "062894, 62914" as one number of eleven digits and masked it whole - so A1 and the marks lost
    the two documents the payment cites. In the two charge boards' invoice columns, a cell A1 reads as a list of numbers
    (`parse_refs`) is kept as typed, each of at most six digits; a number of seven digits or more in it is still masked,
    and a cell A1 cannot read is masked as every text is. Every other text column is masked as before."""
    for text in ("062894, 62914", "62894, 62914", "123456, 50348", "50347", "0628, 1234", "62894,62914,62915",
                 "62894\u200f, 62914", ""):
        assert rd.mask_refs(text) == text, text
    assert rd.mask_ids("062894, 62914") == rd.ID_MARK                      # the mask of every other text: one number
    assert a1.parse_refs(rd.mask_refs("062894, 62914")) == ["62894", "62914"]
    for text, want in (("62894, 1234567", "62894, #"), ("123456789", "#"), ("62894, 039482716", "62894, #"),
                       ("0001234567, 62894", "#, 62894"), ("חשבונית 039 482 716", "חשבונית #")):
        got = rd.mask_refs(text)
        assert got == want.replace("#", rd.ID_MARK), (text, got)
    assert rd.mask_refs(None) is None
    # the two invoice columns only: a credit reference is masked as before
    sc_rows = [{"name": "תלמידה", rd.SC["invoice"]: "062894, 62914", rd.SC["credit_ref"]: "062894, 62914"}]
    b2_rows = [{"name": "חיוב", rd.B2["invoice"]: "062894, 62914", rd.B2["month"]: "062894, 62914"}]
    rd.masked("student_charge", sc_rows)
    rd.masked("b2b_charge", b2_rows)
    assert (sc_rows[0][rd.SC["invoice"]], sc_rows[0][rd.SC["credit_ref"]]) == ("062894, 62914", rd.ID_MARK)
    assert (b2_rows[0][rd.B2["invoice"]], b2_rows[0][rd.B2["month"]]) == ("062894, 62914", rd.ID_MARK)
    # and as the desk reads monday
    from tests.test_desk import reader_of

    w = world()
    w.set(101, invoice="062894, 62914", touch=False)
    w.set(102, invoice="62894, 039482716", touch=False)
    got = {x["id"]: x[rd.SC["invoice"]] for x in rd.read_desk(reader_of(w))["student_charge"]["rows"]}
    assert (got["101"], got["102"]) == ("062894, 62914", "62894, " + rd.ID_MARK)


def test_the_accounts_keys_are_read_whole_kept_ten_minutes_and_a_short_read_keeps_the_ones_held():
    """`AccountKeys`: the whole Accounts board, its one column and the names; kept KEYS_TTL_S; a read that comes short of
    the board's items_count is a failed read (a company's card missing would make its invoice-receipt a student's), and
    the keys held are kept, tried again after KEYS_FAIL_GAP_S."""
    from tests.test_desk import reader_of

    w, tick = books_world(), Tick()
    keys = db.AccountKeys(lambda: reader_of(w), clock=tick)
    assert keys.get() == {"77001": ["חברה-משלמת"]} and keys.error is None and keys.read_at
    reads = len(w.queries)
    tick.t += db.KEYS_TTL_S - 1
    assert keys.get() == {"77001": ["חברה-משלמת"]} and len(w.queries) == reads
    w.set(702, touch=False, hash_key="77002")
    w.count_off[rd.ACCOUNTS_BOARD] = 1                      # the board says one item more than its pages give
    tick.t += 2
    assert keys.get() == {"77001": ["חברה-משלמת"]} and keys.error == "count_mismatch"
    asked = len(w.queries)
    tick.t += db.KEYS_FAIL_GAP_S - 1
    assert keys.get() == {"77001": ["חברה-משלמת"]} and len(w.queries) == asked
    w.count_off = {}
    tick.t += 2
    assert keys.get() == {"77001": ["חברה-משלמת"], "77002": ["חברה-אם"]} and keys.error is None
    assert keys.get(may_read=False) == keys.keys


def test_the_accounts_names_are_masked_as_the_desk_reads_them():
    """B78 of the review's mutants: the companies' list shows the Accounts that name a card by their names, read with the
    keys - masked as the desk's reading masks every name (`masked`), so an ID number typed into one never leaves."""
    from tests.test_desk import reader_of

    w = books_world()
    w.items["701"]["name"] = "חברה-משלמת 039482716"
    keys = db.AccountKeys(lambda: reader_of(w), clock=Tick())
    assert keys.get() == {"77001": ["חברה-משלמת " + rd.ID_MARK]}


def test_a_cold_instance_answers_at_once_without_reading_the_keys_and_its_next_request_computes(booked):
    """D2's answer at once on an instance that woke from zero (the stored reading, `refreshing`) reads nothing of monday for
    the books either: the part says `pending`, and the page's own request (`?fresh=1`) reads the keys and computes it."""
    def key_reads() -> int:
        return len([q for q, v in booked.w.queries if "items(ids:" in q and v.get("cols") == [rd.AC["hash_key"]]])

    assert booked.get(1).json()["view"]["books"]["state"] == "ok" and key_reads() == 1
    def reads() -> int:  # every request to monday but the gate's own (the members of each board, read by a new instance)
        return len([q for q, _v in booked.w.queries if "subscribers" not in q])

    main.app.state.desk = None                                              # another instance, on the same database
    asked = reads()
    cold = booked.get(1).json()
    assert cold["refreshing"] is True and reads() == asked                  # at once: nothing read of monday
    assert (cold["view"]["books"]["state"], cold["view"]["books"]["reasons"]) == ("none", ["pending"])
    assert cold["view"]["books"]["said"] == db.REASONS["pending"] and not any(r.get("books") for r in cold["view"]["b2c"]["rows"])
    fresh = booked.get(1, "/api/desk?fresh=1").json()
    assert fresh["view"]["books"]["state"] == "ok" and key_reads() == 2
