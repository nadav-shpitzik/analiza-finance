"""Hostile review of the receipts split (92021e0), LOW: a payment that cites the receipt Chava issued since the last read
of the books is "לא זוהתה בחשבשבת" - "אין בחשבשבת מספר, שם וסכום שמתאימים לתקבול" - and counted in the queue's line as
"לא זוהתה קבלה בחשבשבת", while the same row's details say the number is only "עוד לא בהעתק של הספרים".

The books are copied once an hour. A number just past the end of its series, held by no document of the copy, is not yet in
the copy (app/desk/books.py `not_yet_read`): the desk gives no mark for it and says so in the row's details (`books_note`).
`receipt_in_books` does not know that state: the number is refused (`ref_not_in_books`), so not "cited"; the row cites a
number, so FR-C2 has nothing to find ("has_ref"); and it is "לא זוהתה" - which the commit calls "the real backlog". Issuing
a receipt and typing its number is exactly what she does with the queue, so for up to an hour after, every receipt she just
issued is counted back into the backlog, under a title that says Hashavshevet has no such number.

Fails on 76f193a: row 1 is laid {"issued": False, ...} (and the split counts it "not") beside the note. Synthetic fixtures."""
from __future__ import annotations

from app.desk import books as db
from app.desk import view as fd
from tests.test_desk_receipts import AMOUNT, computed, current, pending
from tests.test_recon_ref_fill import doc

ROWS = [pending(1, "נועה ברק", ref="62711")]                      # the receipt issued after the copy's last read
DOCS = [doc(n, total=AMOUNT, name="סטודנט/ית %d" % n, card=str(20000 + n), day="2026-11-01") for n in range(62701, 62711)]


def test_a_receipt_not_yet_in_the_copy_is_not_counted_as_none_identified():
    part, view = computed(ROWS, DOCS)
    row = {r["id"]: r for r in db.merge(view, current(part, view))["b2c"]["rows"]}["1"]
    assert row.get("books_note") == "62711: %s" % db.NOT_YET_SAID       # the desk's own words: not yet in the copy
    assert not row.get("books")                                        # and no mark for it
    said = row.get("receipt_books") or {}
    assert said.get("title") != fd.RECEIPT_UNSEEN_WHY, said
    assert db.receipts_split([row])["not"] == 0
