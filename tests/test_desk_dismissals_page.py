"""R5 on the page (frontend/finance.js): "לא פער" beside each mark's sentence, on each document of the books' lists and
("לא להציע") beside each proposal; its floating confirmation, which says what it does; what is sent (the server's
evidence, the reading and the part on the screen) and how the server's answer is drawn; "הוסתרו כלא פער (N)" with what,
by whom and when, and "החזרה" on one's own; "חזר" on an alert that came back. Drawn from views the server built
(app/desk/books.py compute and merge, app/desk/dismissals.py hide, view.visible_part), never hand-made. Needs node; the
browser test needs Playwright, each skipped without it."""
from __future__ import annotations

import html as h
import json
import re

import pytest

from app.desk import books as db
from app.desk import dismissals as dz
from app.desk import view as fd
from app.desk.view import json_safe
from tests.test_desk_dismissals import ALL_DOCS, B2B, KEYS, ROWS, alerts, computed, live_row, with_row
from tests.test_desk_page import LOAD, NODE, drive, node

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")
AS_OF = "2026-11-25T08:00:00+00:00"
TASKS = {"tab": "b2c", "sc": {"state": "tasks", "period": "", "open": {}}}
B2_TASKS = {"tab": "b2b", "b2": {"state": "tasks", "view": "list", "customer": "", "open": {}}}
NAG, UNDO = "/api/alerts/not-a-gap", "/api/alerts/not-a-gap/undo"


def nag_view(live=(), rows=ROWS, docs=ALL_DOCS, b2c: bool = True, b2b: bool = True, key: str = "k-1") -> dict:
    """The view the server sends a reader: computed, shown by `live` (the live dismissals), laid over the reading, cut."""
    raw, view, reading = computed(rows, docs, B2B, KEYS)
    part = dict(dz.hide(raw, list(live)), state="ok", reasons=[], as_of=AS_OF, key=key)
    return json_safe(fd.visible_part(db.merge(view, part), b2c, b2b))


def body(view: dict, user: str = "1", view_only: bool = False, taken: str = "2026-11-24T10:00:01+00:00") -> dict:
    return {"view": view, "taken_at": taken, "age_seconds": 0, "refreshing": False, "stale": False, "last_attempt": None,
            "user": {"id": user, "view_only": view_only, "guest": False, "claims": []}, "tabs": view["tabs"]}


def draw(b: dict, cases: list) -> list:
    return node(LOAD + "console.log(JSON.stringify(input.cases.map(c => F._render(input.body, c))));",
                {"body": b, "cases": cases})


def press_of(html: str, kind: str, subject: str, frm: str) -> str:
    """The press as `html` draws it - its key, where it is drawn from, its title (what it does, the confirmation's own
    sentence: the UX review of 26.11) and its word - or a text no drawing holds when it is not there."""
    m = re.search(r'<button type="button" class="fin-linkbtn fin-nag" data-nag="%s:%s" data-nag-from="%s" title="[^"]+">%s'
                  r'</button>' % (re.escape(kind), re.escape(subject), re.escape(frm),
                                  re.escape(h.escape(dz.WORD[kind], quote=False))), html)
    return m.group(0) if m else "<no press %s:%s from %s>" % (kind, subject, frm)


def detail_of(html: str, rid: str) -> str:
    part = html[html.index('<tr class="fin-detail">', html.index('data-row="%s"' % rid)):]
    return part[:part.index("</td></tr>")]


def doc_row(html: str, number: str) -> str:
    return re.search(r'<tr><td class="b-number"><bdi dir="ltr">%s</bdi>.*?</tr>' % number, html, flags=re.S).group(0)


