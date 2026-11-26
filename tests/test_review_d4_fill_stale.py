"""Hostile review of D4 "רישום מחשבשבת" (frontend/finance.js startBatch / settleFills / confirmBatch): the page writes a
proposal the books no longer stand behind.

The page re-reads, before each write, only the two cells it writes (`stepWrites(a).map(w => w.column)`: "אסמכתת חשבונית"
and "תאריך תקבול") and compares them to the reading. Everything the proposal rests on - the row's "סטטוס תשלום" (FR-C2
condition 1, MONEY_IN), its "סכום ששולם" (3), its name (4), its day (5), and that no OTHER row cites or holds the document
(6) - is never looked at again. And a batch keeps the row objects (and their `books_fill`) of the reading it was pressed on:
`index()` replaces ROWS when a newer reading lands (a refresh press, or `askAgain` when the view takes the focus back from
monday's item card), but BATCH, in its "confirm" phase, is left as it was.

The legend tells Chava the opposite (view.py LEGEND, the entry of "רישום מחשבשבת · הצעה · לרשום את כל ההצעות"):
"שורה שהשתנתה במאנדיי מאז הקריאה לא נכתבת".

Copy into tests/ and run from the worktree (needs node): python -m pytest -q tests/test_review_d4_fill_stale.py
"""
from __future__ import annotations

import pytest

from app.desk import view as fd
from tests.test_desk_books_fill import ROWS
from tests.test_desk_books_fill_page import (DATE_COL, NUMBER_COL, SC_BOARD, TASKS, body, fill_view, monday_of, run,
                                             writes_of)
from tests.test_desk_page import NODE

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")
LATER = "2026-11-26T10:05:00+00:00"


def test_a_proposal_a_newer_reading_withdrew_is_still_written_by_the_open_confirmation():
    """HIGH (a wrong write). Chava presses "לרשום את כל ההצעות"; the confirmation lists row 1 (62601 and 12.11.26). Before
    she presses "שמירה" a newer reading lands - here the refresh button; in life `askAgain` when monday's item card closes
    over the view - in which row 1's "סכום ששולם" was corrected (3517 -> 3600), so FR-C2 no longer proposes 62601 for it
    (`amount_differs`): the newer view on her screen has no proposal on row 1. The open confirmation still writes 62601
    and its date into row 1: its two cells are as they were, and nothing else is read again."""
    corrected = [dict(r, **{fd.SC["paid"]: "3600"}) if r["id"] == "1" else r for r in ROWS]
    newer, merged = fill_view(corrected)
    assert "books_fill" not in {r["id"]: r for r in merged["b2c"]["rows"]}["1"]      # the server withdrew it
    got = run([{"show": TASKS}, {"click": [{"booksAll": "1"}]}, {"click": [{"refresh": "1"}]},
               {"click": [{"batchGo": "1"}]}], refreshed=newer)
    shown = [r for r in got["steps"][2].get("batch", {}).get("items", [])]
    assert shown, "the confirmation is still on the screen after the newer reading landed"
    written = {w[1]: w[2] for w in writes_of(got)}
    assert "1" not in written, ("row 1 was written %s although the reading on the screen no longer proposes it"
                                % written.get("1"))


def test_the_same_when_the_newer_reading_lands_because_monday_s_item_card_closed_over_the_view():
    """The everyday path of the same HIGH: Chava opens the confirmation, opens row 1 in monday's item card (and fixes its
    amount there), closes the card; the view takes the focus back and `askAgain` reads the boards again - no click of hers.
    The confirmation survives the newer reading and "שמירה" writes the proposal that reading withdrew."""
    from tests.test_desk_books_fill_page import WRITES
    from tests.test_desk_page import drive

    corrected = [dict(r, **{fd.SC["paid"]: "3600"}) if r["id"] == "1" else r for r in ROWS]
    newer, _m = fill_view(corrected)
    older, _m2 = fill_view()
    got = drive(steps=[{"show": TASKS}, {"click": [{"booksAll": "1"}]}, {"back": [60000, "focus"]},
                       {"click": [{"batchGo": "1"}]}], monday=monday_of(), context=WRITES, clock=True,
                answers={"GET /api/desk": [{"body": body(older)}],
                         "POST /api/desk/refresh": [{"body": body(newer, LATER)}]})
    assert ["/api/desk/refresh", None] in got["posts"], "the return to the view read the boards again"
    written = {w[1]: w[2] for w in writes_of(got)}
    assert "1" not in written, "row 1 written %s after a newer reading withdrew its proposal" % written.get("1")


