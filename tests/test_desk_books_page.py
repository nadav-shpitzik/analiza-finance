"""D3 on the page (frontend/finance.js): what the books say that monday does not, drawn from the server's answer
(app/desk/books.py, merged into the view and cut for the reader by app/desk/view.py `visible_part`). Needs node; skipped
without it.

The page computes nothing of it: the chips' words, the sentences, the documents and their candidates, the count of
"לטיפול" and the books' state are the server's, and these tests draw them from a view the server built (`body`): the
marks on their rows in "לטיפול" (red, in the server's order and words), the list of each tab and the unclear one under
"לטיפול" only, a candidate drawn as one, the state line, the totals asked by the part on the screen, and every label in
the legend. The filter of "לטיפול" is held to the server's (filter_b2c) on the same rows."""
from __future__ import annotations

import html as h
import json
import re
from pathlib import Path

import pytest

from app.desk import books as db
from app.desk import view as fd
from app.desk.view import json_safe
from tests.desk_fixtures import TODAY, b2, reading_of, snap
from tests.test_desk_books import copy, doc, pay
from tests.test_desk_page import LOAD, NODE, drive, node
from tests.test_desk_parity_js import CONTROLS, MONDAY_TITLES, _labels, _words_of

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")
PAGE = Path(__file__).resolve().parents[1] / "frontend" / "finance.js"
TAKEN = "2026-11-24T10:00:01+00:00"
AS_OF = "2026-11-25T08:00:00+00:00"

# the rows: 1 cites invoice-receipt 62502 and says "לא שולם" (marked, of the era); 2 cites 40000, below every series
# (marked "not found", made before the era, so it keeps its mark and stays out of "לטיפול"); 3 waits for its receipt (a
# task state); 4 is done, unmarked, and named like the card of 62503; 5 cites 62504, just past the end of its series: not
# yet in the copy, no mark, said in its details
ROWS = [pay(1, "62502", st="0", receipt="0"), pay(2, "40000", on="2026-10-01"), pay(3, "", receipt="0"),
        pay(4, "", name="תלמיד גימל"), pay(5, "62504", on="2026-10-02")]
DOCS = [doc(62502), doc(62503, card="20002", name="גימל תלמיד"),
        doc(50500, doc_type=1, card="77001", name="כרטיס של חברה", total="11800", excl="10000"),
        doc(50501, doc_type=1, card="20009", name="כרטיס לא ברור", total="5900", excl="5000"),
        doc(50502, doc_type=1, card="10091", total="1180", excl="1000")]
KEYS = {"77001": ["חברה בדויה"]}


def books_view(state: str = "ok", reasons=(), b2c: bool = True, b2b: bool = True, rows=ROWS, docs=DOCS,
               keys=None) -> tuple:
    """(the view the server sends this reader, the whole merged view): computed from the rows and the copy, then laid over
    the view and cut for the reader exactly as GET /api/desk does."""
    reading = reading_of(snap(list(rows), [b2(1, invoice="")]))
    view = fd.build_view(reading, TODAY)
    part = db.compute(reading, view, copy(docs), KEYS if keys is None else keys)
    part.update(state=state, reasons=list(reasons), as_of=AS_OF if state in ("ok", "held") else None, key="k-1")
    if state in ("none", "off"):
        part.update(marks={}, students=[], companies=[], unclear=[], vouchers=0)
    merged = db.merge(view, part)
    return json_safe(fd.visible_part(merged, b2c, b2b)), merged


def body(view: dict) -> dict:
    return {"view": view, "taken_at": TAKEN, "age_seconds": 0, "refreshing": False, "stale": False, "last_attempt": None}


def draw(view: dict, cases: list) -> list:
    return node(LOAD + "console.log(JSON.stringify(input.cases.map(c => F._render(input.body, c))));",
                {"body": body(view), "cases": cases})


TASKS = {"tab": "b2c", "sc": {"state": "tasks", "period": "", "open": {}}}
B2_TASKS = {"tab": "b2b", "b2": {"state": "tasks", "view": "list", "customer": "", "open": {}}}


def cell(html: str, row_id: str, key: str) -> str:
    row = re.search(r'<tr class="fin-rowbtn[^"]*" data-row="%s".*?</tr>' % row_id, html, flags=re.S).group(0)
    return re.search(r'<td class="k-%s[^"]*">(.*?)</td>' % key, row, flags=re.S).group(1)


