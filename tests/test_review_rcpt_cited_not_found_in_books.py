"""Hostile review of the receipts split (92021e0), HIGH: a cited invoice-receipt the books answered "not found" is called
"הופקה בחשבשבת" - its title "חשבונית מס/קבלה N תקפה בחשבשבת" - though the books no longer hold it.

app/desk/books.py `receipt_in_books`, the "cited" branch, asks only that A1 did not refuse the number (one status-1 document,
an invoice or an invoice-receipt with a total) and that the copy's one document under it is an invoice-receipt. A document
that answered "not found" (the copy's `missing`, books_documents.missing_since) stays in A1's index - A1 refuses it at the
group, `document_missing`, never per number - so it passes. Every other rule of the desk refuses it as evidence: A1 gives no
date from it (`document_missing`), the desk's `books_receipt_unpaid` will not say "the money came in" by it
(`receipt_unpaid_facts`: "not answered 'not found'"), an uncited one lands in `place_of`'s "missing", FR-C2 never proposes
it. Only the new label says it is a valid receipt, and tells her the receipt is issued and only monday is behind ("נשאר לסמן
במאנדיי").

Fails on 76f193a: row 1 is {"number": "62701", "how": "cited"}, laid as issued with the title above, and counted issued in
the split. Synthetic fixtures (tests/test_desk_receipts.py's builders)."""
from __future__ import annotations

from app.desk import books as db
from app.recon import receipt_dates as a1
from tests.test_desk_receipts import AMOUNT, computed, current, pending
from tests.test_recon_ref_fill import doc

ROWS = [pending(1, "נועה ברק", ref="62701")]
DOCS = [doc(62701, total=AMOUNT, name="נועה ברק", card="20001", day="2026-11-10", missing=True)]


def test_a_cited_receipt_the_books_answered_not_found_is_not_issued():
    part, view = computed(ROWS, DOCS)
    # the premise: A1 itself refuses to match the row by it (the document answered "not found")
    assert a1.analyse(list(ROWS), DOCS)["rows"]["1"]["bucket"] == "document_missing"
    row = {r["id"]: r for r in db.merge(view, current(part, view))["b2c"]["rows"]}["1"]
    said = row.get("receipt_books") or {}
    assert said.get("issued") is not True and "תקפה בחשבשבת" not in said.get("title", ""), said
    assert db.receipts_split([row])["issued"] == 0
