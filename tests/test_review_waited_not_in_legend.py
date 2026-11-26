"""Hostile review of the pending receipts' queue (304eccd, 9d4d5b8), LOW: "ממתין N ימים" is drawn beside the date in every
list and in the details, and the legend ("מקרא") neither has it nor says what it counts from.

The norm since QA 2.2 (11.11): every word the screen draws is monday's or the legend's. The legend's one entry on relative
days, "היום · בעוד · לפני", says they are said "ליד תאריך בפרטים" (in the details) and counts to or from that date. The
queue now writes a count of days beside the date in the LIST, in every list a pending receipt is in ("לטיפול" and "הכל"
too, whose sentences say nothing of it), with a word of its own, "ממתין", counted from the payment's date or, where none
was typed, from the day the row was made. Only the queue's own sentence mentions it ("וליד התאריך כמה ימים הוא ממתין").

Fails on 76f193a: no legend term of the students' tab holds "ממתין", and the relative days' entry still says the details
only. Synthetic view (tests/desk_page_view.py)."""
from __future__ import annotations

import re

from app.desk import view as fd
from tests.desk_page_view import answer
from tests.test_desk_page import drive


def test_the_waited_days_are_in_the_legend():
    got = drive(answer(), [{"show": {"tab": "b2c", "sc": {"state": "", "period": "", "open": {}}}}])
    everything = got["steps"][0]["html"]
    assert re.search(r'class="fin-waited[^"]*">ממתין ', everything)   # drawn in the list "הכל"
    terms = [term for term, _text in fd.LEGEND["b2c"]]
    assert any("ממתין" in term for term in terms), terms