def test_tasks_holds_the_marked_rows_of_the_era_with_their_red_chip_and_the_tile_counts_them():
    view, merged = books_view()
    tasks, all_ = draw(view, [TASKS, {"tab": "b2c", "sc": {"state": "", "period": "", "open": {}}}])
    ids = re.findall(r'<tr class="fin-rowbtn[^"]*" data-row="([^"]+)"', tasks)
    assert sorted(ids) == ["1", "3"]                                          # 2 is older than 25.10, 4 is unmarked
    assert sorted(ids) == sorted(r["id"] for r in fd.filter_b2c(merged["b2c"], "tasks", "", ""))
    # the mark sits under the row's own state, red, in the server's words - no column was added (QA 4's widths)
    state = cell(tasks, "1", "state")
    assert state.count("ללא תקבול") == 1 and state.index("ללא תקבול") < state.index("fin-books-chips")
    assert state.endswith('<div class="fin-chips fin-books-chips"><span class="fin-chip bad">בחשבשבת חשבונית מס/קבלה 62502, '
                          'והסטטוס &quot;לא שולם&quot;</span></div>')
    assert "k-notes" not in tasks and "fin-books-chips" not in cell(tasks, "3", "state")
    # the tile says the server's count of "לטיפול", which its list shows
    tile = re.search(r'data-bstate="tasks"[^>]*><span class="n">(\d+)</span>', tasks).group(1)
    assert int(tile) == merged["b2c"]["periods"][0]["tasks"] == len(ids) == 2
    # the older row keeps its mark on the row: in "הכל", under its state
    assert "2" in re.findall(r'data-row="([^"]+)"', all_)
    assert "40000 לא נמצא</span>" in cell(all_, "2", "state")


def test_a_rows_details_say_the_marks_sentence():
    view, _m = books_view()
    html = draw(view, [{"tab": "b2c", "sc": {"state": "", "period": "", "open": {"1": True, "2": True}}}])[0]
    detail = html[html.index('<tr class="fin-detail">', html.index('data-row="1"')):]
    assert ('<li>בחשבשבת חשבונית מס/קבלה 62502 מ-03.11.2026 על 4,000.00 ₪ כולל מע&quot;מ, כלומר הכסף התקבל, וסטטוס התשלום '
            'בשורה &quot;לא שולם&quot;.</li>') in detail[:detail.index("</td></tr>")]
    detail = html[html.index('<tr class="fin-detail">', html.index('data-row="2"')):]
    assert "<li>מספר החשבונית שבשורה לא נמצא בחשבשבת כמסמך תקף: 40000: אין בחשבשבת מסמך תקף במספר הזה.</li>" in detail


def test_a_number_not_yet_in_the_copy_is_said_in_the_rows_details_and_nowhere_else():
    view, _m = books_view()
    html = draw(view, [{"tab": "b2c", "sc": {"state": "", "period": "", "open": {"5": True}}}])[0]
    detail = html[html.index('<tr class="fin-detail">', html.index('data-row="5"')):]
    detail = detail[:detail.index("</td></tr>")]
    assert '<p class="fin-dx-books">62504: עוד לא בהעתק של הספרים</p>' in detail and "fin-dx-warn" not in detail
    assert "fin-books-chips" not in cell(html, "5", "state")


def test_the_students_list_draws_each_document_and_its_candidates_as_candidates_only():
    view, _m = books_view()
    tasks = draw(view, [TASKS])[0]
    part = tasks[tasks.index('data-books="b2c"'):]
    part = part[:part.index('data-books="unclear"')] if 'data-books="unclear"' in part else part
    assert "<h2>%s</h2>" % fd.BOOKS_LIST_TITLE in part and "מסמך אחד" in part
    assert re.findall(r'<th class="b-[a-z]+[^"]*">(.*?)</th>', part) == [
        "מספר מסמך", "סוג מסמך", "תאריך מסמך", "סכום בחשבשבת (כולל מע&quot;מ)", "שם בכרטיס", "מועמדת לפי שם"]
    row = part[part.index("<tbody>"):part.index("</tbody>")]
    assert '<bdi dir="ltr">62503</bdi>' in row and "חשבונית מס/קבלה" in row and "4,000.00 ₪" in row and "גימל תלמיד" in row
    # the candidate: the row's name opening its own card in monday, its state and its status - and no word of a match
    assert ('<a class="fin-name" data-open="4" href="https://analiza-college-group.monday.com/boards/5093448677/pulses/4" '
            'target="_blank" rel="noopener">תלמיד גימל</a>') in row
    assert "fin-statechip" in row and "שולם" in row
    assert not re.search("התאמה|מותאם|קושר", h.unescape(row))
    # and the sentence above the list says the law: a candidate by name only, a match by the number cited
    assert "מועמדת לפי שם בלבד: התאמה נעשית רק לפי מספר המסמך באסמכתת חשבונית" in part
    # under the rows of "לטיפול", never above them, and never on another tile or a search
    assert tasks.index('data-books="b2c"') > tasks.index('data-row="1"')
    others = draw(view, [{"tab": "b2c", "sc": {"state": "", "period": "", "open": {}}},
                         {"tab": "b2c", "sc": {"state": "receipt_pending", "period": "", "open": {}}},
                         dict(TASKS, query="תלמיד")])
    assert not any("data-books=" in x for x in others)


