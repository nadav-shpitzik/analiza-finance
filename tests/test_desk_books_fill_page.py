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


# ---- the page drawn from the server's own answer (books.merge, then visible_part, as GET /api/desk) -------------------------
import copy as _copy  # noqa: E402
import html as h  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402

from app.desk import usage  # noqa: E402
from app.desk.view import json_safe  # noqa: E402
from tests.test_desk_books_fill import B2B, DOCS, ROWS, part_of, pay  # noqa: E402
from tests.test_recon_ref_fill import doc  # noqa: E402
from tests.test_desk_books_page import cell  # noqa: E402
from tests.test_desk_page import drive  # noqa: E402
from tests.test_desk_parity_js import CONTROLS, MONDAY_TITLES, _labels, _words_of  # noqa: E402

TAKEN, LATER = "2026-11-26T10:00:01+00:00", "2026-11-26T10:00:09+00:00"
AS_OF = "2026-11-26T08:00:00+00:00"
WRITES = {"permissions": {"approvedScopes": ["boards:read", "boards:write"]}}
TASKS = {"tab": "b2c", "sc": {"state": "tasks", "period": "", "open": {}}}
ALL = {"tab": "b2c", "sc": {"state": "", "period": "", "open": {}}}


def fill_view(rows=ROWS, docs=DOCS, b2b=B2B, state: str = "ok", b2c: bool = True, b2b_tab: bool = True) -> tuple:
    """(the view the server sends this reader, the whole merged view) for these rows and this copy."""
    part, view, _r = part_of(rows, docs, b2b)
    part.update(state=state, reasons=[], as_of=AS_OF if state in ("ok", "held") else None, key="k-%s" % state)
    merged = db.merge(view, part)
    return json_safe(fd.visible_part(merged, b2c, b2b_tab)), merged


def body(view: dict, taken: str = TAKEN) -> dict:
    return {"view": view, "taken_at": taken, "age_seconds": 0, "refreshing": False, "stale": False, "last_attempt": None}


def draw(view: dict, cases: list) -> list:
    return node(LOAD + "console.log(JSON.stringify(input.cases.map(c => F._render(input.body, c))));",
                {"body": body(view), "cases": cases})


STATUS_COL, PAID_COL = fd.SC["status"], fd.SC["paid"]


def status_of(r: dict):
    """A row's "סטטוס תשלום" as monday answers it: its label and index, or an empty cell."""
    idx = r.get(STATUS_COL + "#") or ""
    return {"text": r[STATUS_COL], "index": int(idx), "label": r[STATUS_COL]} if idx else ""


def monday_of(rows=ROWS, **cells) -> dict:
    """monday as the person reads it now: each row's two cells as the desk read them, and what a proposal rests on (D4's
    review: "סטטוס תשלום" and "סכום ששולם", read with them), then `cells` (by item id) over them."""
    board = {r["id"]: {"__name": r["name"], NUMBER_COL: r[NUMBER_COL], DATE_COL: r[DATE_COL], STATUS_COL: status_of(r),
                       PAID_COL: r[PAID_COL]} for r in rows}
    for rid, c in cells.items():
        board[rid].update(c)
    return {"boards": {SC_BOARD: board}}


def after_view(written: dict) -> dict:
    """The reading after the writes (by item id, {column: value}): the rows as monday then holds them, judged again."""
    rows = [dict(r, **written.get(r["id"], {})) for r in ROWS]
    return fill_view(rows)[0]


def run(steps, monday=None, view=None, refreshed=None, hold=False) -> dict:
    v = view or fill_view()[0]
    refresh = {"body": body(refreshed or v, LATER)}
    if hold:
        refresh["hold"] = True
    return drive(steps=steps, monday=monday or monday_of(), context=WRITES,
                 answers={"GET /api/desk": [{"body": body(v)}], "POST /api/desk/refresh": [refresh]})


def writes_of(got: dict) -> list:
    return [(s["variables"]["b"], s["variables"]["i"], json.loads(s["variables"]["v"])) for s in got["sent"]
            if s["kind"] == "mutation"]


def books_part(html: str) -> str:
    part = html[html.index('data-books="b2c"'):]
    return part[:part.index('data-books="unclear"')] if 'data-books="unclear"' in part else part


def test_the_list_says_each_proposal_with_its_button_and_offers_every_one_above_it():
    """"מסמכים בחשבשבת בלי חיוב במאנדיי": under a proposed document's candidates, "הצעה: לרשום בשורה של <the row's name>"
    and the button "רישום מחשבשבת"; a document with no proposal, none; above the list, "לרשום את כל ההצעות (N)" with every
    proposal - the documents' three and the date alone of a row that cites its number, said under the list."""
    view, _m = fill_view()
    part = books_part(draw(view, [TASKS])[0])
    rows = {m.group(1): m.group(0) for m in re.finditer(r'<tr><td class="b-number"><bdi dir="ltr">(\d+)</bdi>.*?</tr>', part, flags=re.S)}
    assert set(rows) == {"62601", "62602", "62604", "62606", "62613"}
    for number, (rid, name) in {"62601": ("1", "נועה ברק"), "62602": ("2", "יואב שטרן"),
                                "62604": ("4", "עידו רז | השלמת תשלום")}.items():
        # R5: and "לא להציע" beside its button, naming the row's proposal
        assert ('<div class="fin-fill">הצעה: לרשום בשורה של %s <button type="button" class="fin-btn fin-fill-btn" '
                'data-books-fill="sc:%s" data-books-from="list">רישום מחשבשבת</button> <button type="button" '
                'class="fin-linkbtn fin-nag" data-nag="books_ref_fill:%s" data-nag-from="list">לא להציע</button></div>'
                % (h.escape(name), rid, rid)) in rows[number]
    assert "fin-fill" not in rows["62606"] + rows["62613"]
    assert 'data-books-all="1">לרשום את כל ההצעות <span class="fin-subtle">(4)</span></button>' in part
    assert part.index('data-books-all="1"') < part.index("<table")                     # above the list
    assert ('<p class="fin-listnote">ועוד הצעה אחת לתאריך תקבול בלבד, בתקבול שכבר יש בו אסמכתת חשבונית: היא ליד השורה '
            'שלו, ו&quot;לרשום את כל ההצעות&quot; כותב גם אותה.</p>') in part
    # a proposal is a candidate's, never called a match
    assert not re.search("התאמה|מותאם|קושר", h.unescape(part[part.index("<tbody>"):]))


