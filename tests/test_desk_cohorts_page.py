"""D4's page: the students by cohort, a view of the students' tab (frontend/finance.js, its block "D4"; Analiza-Monday
backlog 0313), drawn from the server's own view (app/desk/view.py build_view of a reading, as GET /api/desk answers it) -
in node, and in Chromium through Playwright. Needs node (and Playwright for the browser); skipped without them.

What is held: the switch "רשימה | לפי מחזור" in the list's head; "אושר - ממתין לתשלום" the first figure and the one tinted
column; a cohort opening to its students, each balance in its own part's column and what a waived or out student paid
struck through; the cohorts without a price listed on demand with why; the search picking the cohorts the server's
filter_cohorts picks (the file and the totals are built from it); every label the view draws defined in the tab's legend,
whose part "לפי מחזור" is the server's; the file asked as the list `b2c_cohorts`; and the usage events in the server's
vocabulary."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest

from app.desk import cohorts as ch
from app.desk import view as fd
from app.desk.usage import clean_batch
from app.desk.view import json_safe
from tests.desk_fixtures import TODAY, lead, sc, snap
from tests.desk_page_view import answer as page_answer
from tests.desk_page_view import view as hand_made_view
from tests.test_desk_cohorts import coh, cohort_snapshot, enr
from tests.test_desk_parity_js import CONTROLS, MONDAY_TITLES, _labels, _words_of

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")
PAGE = Path(__file__).resolve().parents[1] / "frontend" / "finance.js"
TAKEN = "2026-11-24T08:00:00+00:00"

RENDER = r"""
const vm = require("vm"), fs = require("fs");
global.window = {};
global.document = { getElementById() { return null; } };
vm.runInThisContext(fs.readFileSync(process.argv[1], "utf8"));
const input = JSON.parse(fs.readFileSync(0, "utf8"));
console.log(JSON.stringify(input.cases.map((c) => window.FinanceDesk._render(input.body, c))));
"""


def body_of(v: dict) -> dict:
    return json_safe({"view": v, "taken_at": TAKEN, "age_seconds": 0, "refreshing": False, "stale": False,
                      "last_attempt": None, "tabs": {"b2c": True, "b2b": True}})


def real_view(s: dict | None = None) -> dict:
    return fd.build_view(s or cohort_snapshot(), TODAY)


def case(view="cohorts", cohorts=None, unpriced=False, query="", legend=False) -> dict:
    """A state of the page, whole each time: the page keeps its state between drawings."""
    return {"tab": "b2c", "query": query, "legend": legend,
            "sc": {"state": "tasks", "period": "", "open": {}, "view": view, "cohorts": cohorts or {}, "unpriced": unpriced}}


def render(cases: list, v: dict | None = None) -> list:
    data = json.dumps({"body": body_of(v or real_view()), "cases": cases}, ensure_ascii=False)
    out = subprocess.run([NODE, "-e", RENDER, str(PAGE)], input=data, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def rows_of(html: str) -> list:
    return re.findall(r'<tr class="fin-rowbtn[^"]*" data-cohort="([^"]+)"', html)


def cells_of(html: str, kind: str = "fin-subrow") -> list:
    """Each row of a kind as {column key: its text}."""
    out = []
    for tr in re.findall(r'<tr class="%s[^"]*"[^>]*>(.*?)</tr>' % kind, html, flags=re.S):
        cells = re.findall(r'<td class="k-([a-z_]+)[^"]*">(.*?)</td>', tr, flags=re.S)
        out.append({k: re.sub(r"<[^>]+>", "", v).replace("&quot;", '"').strip() for k, v in cells})
    return out


# ---- drawn ------------------------------------------------------------------------------------------------------------
def test_the_list_has_the_switch_and_the_view_draws_the_servers_figures_approved_first_and_tinted():
    v = real_view()
    listed, cohorts = render([case("list"), case()], v)
    assert 'data-scview="cohorts"' in listed and 'data-scview="list"' in listed and "fin-cohorts-t" not in listed
    assert '<button type="button" class="fin-btn" aria-pressed="false" data-scview="cohorts">' + ch.VIEW_WORD in listed
    # the view: no tiles of the payments, its own figures - "אושר - ממתין לתשלום" first, the one tinted
    assert "fin-states" not in cohorts and "fin-cohorts-t" in cohorts
    figs = re.findall(r'<div( class="fin-hi")?><dt>([^<]+)</dt><dd><bdi>([^<]+)</bdi>', cohorts)
    assert [(bool(h), dt) for h, dt, _ in figs] == [(True, ch.PARTS["approved"]["label"]), (False, ch.BALANCE_WORD),
                                                    (False, ch.PAID_WORD), (False, ch.EXPECTED_WORD)]
    assert [x for _h, _dt, x in figs] == ["6,000 ₪", "26,000 ₪", "18,000 ₪", "44,000 ₪"]
    heads = re.findall(r'<th class="k-([a-z_]+)[^"]*">([^<]*)</th>', cohorts)
    # the price and the students counted are said under the cohort's name, so the table fits monday's widths
    assert [k for k, _ in heads] == ["more", "cohort", "expected", "paid", "part_approved", "part_submitted", "part_hq",
                                     "part_not_submitted", "part_no_status", "balance"]
    assert re.findall(r'<th class="[^"]*fin-hi[^"]*">([^<]+)</th>', cohorts) == [ch.PARTS["approved"]["label"]]
    assert 'data-export="b2c_cohorts"' in cohorts and "<p class=\"fin-about\">%s</p>" % ch.ABOUT.replace('"', "&quot;") in cohorts
    # the cohorts in the server's order, each line its numbers, as money with agorot
    assert rows_of(cohorts) == ["C1", "C2"]
    line = cells_of(cohorts, "fin-rowbtn")[0]
    assert line["cohort"].startswith("A701") and "מחיר 10,000 ₪ · 2 %s · פעיל" % ch.STUDENTS_WORD in line["cohort"]
    assert line["part_approved"] == "6,000 ₪" and line["part_submitted"] == "-" and line["balance"] == "6,000 ₪"
    assert line["expected"] == "20,000 ₪"


def test_a_cohort_opens_to_its_students_each_balance_in_its_own_parts_column():
    shut, opened = render([case(), case(cohorts={"C1": True})])
    assert "fin-subrow" not in shut and 'aria-expanded="false"' in shut
    assert 'data-cohort="C1" tabindex="0" aria-expanded="true"' in opened
    students = cells_of(opened)
    names = [s["cohort"] for s in students if "cohort" in s]
    assert names[0].startswith("רונית כהן") and "אושר - ממתין לתשלום" in names[0]
    one = students[0]
    assert one["part_approved"] == "6,000 ₪" and one["balance"] == "6,000 ₪" and one["expected"] == "10,000 ₪"
    assert one["paid"] == "4,000 ₪" and all(one[k] == "-" for k in ("part_submitted", "part_hq", "part_not_submitted",
                                                                  "part_no_status"))
    settled = students[1]
    assert settled["paid"] == "10,000 ₪" and settled["balance"] == "-"
    waived = students[2]
    assert waived["cohort"].endswith('ויתור: "נדחה"') and waived["expected"] == "-" and waived["balance"] == "-"
    assert '<tr class="fin-subrow fin-subrow-off">' in opened
    # what the line holds besides its students, said under them
    assert "ויתור: סטודנט/ית אחד/ת" in opened
    # each student's name opens their enrollment in monday, the cohort's code its own item; no icon column here
    assert '<a class="fin-name" data-open="E1" href="https://analiza-college-group.monday.com/boards/5092151346/pulses/E1"' in opened
    assert '<a class="fin-name" data-open="C1" href="https://analiza-college-group.monday.com/boards/5089806632/pulses/C1"' in opened
    assert "k-open" not in opened


def test_what_an_out_student_paid_is_struck_through_and_a_students_chips_are_the_servers():
    s = snap([sc(1, paid="4000", enr=["E1"]), sc(2, paid="12000", enr=["E2"])],
             cohorts=[coh(roster=["E1", "E2"])], enrollments=[enr("E1", voucher="10"), enr("E2", voucher="1")])
    html = render([case(cohorts={"C1": True})], real_view(s))[0]
    assert re.search(r'<s class="fin-subtle" title="[^"]*: לא נספר"><bdi>4,000 ₪</bdi></s>', html)
    assert '<span class="fin-chip">%s</span>' % ch.NOTES["overpaid"] in html
    assert "%s: %s" % (ch.OVERPAID_WORD, "2,000.00 ₪") in html                   # a sentence carries agorot, as the details do


def test_money_filed_by_its_lead_that_no_student_holds_is_said_on_the_cohorts_line_shut_or_open():
    # the review of D4: it left no balance smaller, so the line says it - on the line itself, not only when opened; one a
    # student holds is said on them
    s = snap([sc(1, paid="4000", lead=["L1"]), sc(2, paid="3950.5", lead=["L2"])],
             cohorts=[coh(roster=["E1", "E2"])], enrollments=[enr("E1", voucher="1", lead=["L2"]), enr("E2", voucher="17")],
             leads=[lead("L1", "C1"), lead("L2", "C1")])
    v = real_view(s)
    shut, opened = render([case(), case(cohorts={"C1": True})], v)
    line = cells_of(shut, "fin-rowbtn")[0]
    assert line["cohort"].endswith(" · %s 4,000 ₪" % ch.UNLINKED_WORD)
    assert "%s: תקבול אחד, 4,000.00 ₪" % ch.UNLINKED_WORD in opened
    assert '<span class="fin-chip">%s</span>' % ch.NOTES["by_lead"] in opened
    quiet = cells_of(render([case()])[0], "fin-rowbtn")[0]
    assert ch.UNLINKED_WORD not in quiet["cohort"]


def test_an_amount_is_never_rounded_its_agorot_shown_where_it_has_them():
    s = snap([sc(1, paid="3950.5", enr=["E1"]), sc(2, paid="4000", enr=["E2"])], cohorts=[coh(roster=["E1", "E2"])],
             enrollments=[enr("E1", voucher="1"), enr("E2", voucher="1")])
    html = render([case(cohorts={"C1": True})], real_view(s))[0]
    line = cells_of(html, "fin-rowbtn")[0]
    assert line["paid"] == "7,950.50 ₪" and line["part_approved"] == "12,049.50 ₪" and line["expected"] == "20,000 ₪"
    students = cells_of(html)
    assert [x["balance"] for x in students] == ["6,049.50 ₪", "6,000 ₪"]


def test_the_cohorts_without_a_price_are_listed_on_demand_with_why():
    shut, opened = render([case(), case(unpriced=True)])
    assert "%s: 1" % ch.UNPRICED_TITLE in shut and 'data-unpriced="1" aria-expanded="false">הצגה' in shut and "<ul>" not in shut
    assert re.search(r"<li><bdi class=\"fin-nw\"><bdi dir=\"ltr\">A703</bdi></bdi> [^<]* · פעיל · אין מחיר</li>", opened)


def test_a_line_that_does_not_tie_is_said_and_never_exported():
    v = real_view()
    v["b2c"]["cohorts"]["checks"] = {"ties": False}
    html = render([case()], v)[0]
    assert '<p class="fin-badtext">%s</p>' % ch.NOT_TIED in html
    assert re.search(r'data-export="b2c_cohorts" disabled', html)


def test_the_search_on_the_view_picks_the_cohorts_the_servers_filter_picks():
    v = real_view()
    part = v["b2c"]["cohorts"]
    queries = ["", "a701", "A702", "כהן", "רונית", "קורס", " a70 ", "zzz", "E2", "שוברים", "a703"]
    pages = render([case(query=q) for q in queries], v)
    for q, html in zip(queries, pages):
        assert rows_of(html) == [x["id"] for x in ch.filter_cohorts(part, q)], q
        assert len(re.findall(r"<li>", render([case(query=q, unpriced=True)], v)[0])) == len(ch.filter_unpriced(part, q))


def test_every_label_the_view_draws_is_defined_in_the_students_legend_and_the_legend_has_the_views_part():
    s = snap([sc(1, paid="4000", enr=["E1"]), sc(2, paid="15000", enr=["E3"]), sc(3, paid="4000", enr=["E5", "E6"])],
             cohorts=[coh(roster=["E1", "E2", "E3", "E4", "E5", "E6"]), coh("C2", "A702", price="")],
             enrollments=[enr("E1", voucher="4"), enr("E2", voucher="31"), enr("E3", voucher="1", person="P1"),
                          enr("E4", voucher="17", person="P1"), enr("E5", voucher="1"), enr("E6", voucher="2")])
    v = real_view(s)
    pages = render([case(cohorts={"C1": True}, unpriced=True), case(legend=True)], v)
    known = fd.legend_terms("b2c") | {x["term"] for x in v["b2c"]["cohorts"]["legend"]["items"]} | MONDAY_TITLES["b2c"] | CONTROLS
    missing = {label for html in pages for label in _labels(html)
               if label not in known and not all(w in known for w in _words_of(label))}
    assert not missing, sorted(missing)
    legend = pages[1]
    assert '<h3 class="fin-legend-part">%s</h3>' % ch.LEGEND_TITLE in legend
    for p in ch.PARTS.values():
        assert "<dt>%s</dt>" % p["label"] in legend
    # every chip a student can carry is in it
    assert set(ch.NOTES.values()) <= {x["term"] for x in v["b2c"]["cohorts"]["legend"]["items"]}


@pytest.mark.parametrize("state", ["unread", "error"])
def test_a_view_not_read_or_not_computed_says_why_in_the_servers_words_and_the_list_is_as_it_is(state):
    # the review of D4: a reading whose filter monday refused, or a computation of the view that failed, shows no cohort and
    # no money, says why where the table would be, and offers no file; the switch stays, and the payments' list is as it is
    whole = real_view()
    if state == "unread":
        v = real_view(dict(cohort_snapshot(), cohorts_read={"ok": False, "reason": "track_refused", "code": "graphql"}))
        said = ch.UNREAD_SAID["track_refused"]
    else:
        v = dict(whole, b2c=dict(whole["b2c"], cohorts=ch.not_shown("error", "crashed")))
        said = ch.CRASHED_SAID
    assert v["b2c"]["rows"] == whole["b2c"]["rows"] and v["b2c"]["cohorts"]["state"] == state
    cohorts, listed = render([case(), case("list")], v)
    (whole_list,) = render([case("list")], whole)
    part = v["b2c"]["cohorts"]
    assert '<p class="fin-badtext">%s</p>' % part["said"].replace('"', "&quot;") in cohorts
    assert said.replace('"', "&quot;") in cohorts
    assert "fin-cohorts-t" not in cohorts and "fin-figs" not in cohorts and 'data-export="b2c_cohorts"' not in cohorts
    assert 'data-scview="list"' in cohorts and 'data-scview="cohorts"' in cohorts
    assert listed == whole_list


def test_a_view_without_the_cohorts_draws_the_list_without_the_switch():
    # an answer of a server before D4 (the hand-made view has no `cohorts`): the list as it was
    html = render([case()], hand_made_view())[0]
    assert "data-scview" not in html and "fin-cohorts-t" not in html and "fin-states" in html


# ---- driven: the switch, a cohort opened, the file, the usage --------------------------------------------------------
HARNESS = r"""
const vm = require("vm"), fs = require("fs");
const input = JSON.parse(fs.readFileSync(0, "utf8"));
const handlers = {}, docs = {}, posts = [], usage = [], executed = [];
global.document = { visibilityState: "visible", activeElement: null, body: { appendChild() {} },
  addEventListener(t, f) { (docs[t] = docs[t] || []).push(f); }, getElementById() { return null; },
  createElement() { return { setAttribute() {}, remove() {}, click() {}, style: {} }; } };
