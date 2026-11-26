"""Hostile review of the receipts split (ee2fdf9, 92021e0), HIGH: a proposal Chava withdrew with "לא להציע" still calls
the row's receipt "הופקה בחשבשבת", by the very document she rejected.

app/desk/books.py `compute` judges each pending receipt from FR-C2's analysis (`receipt_in_books(..., c2.get(item_id), ...)`)
and keeps the result in the part's `receipts`. R5's `hide` (app/desk/dismissals.py) takes a dismissed proposal out of
`fills`, and its document's candidate line then says "הוצע לרשום את המסמך בשורה הזו, וסומן 'לא להציע'" - but `hide` never
touches `receipts`, and `merge` lays them as they were. So on the same screen:
- the students' list says the proposal to write 62702 in this row was rejected,
- the row's chip says "הופקה בחשבשבת", its title "חשבונית מס/קבלה 62702 תקפה בחשבשבת, אחת בלבד בשם ובסכום של התקבול...",
- the queue's line counts it among "זוהתה קבלה בחשבשבת: יש לעדכן את סטטוס שליחת הקבלה".
"לא להציע" is R5's word for a wrong proposal ("a wrong proposal must stop showing too"): the match by name and amount that the
person refused is exactly the evidence "found" rests on. She is told to mark a receipt sent on a payment whose receipt the
desk has no evidence of any more.

Fails on 76f193a: row 2 still carries {"issued": True, "number": "62702", "how": "found"} and the split counts it issued.
Synthetic fixtures (tests/test_desk_receipts.py's)."""
from __future__ import annotations

from app.desk import books as db
from app.desk import dismissals as dz
from app.desk import view as fd
from tests.test_desk_receipts import computed, current

AT = "2026-11-26T09:00:00+00:00"


def test_a_withdrawn_proposal_is_no_receipt_issued():
    part, view = computed()
    fill = part["fills"]["2"]
    assert fill["values"]["number"] == "62702" and part["receipts"]["2"]["how"] == "found"   # the premise
    live = [{"id": 1, "kind": "books_ref_fill", "subject": "2", "evidence": fill["mark"]["evidence"], "by": 7, "at": AT}]
    shown = dz.hide(part, live)
    merged = db.merge(view, current(shown, view))
    row = {r["id"]: r for r in merged["b2c"]["rows"]}["2"]
    # the proposal is gone, and the document's line says it was rejected for this very row
    assert "books_fill" not in row and "books_ref_fill" not in row["flags"]
    entry = next(e for e in merged["b2c"]["books"]["documents"] if e["number"] == "62702")
    assert any(c["id"] == "2" and c.get("why_key") == "withdrawn" for c in entry["candidates"])
    # ... yet the row still says its receipt was issued, by that document
    said = row.get("receipt_books") or {}
    assert not (said.get("issued") is True and said.get("number") == "62702"), said
    queue = fd.filter_b2c(merged["b2c"], "receipt_pending", "", "")
    assert db.receipts_split([r for r in queue if r["id"] == "2"])["issued"] == 0