def test_every_alert_carries_its_press_where_it_is_shown_and_a_viewer_none():
    v = nag_view()
    every = {"tab": "b2c", "sc": {"state": "", "period": "", "open": {"1": True, "3": True, "7": True, "8": True, "9": True}}}
    rows, tasks, companies = draw(body(v), [every, TASKS, B2_TASKS])
    # beside each mark's sentence in its row's details: "לא פער"
    for kind, rid in (("books_receipt_unpaid", "7"), ("books_ref_invalid", "8"), ("books_amount_differs", "9")):
        warn = detail_of(rows, rid)
        warn = warn[warn.index('<ul class="fin-dx-warn">'):]
        assert press_of(rows, kind, rid, "detail") in warn[:warn.index("</ul>")], kind
    # beside each proposal: "לא להציע", in its row's details and on its document's line
    for kind, rid in (("books_ref_fill", "1"), ("books_date_fill", "3")):
        said = detail_of(rows, rid)
        said = said[said.index('<p class="fin-dx-books fin-fill-said">'):]
        assert press_of(rows, kind, rid, "detail") in said[:said.index("</p>")], kind
    assert press_of(tasks, "books_ref_fill", "1", "list") in doc_row(tasks, "62601")
    # on each document of the three lists: its own list's kind
    for number in ("62601", "62602", "62604", "62606", "62613"):
        assert press_of(tasks, "books_doc_students", number, "list") in doc_row(tasks, number)
    assert press_of(tasks, "books_doc_unclear", "50501", "list") in doc_row(tasks, "50501")
    assert press_of(companies, "books_doc_companies", "62614", "list") in doc_row(companies, "62614")
    # its column has no heading of its own, named for a reader
    assert '<th class="b-nag" aria-label="לא פער"></th>' in tasks
    # a viewer is shown no press at all (the server refuses one): nowhere
    for html in draw(body(v, view_only=True), [every, TASKS, B2_TASKS]):
        assert "data-nag=" not in html


def run(steps, v=None, after=None, refusal=None, user="1", view_only=False, gets=1, undo=None) -> dict:
    """The page mounted on `v`; a press answered with `after` (the server's desk after it) or refused with `refusal`."""
    v = v or nag_view()
    answers = {"GET /api/desk": [{"body": body(v, user, view_only)} for _ in range(gets)]}
    if after is not None:
        answers["POST " + NAG] = [{"body": dict(body(after, user), dismissed={"kind": "", "subject": "", "id": 1})}]
    if refusal is not None:
        answers["POST " + NAG] = [{"reject": {"error": refusal, "status": 409, "body": {"error": refusal}}}]
    if undo is not None:
        answers["POST " + UNDO] = [undo]
    return drive(steps=steps, answers=answers, useFlushMs=0)


def test_a_press_opens_a_confirmation_that_says_what_it_does_and_sends_the_servers_evidence():
    v = nag_view()
    evidence = next(e["evidence"] for e in v["b2c"]["books"]["documents"] if e["subject"] == "62606")
    after = nag_view([live_row("books_doc_students", "62606", evidence)], key="k-2")
    got = run([{"show": TASKS}, {"click": [{"nag": "books_doc_students:62606", "nagFrom": "list"}]},
               {"click": [{"nagGo": "1"}]}], v, after)
    opened, done = got["steps"][1], got["steps"][2]
    box = opened["html"][opened["html"].index('class="fin-nag-box'):]
    assert 'role="group" aria-label="לא פער"' in box[:200]
    assert "<b>חשבונית מס/קבלה 62606, תומר נחום</b>" in box
    assert "המסמך יוסתר מהרשימה לכולם כ&quot;לא פער&quot;, כל עוד הוא נשאר בחשבשבת כמו עכשיו" in box
    assert "לא מתבצע שינוי במאנדיי או בחשבשבת" in box and 'data-nag-go="1">הסתרה</button>' in box
    assert got["posts"] == [[NAG, {"kind": "books_doc_students", "subject": "62606", "evidence": evidence,
                                   "taken": "2026-11-24T10:00:01+00:00", "books": "k-1"}]]
    # the server's desk after it is drawn: the document is gone from the list and listed as hidden, the box closed
    assert "fin-nag-box" not in done["html"] and 'data-nag="books_doc_students:62606"' not in done["html"]
    assert 'data-nag-list="b2c" aria-expanded="false">הוסתרו כלא פער (1)</button>' in done["html"]
    assert any("הוסתר כ\"לא פער\"" in t for t in done["showing"])
    events = [(e["k"], e.get("d")) for b in got["usage"] for e in b["body"]["events"]]
    assert ("not_a_gap", {"alert": "books_doc_students", "phase": "open", "from": "list"}) in events
    assert ("not_a_gap", {"alert": "books_doc_students", "phase": "done"}) in events