def test_a_proposed_row_carries_its_chip_its_sentence_and_its_button_and_never_joins_tasks():
    """On the row, under its state and among its chips: "בחשבשבת: 62601", or "תאריך מחשבשבת: 03.11.2026" for the date
    alone - not red, a proposal is no contradiction; in its details the server's sentence and "רישום מחשבשבת". A proposal
    moves no row: "לטיפול" holds what it held."""
    view, merged = fill_view()
    every, tasks, details = draw(view, [ALL, TASKS, {"tab": "b2c", "sc": {"state": "", "period": "", "open": {"1": True, "3": True}}}])
    assert cell(every, "1", "state").endswith('<div class="fin-chips fin-books-chips"><span class="fin-chip">בחשבשבת: 62601'
                                              '</span></div>')
    assert '<span class="fin-chip">תאריך מחשבשבת: 03.11.2026</span>' in cell(every, "3", "state")
    assert "fin-books-chips" not in cell(every, "12", "state")                           # its date is filled: nothing
    ids = re.findall(r'<tr class="fin-rowbtn[^"]*" data-row="([^"]+)"', tasks)
    assert sorted(ids) == sorted(r["id"] for r in fd.filter_b2c(merged["b2c"], "tasks", "", "")) and "1" not in ids
    for rid in ("1", "3"):
        detail = details[details.index('<tr class="fin-detail">', details.index('data-row="%s"' % rid)):]
        detail = detail[:detail.index("</td></tr>")]
        mark = {r["id"]: r for r in merged["b2c"]["rows"]}[rid]["books"][0]
        # the server's sentence, quietly under the actions - a proposal is no warning
        # R5: and "לא להציע" beside it
        assert ('<p class="fin-dx-books fin-fill-said">%s <button type="button" class="fin-linkbtn fin-nag" data-nag="%s:%s" '
                'data-nag-from="detail">לא להציע</button></p>' % (h.escape(mark["text"]), mark["kind"], rid)) in detail
        assert "fin-dx-warn" not in detail or h.escape(mark["text"]) not in detail[detail.index("fin-dx-warn"):]
        assert ('<button type="button" class="fin-btn" data-books-fill="sc:%s" data-books-from="detail">רישום מחשבשבת'
                '</button>' % rid) in detail


def test_every_word_the_proposals_draw_is_mondays_title_or_in_the_legend():
    view, _m = fill_view()
    opened = {"tab": "b2c", "sc": {"state": "", "period": "", "open": {r["id"]: True for r in ROWS}}}
    known = fd.legend_terms("b2c") | MONDAY_TITLES["b2c"] | CONTROLS
    chips = [re.compile("^" + re.escape(fd.flag_legend_term(t)).replace(re.escape("..."), ".+") + "$")
             for _k, t, _x in fd.LEGEND_FLAGS["b2c"]]
    for html in draw(view, [ALL, TASKS, opened]):
        missing = [x for x in _labels(html) if x not in known and not re.fullmatch(r"ועוד \d+", x)
                   and not any(p.match(x) for p in chips) and not all(w in known for w in _words_of(x))]
        assert not missing, missing
    assert {fd.BOOKS_FILL_BUTTON, fd.BOOKS_PROPOSAL, fd.BOOKS_FILL_ALL} <= fd.legend_terms("b2c")


def test_a_proposal_from_the_list_is_one_confirmation_one_write_of_exactly_what_the_books_propose():
    """The document's button: the row read live as the person (both cells, one request), one confirmation of what will be
    written - floating in the books' list, beside the button - the row read again right before its write, one write of the
    number and the date, checked against monday's answer; the report, and one refresh."""
    got = run([{"show": TASKS}, {"click": [{"booksFill": "sc:1", "booksFrom": "list"}]}, {"click": [{"batchGo": "1"}]}],
              refreshed=after_view({"1": {NUMBER_COL: "62601", DATE_COL: "2026-11-12"}}))
    shown, plan, done = got["steps"]
    assert shown["sent"] == 0 and plan["batch"]["phase"] == "confirm"
    assert plan["batch"]["items"] == [{"key": "sc:1", "verdict": "write", "why": "", "result": None}]
    page = h.unescape(plan["html"])
    assert 'ייכתב במאנדיי בשורה "נועה ברק", בשמך:' in page
    assert ('<li><b>נועה ברק</b> · אסמכתת חשבונית: עכשיו ריק, ייכתב "62601" · תאריך תקבול: עכשיו ריק, ייכתב 12.11.26</li>'
            in page)
    assert DESK_AFTER in page and page.index('data-books="b2c"') < page.index("fin-pop")   # in the books' list
    # D4's review: the row is read with what its proposal rests on ("סטטוס תשלום", "סכום ששולם"), both times, and right
    # before the number is written, the rows that already cite it are asked for - one read, as the person
    first, again, cited = [s for s in got["sent"] if s["kind"] == "query"]
    assert first["variables"] == {"i": ["1"], "c": [NUMBER_COL, DATE_COL, STATUS_COL, PAID_COL], "b": [SC_BOARD]}
    assert again["variables"] == first["variables"]
    assert cited["variables"] == {"b": SC_BOARD, "c": NUMBER_COL, "v": ["62601"]}
    assert "items_page_by_column_values(" in cited["query"] and cited["apiVersion"] == "2026-07"
    assert [s["kind"] for s in got["sent"]] == ["query", "query", "query", "mutation"]
    assert writes_of(got) == [(SC_BOARD, "1", {NUMBER_COL: "62601", DATE_COL: {"date": "2026-11-12"}})]
    assert done["batch"]["phase"] == "done" and done["batch"]["items"][0]["result"] == "written"
    assert [p for p in got["posts"] if p[0] == "/api/desk/refresh"] == [["/api/desk/refresh", None]]
    assert "נכתב במאנדיי בשמך בשורה אחת. המסך מתעדכן." in got["toasts"]
    # the reading after it: the document left the list, and the row cites it
    after = books_part(done["html"])
    table = after[after.index("<tbody>"):after.index("</tbody>")]
    assert "62601" not in table and "62602" in table and "(3)" in after


