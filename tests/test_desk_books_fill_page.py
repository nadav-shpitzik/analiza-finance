"""D4 on the page (frontend/finance.js): "רישום מחשבשבת" - what the books propose for a Student Charge row (the server's
`books_fill`, app/desk/books.py), written from the browser as the person through `mondayWrite`, behind WRITABLE, held
identical to the server's `desk_writes`. Needs node; skipped without it.

The page proposes nothing of its own: each value is the server's, written only into a cell that is empty, and a row whose
cells are no longer what the desk read is skipped and said, never written over."""
from __future__ import annotations

import pytest

from app.desk import books as db
from app.desk import view as fd
from tests.test_desk_page import LOAD, NODE, node

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")
SC_BOARD = fd.STUDENT_CHARGE
NUMBER_COL, DATE_COL = fd.SC["invoice"], fd.SC["paid_date"]


def action() -> dict:
    return dict(fd.DESK_ACTIONS[db.FILL_ACTION], key=db.FILL_ACTION)


def plan(cells: dict, typed: dict) -> dict:
    """planAction of "רישום מחשבשבת" on a row as monday holds it now (`cells`, by column), with the books' proposal."""
    live = {"name": "x", "cells": {c: {"text": t, "index": None, "label": ""} for c, t in cells.items()}, "columns": {}}
    return node(LOAD + "console.log(JSON.stringify(F._plan(input.a, input.live, input.typed, '2026-11-26', {})));",
                {"a": action(), "live": live, "typed": typed})


def lines(p: dict) -> list:
    return [(x["title"], bool(x.get("same")), bool(x.get("kept")), x.get("to")) for x in p["lines"]]


def test_the_plan_writes_the_books_proposal_into_empty_cells_in_one_step():
    p = plan({NUMBER_COL: "", DATE_COL: ""}, {NUMBER_COL: "62601", DATE_COL: "2026-11-12"})
    assert p["steps"] == [{NUMBER_COL: "62601", DATE_COL: {"date": "2026-11-12"}}] and p["problems"] == []
    assert lines(p) == [("אסמכתת חשבונית", False, False, "62601"), ("תאריך תקבול", False, False, "2026-11-12")]


@pytest.mark.parametrize("cells, written, kept", [
    ({NUMBER_COL: "62601", DATE_COL: ""}, {DATE_COL: {"date": "2026-11-12"}}, "אסמכתת חשבונית"),       # the number's cell
    ({NUMBER_COL: "", DATE_COL: "2026-11-12"}, {NUMBER_COL: "62601"}, "תאריך תקבול"),                  # the date's cell
    ({NUMBER_COL: "70000", DATE_COL: ""}, {DATE_COL: {"date": "2026-11-12"}}, "אסמכתת חשבונית"),       # another number
])
def test_a_cell_that_is_not_empty_is_never_written_over(cells, written, kept):
    """`if_empty` on each of the two writes: a cell monday holds a value in now is kept, whatever it holds - the same
    value the books propose, or another (the batch skips such a row before it is planned: tests below)."""
    p = plan(cells, {NUMBER_COL: "62601", DATE_COL: "2026-11-12"})
    assert p["steps"] == [written]
    assert [x[0] for x in lines(p) if x[2]] == [kept]


def test_what_the_books_do_not_propose_is_not_written_and_a_date_is_never_today():
    """The date alone (a row that cites its number), or the number alone (a row whose date is typed): the other write is
    not in the plan at all - never today's date, as "חויב" writes one."""
    assert plan({NUMBER_COL: "62603", DATE_COL: ""}, {DATE_COL: "2026-11-03"})["steps"] == [{DATE_COL: {"date": "2026-11-03"}}]
    assert plan({NUMBER_COL: "", DATE_COL: "2026-11-11"}, {NUMBER_COL: "62602"})["steps"] == [{NUMBER_COL: "62602"}]
    nothing = plan({NUMBER_COL: "", DATE_COL: ""}, {})
    assert nothing["steps"] == [] and nothing["lines"] == []


def test_the_page_may_write_the_date_as_a_date_and_nothing_else_there():
    """WRITABLE gained "תאריך תקבול" as a date (held to desk_writes by tests/test_desk_parity_actions_js.py): a day is sent,
    anything else in that column is refused before monday is asked."""
    run = LOAD + """
const F2 = window.FinanceDesk, cases = input.cases, out = [];
(async () => {
  for (const v of cases) { out.push(await F2._write(input.board, "5101", v).then(() => "sent", (e) => e.code)); }
  console.log(JSON.stringify(out));
})();"""
    got = node(run, {"board": SC_BOARD, "cases": [{DATE_COL: {"date": "2026-11-12"}}, {DATE_COL: "2026-11-12"},
                                                   {DATE_COL: {"date": "12.11.2026"}},
                                                   {DATE_COL: {"date": "2026-11-12", "time": "10:00"}}]})
    # the right shape passes the allowlist and goes to monday - which this page, never mounted, has none of (no_scope)
    assert got == ["no_scope", "write_refused", "write_refused", "write_refused"]
    writable = node(LOAD + "console.log(JSON.stringify(F._writable()));", None)
    assert writable[SC_BOARD][DATE_COL] == "date" and writable == fd.desk_writes()