def test_a_proposals_press_says_it_is_not_to_be_proposed_and_a_marks_says_its_row():
    got = run([{"show": {"tab": "b2c", "sc": {"state": "", "period": "", "open": {"1": True, "9": True}}}},
               {"click": [{"nag": "books_ref_fill:1", "nagFrom": "detail"}]}, {"click": [{"nagClose": "1"}]},
               {"click": [{"nag": "books_amount_differs:9", "nagFrom": "detail"}]}])
    fill, closed, mark = got["steps"][1]["html"], got["steps"][2]["html"], got["steps"][3]["html"]
    box = fill[fill.index('class="fin-nag-box'):]
    assert 'aria-label="לא להציע"' in box[:200] and "ההצעה תוסתר לכל המשתמשים" in box
    assert "<b>&quot;נועה ברק&quot;: בחשבשבת: 62601</b>" in box
    assert "fin-nag-box" not in closed and got["posts"] == []
    box = mark[mark.index('class="fin-nag-box'):]
    assert "<b>&quot;רוני שגב&quot;: בשורה 2,000.00 ₪, בחשבשבת 3,517.00 ₪</b>" in box
    assert "הסימון יוסתר לכולם כ&quot;לא פער&quot;" in box
    events = [(e["k"], e.get("d")) for b in got["usage"] for e in b["body"]["events"]]
    assert ("not_a_gap", {"alert": "books_ref_fill", "phase": "cancel"}) in events


@pytest.mark.parametrize("word, says", [("evidence_changed", "ההתראה השתנתה מאז שהוצגה"), ("other_reading", "המסך התעדכן"),
                                        ("dismissals_unavailable", "לא הצלחנו לשמור עכשיו"),
                                        ("http_500", "לא הוסתר כלום (http_500).")])
def test_a_refused_press_says_why_hides_nothing_and_asks_the_desk_again_when_the_screen_is_behind(word, says):
    got = run([{"show": TASKS}, {"click": [{"nag": "books_doc_unclear:50501", "nagFrom": "list"}]},
               {"click": [{"nagGo": "1"}]}], refusal=word, gets=2)
    failed = got["steps"][2]
    box = failed["html"][failed["html"].index('class="fin-nag-box'):]
    assert says in box and 'role="alert"' in box and 'data-nag="books_doc_unclear:50501"' in failed["html"]
    assert got["calls"].count("GET /api/desk") == (2 if word in ("evidence_changed", "other_reading") else 1)
    events = [(e["k"], e.get("d")) for b in got["usage"] for e in b["body"]["events"]]
    assert ("not_a_gap", {"alert": "books_doc_unclear", "phase": "failed", "code": word}) in events