DESK_AFTER = fd.DESK_ACTIONS["books_fill"]["after"]


def test_every_proposal_at_once_writes_exactly_the_proposals_each_its_own():
    """"לרשום את כל ההצעות": one request reads the four rows, one confirmation lists each row's own writes, and each row is
    written exactly what the books propose for it - the number and the date, the number alone (its date typed), the date
    alone (it cites its number) - in the list's order, then the date alone; nothing else is sent."""
    got = run([{"show": TASKS}, {"click": [{"booksAll": "1"}]}, {"click": [{"batchGo": "1"}]}, {"hide": True}])
    plan, done = got["steps"][1], got["steps"][2]
    assert [(i["key"], i["verdict"]) for i in plan["batch"]["items"]] == [
        ("sc:4", "write"), ("sc:2", "write"), ("sc:1", "write"), ("sc:3", "write")]
    assert got["sent"][0]["variables"] == {"i": ["4", "2", "1", "3"], "c": [NUMBER_COL, DATE_COL, STATUS_COL, PAID_COL],
                                           "b": [SC_BOARD]}
    page = h.unescape(plan["html"])
    assert "ייכתב במאנדיי בשמך, ב-4 שורות:" in page
    assert '<li><b>יואב שטרן</b> · אסמכתת חשבונית: עכשיו ריק, ייכתב "62602"</li>' in page
    assert '<li><b>מאיה גולן</b> · תאריך תקבול: עכשיו ריק, ייכתב 03.11.26</li>' in page
    assert writes_of(got) == [(SC_BOARD, "4", {NUMBER_COL: "62604", DATE_COL: {"date": "2026-11-18"}}),
                              (SC_BOARD, "2", {NUMBER_COL: "62602"}),
                              (SC_BOARD, "1", {NUMBER_COL: "62601", DATE_COL: {"date": "2026-11-12"}}),
                              (SC_BOARD, "3", {DATE_COL: {"date": "2026-11-03"}})]
    report = h.unescape(done["html"])
    assert "נכתב במאנדיי בשמך ב-4 שורות. המסך מתעדכן." in report and "יעברו לשיבוץ" not in report
    assert '<li><b>מאיה גולן</b> · תאריך תקבול 03.11.26</li>' in report
    assert '<li><b>עידו רז | השלמת תשלום</b> · אסמכתת חשבונית "62604", תאריך תקבול 18.11.26</li>' in report
    # its usage: the press and its batch's life, closed words and counts only, each passing the server's check
    events = [e for b in got["usage"] for e in b["body"]["events"]]
    fills = [e["d"] for e in events if e["k"] == "books_fill"]
    acts = [e["d"]["phase"] for e in events if e["k"] == "act" and e["d"]["action"] == "books_fill"]
    assert fills == [{"from": "all", "rows": 4}] and acts == ["batch_start", "batch_shown", "batch_confirm", "batch_done"]
    kept, refused = usage.clean_batch(dict(got["usage"][0]["body"], events=events), 7)
    assert refused == 0 and len(kept) == len(events)


@pytest.mark.parametrize("now, verdicts", [
    # another number typed since the reading: the row is not written, its date neither
    ({"1": {NUMBER_COL: "70000"}}, {"sc:1": 'השתנתה במאנדיי מאז הקריאה, ועכשיו אסמכתת חשבונית "70000"'}),
    # a date typed since: the number rests on the row's day, so neither is written
    ({"1": {DATE_COL: "2026-11-20"}}, {"sc:1": "השתנתה במאנדיי מאז הקריאה, ועכשיו תאריך תקבול 20.11.26"}),
    # the number the date alone rests on changed: the date is never written
    ({"3": {NUMBER_COL: "62699"}}, {"sc:3": 'השתנתה במאנדיי מאז הקריאה, ועכשיו אסמכתת חשבונית "62699"'}),
    # a row of the batch emptied of its typed date: not what the desk read
    ({"2": {DATE_COL: ""}}, {"sc:2": "השתנתה במאנדיי מאז הקריאה, ועכשיו תאריך תקבול ריק"}),
])
def test_a_row_not_as_the_desk_read_it_is_skipped_and_said_and_never_written_over(now, verdicts):
    got = run([{"show": TASKS}, {"click": [{"booksAll": "1"}]}, {"click": [{"batchGo": "1"}]}], monday=monday_of(**now))
    plan = got["steps"][1]
    skipped = {i["key"]: i["why"] for i in plan["batch"]["items"] if i["verdict"] == "skip"}
    assert skipped == verdicts
    assert not [w for w in writes_of(got) if "sc:" + w[1] in verdicts]
    assert len(writes_of(got)) == 3
    page = h.unescape(plan["html"])
    assert "<p>לא ייכתב:</p>" in page and list(verdicts.values())[0] in page


@pytest.mark.parametrize("cells, written, said", [
    ({NUMBER_COL: "62601"}, {DATE_COL: {"date": "2026-11-12"}}, 'אסמכתת חשבונית: כבר "62601", לא נכתב'),
    ({DATE_COL: "2026-11-12"}, {NUMBER_COL: "62601"}, "תאריך תקבול: כבר 12.11.26, לא נכתב"),
])
def test_a_cell_written_since_with_what_the_books_propose_is_not_written_again_and_the_other_is(cells, written, said):
    """if_empty, each column: a cell monday holds the proposed value in now (written in monday since the reading) stays
    as it is, said so, and the row's other cell is still written."""
    got = run([{"show": TASKS}, {"click": [{"booksFill": "sc:1", "booksFrom": "list"}]}, {"click": [{"batchGo": "1"}]}],
              monday=monday_of(**{"1": cells}))
    plan = got["steps"][1]
    assert [i["verdict"] for i in plan["batch"]["items"]] == ["write"]
    assert said in h.unescape(plan["html"])
    assert writes_of(got) == [(SC_BOARD, "1", written)]


