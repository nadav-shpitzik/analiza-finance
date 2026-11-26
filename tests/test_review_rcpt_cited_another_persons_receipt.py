"""Hostile review of the receipts split (92021e0), LOW: a payment that cites another student's invoice-receipt is "הופקה
בחשבשבת", and the student whose receipt it is gets "לא זוהתה בחשבשבת" - both labels wrong, by one slip.

Two students of one course, one price, one week: נועה's payment cites 62702 (a slip - it is יואב's invoice-receipt, on
יואב's card); יואב's payment cites nothing. app/desk/books.py `receipt_in_books`:
- נועה: "cited" - A1 matches the number (one document, the sums tie, no name is read), and the branch asks nothing else.
  The chip says "הופקה בחשבשבת", its title "חשבונית מס/קבלה 62702 תקפה בחשבשבת, המספר שבאסמכתת חשבונית", and the legend
  promises "יש בחשבשבת חשבונית מס/קבלה תקפה עליו".
- יואב: FR-C2 never offers a document some row cites, so nothing is found: "לא זוהתה בחשבשבת".
The desk has what it needs to tell: the copy is the wide snapshot, with each document's card name, and the desk's own
read-back of a number it writes (app/recon/ref_fill.py `verify`) refuses exactly this (`name_differs`).

LOW: under the house law a cited number is the match (the rules' §5), and it takes a wrong number in the cell; but the label
then tells Chava to mark נועה's receipt sent - one that was never issued - and to look for יואב's, which was. Fails on
76f193a: נועה is laid issued by 62702. Synthetic fixtures."""
from __future__ import annotations

from app.desk import books as db
from tests.test_desk_receipts import AMOUNT, computed, current, pending
from tests.test_recon_ref_fill import doc

ROWS = [pending(31, "נועה ברק", ref="62702", day="2026-11-10"),    # cites יואב's invoice-receipt
        pending(32, "יואב שטרן", day="2026-11-10")]                # his own payment, uncited
DOCS = [doc(62702, total=AMOUNT, name="יואב שטרן", card="20002", day="2026-11-10")]


def test_another_students_receipt_is_not_this_payments():
    part, view = computed(ROWS, DOCS)
    rows = {r["id"]: r for r in db.merge(view, current(part, view))["b2c"]["rows"]}
    assert rows["32"]["receipt_books"]["issued"] is False          # יואב: his receipt is the one cited elsewhere
    said = rows["31"].get("receipt_books") or {}
    assert said.get("issued") is not True, said                     # נועה: not hers