def test_the_hidden_list_says_what_by_whom_and_when_and_offers_the_undo_only_on_ones_own():
    raw = computed()[0]
    live = [live_row("books_ref_invalid", "8", alerts(raw)[("books_ref_invalid", "8")], 1, by=1),
            live_row("books_doc_students", "62606", alerts(raw)[("books_doc_students", "62606")], 2, by=9)]
    v = nag_view(live)
    shut = draw(body(v), [TASKS])[0]
    assert 'data-nag-list="b2c" aria-expanded="false">הוסתרו כלא פער (2)</button>' in shut and "fin-nag-list" not in shut
    got = run([{"show": TASKS}, {"click": [{"nagList": "b2c"}]},
               {"click": [{"nagUndo": "books_ref_invalid:8:1"}]}], v,
              undo={"body": dict(body(nag_view()), undone={"kind": "books_ref_invalid", "subject": "8", "id": 1})})
    open_ = got["steps"][1]["html"]
    items = re.findall(r"<li>(.*?)</li>", open_[open_.index('<ul class="fin-nag-list">'):open_.index("</ul>", open_.index("fin-nag-list"))])
    assert len(items) == 2
    mine, theirs = items
    assert "<b>אורי פז</b> · מספר החשבונית שבשורה לא נמצא בחשבשבת כמסמך תקף: 40000 לא נמצא" in mine
    assert "לא פער · בידי אתה · " in mine and 'data-nag-undo="books_ref_invalid:8:1">החזרה</button>' in mine
    assert '<bdi dir="ltr">62606</bdi> חשבונית מס/קבלה' in theirs and "בידי משתמש/ת 9" in theirs and "data-nag-undo" not in theirs
    assert got["posts"] == [[UNDO, {"kind": "books_ref_invalid", "subject": "8", "id": 1}]]
    back = got["steps"][2]["html"]
    assert "הוסתרו כלא פער" not in back and any("הוחזר" in t for t in got["steps"][2]["showing"])
    events = [(e["k"], e.get("d")) for b in got["usage"] for e in b["body"]["events"]]
    assert ("not_a_gap_list", {"open": True, "list": "b2c"}) in events
    assert ("not_a_gap_undo", {"alert": "books_ref_invalid", "phase": "done"}) in events


def test_an_undo_refused_says_why():
    raw = computed()[0]
    v = nag_view([live_row("books_ref_invalid", "8", alerts(raw)[("books_ref_invalid", "8")], 1, by=1)])
    got = run([{"show": TASKS}, {"click": [{"nagList": "b2c"}]}, {"click": [{"nagUndo": "books_ref_invalid:8:1"}]}], v,
              undo={"reject": {"error": "not_yours", "status": 403, "body": {"error": "not_yours"}}})
    assert "רק מי שהסתיר יכול להחזיר." in got["steps"][2]["showing"]
    events = [(e["k"], e.get("d")) for b in got["usage"] for e in b["body"]["events"]]
    assert ("not_a_gap_undo", {"alert": "books_ref_invalid", "phase": "failed", "code": "not_yours"}) in events


def test_an_alert_that_came_back_says_so_beside_it():
    raw = computed()[0]
    stale = [live_row("books_receipt_unpaid", "7", "0" * 64, 3, by=9), live_row("books_doc_unclear", "50501", "1" * 64, 4, by=1),
             live_row("books_ref_fill", "1", "2" * 64, 5, by=1)]
    v = nag_view(stale)
    rows, tasks = draw(body(v), [{"tab": "b2c", "sc": {"state": "", "period": "", "open": {"7": True, "1": True}}}, TASKS])
    assert ("חזר: הוסתר כ&quot;לא פער&quot; ב-26.11, 11:00 על ידי משתמש/ת 9. הנתונים השתנו מאז.") in detail_of(rows, "7")
    assert "חזר: הוסתר כ&quot;לא להציע&quot; ב-26.11, 11:00 על ידי אתה" in detail_of(rows, "1")
    assert "חזר: הוסתר כ&quot;לא פער&quot; ב-26.11, 11:00 על ידי אתה" in doc_row(tasks, "50501")
    assert v["b2c"]["books"]["returned_counts"] == {"books_receipt_unpaid": 1, "books_ref_fill": 1}


def test_a_newer_reading_that_changes_the_alert_closes_its_confirmation():
    v = nag_view()
    moved = nag_view(rows=with_row(ROWS, "8", paid="3600"))
    got = drive(steps=[{"show": TASKS}, {"show": {"tab": "b2c", "sc": {"state": "", "period": "", "open": {"8": True}}}},
                       {"click": [{"nag": "books_ref_invalid:8", "nagFrom": "detail"}]}, {"click": [{"refresh": "1"}]}],
                answers={"GET /api/desk": [{"body": body(v)}],
                         "POST /api/desk/refresh": [{"body": body(moved, taken="2026-11-24T10:00:09+00:00")}]})
    assert got["steps"][2]["html"].count('class="fin-nag-box') == 1
    assert "fin-nag-box" not in got["steps"][3]["html"] and got["posts"] == [["/api/desk/refresh", None]]
    assert any("ההתראה השתנתה בקריאה החדשה" in t for t in got["steps"][3]["showing"])


