"""D3 (Analiza-Monday backlog 0301): what the books say that monday does not, in the desk, read only - app/desk/books.py, and
what it needs of the books copy (app/books/store.py `desk_snapshot`, `not_ready`) and of A1 (app/recon/receipt_dates.py).

Each rule with its test (the rules' contract §0.1), each named by its rule: the marks from A1's buckets (FR-C1, FR-3, FR-4),
the documents no charge cites (FR-C4, FR-D1), the side of a card (FR-6), the era (spec §6.4.2, ADR-0168 §5), the law of §5
(a name is only ever a candidate), the gate (spec §6.4.1), and only a whole copy changing what is shown (spec §6.1.5).

Fixtures are synthetic and shaped like the incidents (the rules' contract §0.2): numbers, amounts and dates mirror the
records, names are invented, and no card carries an ID number."""
from __future__ import annotations

from datetime import date, datetime, timezone
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


def test_every_mark_is_a1s_bucket_and_every_other_bucket_gives_none():
    """The invariant of the marks: for the same rows and the same copy, a row is marked exactly when A1 put it in a bucket
    of MARK_OF_BUCKET (and `status_not_money_in` only when its group holds an invoice-receipt), with that bucket's mark."""
    docs = [doc(62500), doc(62501), doc(62600), doc(62600), doc(70100, doc_type=3), doc(50400, doc_type=1, total="-4000"),
            doc(63109, total="4000"), doc(62800, total="3347.46"), doc(62502), doc(50401, doc_type=1),
            doc(62990, day="2025-12-28"), doc(62700, missing=True), doc(62510, total="10000")]
    rows = [pay(1, "62500"), pay(2, "99999"), pay(3, "62600"), pay(4, "70100"), pay(5, "50400"), pay(6, "63109"),
            pay(7, "63109"), pay(8, "62800", paid="3350"), pay(9, "62502", st="0"), pay(10, "50401", st="0"),
            pay(11, "62990"), pay(12, "62700"), pay(13, ""), pay(14, "62,5O1"), pay(15, "62510", paid="4000"),
            pay(16, "62510", paid="6000"), pay(17, "62501", paid="")]
    part, view, reading = run(rows, docs)
    buckets = a1.analyse(reading["student_charge"]["rows"], docs)["rows"]
    got = {i: [m["kind"] for m in ms] for i, ms in part["marks"].items()}
    for item_id, entry in buckets.items():
        want = db.MARK_OF_BUCKET.get(entry["bucket"])
        if entry["bucket"] == "status_not_money_in":
            want = want if any(a1.role(d.get("doc_type")) == "invoice_receipt"
                               for n in entry["group"]["documents"] for d in a1.index_documents(docs)[n]) else None
        assert got.get(item_id, [None]) == [want], (item_id, entry["bucket"])
    assert part["buckets"] == {i: e["bucket"] for i, e in buckets.items()}
    # the buckets this set reaches, so the test is not vacuous
    seen = {e["bucket"] for e in buckets.values()}
    assert {"ref_not_in_books", "ref_ambiguous", "ref_not_an_invoice", "amount_mismatch", "rounding_band",
            "status_not_money_in", "invoice_needs_receipt", "out_of_window", "document_missing", "no_ref",
            "ref_unreadable", "filled_same", "amount_unreadable"} <= seen