def test_the_companies_list_and_the_vouchers_line_under_their_tasks():
    view, _m = books_view()
    html = draw(view, [B2_TASKS])[0]
    part = html[html.index('data-books="b2b"'):]
    part = part[:part.index('data-books="unclear"')]
    assert re.findall(r'<th class="b-[a-z]+[^"]*">(.*?)</th>', part) == [
        "מספר מסמך", "סוג מסמך", "תאריך מסמך", "סכום בחשבשבת (ללא מע&quot;מ)", "סכום בחשבשבת (כולל מע&quot;מ)", "שם בכרטיס", "חברה"]
    assert "50500" in part and "10,000.00 ₪" in part and "11,800.00 ₪" in part and "חברה בדויה" in part
    assert '<p class="fin-listnote">בכרטיס 10091 בחשבשבת (השוברים של משרד הביטחון): מסמך אחד בלי חיוב במאנדיי' in part
    assert "50502" not in html                                                  # counted apart, never listed
    with_customer = draw(view, [{"tab": "b2b", "b2": {"state": "tasks", "view": "list", "customer": "A1", "open": {}}}])[0]
    assert "data-books=" not in with_customer


def test_the_unclear_documents_only_where_the_server_sent_them():
    both, _m = books_view()
    for html in draw(both, [TASKS, B2_TASKS]):
        assert 'data-books="unclear"' in html and "50501" in html and "כרטיס לא ברור" in html
        assert "<th class=\"b-why\">למה לא ברור</th>" in html
    students, _m = books_view(b2b=False)
    companies, _m = books_view(b2c=False)
    assert students["books_unclear"] is None and companies["books_unclear"] is None
    for v, case in ((students, TASKS), (companies, B2_TASKS)):
        html = draw(v, [case])[0]
        assert 'data-books="unclear"' not in html and "50501" not in html


def test_the_books_state_line_says_when_or_why():
    ok, _m = books_view()
    html = draw(ok, [TASKS])[0]
    assert '<p class="fin-books-asof">לפי הספרים נכון ל-25.11, 10:00.</p>' in html
    assert '<span class="fin-subtle">לפי הספרים נכון ל-25.11, 10:00.</span>' in html          # and in the footer
    held, _m = books_view("held", ["last_run_incomplete"])
    html = draw(held, [TASKS])[0]
    said = "לפי הספרים נכון ל-25.11, 10:00. %s %s" % (db.REASONS["last_run_incomplete"], db.HELD_SAID)
    assert '<p class="fin-books-asof fin-badtext">%s</p>' % said in html and "62503" in html
    none, _m = books_view("none", ["never_completed"])
    html = draw(none, [TASKS])[0]
    assert '<p class="fin-books-asof fin-badtext">%s %s</p>' % (db.REASONS["never_completed"], db.NONE_SAID) in html
    assert "<table class=\"fin-t fin-books-t\">" not in html and "אין מסמכים כאלה" not in html
    off, _m = books_view("off", ["demo"])
    html = draw(off, [TASKS])[0]
    assert "data-books=" not in html and '<span class="fin-subtle">%s</span>' % db.REASONS["demo"] in html


def test_past_the_window_the_students_list_says_so_calmly():
    """10(c) of the review of dfa967b: past Student Charge's window a current part says, above its list and in the footer,
    that no student's document of the next year is listed - in the server's words, calm (the part is current)."""
    view, _m = books_view("ok", ["student_window_ended"])
    html = draw(view, [TASKS])[0]
    said = "לפי הספרים נכון ל-25.11, 10:00. %s" % db.REASONS["student_window_ended"]
    assert '<p class="fin-books-asof">%s</p>' % said in html and '<span class="fin-subtle">%s</span>' % said in html