def test_a_cell_the_rules_read_as_empty_and_monday_does_not_is_never_written():
    """A mark a right-to-left editor leaves reads as empty to the rules (A1's `_clean`), so the books propose a number for
    the cell; monday holds the mark, and the page never writes over it - the row is skipped, and said."""
    rows = [dict(r, **{NUMBER_COL: "‏"}) if r["id"] == "1" else r for r in ROWS]
    view, merged = fill_view(rows)
    assert {r["id"]: r for r in merged["b2c"]["rows"]}["1"]["books_fill"] == {"number": "62601", "date": "2026-11-12"}
    got = run([{"show": TASKS}, {"click": [{"booksFill": "sc:1", "booksFrom": "list"}]}], view=view,
              monday=monday_of(rows))
    item, = got["steps"][1]["batch"]["items"]
    assert item["verdict"] == "skip" and item["why"].startswith("יש כבר ערך באסמכתת חשבונית במאנדיי")
    assert writes_of(got) == [] and got["steps"][1]["batch"]["phase"] == "nothing"


def test_a_row_changed_after_the_confirmation_was_shown_is_not_written_and_the_rest_is():
    got = run([{"show": TASKS}, {"click": [{"booksAll": "1"}]}, {"mutate": {SC_BOARD: {"2": {NUMBER_COL: "70000"}}}},
               {"click": [{"batchGo": "1"}]}])
    done = got["steps"][3]["batch"]
    assert [(i["key"], i["result"]) for i in done["items"]] == [
        ("sc:4", "written"), ("sc:2", "skipped"), ("sc:1", "written"), ("sc:3", "written")]
    assert done["items"][1]["why"] == 'השתנתה במאנדיי מאז שהתוכנית הוצגה, ועכשיו אסמכתת חשבונית "70000"'
    assert [w[1] for w in writes_of(got)] == ["4", "1", "3"]


def test_the_rows_details_write_its_proposal_and_show_it_at_once():
    """From the row's details: the batch of that one row, beside its button; once written, before the next reading lands,
    the row shows what was written - its "תאריך תקבול" as its date, no longer "by the day it was made" - marked "נכתב,
    מתעדכן", and its buttons are gone from the list and the details."""
    opened = {"tab": "b2c", "sc": {"state": "", "period": "", "open": {"3": True}}}
    got = run([{"show": opened}, {"click": [{"booksFill": "sc:3", "booksFrom": "detail"}]}, {"click": [{"batchGo": "1"}]}],
              hold=True)
    before, plan, done = got["steps"]
    assert "(לפי יום היצירה)" in cell(before["html"], "3", "date")
    assert writes_of(got) == [(SC_BOARD, "3", {DATE_COL: {"date": "2026-11-03"}})]
    # in the list's own panel, the one that holds the button pressed (placePop anchors it there)
    panel_of = lambda x: plan["html"].rindex('<div class="fin-panel', 0, plan["html"].index(x))  # noqa: E731
    assert panel_of("fin-pop") == panel_of('data-books-fill="sc:3" data-books-from="detail"')
    html = done["html"]
    assert cell(html, "3", "date") == "03.11.26" and "נכתב, מתעדכן" in cell(html, "3", "student")
    assert 'data-books-fill="sc:3" data-books-from="detail" disabled' in html
    tasks = run([{"show": TASKS}, {"click": [{"booksFill": "sc:1", "booksFrom": "list"}]}, {"click": [{"batchGo": "1"}]}],
                hold=True)["steps"][2]["html"]
    part = books_part(tasks)
    assert 'data-books-fill="sc:1"' not in part and "הצעה: לרשום בשורה של נועה ברק <span class=\"fin-written\">" in part
    assert "(3)" in part                                                               # the others still wait


def test_a_date_written_here_files_the_row_under_its_new_month_at_once():
    """D4's review (3): applyWritten laid the written "תאריך תקבול" on the row as the day it is shown by, but not its month,
    so until the next reading the row stayed under its old period and was missing from the new one. Row 15 was made on 30.10
    and the books date its invoice-receipt 02.11: written from its details under "2026-10", it is listed at once under
    "2026-11" by that date, marked "נכתב, מתעדכן", and no longer under "2026-10"."""
    rows = ROWS + [pay(15, "טל אור", ref="62617", made="2026-10-30T08:00:00Z")]
    view = fill_view(rows, DOCS + [doc(62617, name="טל אור", card="20017", day="2026-11-02")])[0]
    assert {r["id"]: r for r in view["b2c"]["rows"]}["15"]["books_fill"] == {"date": "2026-11-02"}
    october = {"tab": "b2c", "sc": {"state": "", "period": "2026-10", "open": {"15": True}}}
    november = {"tab": "b2c", "sc": {"state": "", "period": "2026-11", "open": {}}}
    got = run([{"show": october}, {"click": [{"booksFill": "sc:15", "booksFrom": "detail"}]}, {"click": [{"batchGo": "1"}]},
               {"show": november}, {"show": dict(october, sc=dict(october["sc"], open={}))}],
              view=view, monday=monday_of(rows), hold=True)
    before, _plan, _done, nov, octo = got["steps"]
    assert 'data-row="15"' in before["html"] and writes_of(got) == [(SC_BOARD, "15", {DATE_COL: {"date": "2026-11-02"}})]
    assert cell(nov["html"], "15", "date") == "02.11.26" and "נכתב, מתעדכן" in cell(nov["html"], "15", "student")
    assert 'data-row="15"' not in octo["html"]


def test_past_one_batchs_worth_the_rest_waits_for_the_next_press():
    """Twenty-nine proposals: "לרשום את כל ההצעות (29)" takes the first 25 - one request reads them (monday's items(ids:)
    answers 25 at most) - and the confirmation says the four left wait for the next press."""
    view, _m = fill_view()
    row = next(r for r in view["b2c"]["rows"] if r["id"] == "1")
    more = [dict(_copy.deepcopy(row), id=str(7000 + i), name="תלמיד/ה %d" % i, books_fill={"number": str(70000 + i)})
            for i in range(26)]
    view["b2c"]["rows"] = [r for r in view["b2c"]["rows"] if r["id"] != "1"] + more
    for e in view["b2c"]["books"]["documents"]:
        e.pop("proposal", None)
    board = {r["id"]: {"__name": r["name"], NUMBER_COL: "", DATE_COL: ""} for r in more}
    board.update(monday_of()["boards"][SC_BOARD])
    got = run([{"show": TASKS}, {"click": [{"booksAll": "1"}]}], view=view, monday={"boards": {SC_BOARD: board}})
    assert 'לרשום את כל ההצעות <span class="fin-subtle">(29)</span>' in got["steps"][0]["html"]
    assert len(got["sent"][0]["variables"]["i"]) == 25 and len(got["steps"][1]["batch"]["items"]) == 25
    assert "ועוד 4 הצעות: בלחיצה נוספת אחרי הכתיבה, עד 25 שורות בכתיבה אחת." in h.unescape(got["steps"][1]["html"])