global.window = { scrollY: 0, scrollTo() {}, open() {}, location: { href: "https://desk.test/desk/" }, addEventListener() {},
  crypto: require("crypto").webcrypto };
let html = "";
const root = { get innerHTML() { return html; }, set innerHTML(v) { html = v; }, addEventListener(t, f) { handlers[t] = f; },
  contains() { return true; }, querySelector() { return null; }, querySelectorAll() { return []; } };
vm.runInThisContext(fs.readFileSync(process.argv[1], "utf8"));
const F = window.FinanceDesk;
const answers = input.answers || {};
function answer(key) { const a = (answers[key] || []).shift(); return a ? Promise.resolve(a) : new Promise(() => {}); }
const deps = { api: (p) => answer("GET " + p.split("?")[0]),
  post: (p, body) => { if (p === "/api/usage") { usage.push(body); return Promise.resolve({}); } posts.push([p, body]); return answer("POST " + p); },
  monday: { get: () => Promise.resolve({ data: {} }), api: () => Promise.resolve({ data: {} }),
    execute: (what, args) => { executed.push({ what: what, args: args }); return Promise.resolve(); } },
  board: "5093448677", later: (fn) => fn(), useFlushMs: 0 };
/* a click on an element of the desk carrying `ds`: the desk's own handler on its root, then the document's */
function click(ds, inside) {
  const el = { dataset: ds, classList: { contains: () => false }, getAttribute: () => "", disabled: false, textContent: "" };
  const keys = Object.keys(ds).map((k) => "[data-" + k.replace(/[A-Z]/g, (m) => "-" + m.toLowerCase()));
  const target = { closest: (sel) => (keys.some((k) => sel.indexOf(k) !== -1) ? el : inside && sel.indexOf(inside) !== -1 ? {} : null) };
  const ev = { target: target, preventDefault() {} };
  handlers.click(ev); (docs.click || []).forEach((f) => f(ev));
}
const settle = async () => { for (let i = 0; i < 40; i++) { await new Promise((r) => setTimeout(r, 0)); } };
(async () => {
  await F.mount(root, deps);
  await settle();
  const steps = [];
  for (const step of input.steps) {
    if (step.click) { click(step.click, step.inside); }
    if (step.key) { (docs.keydown || []).forEach((f) => f({ key: step.key, target: { matches: (s) => s === "[data-cohort]",
      click: () => click({ cohort: step.on }) }, preventDefault() {} })); }
    await settle();
    steps.push(html);
  }
  await new Promise((r) => setTimeout(r, 5));
  fs.writeSync(1, JSON.stringify({ steps: steps, posts: posts, usage: usage, executed: executed }));
  process.exit(0);
})();
"""


def drive(steps: list, v: dict | None = None) -> dict:
    data = {"answers": {"GET /api/desk": [body_of(v or real_view())],
                        "POST /api/desk/export-link": [{"url": "/api/desk/export.xlsx?tab=b2c_cohorts&sig=x", "expires_in": 120}]},
            "steps": steps}
    out = subprocess.run([NODE, "-e", HARNESS, str(PAGE)], input=json.dumps(data, ensure_ascii=False), capture_output=True,
                         text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_the_switch_a_cohort_a_key_the_file_and_the_way_back_and_every_event_in_the_servers_words():
    got = drive([{"click": {"scview": "cohorts"}}, {"click": {"cohort": "C1"}}, {"click": {"cohort": "C2"}, "inside": "a,button"},
                 {"key": "Enter", "on": "C2"}, {"click": {"unpriced": "1"}}, {"click": {"export": "b2c_cohorts"}},
                 {"click": {"scview": "list"}}])
    view, one, link, key, unpriced, _exported, back = got["steps"]
    assert "fin-cohorts-t" in view and "fin-subrow" not in view
    assert "fin-subrow" in one and 'data-cohort="C1" tabindex="0" aria-expanded="true"' in one
    # a click on a link or a button inside a cohort's row is that element's, never the row's
    assert link.count('aria-expanded="true"') == one.count('aria-expanded="true"')
    assert 'data-cohort="C2" tabindex="0" aria-expanded="true"' in key                 # Enter on a focused row opens it
    assert "<ul>" in unpriced
    assert "fin-cohorts-t" not in back and "fin-states" in back
    # the file: the list b2c_cohorts, with the search box only, opened in a new tab through monday
    path, body = got["posts"][-1]
    assert path == "/api/desk/export-link" and body["tab"] == "b2c_cohorts"
    assert body["state"] == "" and body["period"] == "" and body["customer"] == "" and body["taken"] == TAKEN
    assert got["executed"][-1] == {"what": "openLinkInTab", "args": {"url": "https://desk.test/api/desk/export.xlsx?tab=b2c_cohorts&sig=x"}}
    # every event the view sent is a word of the server's vocabulary
    events = [e for b in got["usage"] for e in b["events"]]
    kinds = [(e["k"], e.get("d")) for e in events]
    assert ("view", {"view": "cohorts"}) in kinds and ("view", {"view": "list"}) in kinds
    assert ("row", {"state": "cohort", "open": True}) in kinds and ("export", {"list": "b2c_cohorts"}) in kinds
    rows, refused = clean_batch({"desk": "finance", "session": "abcdefghabcdefgh", "events": events}, 1)
    assert refused == 0 and len(rows) == len(events)


# ---- in Chromium --------------------------------------------------------------------------------------------------------
def _playwright_root() -> str | None:
    if NODE is None:
        return None
    npm = shutil.which("npm")
    roots = [subprocess.run([npm, "root", "-g"], capture_output=True, text=True).stdout.strip()] if npm else []
    for r in [x for x in roots if x] + [os.environ.get("NODE_PATH", "")]:
        if r and os.path.isdir(os.path.join(r, "playwright")):
            return r
    return None


PW_ROOT = _playwright_root()


def stub_app(log: list, v: dict):
    """This service's page routes, behind a stub of /api/desk answering the server's own view of a reading, and the totals
    of the cohorts the server's own way (cohort_totals of filter_cohorts)."""
    from fastapi import FastAPI, Request

    from app.desk.routes import router as desk_router
    from app.main import CSP

    app = FastAPI()

    @app.middleware("http")
    async def record(request: Request, call_next):
        body = (await request.body()).decode("utf-8", "replace")
        log.append({"method": request.method, "path": request.url.path, "query": request.url.query, "body": body})
        resp = await call_next(request)
        resp.headers["Content-Security-Policy"] = CSP
        return resp

    @app.get("/api/desk")
    def desk():
        return page_answer(json_safe(v), taken_at=TAKEN)

    @app.get("/api/desk/totals")
    def totals(tab: str = "b2c", q: str = ""):
        if tab != "b2c_cohorts":
            return {"other_reading": True, "taken_at": TAKEN}
        rows = ch.filter_cohorts(v["b2c"]["cohorts"], q)
        return json_safe({"count": len(rows), "sums": ch.cohort_totals(rows), "taken_at": TAKEN})

    @app.post("/api/desk/export-link")
    def export_link():
        return {"url": "/api/desk/export.xlsx?tab=b2c_cohorts&exp=1&sig=stub", "expires_in": 120}

    app.include_router(desk_router)
    return app