def test_the_pages_words_are_the_servers_and_the_legends():
    words = node(LOAD + "console.log(JSON.stringify(F._nagWords()));", None)
    assert words == {"word": {"gap": fd.NAG_WORD, "fill": fd.NAG_FILL_WORD}, "list": fd.NAG_LIST_WORD}
    assert {dz.WORD[k] for k in dz.KINDS} == {fd.NAG_WORD, fd.NAG_FILL_WORD}
    for tab, words_ in (("b2c", (fd.NAG_WORD, fd.NAG_FILL_WORD, fd.NAG_LIST_WORD, fd.NAG_BACK_WORD)),
                        ("b2b", (fd.NAG_WORD, fd.NAG_LIST_WORD, fd.NAG_BACK_WORD))):
        assert set(words_) <= fd.legend_terms(tab), tab
    legend = dict(fd.LEGEND["b2c"])[" · ".join((fd.NAG_WORD, fd.NAG_FILL_WORD, fd.NAG_LIST_WORD, fd.NAG_BACK_WORD))]
    assert "לא מתבצע שינוי במאנדיי או בחשבשבת" in legend and "צופה במאנדיי לא יכול להסתיר" in legend
    page = (fd.__file__.rsplit("/app/", 1)[0] + "/frontend/finance.js")
    code = open(page, encoding="utf-8").read()
    assert '"' + fd.NAG_BACK_WORD + ': הוסתר כ' in code.replace("\\\"", '"')


# ---- in a browser (Chromium through Playwright, as tests/test_desk_browser.py; skipped without it) -------------------------
import threading  # noqa: E402
import time  # noqa: E402

from fastapi import Request  # noqa: E402 - at the module's top: FastAPI reads the routes' annotations from its globals

from tests.test_desk_browser import PW_ROOT, _free_port, fake_sdk, qa_visit  # noqa: E402
from tests.desk_page_view import answer  # noqa: E402


@pytest.fixture
def serve_nag():
    """D2's stub of the desk's routes (tests/test_desk_browser.py stub_app) answering `v`, behind the two routes of R5: a
    press answers the desk `after` it, an undo the desk `v` again - each recorded in the log."""
    import uvicorn
    from fastapi import FastAPI

    from tests.test_desk_browser import stub_app

    started = []

    def start(v, after):
        log: list = []
        outer = FastAPI()

        @outer.post(NAG)
        async def press(request: Request):
            log.append({"path": NAG, "body": await request.json()})
            return dict(answer(after), dismissed={"kind": "", "subject": "", "id": 1})

        @outer.post(UNDO)
        async def undo(request: Request):
            log.append({"path": UNDO, "body": await request.json()})
            return dict(answer(v), undone={"kind": "", "subject": "", "id": 1})

        outer.mount("/", stub_app(log, v))
        port = _free_port()
        server = uvicorn.Server(uvicorn.Config(outer, host="127.0.0.1", port=port, log_level="warning"))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        deadline = time.time() + 15
        while not server.started and time.time() < deadline:
            time.sleep(0.05)
        assert server.started
        started.append((server, thread))
        return "http://127.0.0.1:%d" % port, log

    yield start
    for server, thread in started:
        server.should_exit = True
        thread.join(timeout=10)