def test_the_fills_usage_kind_is_one_closed_word_of_the_vocabulary_and_the_page():
    """`books_fill` (app/desk/usage.py KINDS): where "רישום מחשבשבת" was pressed and how many rows its batch takes - the
    page sends it with those two details only, each literal a word the server keeps; its batch's life is `act`'s, with the
    action's own key. A count past its range, a fraction, a name or an item id is refused."""
    from tests.test_desk_usage import batch, ev, page_code, page_events

    assert usage.KINDS["books_fill"] == ("from", "rows") and usage.NUMBERS["rows"] == 100
    sent = [d for k, d in page_events(page_code()) if k == "books_fill"]
    assert sent == [{"from": ["all", "all", "list", "list", "detail"], "rows": []}]
    assert re.search(r'use\("act", \{ action: actionKey, phase: "batch_start" \}\);\s*if \(books\) \{ use\("books_fill"',
                     page_code())
    good = [ev("books_fill", **{"from": f, "rows": n}) for f, n in (("detail", 1), ("list", 1), ("all", 25))]
    rows, refused = usage.clean_batch(batch(*good), 7)
    assert refused == 0 and [r["detail"] for r in rows] == [e["d"] for e in good]
    for bad in (ev("books_fill", **{"from": "all", "rows": 101}), ev("books_fill", **{"from": "all", "rows": 2.5}),
                ev("books_fill", **{"from": "נועה ברק"}), ev("books_fill", item="5093448677"),
                ev("books_fill", **{"from": "all", "number": "62601"})):
        assert usage.clean_batch(batch(bad), 7) == ([], 1), bad


# ---- the hostile review of D4 (26.11): a proposal is always the reading's on the screen ----------------------------------
# A batch kept the rows of the reading it was pressed on, so a newer reading (a refresh, or askAgain when the view takes the
# focus back from monday's item card) that withdrew a proposal left the confirmation open, and "כתיבה" wrote it
# (tests/test_review_d4_fill_stale.py). The proposal is now read off the rows on the screen when the confirmation is made, held
# to each newer reading while it is open, and checked again right before each row's write.
FILL_GONE = "בקריאה החדשה של הלוחות כבר אין הצעה לשורה הזאת"
FILL_CHANGED = "ההצעה השתנתה מאז שנפתח האישור, בקריאה החדשה של הלוחות"
FILL_CLOSED = "קריאה חדשה של הלוחות נחתה בזמן שהאישור היה פתוח, ואין בה עוד מה לכתוב מההצעות שבו. האישור נסגר."


def corrected(*ids: str, paid: str = "3600") -> list:
    """The rows as a newer reading holds them after "סכום ששולם" of these was corrected: FR-C2 proposes none of their
    documents any more (`amount_differs`)."""
    return [dict(r, **{fd.SC["paid"]: paid}) if r["id"] in ids else r for r in ROWS]


def items_of(step: dict) -> list:
    return [(i["key"], i["verdict"], i["why"]) for i in step["batch"]["items"]]


def test_a_newer_reading_while_the_confirmation_is_open_moves_a_changed_proposal_out_and_counts_again():
    """The confirmation of every proposal is open (four rows); a newer reading lands in which the books date row 1's document
    a day later (its proposal changed) and a fifth row gained a proposal. Row 1 moves to "לא ייכתב" with why, the count says
    three rows, the new proposal waits for the next press - and "כתיבה" writes the three, never row 1, never the new row."""
    rows = ROWS + [dict(ROWS[0], id="14", name="רז כהן")]
    docs = [doc(62601, name="נועה ברק", card="20001", day="2026-11-13") if e["doc_number"] == "62601" else e for e in DOCS]
    docs.append(doc(62616, name="רז כהן", card="20016", day="2026-11-12"))
    newer, merged = fill_view(rows, docs)
    proposed = {r["id"]: r.get("books_fill") for r in merged["b2c"]["rows"]}
    assert proposed["1"] == {"number": "62601", "date": "2026-11-13"} and proposed["14"] == {"number": "62616",
                                                                                              "date": "2026-11-12"}
    monday = monday_of()
    monday["boards"][SC_BOARD]["14"] = {"__name": "רז כהן", NUMBER_COL: "", DATE_COL: ""}
    got = run([{"show": TASKS}, {"click": [{"booksAll": "1"}]}, {"click": [{"refresh": "1"}]}, {"click": [{"batchGo": "1"}]}],
              monday=monday, refreshed=newer)
    shown, landed, done = got["steps"][1], got["steps"][2], got["steps"][3]
    assert [i[1] for i in items_of(shown)] == ["write"] * 4
    assert items_of(landed) == [("sc:4", "write", ""), ("sc:2", "write", ""), ("sc:1", "skip", FILL_CHANGED),
                                ("sc:3", "write", "")] and landed["batch"]["phase"] == "confirm"
    page = h.unescape(landed["html"])
    assert "ייכתב במאנדיי בשמך, ב-3 שורות:" in page and "<p>לא ייכתב:</p><ul><li><b>נועה ברק</b> · %s</li>" % FILL_CHANGED in page
    assert "ועוד הצעה אחת: בלחיצה נוספת אחרי הכתיבה, עד 25 שורות בכתיבה אחת." in page
    assert [w[1] for w in writes_of(got)] == ["4", "2", "3"]
    assert [(i["key"], i["result"]) for i in done["batch"]["items"]] == [
        ("sc:4", "written"), ("sc:2", "written"), ("sc:1", None), ("sc:3", "written")]