def test_the_totals_of_tasks_are_asked_by_the_books_part_on_the_screen():
    view, _m = books_view()
    got = drive(body(view), [{"show": TASKS}])
    totals = [c for c in got["calls"] if c.startswith("GET /api/desk/totals?") and "state=tasks" in c]
    assert totals and all("books=k-1" in c for c in totals)


def test_the_excel_link_is_asked_with_the_books_part_on_the_screen():
    """10(a) of the review of dfa967b: the link to a file carries the books' part the page shows, as the totals do."""
    view, _m = books_view()
    got = drive(body(view), [{"show": TASKS}, {"click": [{"export": "b2c"}]}],
                answers={"GET /api/desk": [{"body": body(view)}],
                         "POST /api/desk/export-link": [{"body": {"url": "/api/desk/export.xlsx?x=1"}}]})
    links = [p[1] for p in got["posts"] if p[0] == "/api/desk/export-link"]
    assert links and all(x.get("books") == "k-1" for x in links)


def test_the_page_and_the_file_pick_the_same_rows_of_tasks():
    """The page's filter (scFilter) and the server's (filter_b2c), which builds the Excel file, on the same merged rows:
    every period and the search, with the books' rows of the era in "לטיפול"."""
    _v, merged = books_view()
    b2c = json_safe(merged["b2c"])
    cases = [["tasks", p["key"], q] for p in merged["b2c"]["periods"] for q in ("", "תלמיד", "4000")]
    run = LOAD + "console.log(JSON.stringify(input.cases.map(c => F._scFilter(input.rows, input.b2c, c[0], c[1], c[2]).map(r => r.id))));"
    page = node(run, {"rows": b2c["rows"], "b2c": b2c, "cases": cases})
    server = [[r["id"] for r in fd.filter_b2c(merged["b2c"], *c)] for c in cases]
    assert page == server and any("1" in ids for ids in server)


def test_every_label_the_books_draw_is_mondays_title_or_in_the_legend():
    view, _m = books_view()
    for case in (TASKS, B2_TASKS, dict(TASKS, legend=True), dict(B2_TASKS, legend=True)):
        tab = case["tab"]
        html = draw(view, [case])[0]
        known = fd.legend_terms(tab) | MONDAY_TITLES[tab] | CONTROLS
        chips = [re.compile("^" + re.escape(fd.flag_legend_term(t)).replace(re.escape("..."), ".+") + "$")
                 for _k, t, _x in fd.LEGEND_FLAGS[tab]]
        missing = [x for x in _labels(html) if x not in known and not re.fullmatch(r"ועוד \d+", x)
                   and not any(p.match(x) for p in chips) and not all(w in known for w in _words_of(x))]
        assert not missing, (tab, missing)
    # and the words of the books' part are in both tabs' legends
    for tab in ("b2c", "b2b"):
        terms = fd.legend_terms(tab)
        assert {fd.BOOKS_LIST_TITLE, fd.BOOKS_AS_OF_WORD, fd.BOOKS_SUM_WORD, "למה לא ברור", fd.BOOKS_UNCLEAR_TITLE} <= terms
        assert set(fd.BOOKS_DOC_HEADS) <= terms
    assert fd.BOOKS_CANDIDATE in fd.legend_terms("b2c")
    legend = dict(fd.LEGEND["b2c"])[fd.BOOKS_CANDIDATE]
    assert "רמז בלבד" in legend and "לא מקשר" in legend


def test_every_chip_the_books_draw_for_a_group_of_rows_is_in_the_legend():
    """The review of dfa967b, finding 9 (tests/test_review_d3_legend_chip.py, ported): the amount's chip of a group of
    several rows was rewritten to "ב-2 השורות X, בחשבשבת Y", drawn red under the row's state while the legend's term for it
    is "בשורה ..., בחשבשבת ..." - a label neither monday's nor the legend's (QA 11.11, 2.2). Its test drew one-row groups
    only. One invoice-receipt of 4,000 cited by two rows of 4,000 (63109's shape): the chip stays inside the term."""
    view, merged = books_view(rows=[pay(1, "63109"), pay(2, "63109")], docs=[doc(63109, total="4000")])
    chips = [m["chip"] for r in merged["b2c"]["rows"] for m in r.get("books") or []]
    assert chips and all("השורות" in c for c in chips)                      # the case is reached
    html = draw(view, [{"tab": "b2c", "sc": {"state": "", "period": "", "open": {}}}])[0]
    known = fd.legend_terms("b2c") | MONDAY_TITLES["b2c"] | CONTROLS
    patterns = [re.compile("^" + re.escape(fd.flag_legend_term(t)).replace(re.escape("..."), ".+") + "$")
                for _k, t, _x in fd.LEGEND_FLAGS["b2c"]]
    missing = [x for x in _labels(html) if x not in known and not re.fullmatch(r"ועוד \d+", x)
               and not any(p.match(x) for p in patterns) and not all(w in known for w in _words_of(x))]
    assert not missing, missing