@pytest.mark.skipif(PW_ROOT is None, reason="node with playwright is not installed")
def test_in_a_browser_the_confirmation_floats_beside_the_press_and_the_hidden_list_undoes_ones_own(serve_nag):
    """Under "לטיפול", at monday's view widths: "לא פער" on a document of the books' list, and on a mark in a row's
    details, each open their confirmation beside what was pressed - in the view, clear of the pressed row, the pointer not
    on "הסתרה", the focus on the box - and no row of either list moves, open or closed, nor the page. "הסתרה" sends the
    server's evidence and draws the desk after it: the document is gone, "הוסתרו כלא פער (1)" opens it with "בידי אתה" and
    "החזרה", which brings it back. The page logs no console error and throws nothing."""
    raw = computed()[0]
    v = nag_view()
    after = nag_view([live_row("books_doc_students", "62606", alerts(raw)[("books_doc_students", "62606")], 1, by=1)],
                     key="k-2")
    base, log = serve_nag(v, after)
    got = qa_visit(r"""
  await page.waitForSelector('.fin-books [data-nag="books_doc_students:62606"]');
  const rows = () => page.evaluate(() => [...document.querySelectorAll("tr[data-row], .fin-books-t tbody tr")].map((r, i) =>
    [r.dataset.row || "doc" + i, Math.round(r.getBoundingClientRect().top)]));
  const center = (sel) => page.$eval(sel, (el) => { const b = el.getBoundingClientRect(); return [b.x + b.width / 2, b.y + b.height / 2]; });
  const box = (around, at) => page.evaluate(([around, at]) => {
    const pop = document.querySelector(".fin-nag-box"), p = pop.getBoundingClientRect();
    const what = document.querySelector(around), r = what && what.getBoundingClientRect();
    const go = pop.querySelector("[data-nag-go]").getBoundingClientRect();
    return { y: Math.round(window.scrollY), focus: document.activeElement === pop, inView: p.top >= 0 && p.bottom <= window.innerHeight,
      clear: !!r && (p.bottom <= r.top || p.top >= r.bottom),
      onGo: at[0] >= go.left && at[0] <= go.right && at[1] >= go.top && at[1] <= go.bottom, text: pop.innerText };
  }, [around, at]);
  const press = async (sel) => {
    await page.$eval(sel, (el) => el.scrollIntoView({ block: "center" }));
    await page.waitForTimeout(100);
    const spot = await center(sel);
    await page.mouse.click(spot[0], spot[1]);
    await page.waitForSelector(".fin-nag-box [data-nag-go]");
    return spot;
  };
  const close = async () => {
    await page.click('.fin-nag-box [data-nag-close="1"]');
    await page.waitForSelector(".fin-nag-box", { state: "detached" });
  };
  out.steps[at] = {};
  const doc = '[data-nag="books_doc_students:62606"]';
  await page.$eval(doc, (el) => el.scrollIntoView({ block: "center" }));
  await page.waitForTimeout(100);
  let before = await rows(), y = Math.round(await page.evaluate(() => window.scrollY)), spot = await press(doc);
  out.steps[at].doc = { before: before, y: y, open: await rows(), box: await box('tr:has(' + doc + ')', spot) };
  await close();
  out.steps[at].doc.closed = await rows();
  /* a mark in a row's details: the row 8 ("אורי פז") opened from "הכל" */
  await page.click('[data-bstate=""]');
  await page.click('tr[data-row="8"] td.k-student', { position: { x: 4, y: 4 } }).catch(() => page.click('tr[data-row="8"]'));
  const mark = '.fin-dx-warn [data-nag="books_ref_invalid:8"]';
  await page.waitForSelector(mark);
  await page.$eval(mark, (el) => el.scrollIntoView({ block: "center" }));
  await page.waitForTimeout(100);
  before = await rows(); y = Math.round(await page.evaluate(() => window.scrollY)); spot = await press(mark);
  out.steps[at].mark = { before: before, y: y, open: await rows(), box: await box(mark, spot) };
  await close();
  out.steps[at].mark.closed = await rows();
  /* "הסתרה" on the document: the desk after it, and its list of what is hidden */
  await page.click('[data-bstate="tasks"]');
  await press(doc);
  await page.click('.fin-nag-box [data-nag-go="1"]');
  await page.waitForSelector(doc, { state: "detached" });
  await page.click('[data-nag-list="b2c"]');
  await page.waitForSelector(".fin-nag-list");
  out.steps[at].hidden = await page.$eval(".fin-nag-list", (el) => el.innerText);
  await page.click('[data-nag-undo="books_doc_students:62606:1"]');
  await page.waitForSelector(doc);
  out.steps[at].back = await page.$$eval(".fin-nag-list", (els) => els.length);
""", base, fake_sdk({}), sizes=((1280, 748), (1400, 748)))
    assert got["errors"] == [], got["errors"]
    for size in ("1280x748", "1400x748"):
        steps = got["steps"][size]
        for name in ("doc", "mark"):
            step, box = steps[name], steps[name]["box"]
            assert step["open"] == step["before"] == step["closed"] and box["y"] == step["y"], (size, name)
            assert box["inView"] and box["clear"] and not box["onGo"] and box["focus"], (size, name, box)
        assert "המסמך יוסתר מהרשימה לכולם" in steps["doc"]["box"]["text"]
        assert "הסימון יוסתר לכולם" in steps["mark"]["box"]["text"]
        assert "62606" in steps["hidden"] and "בידי אתה" in steps["hidden"] and "החזרה" in steps["hidden"]
        assert steps["back"] == 0
    sent = [x for x in log if x.get("path") in (NAG, UNDO)]
    evidence = alerts(raw)[("books_doc_students", "62606")]
    assert sent[0] == {"path": NAG, "body": {"kind": "books_doc_students", "subject": "62606", "evidence": evidence,
                                             "taken": "2026-11-24T08:00:00+00:00", "books": "k-1"}}
    assert sent[1] == {"path": UNDO, "body": {"kind": "books_doc_students", "subject": "62606", "id": 1}}