def test_a_newer_reading_that_withdraws_every_proposal_of_the_confirmation_closes_it_with_a_sentence():
    """One row's proposal from its document's line; a newer reading withdraws it (its amount corrected). The confirmation
    closes: no "כתיבה" is left on the screen, the box says why in one sentence and the row's own reason, and nothing is
    written."""
    newer, _m = fill_view(corrected("1"))
    got = run([{"show": TASKS}, {"click": [{"booksFill": "sc:1", "booksFrom": "list"}]}, {"click": [{"refresh": "1"}]},
               {"click": [{"batchGo": "1"}]}], refreshed=newer)
    landed = got["steps"][2]
    assert landed["batch"]["phase"] == "nothing" and items_of(landed) == [("sc:1", "skip", FILL_GONE)]
    page = h.unescape(landed["html"])
    assert '<p class="fin-act-msg bad" role="alert">%s</p><p>אין מה לכתוב במאנדיי:</p>' % FILL_CLOSED in page
    assert "<li><b>נועה ברק</b> · %s</li>" % FILL_GONE in page and "data-batch-go" not in page
    assert writes_of(got) == []


def test_a_newer_reading_that_lands_while_the_rows_are_read_is_the_one_the_confirmation_is_made_of():
    """"לרשום את כל ההצעות" pressed; while monday reads the four rows, a newer reading lands that withdrew row 2's proposal.
    The confirmation is made of the reading on the screen: row 2 is said, not offered."""
    monday = monday_of()
    monday["answers"] = [{"park": "rows"}]
    got = run([{"show": TASKS}, {"click": [{"booksAll": "1"}]}, {"click": [{"refresh": "1"}]}, {"unpark": "rows"},
               {"click": [{"batchGo": "1"}]}], monday=monday, refreshed=fill_view(corrected("2"))[0])
    assert got["steps"][1]["batch"]["phase"] == "reading"
    assert items_of(got["steps"][3]) == [("sc:4", "write", ""), ("sc:2", "skip", FILL_GONE), ("sc:1", "write", ""),
                                         ("sc:3", "write", "")]
    assert [w[1] for w in writes_of(got)] == ["4", "1", "3"]


def test_a_newer_reading_that_lands_between_two_rows_writes_is_held_to_right_before_the_next_write():
    """"כתיבה" pressed on four rows; while the first is read again before its write, a newer reading lands that withdrew row
    2's proposal - the confirmation is no longer on the screen to be checked. Each row's proposal is checked against the
    reading on the screen right before its own write: row 2 is not written, and the report says why."""
    monday = monday_of()
    monday["answers"] = [{}, {"park": "row4"}]
    got = run([{"show": TASKS}, {"click": [{"booksAll": "1"}]}, {"click": [{"batchGo": "1"}]}, {"click": [{"refresh": "1"}]},
               {"unpark": "row4"}], monday=monday, refreshed=fill_view(corrected("2"))[0])
    assert got["steps"][2]["batch"]["phase"] == "writing"
    done = got["steps"][4]["batch"]
    assert [(i["key"], i["result"], i["why"]) for i in done["items"]] == [
        ("sc:4", "written", ""), ("sc:2", "skipped", FILL_GONE), ("sc:1", "written", ""), ("sc:3", "written", "")]
    assert [w[1] for w in writes_of(got)] == ["4", "1", "3"]
    assert "<li><b>יואב שטרן</b> · %s</li>" % FILL_GONE in h.unescape(got["steps"][4]["html"])


# The same review: the legend promised that a row changed in monday since the reading is not written, and the batch read
# only the two cells it writes. What a proposal rests on ("סטטוס תשלום", "סכום ששולם": the action's `rests_on`) is read with
# them and held to the reading; and before a number is written, no other row of Student Charge may cite it.
CITED_UNCHECKED = "לא נבדק במאנדיי אם שורה אחרת כבר מפנה למספר הזה"
PROBLEM = {"text": "יש בעיה", "index": 2, "label": "יש בעיה"}


def citing_reads(got: dict) -> list:
    return [s["variables"]["v"] for s in got["sent"] if "items_page_by_column_values(" in s["query"]]


@pytest.mark.parametrize("now, why", [
    ({STATUS_COL: PROBLEM}, 'השתנתה במאנדיי מאז הקריאה, ועכשיו סטטוס תשלום "יש בעיה"'),
    ({STATUS_COL: ""}, "השתנתה במאנדיי מאז הקריאה, ועכשיו סטטוס תשלום ריק"),
    ({PAID_COL: "4000"}, "השתנתה במאנדיי מאז הקריאה, ועכשיו סכום ששולם 4,000.00 ₪"),
    ({PAID_COL: ""}, "השתנתה במאנדיי מאז הקריאה, ועכשיו סכום ששולם ריק"),
])
def test_a_row_whose_status_or_amount_moved_since_the_reading_is_said_in_the_confirmation_and_not_written(now, why):
    got = run([{"show": TASKS}, {"click": [{"booksAll": "1"}]}, {"click": [{"batchGo": "1"}]}], monday=monday_of(**{"2": now}))
    assert items_of(got["steps"][1])[1] == ("sc:2", "skip", why)
    assert "<li><b>יואב שטרן</b> · %s</li>" % why in h.unescape(got["steps"][1]["html"])
    assert [w[1] for w in writes_of(got)] == ["4", "1", "3"]


@pytest.mark.parametrize("cells, why", [
    ({"2": {STATUS_COL: PROBLEM}}, 'השתנתה במאנדיי מאז שהתוכנית הוצגה, ועכשיו סטטוס תשלום "יש בעיה"'),
    ({"2": {PAID_COL: "4000"}}, "השתנתה במאנדיי מאז שהתוכנית הוצגה, ועכשיו סכום ששולם 4,000.00 ₪"),
])
def test_a_row_whose_status_or_amount_moved_after_the_confirmation_was_shown_is_not_written(cells, why):
    """Read again right before its write, with what its proposal rests on: the payment bounced ("יש בעיה") or its amount
    was corrected after "לרשום את כל ההצעות" showed the confirmation. The row is skipped and said; the rest is written."""
    got = run([{"show": TASKS}, {"click": [{"booksAll": "1"}]}, {"mutate": {SC_BOARD: cells}}, {"click": [{"batchGo": "1"}]}])
    done = got["steps"][3]["batch"]
    assert [(i["key"], i["result"], i["why"]) for i in done["items"]] == [
        ("sc:4", "written", ""), ("sc:2", "skipped", why), ("sc:1", "written", ""), ("sc:3", "written", "")]
    assert [w[1] for w in writes_of(got)] == ["4", "1", "3"]