def test_books_ref_invalid_names_each_number_and_why():
    """FR-C1, FR-3, FR-4: not found, held by two valid documents, or not an invoice (its type); the chip says each in a
    word, the row's details in a sentence. A draft's number is not a valid document (FR-2)."""
    docs = [doc(62600), doc(62600), doc(70100, doc_type=3), doc(80500, doc_type=31), doc(40505, doc_type=78),
            doc(50400, doc_type=1, total="-4000"), doc(62610, status=0)]
    rows = [pay(1, "99999"), pay(2, "62600"), pay(3, "70100"), pay(4, "80500, 40505"), pay(5, "50400"), pay(6, "62610")]
    part, _v, _r = run(rows, docs)
    head = "מספר החשבונית שבשורה לא נמצא בחשבשבת כמסמך תקף: "
    got = {int(i) % 100: ms[0] for i, ms in part["marks"].items()}
    assert {n: m["chip"] for n, m in got.items()} == {
        1: head + "99999 לא נמצא", 2: head + "62600 בשני מסמכים", 3: head + "70100 חשבונית זיכוי",
        4: head + "80500 קבלה; 40505 חשבון עסקה", 5: head + "50400 חשבונית מס בסכום שלילי", 6: head + "62610 לא נמצא"}
    assert got[2]["text"] == "מספר החשבונית שבשורה לא נמצא בחשבשבת כמסמך תקף: 62600: שני מסמכים תקפים בחשבשבת נושאים את המספר הזה."
    assert "70100: חשבונית זיכוי, ולא חשבונית מס או חשבונית מס/קבלה" in got[3]["text"]
    assert "50400: חשבונית מס בסכום שלילי, כלומר ביטול" in got[5]["text"]
    assert got[4]["documents"] == ["80500", "40505"]


def test_amount_differs_says_the_groups_two_sums_and_the_rounding_band_is_no_mark():
    """FR-C1 and the contract §0.3: past 1 ₪ a document and 0.1% is a mark, "בשורה X, בחשבשבת Y" (students with VAT); one
    document over two rows sums its rows (63109: 4,000 cited by two 4,000 rows); Haemek's 3,350 against 3,347.46 is the
    rounding band, shown to a person by A1 and no mark here."""
    docs = [doc(62700, total="3000"), doc(63109, total="4000"), doc(62800, total="3347.46")]
    rows = [pay(1, "62700", paid="4000"), pay(2, "63109"), pay(3, "63109"), pay(4, "62800", paid="3350")]
    part, _v, _r = run(rows, docs)
    by = {int(i): v for i, v in marks(part).items()}
    assert by[1] == [("books_amount_differs", "בשורה 4,000.00 ₪, בחשבשבת 3,000.00 ₪")]
    assert by[2] == by[3] == [("books_amount_differs", "ב-2 השורות 8,000.00 ₪, בחשבשבת 4,000.00 ₪")]
    assert 4 not in by
    text = part["marks"]["2"][0]["text"]
    assert "8,000.00 ₪" in text and "4,000.00 ₪" in text and "חשבונית מס/קבלה 63109" in text and "בשורה הזאת 4,000.00 ₪" in text


def test_an_invoice_receipt_on_a_row_whose_status_says_no_money_came_is_marked_the_case_of_23_11():
    """FR-L12: an invoice-receipt pays itself. On "לא שולם" (and every status that does not say the money came in) the
    books contradict the row: "בחשבשבת חשבונית מס/קבלה N, והסטטוס {status}". A tax invoice alone is a demand, so "לא שולם"
    beside it is no contradiction (A1: invoice_needs_receipt), and no mark."""
    docs = [doc(62502), doc(62503), doc(62504), doc(50401, doc_type=1)]
    rows = [pay(1, "62502", st="0"), pay(2, "62503", st="2"), pay(3, "62504", st="3"), pay(4, "50401", st="0")]
    part, _v, _r = run(rows, docs)
    by = {int(i): v for i, v in marks(part).items()}
    assert by == {1: [("books_receipt_unpaid", 'בחשבשבת חשבונית מס/קבלה 62502, והסטטוס "לא שולם"')],
                  2: [("books_receipt_unpaid", 'בחשבשבת חשבונית מס/קבלה 62503, והסטטוס "יש בעיה"')],
                  3: [("books_receipt_unpaid", 'בחשבשבת חשבונית מס/קבלה 62504, והסטטוס "מלגת אנליזה"')]}
    assert part["marks"]["1"][0]["text"] == ('בחשבשבת חשבונית מס/קבלה 62502 מ-03.11.2026 על 4,000.00 ₪ כולל מע"מ, כלומר הכסף '
                                             'התקבל, וסטטוס התשלום בשורה "לא שולם".')


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


