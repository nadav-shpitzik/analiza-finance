"""Hostile review of the receipts split (92021e0), LOW: a payment of November 2026 that cites an invoice-receipt of November
2025 is "הופקה בחשבשבת".

app/desk/books.py `receipt_in_books`'s "cited" branch has no window and no nearness at all: any number A1 did not refuse per
number is the receipt. A1 itself never matches a row by a document outside Student Charge's window (FR-7, its bucket
`out_of_window`: "never a gap, whatever the sums"), the desk's `books_receipt_unpaid` refuses one too (`receipt_unpaid_facts`:
"each dated in Student Charge's window"), and the other branch of this very function, "found", asks a document within 30 days
of the payment. A number a year away is an earlier payment's receipt (a returning student) or a slip - the label still tells
her this payment's receipt is issued and only monday is behind.

LOW: it needs a wrong number in the cell, and a payment of late December 2025 receipted in its own days would rightly be
"cited" (so a fix should ask nearness, as "found" does, rather than the year alone). Fails on 76f193a: row 1 is laid
{"issued": True, "how": "cited"}. Synthetic fixtures."""
from __future__ import annotations

from app.desk import books as db
from app.recon import receipt_dates as a1
from tests.test_desk_receipts import AMOUNT, computed, current, pending
from tests.test_recon_ref_fill import doc

ROWS = [pending(1, "נועה ברק", ref="62701", day="2026-11-10")]            # paid 10.11.2026
DOCS = [doc(62701, total=AMOUNT, name="נועה ברק", card="20001", day="2025-11-10")]   # an invoice-receipt of 10.11.2025


def test_a_receipt_a_year_before_the_payment_is_not_its_receipt():
    part, view = computed(ROWS, DOCS)
    assert a1.analyse(list(ROWS), DOCS)["rows"]["1"]["bucket"] == "out_of_window"      # A1 will not match by it
    row = {r["id"]: r for r in db.merge(view, current(part, view))["b2c"]["rows"]}["1"]
    assert row["state"] == "receipt_pending"
    said = row.get("receipt_books") or {}
    assert said.get("issued") is not True, said