def test_an_amount_monday_writes_with_its_agorot_is_the_amount_the_desk_read():
    """"3517.00" in monday and 3517 in the reading are one amount, in whole agorot: the row is not taken as changed."""
    got = run([{"show": TASKS}, {"click": [{"booksFill": "sc:1", "booksFrom": "list"}]}, {"click": [{"batchGo": "1"}]}],
              monday=monday_of(**{"1": {PAID_COL: "3517.00"}}))
    assert items_of(got["steps"][1]) == [("sc:1", "write", "")]
    assert writes_of(got) == [(SC_BOARD, "1", {NUMBER_COL: "62601", DATE_COL: {"date": "2026-11-12"}})]


def test_before_a_number_is_written_the_rows_that_already_cite_it_are_read_once_and_another_one_stops_it():
    """FR-C2's condition 6 at the write: after the reading, 62602 was typed into another row of Student Charge (row 12).
    Right before each number is written, one read as the person asks monday for the rows whose "אסמכתת חשבונית" is that
    number - one per row that writes a number, none for the date alone - and row 2 is not written; the report names the row
    that cites it, as monday does."""
    got = run([{"show": TASKS}, {"click": [{"booksAll": "1"}]}, {"click": [{"batchGo": "1"}]}],
              monday=monday_of(**{"12": {NUMBER_COL: "62602"}}))
    assert citing_reads(got) == [["62604"], ["62602"], ["62601"]]
    why = 'כבר יש "62602" באסמכתת חשבונית של שורה אחרת במאנדיי: "אפרת חן"'
    done = got["steps"][2]["batch"]
    assert [(i["key"], i["result"], i["why"]) for i in done["items"]] == [
        ("sc:4", "written", ""), ("sc:2", "skipped", why), ("sc:1", "written", ""), ("sc:3", "written", "")]
    assert [w[1] for w in writes_of(got)] == ["4", "1", "3"]
    assert "<li><b>יואב שטרן</b> · %s</li>" % why in h.unescape(got["steps"][2]["html"])


def test_the_row_itself_in_monday_s_answer_is_not_another_row():
    """monday answers the row being written itself (its number typed into it a moment before): no other row cites it, and
    the write goes on - its own cell, read right before, was empty."""
    monday = monday_of()
    monday["answers"] = [{}, {}, {"answer": {"data": {"items_page_by_column_values": {"items": [{"id": "1", "name": "נועה ברק"}]}}}}]
    got = run([{"show": TASKS}, {"click": [{"booksFill": "sc:1", "booksFrom": "list"}]}, {"click": [{"batchGo": "1"}]}],
              monday=monday)
    assert writes_of(got) == [(SC_BOARD, "1", {NUMBER_COL: "62601", DATE_COL: {"date": "2026-11-12"}})]


@pytest.mark.parametrize("answer, why, then", [
    # another shape: never taken as "none"
    ({"answer": {"data": {"items": []}}}, CITED_UNCHECKED + ". לא נכתב כלום.", "written"),
    ({"answer": {"data": {"items_page_by_column_values": {"cursor": None}}}}, CITED_UNCHECKED + ". לא נכתב כלום.", "written"),
    ({"reject": {"message": "Internal server error"}}, CITED_UNCHECKED + ". לא נכתב כלום.", "written"),
    # monday asking to slow down: said, and it stops the rest as a write's would
    ({"reject": {"message": "Rate limit exceeded"}}, CITED_UNCHECKED + " (מאנדיי ביקשה להאט; לנסות שוב בעוד דקה). לא נכתב כלום.",
     "failed"),
])
def test_a_row_whose_check_could_not_be_made_is_not_written(answer, why, then):
    monday = monday_of()
    monday["answers"] = [{}, {}, answer]           # the batch's read, row 4 read again, the rows that cite 62604
    got = run([{"show": TASKS}, {"click": [{"booksAll": "1"}]}, {"click": [{"batchGo": "1"}]}], monday=monday)
    done = got["steps"][2]["batch"]
    assert (done["items"][0]["key"], done["items"][0]["result"], done["items"][0]["why"]) == ("sc:4", "failed", why)
    assert "4" not in [w[1] for w in writes_of(got)]
    assert {i["result"] for i in done["items"][1:]} == {then}


# ---- in a browser (Chromium through Playwright, as tests/test_desk_browser.py; skipped without it) -------------------------
from tests.test_desk_browser import FAKE_SDK, PW_ROOT, qa_visit, serve  # noqa: E402,F401 - `serve` is the fixture

# the repo's fake SDK answers reads; here it also takes a write as monday would (change_multiple_column_values), keeping it
WRITE_BRANCH = r"""
      if (query.trim().indexOf("mutation") === 0) {
        var vals = JSON.parse(v.v), item = (store[String(v.b)] || {})[String(v.i)];
        Object.keys(vals).forEach(function (c) { var x = vals[c]; item[c] = x && typeof x === "object" ? x.date : String(x); });
        return Promise.resolve({ data: { change_multiple_column_values: { id: v.i,
          column_values: (v.c || []).map(function (c) { return cell(item, c); }) } } });
      }
"""
ANCHOR = "      var board = String((v.b || [])[0]), cells = store[board] || {};"


def writing_sdk(store: dict) -> str:
    assert ANCHOR in FAKE_SDK
    return FAKE_SDK.replace(ANCHOR, WRITE_BRANCH + ANCHOR) % {"store": json.dumps(store, ensure_ascii=False), "labels": "{}"}