def test_a_masked_or_empty_card_key_is_no_card_and_its_documents_are_judged_alone():
    """The copy cleans a card's key like any text (app/books/parse.py "key"), so a key of six digits or more - a student's
    card keyed by an ID number, a company's by its ח.פ - is stored "#", and an empty key is "": pooled by key they would be
    two cards of many people's documents, and one proforma on any of them would make every invoice-receipt there unclear.
    A key that is masked or empty names no card: each document is judged alone by its own type - an invoice-receipt a
    student's (FR-6's interim rule), a tax invoice unclear with its reason - and never an Account's, even if a key the desk
    read from monday was masked to "#" too."""
    docs = [doc(62406, card="#", name="דנה לוי"), doc(62407, card="#", name="משה כהן"),
            doc(40505, doc_type=78, card="#"),                                   # a proforma on another masked card
            doc(62408, card="", name="רות אבן"), doc(62409, card=None, name="יעל גל"),
            doc(40506, doc_type=78, card=""),                                    # a proforma with no key at all
            doc(50400, doc_type=1, card="AB-#", name="כרטיס מוסתר"), doc(50401, doc_type=1, card=None)]
    keys = {"#": ["חברה שהמפתח שלה הוסתר"]}                                      # as if one came through: it never matches
    part, _v, _r = run([], docs, keys=keys)
    assert sorted(e["number"] for e in part["students"]) == ["62406", "62407", "62408", "62409"]
    assert [(e["number"], e["why"]) for e in sorted(part["unclear"], key=lambda e: e["number"])] == [
        ("50400", "key_masked"), ("50401", "key_empty")]
    assert part["companies"] == [] and part["counts"] == {"students": 4, "unclear": 2, "outside": 2}
    assert part["unclear"][0]["why_text"] == db.UNCLEAR_WHY[part["unclear"][0]["why"]]
    assert db.card_sides(docs, keys) == {}                                       # no card at all among them
    # a key the desk read from monday and masked (`mask_ids`) names no Account's card either
    assert db.account_keys_of([{"name": "חברה", fd.AC["hash_key"]: "#"}, {"name": "חברה אחרת", fd.AC["hash_key"]: "10092"}]) \
        == {"10092": ["חברה אחרת"]}
    # beside them, a real card keeps its rule: its invoice-receipt with a proforma on the same card is unclear
    mixed = docs + [doc(62410, card="20005"), doc(40507, doc_type=78, card="20005")]
    part, _v, _r = run([], mixed, keys=keys)
    assert ("62410", "invoice_receipt_and_proforma") in [(e["number"], e["why"]) for e in part["unclear"]]
    assert len(part["students"]) == 4


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
    assert listed["62406"]["candidates"][1] == {"id": "1", "name": "דנה לוי", "state": "done", "status": "שולם",
                                                "date": "2026-11-03", "invoice": ""}
    assert part["marks"] == {}                                               # no row is marked by a name
    assert db.name_words("דנה לוי") == db.name_words("לוי   דנה") != db.name_words("דנה לוין")
    assert db.name_words("a 12 b3 #") == frozenset()


def test_a_common_name_shows_ten_candidates_and_how_many_more():
    rows = [pay(i, "", name="דנה לוי") for i in range(1, 14)]
    part, _v, _r = run(rows, [doc(62406)])
    e = part["students"][0]
    assert len(e["candidates"]) == db.CANDIDATES_MAX == 10 and e["candidates_more"] == 3


# ---- only a whole copy on a whole reading changes what is shown (spec §6.1.5) ------------------------------------------------
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


def holder(store, keys=None, clock=None) -> db.DeskBooks:
    b = db.DeskBooks(lambda _url: store, lambda: None, clock=clock or Tick())
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


def _merged_b2c() -> dict:
    st = main.app.state.desk
    return st.merged[2]["b2c"]


# ---- the Excel files: "לטיפול" carries the marks and its tab's list --------------------------------------------------------------
def _file(booked, user: int, tab: str, state: str = "tasks"):
    import io

    import openpyxl

    body = booked.get(user).json()
    link = booked.client.post("/api/desk/export-link", headers={"Authorization": "Bearer " + session_token(user)},
                              json={"tab": tab, "state": state, "taken": body["taken_at"]}).json()
    got = booked.client.get(link["url"])
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