@pytest.mark.skipif(PW_ROOT is None, reason="node with playwright is not installed")
def test_in_a_browser_the_confirmation_stays_below_a_stuck_header_and_where_it_was_put_once_its_press_left(serve_nag):
    """The QA of 26.11 (A and 1) on R5's box, as merged: "לא פער" on a mark in a row's details, the mark put just low enough
    for the box to fit above it up to the view's top - so above it, under the table's header stuck at the top, is where it
    would go - opens its confirmation below that header, whole in the view, its first line seen. When its press leaves the
    page while it is open (the row's details closed), the box stays beside where its press was: its own remembered spot
    (`POP_AT`, owned by `NAG`), not the view's top; and as the page scrolls on, never cut off nor under a header."""
    v = nag_view()
    base, _log = serve_nag(v, v)
    got = qa_visit(r"""
  await page.addStyleTag({ content: "body { padding-bottom: 1600px; }" });
  await page.click('[data-bstate=""]');
  const row = 'tr[data-row="8"] td.k-student', mark = '.fin-dx-warn [data-nag="books_ref_invalid:8"]';
  await page.click(row, { position: { x: 4, y: 4 } });
  await page.waitForSelector(mark);
  const look = () => page.evaluate(() => {
    const box = document.querySelector(".fin-nag-box"), p = box.getBoundingClientRect(), first = box.querySelector("p").getBoundingClientRect();
    const heads = [...document.querySelectorAll("table.fin-t thead")].map((t) => t.getBoundingClientRect())
      .filter((r) => r.top <= 8 && r.bottom > 0);
    const hit = document.elementFromPoint(first.left + first.width / 2, first.top + first.height / 2);
    const press = document.querySelector('.fin-dx-warn [data-nag="books_ref_invalid:8"]'), line = press && press.closest("li,p").getBoundingClientRect();
    return { top: p.top, bottom: p.bottom, h: p.height, page: p.top + window.scrollY, y: window.scrollY,
      head: heads.length ? heads[0].bottom : null, inView: p.top >= 0 && p.bottom <= window.innerHeight,
      firstSeen: !!hit && box.contains(hit), line: line ? [line.top + window.scrollY, line.bottom + window.scrollY] : null };
  });
  /* put the mark's line where a box of height h fits above it only up to the view's top, then press it */
  const pressAt = async (h) => {
    await page.$eval(mark, (el) => el.scrollIntoView({ block: "center" }));
    const top = await page.$eval(mark, (el) => el.closest("li,p").getBoundingClientRect().top);
    await page.evaluate((dy) => window.scrollBy(0, dy), top - (h + 20));
    await page.waitForTimeout(150);
    await page.click(mark);
    await page.waitForSelector(".fin-nag-box [data-nag-go]");
    await page.waitForTimeout(150);
  };
  out.steps[at] = {};
  await pressAt(0);
  const h = await page.$eval(".fin-nag-box", (b) => b.getBoundingClientRect().height);
  await page.click('.fin-nag-box [data-nag-close="1"]');
  await page.waitForSelector(".fin-nag-box", { state: "detached" });
  await pressAt(h);
  out.steps[at].top = await look();
  /* its press leaves the page while it is open: the row's details closed, by a click that scrolls nothing */
  await page.$eval(row, (el) => el.click());
  await page.waitForSelector(mark, { state: "detached" });
  await page.waitForTimeout(150);
  out.steps[at].gone = await look();
  /* the page scrolls on, its spot now above the view */
  await page.evaluate(() => window.scrollBy(0, 400));
  await page.waitForTimeout(250);
  out.steps[at].scrolled = await look();
""", base, fake_sdk({}), sizes=((1280, 748), (1400, 748)))
    assert got["errors"] == [], got["errors"]
    for size, steps in got["steps"].items():
        top, gone, scrolled = steps["top"], steps["gone"], steps["scrolled"]
        assert top["head"] is not None and top["line"], (size, top)                     # the header is stuck at the top
        assert top["top"] >= top["head"] and top["inView"] and top["firstSeen"], (size, top)
        # beside where the press was - just below its line or just above it, by the room the view has now - never the
        # view's top, where a box with no spot of its own goes
        line, gap = top["line"], 6
        beside = (line[1] + gap, line[0] - gap - gone["h"])
        assert gone["line"] is None and gone["y"] == top["y"], (size, top, gone)
        assert min(abs(gone["page"] - b) for b in beside) <= 1, (size, top, gone)
        assert gone["inView"] and gone["firstSeen"], (size, gone)
        assert scrolled["head"] is None or scrolled["top"] >= scrolled["head"], (size, scrolled)
        assert scrolled["inView"] and scrolled["firstSeen"], (size, scrolled)