VISIT = r"""
const { chromium } = require("playwright");
const [base, sdk, shot, width] = process.argv.slice(1);
(async () => {
  const browser = await chromium.launch();
  const out = { errors: [], steps: {} };
  const page = await browser.newPage({ viewport: { width: Number(width), height: 820 }, deviceScaleFactor: 1 });
  page.on("pageerror", (e) => out.errors.push("pageerror: " + e));
  page.on("console", (m) => { if (m.type() === "error") { out.errors.push("console: " + m.text()); } });
  await page.route("**/desk/vendor/monday-sdk.js*", (r) => r.fulfill({ contentType: "application/javascript", body: sdk }));
  await page.goto(base + "/desk/");
  await page.waitForSelector("#screen-finance:not([hidden]) .fin-tabs", { timeout: 15000 });
  await page.click('[data-scview="cohorts"]');
  await page.waitForSelector("table.fin-cohorts-t");
  await page.waitForSelector("table.fin-cohorts-t tfoot");
  out.steps.view = { rows: await page.$$eval("tr[data-cohort]", (r) => r.map((x) => x.dataset.cohort)),
    foot: await page.$eval("table.fin-cohorts-t tfoot", (f) => f.innerText),
    tint: await page.$eval("th.fin-hi", (t) => getComputedStyle(t).backgroundColor),
    plain: await page.$eval("th.k-paid", (t) => getComputedStyle(t).backgroundColor),
    scrolls: await page.$eval(".fin-tw", (w) => w.classList.contains("fin-tw-x")) };
  await page.click('tr[data-cohort="C1"] td.k-paid');
  await page.waitForSelector("tr.fin-subrow");
  out.steps.opened = { students: await page.$$eval("tr.fin-subrow td.k-cohort a.fin-name", (a) => a.map((x) => x.innerText)),
    scrolls: await page.$eval(".fin-tw", (w) => w.classList.contains("fin-tw-x")) };
  await page.focus('tr[data-cohort="C2"]');
  await page.keyboard.press("Enter");
  await page.waitForSelector('tr[data-cohort="C2"][aria-expanded="true"]');
  if (shot) { await page.screenshot({ path: shot, fullPage: true }); }
  await page.fill("#fin-q", "כהן");
  await page.waitForFunction(() => document.querySelectorAll("tr[data-cohort]").length === 1, null, { timeout: 15000 });
  out.steps.searched = await page.$$eval("tr[data-cohort]", (r) => r.map((x) => x.dataset.cohort));
  await page.click('[data-export="b2c_cohorts"]');
  await page.waitForFunction(() => window.__executed.length > 0, null, { timeout: 15000 });
  out.steps.exported = await page.evaluate(() => window.__executed);
  await page.click('[data-scview="list"]');
  await page.waitForSelector(".fin-states");
  out.steps.back = await page.$$eval("table.fin-cohorts-t", (t) => t.length);
  await browser.close();
  console.log(JSON.stringify(out));
})().catch((e) => { console.log(JSON.stringify({ crashed: String(e && e.stack || e) })); process.exit(1); });
"""