def test_a_marks_chip_in_what_to_check_is_the_servers_filled():
    """J10 of the review's mutants: in a list that shows "מה לבדוק" (here "חריג, לבדיקה": "לא שולם" with "שליחת קבלה:
    יצאה"), a mark of the books is said in the server's filled words - its document and the row's status - never the
    legend's template."""
    view, merged = books_view(rows=[pay(1, "62502", st="0", receipt="1")], docs=DOCS)
    row = {r["id"]: r for r in merged["b2c"]["rows"]}["1"]
    assert row["state"] == "exception" and row["books"][0]["kind"] == "books_receipt_unpaid"
    html = draw(view, [{"tab": "b2c", "sc": {"state": "exception", "period": "", "open": {}}}])[0]
    notes = cell(html, "1", "notes")
    assert "בחשבשבת חשבונית מס/קבלה 62502, והסטטוס &quot;לא שולם&quot;" in notes and "{doc}" not in notes


def test_what_the_books_and_monday_name_is_drawn_as_text():
    """J06, J07, J09, J14 and J15 of the review's mutants: a card's name (the books'), a candidate's name and an Account's
    name (monday's), a row's note and an unclear document's why (the server's words) are drawn escaped - markup in any of
    them is text, never an element of the page."""
    mark = "<marquee>שם</marquee>"
    docs = [doc(62502), doc(62503, card="20002", name=mark),
            doc(50500, doc_type=1, card="77001", name=mark, total="11800", excl="10000"),
            doc(50501, doc_type=1, card="20009", name=mark, total="5900", excl="5000")]
    rows = [pay(1, "62502", st="0", receipt="0"), pay(4, "", name=mark), pay(5, "62504", on="2026-10-02")]
    view, _m = books_view(rows=rows, docs=docs, keys={"77001": [mark]})
    for r in view["b2c"]["rows"]:
        if r["id"] == "5":
            r["books_note"] = mark
    for e in view["books_unclear"]["documents"]:
        e["why_text"] = mark
    tasks, b2_tasks, details = draw(view, [TASKS, B2_TASKS, {"tab": "b2c", "sc": {"state": "", "period": "",
                                                                                  "open": {"5": True}}}])
    lists = tasks[tasks.index('data-books="b2c"'):] + b2_tasks[b2_tasks.index('data-books="b2b"'):]
    assert "<marquee>" not in tasks + b2_tasks + details
    shown = h.escape(mark, quote=False)
    assert '<td class="b-card">%s</td>' % shown in lists                                  # the card's name
    assert ">%s</a>" % shown in lists                                                  # the candidate's name, its link
    assert '<td class="b-accounts">%s</td>' % shown in lists                            # the Account's name
    assert '<td class="b-why">%s</td>' % shown in lists                                 # why it is unclear
    assert '<p class="fin-dx-books">%s</p>' % shown in details                           # the row's note


def test_the_chips_are_the_legends_words_red_and_after_the_contradictions_between_two_columns():
    flags = node(LOAD + "console.log(JSON.stringify(F._flags()));", None)
    texts = {k: t for k, t, _x in fd.LEGEND_FLAGS["b2c"]}
    for k in db.MARKS:
        assert flags["all"][k] == texts[k]
    source = PAGE.read_text(encoding="utf-8")
    order = json.loads(re.search(r"var FLAG_ORDER = (\[.*?\]);", source, flags=re.S).group(1))
    at = order.index("receipt_no_money")
    assert order[at + 1:at + 4] == list(db.MARKS) and order == fd.FLAG_ORDER
    bad = re.search(r"var BAD = \{(.*?)\};", source, flags=re.S).group(1)
    assert all("%s: 1" % k in bad for k in db.MARKS)


# ---- in a browser (Chromium through Playwright, as tests/test_desk_browser.py; skipped without it) -------------------------------
from tests.test_desk_browser import PW_ROOT, fake_sdk, long_view, qa_visit, serve  # noqa: E402,F401 - `serve` is the fixture