def test_each_press_says_on_hover_what_it_does_in_the_confirmations_own_words():
    """The UX review of 26.11: a document's line can carry two hiding words, "לא פער" and "לא להציע", and neither said what
    it does until pressed. Each press's title is its confirmation's sentence: a mark's, a document's, a proposal's."""
    tasks = draw(body(nag_view()), [TASKS])[0]
    detail = draw(body(nag_view()), [{"tab": "b2c", "sc": {"state": "", "period": "", "open": {"9": True}}}])[0]
    title = lambda press: h.unescape(re.search(r'title="([^"]+)"', press).group(1))
    assert title(press_of(tasks, "books_doc_students", "62606", "list")).startswith("המסמך יוסתר מהרשימה לכולם")
    assert title(press_of(tasks, "books_ref_fill", "1", "list")).startswith("ההצעה תוסתר לכל המשתמשים")
    assert title(press_of(detail, "books_amount_differs", "9", "detail")).startswith("הסימון יוסתר לכולם")


def test_a_confirmation_closes_when_another_tab_is_chosen_and_hides_nothing():
    """The UX review of 26.11: a confirmation of "לא פער" opened on the students' tab stayed open, floating over the
    companies' tab, about a student. Another tab closes it as its "ביטול" would; nothing is sent, and back on the students'
    tab it is closed. One being saved is left to its answer (busyNag)."""
    got = run([{"show": TASKS}, {"click": [{"nag": "books_doc_students:62606", "nagFrom": "list"}]},
               {"click": [{"fintab": "b2b"}]}, {"click": [{"fintab": "b2c"}]}])
    opened, other, back = (s["html"] for s in got["steps"][1:])
    assert "fin-nag-box" in opened and "fin-nag-box" not in other and "fin-nag-box" not in back
    assert got["posts"] == []
    events = [(e["k"], e.get("d")) for b in got["usage"] for e in b["body"]["events"]]
    assert ("not_a_gap", {"alert": "books_doc_students", "phase": "cancel"}) in events