@pytest.mark.skipif(PW_ROOT is None, reason="node with playwright is not installed")
@pytest.mark.parametrize("width", [1280, 1434])
def test_the_view_in_a_browser_opens_searches_exports_and_fits_monday_s_width(width):
    import socket

    import uvicorn

    from tests.test_desk_browser import fake_sdk

    log: list = []
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(stub_app(log, real_view()), host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 15
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    assert server.started
    try:
        base = "http://127.0.0.1:%d" % port
        shot = os.environ.get("DESK_COHORTS_SCREENSHOT", "")
        run = subprocess.run([NODE, "-e", VISIT, base, fake_sdk({}), shot.replace(".png", "-%d.png" % width) if shot else "",
                              str(width)], capture_output=True, text=True, timeout=120, env=dict(os.environ, NODE_PATH=PW_ROOT))
    finally:
        server.should_exit = True
        thread.join(timeout=10)
    assert run.returncode == 0, (run.stdout[-2000:], run.stderr[-2000:])
    got = json.loads(run.stdout.strip().splitlines()[-1])
    steps = got["steps"]
    assert got["errors"] == []
    assert steps["view"]["rows"] == ["C1", "C2"] and "26,000 ₪" in steps["view"]["foot"]       # the server's totals
    assert steps["view"]["tint"] != steps["view"]["plain"]                     # the one tinted column
    # at monday's widths the table fits its frame, opened or not (QA 4 and QA 9 of 24.11)
    assert steps["view"]["scrolls"] is False and steps["opened"]["scrolls"] is False
    assert steps["opened"]["students"] == ["רונית כהן", "סטודנט/ית E2", "סטודנט/ית E3"]
    assert steps["searched"] == ["C1"]
    assert steps["exported"][0]["what"] == "openLinkInTab"
    assert steps["back"] == 0
    posted = [x for x in log if x["path"] == "/api/desk/export-link"]
    assert json.loads(posted[0]["body"])["tab"] == "b2c_cohorts"
    assert any(x["path"] == "/api/desk/totals" and "tab=b2c_cohorts" in x["query"] for x in log)