def marked_long_view() -> dict:
    """tests/test_desk_browser.py's view with rows as long as real ones, three of them marked by the books (one in a state
    that is no task, so "לטיפול" holds it by its mark), and the books' three lists - shaped as the server sends them."""
    v = long_view()
    chip = "בחשבשבת חשבונית מס/קבלה 62502, והסטטוס \"לא שולם\""
    for r in v["b2c"]["rows"][:3]:
        r.update(flags=r["flags"] + ["books_receipt_unpaid"], books_task=True, books=[
            {"kind": "books_receipt_unpaid", "chip": chip, "text": chip + ".", "documents": ["62502"]}])
    for p in v["b2c"]["periods"]:
        p["tasks"] = len([r for r in v["b2c"]["rows"] if r["state"] in ("exception", "refund_to_execute", "receipt_pending")
                          or r.get("books_task")])
    entry = {"number": "62503", "type": 2, "type_name": "חשבונית מס/קבלה", "date": "2026-11-03", "total_incl": "12500.00",
             "total_excl": "10593.22", "card": "תלמיד/ה לדוגמה בשם ארוך-ארוך 3"}
    v["books"] = {"state": "ok", "as_of": AS_OF, "reasons": [], "said": "", "key": "k-1"}
    v["b2c"]["books"] = {"title": fd.BOOKS_LIST_TITLE, "about": db.STUDENTS_ABOUT, "documents": [dict(entry, candidates=[
        {"id": "5003", "name": "תלמיד/ה לדוגמה בשם ארוך-ארוך 3", "state": "done", "status": "שולם", "date": "2026-11-03",
         "invoice": ""}], candidates_more=0)]}
    v["b2b"]["books"] = {"title": fd.BOOKS_LIST_TITLE, "about": db.COMPANIES_ABOUT, "vouchers": 2,
                         "vouchers_said": db.VOUCHERS_SAID % (db.VOUCHER_CARD, "2 מסמכים"),
                         "documents": [dict(entry, number="50500", type=1, type_name="חשבונית מס", accounts=["חברה לדוגמה"])]}
    v["books_unclear"] = {"title": fd.BOOKS_UNCLEAR_TITLE, "about": db.UNCLEAR_ABOUT, "documents": [
        dict(entry, number="50501", type=1, type_name="חשבונית מס", why="no_invoice_receipt",
             why_text=db.UNCLEAR_WHY["no_invoice_receipt"])]}
    return v


@pytest.mark.skipif(PW_ROOT is None, reason="node with playwright is not installed")
def test_in_a_browser_the_books_draw_with_no_error_and_tasks_still_fits_its_frame(serve):  # noqa: F811
    """QA 4's widths hold with the marks (they sit under the state, no column was added), the lists draw under "לטיפול" on
    both tabs, a candidate's name is a link to its card, and the page logs no console error and throws nothing."""
    base, _log = serve(marked_long_view())
    got = qa_visit(r"""
  out.steps[at] = {};
  for (const [tab, tile] of [["b2c", '[data-bstate="tasks"]'], ["b2b", '[data-b2state="tasks"]']]) {
    await page.click('[data-fintab="' + tab + '"]');
    await page.click(tile);
    await page.waitForSelector(".fin-books");
    out.steps[at][tab] = await page.evaluate(() => {
      const tw = document.querySelector(".fin-tw"), t = tw.querySelector("table");
      return { fits: t.getBoundingClientRect().width <= tw.getBoundingClientRect().width && tw.scrollWidth <= tw.clientWidth,
        marks: document.querySelectorAll(".fin-books-chips .fin-chip.bad").length,
        lists: [...document.querySelectorAll(".fin-books")].map((p) => p.getAttribute("data-books")),
        candidate: (document.querySelector('.fin-books .fin-cand a.fin-name') || {}).textContent || "" };
    });
  }
""", base, fake_sdk({}), sizes=((1280, 748), (1400, 748)))
    assert got["errors"] == [], got["errors"]
    for size in ("1280x748", "1400x748"):
        b2c, b2b = got["steps"][size]["b2c"], got["steps"][size]["b2b"]
        assert b2c["fits"] and b2c["marks"] == 3 and b2c["lists"] == ["b2c", "unclear"], (size, b2c)
        assert b2c["candidate"] == "תלמיד/ה לדוגמה בשם ארוך-ארוך 3"
        assert b2b["fits"] and b2b["lists"] == ["b2b", "unclear"], (size, b2b)