@pytest.mark.skipif(PW_ROOT is None, reason="node with playwright is not installed")
def test_in_a_browser_a_proposals_confirmation_floats_beside_what_was_pressed_and_writes_exactly_the_proposals(serve):  # noqa: F811
    """The books' list under "לטיפול", at monday's view widths: a document's button, and "לרשום את כל ההצעות" above the
    list, each open their one confirmation beside what was pressed - in the view, clear of it, the pointer not on "כתיבה",
    the focus on the box - and no row of either list moves, open or closed, nor the page. "כתיבה" then writes in monday
    exactly the proposals, each row its own, and the page logs no console error and throws nothing."""
    view = fill_view()[0]
    base, _log = serve(view, after_view({"1": {NUMBER_COL: "62601", DATE_COL: "2026-11-12"},
                                         "2": {NUMBER_COL: "62602"}, "3": {DATE_COL: "2026-11-03"},
                                         "4": {NUMBER_COL: "62604", DATE_COL: "2026-11-18"}}))
    store = {SC_BOARD: monday_of()["boards"][SC_BOARD]}
    got = qa_visit(r"""
  await page.waitForSelector('.fin-books [data-books-all="1"]');
  const rows = () => page.evaluate(() => [...document.querySelectorAll("tr[data-row], .fin-books-t tbody tr")].map((r, i) =>
    [r.dataset.row || "doc" + i, Math.round(r.getBoundingClientRect().top)]));
  const center = (sel) => page.$eval(sel, (el) => { const b = el.getBoundingClientRect(); return [b.x + b.width / 2, b.y + b.height / 2]; });
  const box = (pressed, at) => page.evaluate(([pressed, at]) => {
    const pop = document.querySelector(".fin-batch-plan"), p = pop.getBoundingClientRect();
    const what = document.querySelector(pressed), r = what && what.getBoundingClientRect();
    const go = pop.querySelector("[data-batch-go]").getBoundingClientRect();
    return { y: Math.round(window.scrollY), focus: document.activeElement === pop, inView: p.top >= 0 && p.bottom <= window.innerHeight,
      clear: !!r && (p.bottom <= r.top || p.top >= r.bottom), inBooks: !!pop.closest('.fin-books[data-books="b2c"]'),
      onGo: at[0] >= go.left && at[0] <= go.right && at[1] >= go.top && at[1] <= go.bottom, text: pop.innerText };
  }, [pressed, at]);
  const press = async (sel) => {
    await page.$eval(sel, (el) => el.scrollIntoView({ block: "center" }));
    await page.waitForTimeout(100);
    const spot = await center(sel);
    await page.mouse.click(spot[0], spot[1]);
    await page.waitForSelector(".fin-batch-plan [data-batch-go]");
    return spot;
  };
  const close = async () => {
    await page.click('.fin-batch-plan [data-batch-close="1"]');
    await page.waitForSelector(".fin-batch-plan", { state: "detached" });
  };
  out.steps[at] = {};
  for (const [name, sel, around] of [["one", '[data-books-fill="sc:1"][data-books-from="list"]', 'tr:has([data-books-fill="sc:1"])'],
                                     ["all", '[data-books-all="1"]', '[data-books-all="1"]']]) {
    await page.$eval(sel, (el) => el.scrollIntoView({ block: "center" }));
    await page.waitForTimeout(100);
    const before = await rows(), y = Math.round(await page.evaluate(() => window.scrollY)), spot = await press(sel);
    out.steps[at][name] = { before: before, y: y, open: await rows(), box: await box(around, spot) };
    if (name === "one") { await close(); out.steps[at][name].closed = await rows(); }
  }
  await page.click('.fin-batch-plan [data-batch-go="1"]');
  await page.waitForSelector(".fin-batch-done");
  out.steps[at].writes = await page.evaluate(() => window.__monday.filter((m) => m.query.trim().indexOf("mutation") === 0)
    .map((m) => [m.variables.i, JSON.parse(m.variables.v)]));
  out.steps[at].report = await page.$eval(".fin-batch-done", (el) => el.innerText);
""", base, writing_sdk(store), sizes=((1280, 748), (1400, 748)))
    assert got["errors"] == [], got["errors"]
    for size in ("1280x748", "1400x748"):
        steps = got["steps"][size]
        for name in ("one", "all"):
            step, box = steps[name], steps[name]["box"]
            assert step["open"] == step["before"] and box["y"] == step["y"], (size, name)
            assert box["inView"] and box["clear"] and not box["onGo"] and box["focus"] and box["inBooks"], (size, name, box)
        assert steps["one"]["closed"] == steps["one"]["before"]
        assert 'ייכתב במאנדיי בשורה "נועה ברק", בשמך:' in steps["one"]["box"]["text"]
        assert "ייכתב במאנדיי בשמך, ב-4 שורות:" in steps["all"]["box"]["text"]
        assert steps["writes"] == [["4", {NUMBER_COL: "62604", DATE_COL: {"date": "2026-11-18"}}], ["2", {NUMBER_COL: "62602"}],
                                   ["1", {NUMBER_COL: "62601", DATE_COL: {"date": "2026-11-12"}}],
                                   ["3", {DATE_COL: {"date": "2026-11-03"}}]], size
        assert "נכתב במאנדיי בשמך ב-4 שורות." in steps["report"]


def test_a_proposals_batch_takes_no_pick_and_leaves_the_rows_picked_for_the_others():
    """The boxes at a row's start pick rows for D2's two (here "שליחת קבלה: יצאה"); a batch of the books' proposals takes
    none of them - every proposal, from above the list - and leaves them picked, its own rows among them."""
    view, _m = fill_view()
    for r in view["b2c"]["rows"]:
        if r["id"] == "2":   # waiting for its receipt: a box at its start, and a proposal of the books
            r.update(state="receipt_pending", receipt="טרם", receipt_idx="0", actions=["receipt_sent"] + r["actions"])
    pending = {"tab": "b2c", "sc": {"state": "receipt_pending", "period": "", "open": {}}}
    got = run([{"show": pending}, {"click": [{"pick": "sc:2"}]}, {"show": TASKS}, {"click": [{"booksAll": "1"}]},
               {"click": [{"batchGo": "1"}]}], view=view)
    picked, plan, done = got["steps"][1], got["steps"][3], got["steps"][4]
    assert picked["picked"] == ["sc:2"] and "נבחרה שורה אחת" in picked["html"]
    assert "sc:2" in [i["key"] for i in plan["batch"]["items"]] and done["batch"]["phase"] == "done"
    assert done["picked"] == ["sc:2"] and ("2", {NUMBER_COL: "62602"}) in [(w[1], w[2]) for w in writes_of(got)]