def test_a_row_whose_status_changed_since_the_reading_is_still_written():
    """MEDIUM. Row 2 (number 62602 proposed) is marked "יש בעיה" in monday after the reading - the payment bounced. FR-C2
    proposes only to a row whose status says the money came in, and the legend promises that a row changed in monday since
    the reading is not written. The batch reads only the two cells it writes, finds them empty, and writes 62602."""
    monday = monday_of()
    monday["boards"][SC_BOARD]["2"][fd.SC["status"]] = {"text": "יש בעיה", "index": 2, "label": "יש בעיה"}
    monday["boards"][SC_BOARD]["2"][fd.SC["paid"]] = "3517"
    got = run([{"show": TASKS}, {"click": [{"booksFill": "sc:2", "booksFrom": "list"}]}, {"click": [{"batchGo": "1"}]}],
              monday=monday)
    asked = {c for s in got["sent"] if s["kind"] == "query" for c in s["variables"].get("c", [])}
    assert writes_of(got) == [], ("62602 written into a row whose status is now 'יש בעיה'; the page asked monday only "
                                  "for %s" % sorted(asked))


def test_a_row_whose_amount_changed_since_the_reading_is_still_written():
    """MEDIUM. The same with "סכום ששולם": row 2's amount corrected in monday to 4,000 after the reading; the document is
    3,517 (FR-C2 condition 3, within 1 ₪). The page never reads the amount again and writes 62602."""
    monday = monday_of()
    monday["boards"][SC_BOARD]["2"][fd.SC["paid"]] = "4000"
    got = run([{"show": TASKS}, {"click": [{"booksFill": "sc:2", "booksFrom": "list"}]}, {"click": [{"batchGo": "1"}]}],
              monday=monday)
    assert writes_of(got) == [], "62602 (a 3,517 document) written into a row whose amount is now 4,000"


def test_a_document_another_row_cites_since_the_reading_is_still_written_again():
    """MEDIUM. FR-C2 condition 6: a document is proposed only when no other row cites or holds it. After the reading,
    someone types 62601 into another Student Charge row (row 12, the student's duplicate). The batch for row 1 re-reads
    only row 1's two cells, finds them empty, and writes 62601 a second time: one document cited by two rows."""
    monday = monday_of()
    monday["boards"][SC_BOARD]["12"][NUMBER_COL] = "62601"
    got = run([{"show": TASKS}, {"click": [{"booksFill": "sc:1", "booksFrom": "list"}]}, {"click": [{"batchGo": "1"}]}],
              monday=monday)
    cited = [w for w in writes_of(got) if w[2].get(NUMBER_COL) == "62601"]
    assert cited == [], "62601 written into row 1 while row 12 already cites it in monday"
    assert DATE_COL                                                                     # (the date column, for symmetry)


def test_the_legend_promises_what_the_page_does_not_check():
    """The words Chava reads: a row changed in monday since the reading is not written."""
    legend = " ".join(x for _t, x in fd.LEGEND["b2c"] if "רישום מחשבשבת" in _t)
    assert "שורה שהשתנתה במאנדיי מאז הקריאה לא נכתבת" in legend
    # ...and the page reads two columns of that row before writing: the promise covers "סטטוס תשלום", "סכום ששולם", the
    # name and every other row, which it never reads (see the three tests above)
    got = run([{"show": TASKS}, {"click": [{"booksFill": "sc:2", "booksFrom": "list"}]}])
    read = got["sent"][0]["variables"]["c"]
    assert set(read) >= {fd.SC["status"], fd.SC["paid"]}, "the batch reads only %s before writing" % read
    assert body                                                                         # (imported for the harness)
