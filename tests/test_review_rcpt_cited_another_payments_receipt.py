"""Hostile review of the receipts split (92021e0), MEDIUM: a pending payment that cites ANOTHER payment's receipt is called
"הופקה בחשבשבת", while the same row carries the books' red "חשבשבת: סכום שונה" that says the receipt is not its own.

The case: one student's two payments of 2,000 ₪. The first (receipt sent) cites its invoice-receipt 62701 of 2,000 ₪; the
second, still "שליחת קבלה: טרם", cites 62701 too (the number copied - a duplicated item, a typing slip). A1 judges the two
rows over the one document together: 4,000 ₪ paid against a 2,000 ₪ document, `amount_mismatch` - no match, and the desk
marks both rows "חשבשבת: סכום שונה". But app/desk/books.py `receipt_in_books`'s "cited" branch reads only A1's per-number
refusals (`refused`), never A1's verdict on the group, so the second payment is "הופקה בחשבשבת" (title: "חשבונית מס/קבלה
62701 תקפה בחשבשבת, המספר שבאסמכתת חשבונית") and the queue's line counts it among "הקבלה כבר הופקה בחשבשבת: נשאר לסמן
במאנדיי". Acted on, the second payment's receipt is marked sent and is never issued.

The same holds for a single row whose amount and cited invoice-receipt differ past the rounding band (second test): the
desk's own mark says "בשורה X, בחשבשבת Y", and the label says the receipt for it is issued.

Fails on 76f193a: both rows are laid {"issued": True, "how": "cited"}. Synthetic fixtures."""
from __future__ import annotations

from app.desk import books as db
from app.desk import view as fd
from tests.test_desk_receipts import computed, current, pending
from tests.test_recon_ref_fill import doc


def _rows(rows, docs) -> dict:
    part, view = computed(rows, docs)
    return {r["id"]: r for r in db.merge(view, current(part, view))["b2c"]["rows"]}


def test_a_payment_citing_another_payments_receipt_is_not_issued():
    first = pending(21, "נועה ברק", ref="62701", receipt="1")      # its receipt went out: "הושלם"
    second = pending(22, "נועה ברק", ref="62701")                  # the number copied: still waiting
    for r in (first, second):
        r[fd.SC["paid"]] = "2000"
    rows = _rows([first, second], [doc(62701, total="2000", name="נועה ברק", card="20001", day="2026-11-10")])
    assert rows["21"]["state"] == "done" and rows["22"]["state"] == "receipt_pending"
    # the desk's own mark on the waiting payment: its group's sums differ, 4,000 against 2,000
    assert "books_amount_differs" in rows["22"]["flags"]
    said = rows["22"].get("receipt_books") or {}
    assert said.get("issued") is not True, said
    assert db.receipts_split([rows["22"]])["issued"] == 0


def test_a_cited_receipt_of_another_amount_is_not_this_payments_receipt():
    rows = _rows([pending(23, "יואב שטרן", ref="62702")],                       # 3,517 ₪ paid
                 [doc(62702, total="1000", name="יואב שטרן", card="20002", day="2026-11-10")])
    assert "books_amount_differs" in rows["23"]["flags"]
    said = rows["23"].get("receipt_books") or {}
    assert said.get("issued") is not True, said
