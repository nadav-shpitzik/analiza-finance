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
