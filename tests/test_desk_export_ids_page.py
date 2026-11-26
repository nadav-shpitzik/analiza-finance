"""D4 (26.11): the students' Excel file with the ID number - the page's half (frontend/finance.js, its block "D4 ... the
students' Excel file with the ID number"; the file written by frontend/xlsx.js). The server's half is
tests/test_desk_export_ids.py. In node here (needs node; skipped without it), and in Chromium through Playwright against
this service's own app (needs Playwright too).

Held here: the button sits on the students' list only, beside its "ייצוא לאקסל"; one press asks the server for the list's
table with the very filter the list's own file is asked with, reads the leads' numbers from monday as the person - each lead
once, in pages of at most 100 asked with monday's limit, one request at a time - fills "ת.ז" by analiza-metrics' rule (the
first of a row's leads, in monday's order, that holds one), and saves the file under the server's name; a row with no number
is counted by why, in the flash and in "פרטים". A read that fails or is cut makes no file, says what failed, and offers the
file without the ID number and another try. And the numbers go nowhere: not to the server, not into a usage event (only
`export_ids` with the rows or a closed word), not to monday in a request, not into the page's drawing or a flash, not to the
console - only into the file."""
from __future__ import annotations

import base64
import io
import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import openpyxl
import pytest

from app.desk import export as fx
from app.desk import view as fd
from app.desk.usage import clean_batch
from app.desk.view import json_safe
from tests.desk_fixtures import TODAY, b2, lead, sc, snap
from tests.desk_page_view import TAKEN
from tests.desk_page_view import answer as page_answer
from tests.test_desk import desk  # noqa: F401 - `desk` is the fixture (the browser's visit runs this service's own app)

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")
FRONT = Path(__file__).resolve().parents[1] / "frontend"
PAGE, WRITER = FRONT / "finance.js", FRONT / "xlsx.js"
LEADS_BOARD, ID_COLUMN = "5089697337", "text_mkzvem4"
NAME = "תקבולים-סטודנטים_הכל_כל-התקופה_24.11.2026.xlsx"

# mount() with a fake monday holding the Leads board's ID numbers (`leads`; `moved`: a lead monday answers from another
# board; `plan`: per request, in order - a refusal, GraphQL errors, no board, no column, or held forever), the server's
# answers by request, and the file the page saves kept as bytes (deps.saveFile). Every console line is kept too.
HARNESS = r"""
const vm = require("vm"), fs = require("fs");
const input = JSON.parse(fs.readFileSync(0, "utf8"));
const toasts = [], sent = [], posts = [], usage = [], saved = [], handlers = {}, logged = [];
for (const k of ["log", "info", "warn", "error", "debug"]) { console[k] = (...a) => { logged.push(a.map(String).join(" ")); }; }
global.document = { visibilityState: "visible", activeElement: null, body: { appendChild() {} },
  addEventListener() {}, getElementById() { return null; },
  createElement() { const el = { setAttribute() {}, remove() {}, click() {}, style: {} };
    Object.defineProperty(el, "textContent", { set(v) { toasts.push(v); } }); return el; } };
global.window = { scrollY: 0, scrollTo() {}, open() {}, location: { href: "https://desk.test/desk/" },
  addEventListener() {}, crypto: require("crypto").webcrypto };
let html = "";
const root = { get innerHTML() { return html; }, set innerHTML(v) { html = v; }, addEventListener(t, f) { handlers[t] = f; },
  contains() { return true; }, querySelector() { return null; }, querySelectorAll() { return []; } };
if (!input.noWriter) { vm.runInThisContext(fs.readFileSync(process.argv[2], "utf8")); }
vm.runInThisContext(fs.readFileSync(process.argv[1], "utf8"));
const F = window.FinanceDesk;
const LEADS = "5089697337", leads = input.leads || {}, plan = input.plan || [];
const has = (o, k) => Object.prototype.hasOwnProperty.call(o, k);
const monday = input.outside ? null : {
  get: (what) => Promise.resolve({ data: what === "context" ? {} : "" }),
  execute: () => Promise.resolve(),
  api: (query, opts) => {
    const v = (opts || {}).variables || {};
    sent.push({ query: query, variables: v });
    const a = plan.shift() || {};
    if (a.reject) { return Promise.reject(a.reject); }
    if (a.errors) { return Promise.resolve({ errors: a.errors }); }
    if (a.hold) { return new Promise(() => {}); }
    const b = String((v.b || [])[0]), cells = b === LEADS ? leads : {};
    /* monday's items(ids:): the items it holds, 25 at most without a limit; one moved answers from its new board */
    const items = (v.i || []).filter((id) => has(cells, id)).slice(0, v.n || 25).map((id) => ({ id: id,
      board: { id: (input.moved || {})[id] || b }, column_values: (v.c || []).map((c) => ({ id: c, text: cells[id] })) }));
    return Promise.resolve({ data: { items: items,
      boards: a.noBoard ? [] : [{ id: b, columns: a.noColumn ? [] : (v.c || []).map((c) => ({ id: c })) }] } });
  } };
const answers = input.answers || {};
function answer(key) {
  const a = (answers[key] || []).shift();
  return !a ? new Promise(() => {}) : a.reject ? Promise.reject(a.reject) : Promise.resolve(a.body);
}
const deps = { api: (p) => answer("GET " + p),
  post: (p, body) => {
    if (p === "/api/usage") { usage.push(body); return Promise.resolve({ stored: body.events.length, refused: 0 }); }
    posts.push([p, body === undefined ? null : body]); return answer("POST " + p);
  },
  monday: monday, board: "5093448677", later: (fn) => fn(), today: () => "2026-11-24", useFlushMs: 0,
  mondayWaitMs: input.mondayWaitMs,
  saveFile: (bytes, name) => { saved.push({ name: name, b64: Buffer.from(bytes).toString("base64") }); } };
const settle = async () => { for (let i = 0; i < 80; i++) { await new Promise((r) => setTimeout(r, 0)); } };
function click(ds) {
  const el = { dataset: ds, classList: { contains: () => false }, closest: () => null, getAttribute: () => "",
    disabled: false, textContent: "" };
  handlers.click({ target: { closest: (sel) => sel.indexOf("[data-") !== -1 ? el : null }, preventDefault() {} });
}
(async () => {
  await F.mount(root, deps);
  await settle();
  const steps = [];
  for (const step of input.steps || []) {
    if (step.show) { F._show(step.show); }
    if (step.click) { click(step.click); }
    if (step.hook) { F._exportIds(); }
    if (step.wait) { await new Promise((r) => setTimeout(r, step.wait)); }
    await settle();
    steps.push({ html: F._show({}), idx: F._idsState(), saved: saved.length, sent: sent.length });
  }
  fs.writeSync(1, JSON.stringify({ steps: steps, sent: sent, posts: posts, usage: usage, saved: saved, toasts: toasts,
    logged: logged }));
  process.exit(0);
})();
"""

ALL = {"show": {"tab": "b2c", "sc": {"state": "", "period": "", "view": "list"}, "query": ""}}
PRESS = {"click": {"exportIds": "1"}}


def run(answer_body: dict, table: dict | None, steps=(ALL, PRESS), leads: dict | None = None, **extra) -> dict:
    answers = {"GET /api/desk": [{"body": answer_body}]}
    if table is not None:
        answers["POST /api/desk/export.json"] = [table] if "reject" in table else [{"body": table}]
    data = {"answers": answers, "leads": leads or {}, "steps": list(steps), **extra}
    out = subprocess.run([NODE, "-e", HARNESS, str(PAGE), str(WRITER)], input=json.dumps(data, ensure_ascii=False),
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-3000:]
    return json.loads(out.stdout)


def built(s: dict, q: str = "") -> tuple:
    """The page's answer of the server's own view of a snapshot, and the table the server answers for the list "הכל" of the
    whole span (export.py `b2c_table` of view.filter_b2c, as the route builds it)."""
    v = fd.build_view(s, TODAY)
    rows = fd.filter_b2c(v["b2c"], "", "", q)
    about = {"taken_at": "24/11/2026 10:00:00", "screen_taken_at": "", "complete": True, "state_label": "הכל", "q": q,
             "books_lines": [], "books_other": False, "period_label": "כל התקופה"}
    table = json_safe(dict(fx.b2c_table(rows, about), name=NAME, taken_at=TAKEN, count=len(rows)))
    return page_answer(json_safe(v), taken_at=TAKEN), table


def book_of(got: dict):
    assert len(got["saved"]) == 1, got["saved"]
    return openpyxl.load_workbook(io.BytesIO(base64.b64decode(got["saved"][0]["b64"])))


def ids_by_row(wb) -> dict:
    ws = wb["תקבולים"]
    heads = [c.value for c in ws[1]]
    at, key = heads.index("ת.ז") + 1, heads.index("מזהה במאנדיי") + 1
    return {ws.cell(row=r, column=key).value: ws.cell(row=r, column=at) for r in range(2, ws.max_row + 1)
            if ws.cell(row=r, column=key).value}


def export_events(got: dict) -> list:
    return [e for b in got["usage"] for e in b["events"] if e["k"] == "export_ids"]


# --- the read: each lead once, in pages of at most 100, one at a time ---------------------------------------------------

def many():
    """230 payments: each its own lead; every tenth a second one; every fiftieth the first lead's too (asked once)."""
    rows, leads = [], {}
    for i in range(1, 231):
        mine = ["7%05d" % i] + (["8%05d" % i] if i % 10 == 0 else []) + (["700001"] if i % 50 == 0 else [])
        rows.append(sc(i, lead=mine, created="2026-10-%02dT08:00:00Z" % (1 + i % 28)))
        for x in mine:
            leads[x] = "%09d" % (int(x) % 1000)
    return snap(rows, [b2(1)], leads=[lead(x) for x in leads]), leads


def test_the_numbers_are_read_once_each_in_pages_of_100_with_mondays_limit_one_request_at_a_time():
    s, leads = many()
    body, table = built(s)
    got = run(body, table, leads=leads)
    reads = got["sent"]
    distinct = list(dict.fromkeys(x for k in table["sheets"][0]["keys"] for x in k["lead_ids"]))
    assert len(distinct) == 253
    assert [len(r["variables"]["i"]) for r in reads] == [100, 100, 53]
    assert [x for r in reads for x in r["variables"]["i"]] == distinct                  # each lead once, in order
    for r in reads:
        v = r["variables"]
        assert v == {"i": v["i"], "n": len(v["i"]), "c": [ID_COLUMN], "b": [LEADS_BOARD]}
        assert r["query"].startswith("query (") and "items(ids: $i, limit: $n)" in r["query"] and "mutation" not in r["query"]
    # the table asked with the list's own filter, exactly as its Excel file is asked
    assert got["posts"][0] == ["/api/desk/export.json", {"tab": "b2c", "state": "", "period": "", "customer": "", "q": "",
                                                         "taken": TAKEN}]
    ids = ids_by_row(book_of(got))
    assert len(ids) == 230 and all(c.data_type == "s" and c.number_format == "@" for c in ids.values())
    assert ids["1"].value == "000000001" and ids["50"].value == "000000050" and ids["230"].value == "000000230"
    assert got["saved"][0]["name"] == NAME
    assert got["toasts"][-1] == "קובץ האקסל עם ת.ז יורד: 230 תקבולים."
    assert [e["d"] for e in export_events(got)] == [{"results": 230}]
    assert got["steps"][-1]["idx"] is None


def test_a_page_on_its_way_holds_the_next_and_a_second_press_starts_nothing():
    s, leads = many()
    body, table = built(s)
    got = run(body, table, leads=leads, plan=[{"hold": True}], steps=[ALL, PRESS, PRESS])
    assert len(got["sent"]) == 1 and len(got["posts"]) == 1 and got["saved"] == []
    reading = got["steps"][-1]
    assert reading["idx"]["phase"] == "reading" and reading["idx"]["total"] == 253
    assert "קורא ממאנדיי, בשמך, ת.ז של 253 לידים..." in reading["html"]
    assert re.search(r'<button class="fin-btn" type="button" data-export-ids="1" disabled[^>]*>מכין קובץ עם ת\.ז\.\.\.</button>',
                     reading["html"])


# --- the old file's rule, and the rows without a number -----------------------------------------------------------------

def rule_snapshot():
    """r1 two leads, the first empty; r2 two leads, both with a number; r3 no lead; r4 a lead monday does not answer; r5
    a lead without a number; r6 a lead monday does not answer before one it does; r7 a lead answered from another board."""
    rows = [sc(1, lead=["6001", "6002"]), sc(2, lead=["6003", "6004"]), sc(3, lead=[]), sc(4, lead=["6009"]),
            sc(5, lead=["6005"]), sc(6, lead=["6009", "6002"]), sc(7, lead=["6007"])]
    leads = {"6001": "", "6002": "000000026", "6003": "000000034", "6004": "000000042", "6005": "", "6007": "000000075"}
    return snap(rows, [b2(1)], leads=[lead(x) for x in ("6001", "6002", "6003", "6004", "6005", "6007", "6009")]), leads


def test_each_row_takes_the_first_of_its_leads_that_holds_a_number_and_the_rest_are_counted_by_why():
    s, leads = rule_snapshot()
    body, table = built(s)
    got = run(body, table, leads=leads, moved={"6007": "5000000001"})
    wb = book_of(got)
    ids = {k: c.value for k, c in ids_by_row(wb).items()}
    assert ids == {"1": "000000026", "2": "000000034", "3": None, "4": None, "5": None, "6": "000000026", "7": None}
    info = [(r[0].value, r[1].value) for r in wb["פרטים"].iter_rows()]
    at = [k for k, _v in info].index("ת.ז")
    assert info[at] == ("ת.ז", fx.ID_NUMBERS_IN_FILE)
    # right under it: two rows whose leads monday did not answer (r4, and r7's answered from another board), one whose
    # lead holds none, one with no lead
    assert info[at + 1:at + 4] == [
        ("שימו לב", "ל-2 תקבולים אין ת.ז בקובץ: הלידים שלהם לא נמצאו בלוח הלידים במאנדיי (נמחקו, הועברו ללוח אחר, או "
                    "שאין לך גישה אליהם)."),
        ("תקבולים שבליד שלהם אין ת.ז", 1), ("תקבולים בלי ליד", 1)]
    assert got["toasts"][-1] == "קובץ האקסל עם ת.ז יורד: 7 תקבולים. ל-4 מהם אין ת.ז, ובגיליון \"פרטים\" כתוב למה."


def test_the_rule_is_analiza_metrics_with_id_numbers():
    """with_id_numbers (analiza-metrics app/finance.py at da671b2): `next((numbers[x] for x in lead_ids if
    numbers.get(x)), "")` - the first of the leads, in their order, whose number is not empty."""
    cases = [[["a", "b"], {"a": "", "b": "2"}], [["a", "b"], {"a": "1", "b": "2"}], [[], {}], [["z"], {}],
             [["a"], {"a": ""}], [["z", "b"], {"b": "2"}], [["b", "a"], {"a": "1", "b": "2"}]]
    script = ("const vm=require('vm'),fs=require('fs');global.window={};vm.runInThisContext(fs.readFileSync(process.argv[1],'utf8'));"
              "const c=JSON.parse(fs.readFileSync(0,'utf8'));"
              "console.log(JSON.stringify(c.map(([l,g])=>window.FinanceDesk._idOf(l,Object.assign(Object.create(null),g)))));")
    out = subprocess.run([NODE, "-e", script, str(PAGE)], input=json.dumps(cases), capture_output=True, text=True, timeout=60)
    got = json.loads(out.stdout)
    assert [g["value"] for g in got] == [next((g[x] for x in ids if g.get(x)), "") for ids, g in cases]
    assert [g.get("why") for g in got] == [None, None, "no_lead", "not_found", "none", None, None]


# --- a read that fails makes no file ------------------------------------------------------------------------------------

RATE = {"errors": [{"message": "Complexity budget exhausted, query cost 30001 budget remaining 1000 out of 1000000 reset in 14 seconds"}]}


@pytest.mark.parametrize("plan, extra, code, words", [
    ([{}, RATE], {}, "rate_limited", "מאנדיי ביקשה להאט; לנסות שוב בעוד דקה"),          # cut on its second page
    ([{"noBoard": True}], {}, "denied", "אין לך גישה ללוח הלידים במאנדיי"),
    ([{"noColumn": True}], {}, "columns_changed", "העמודה ת.ז לא נמצאה בלוח הלידים; נדב צריך לבדוק"),
    ([{"errors": [{"message": "Missing required permission scope: boards:read"}]}], {}, "no_scope",
     "לאפליקציה אין הרשאה לקרוא את לוח הלידים"),
    ([{"hold": True}], {"mondayWaitMs": 40, "wait": 120}, "timeout", "מאנדיי לא ענתה בזמן"),
    ([{"reject": {"message": "Network error"}}], {}, "monday", "מספרי ת.ז לא נקראו ממאנדיי"),
])
def test_a_read_that_fails_or_is_cut_makes_no_file_and_offers_the_file_without_it(plan, extra, code, words):
    s, leads = many()
    body, table = built(s)
    wait = extra.pop("wait", 0)
    press = dict(PRESS, wait=wait) if wait else PRESS
    got = run(body, table, leads=leads, plan=plan, steps=[ALL, press, {"click": {"export": "b2c"}}], **extra)
    failed = got["steps"][1]
    assert got["saved"] == [] and failed["idx"]["phase"] == "failed" and failed["idx"]["code"] == code
    note = re.search(r'<div class="fin-idsnote" role="alert">(.*?)</div>', failed["html"]).group(1)
    assert "קובץ עם ת.ז לא נוצר: %s." % words in note
    assert '<button type="button" class="fin-linkbtn" data-export="b2c">ייצוא לאקסל בלי ת.ז</button>' in note
    assert 'data-export-ids="1">לנסות שוב</button>' in note
    assert [e["d"] for e in export_events(got)] == [{"code": code}]
    # the file without the ID number, one press away: the list's own link, its own filter, and the note goes
    link = [p for p in got["posts"] if p[0] == "/api/desk/export-link"]
    assert link == [["/api/desk/export-link", got["posts"][0][1]]] and got["steps"][2]["idx"] is None


@pytest.mark.parametrize("table, code, said", [
    ({"reject": {"error": "finance_unavailable", "status": 503}}, "finance_unavailable",
     "לא הצלחנו לקרוא עכשיו את לוחות החיובים. נסו שוב בעוד כמה דקות"),
    ({"reject": {"error": "rate_limited", "retryAfter": 30, "status": 429}}, "rate_limited", "השולחן ביקש להאט; לנסות שוב בעוד דקה"),
    ({"reject": {"error": "not_a_board_member", "status": 403}}, "not_a_board_member",
     "התצוגה הזאת מראה את לוחות החיובים, ואין לך גישה לאף אחד מהם"),
    ({"name": NAME, "sheets": [{"title": "תקבולים"}]}, "table", "השרת לא החזיר את רשימת התקבולים כפי שהקובץ צריך"),
])
def test_a_table_the_server_refuses_or_answers_in_another_shape_reads_nothing_from_monday(table, code, said):
    s, leads = many()
    body, _table = built(s)
    got = run(body, table, leads=leads)
    assert got["saved"] == [] and got["sent"] == [] and got["steps"][-1]["idx"]["code"] == code
    assert "קובץ עם ת.ז לא נוצר: %s." % said in got["steps"][-1]["html"]


def test_a_table_the_writer_cannot_write_is_no_file_and_a_page_without_the_writer_is_said():
    s, leads = rule_snapshot()
    body, table = built(s)
    bad = json.loads(json.dumps(table))
    heads = [c["head"] for c in bad["sheets"][0]["columns"]]
    bad["sheets"][0]["rows"][0][heads.index("סכום ששולם")] = "4,000"
    got = run(body, bad, leads=leads)
    assert got["saved"] == [] and got["steps"][-1]["idx"]["code"] == "file"
    assert "קובץ עם ת.ז לא נוצר: הקובץ לא נבנה בדפדפן." in got["steps"][-1]["html"]
    assert run(body, table, leads=leads, noWriter=True)["steps"][-1]["idx"]["code"] == "file"


def test_outside_monday_the_button_says_why_and_nothing_is_asked():
    s, leads = rule_snapshot()
    body, table = built(s)
    got = run(body, table, leads=leads, outside=True, steps=[ALL, {"hook": True}])
    shown = got["steps"][0]["html"]
    assert re.search(r'data-export-ids="1" disabled title="מספרי ת\.ז נקראים ממאנדיי בשמך, ולכן רק כשהתצוגה פתוחה מתוך '
                     r'מאנדיי">ייצוא לאקסל עם ת\.ז</button>', shown)
    assert got["posts"] == [] and got["saved"] == [] and got["steps"][-1]["idx"]["code"] == "outside"


# --- where the button is ---------------------------------------------------------------------------------------------

def test_the_button_is_on_the_students_list_only_beside_its_export_and_follows_its_rules():
    s, leads = rule_snapshot()
    body, table = built(s)
    got = run(body, table, leads=leads, steps=[
        ALL, {"show": {"query": "תלמיד/ה‏"}}, {"show": {"query": "", "sc": {"view": "cohorts"}}},
        {"show": {"tab": "b2b", "sc": {"view": "list"}}}])
    plain, odd, cohorts, companies = (x["html"] for x in got["steps"])
    assert re.search(r'data-export="b2c"[^>]*>ייצוא לאקסל</button><button class="fin-btn" type="button" data-export-ids="1" '
                     r'title="מספרי ת\.ז נקראים ממאנדיי בשמך, כאן בדפדפן, ולא נשלחים לשום מקום">ייצוא לאקסל עם ת\.ז</button>',
                     plain)
    # a search outside the exportable alphabet: no file of the list, and none with the ID number
    assert re.search(r'data-export-ids="1" disabled title="בחיפוש יש תו מיוחד', odd)
    assert "data-export-ids" not in cohorts and "data-export-ids" not in companies
    assert got["posts"] == [] and got["sent"] == []                              # nothing is read before the press


def test_an_incomplete_reading_offers_no_file_with_the_id_number_either():
    s, leads = rule_snapshot()
    body, table = built(s)
    body["view"]["checks"]["b2c_ok"] = False
    got = run(body, table, leads=leads, steps=[ALL])
    assert re.search(r'data-export-ids="1" disabled title="הקריאה לא שלמה', got["steps"][0]["html"])


# --- the numbers go nowhere but into the file ---------------------------------------------------------------------------

def test_the_numbers_reach_the_file_and_nothing_else():
    s, leads = rule_snapshot()
    body, table = built(s)
    got = run(body, table, leads=leads, steps=[ALL, PRESS, ALL])
    numbers = [v for v in leads.values() if v]
    # the file has the numbers the rule picks (000000042 is a second lead's, after a first that holds one: read, unused)
    assert {c.value for c in ids_by_row(book_of(got)).values()} - {None} == {"000000026", "000000034", "000000075"}
    elsewhere = json.dumps({"posts": got["posts"], "usage": got["usage"], "toasts": got["toasts"], "logged": got["logged"],
                            "html": [x["html"] for x in got["steps"]], "idx": [x["idx"] for x in got["steps"]],
                            "monday": [r["variables"] for r in got["sent"]]}, ensure_ascii=False)
    for n in numbers:
        assert n not in elsewhere, n
    assert got["logged"] == []
    # the usage events are the server's closed words: every one of them is kept, none refused
    for b in got["usage"]:
        rows, refused = clean_batch(b, 7)
        assert refused == 0 and len(rows) == len(b["events"])
    assert [e["d"] for e in export_events(got)] == [{"results": 7}]


# --- in a browser, against this service's own app -----------------------------------------------------------------------

def _playwright_root() -> str | None:
    npm = shutil.which("npm")
    roots = [subprocess.run([npm, "root", "-g"], capture_output=True, text=True).stdout.strip()] if npm else []
    for r in [x for x in roots if x] + [os.environ.get("NODE_PATH", "")]:
        if r and os.path.isdir(os.path.join(r, "playwright")):
            return r
    return None


PW_ROOT = _playwright_root()

VISIT = r"""
const { chromium } = require("playwright");
const fs = require("fs");
const [base, sdk, file] = process.argv.slice(1);
(async () => {
  const browser = await chromium.launch();
  const out = { errors: [], requests: [], steps: {} };
  const context = await browser.newContext({ acceptDownloads: true, viewport: { width: 1434, height: 900 } });
  const page = await context.newPage();
  page.on("pageerror", (e) => out.errors.push("pageerror: " + e));
  /* the page writes nothing to the console (the node test holds every call); Chromium's own warnings are not the page's */
  page.on("console", (m) => { if (m.type() !== "warning" && m.type() !== "verbose") { out.errors.push("console " + m.type() + ": " + m.text()); } });
  /* every request the page makes, to this service or anywhere else: where, how, and what it carried */
  page.on("request", (r) => out.requests.push({ url: r.url(), method: r.method(), body: r.postData() || "",
    headers: JSON.stringify(r.headers()) }));
  await page.route("**/desk/vendor/monday-sdk.js*", (r) => r.fulfill({ contentType: "application/javascript", body: sdk }));
  /* no database here: the usage batches are taken by the test itself, so every event the visit sends is seen */
  await page.route("**/api/usage", (r) => r.fulfill({ contentType: "application/json",
    body: JSON.stringify({ stored: JSON.parse(r.request().postData() || "{}").events.length, refused: 0 }) }));
  await page.goto(base + "/desk/");
  await page.waitForSelector("#screen-finance:not([hidden]) .fin-tabs", { timeout: 15000 });
  await page.click('[data-bstate=""]');
  await page.waitForSelector('tr[data-row]');
  out.steps.screen = await page.$$eval("tr[data-row]", (r) => r.map((x) => x.dataset.row));
  out.steps.button = await page.$eval("[data-export-ids]", (b) => ({ text: b.innerText, disabled: b.disabled, title: b.title }));
  const [download] = await Promise.all([page.waitForEvent("download", { timeout: 20000 }), page.click("[data-export-ids]")]);
  await download.saveAs(file);
  out.steps.name = download.suggestedFilename();
  await page.waitForFunction(() => /קובץ האקסל עם ת\.ז יורד/.test((document.querySelector(".fin-toast") || {}).innerText || ""),
    null, { timeout: 15000 });
  out.steps.flash = await page.$eval(".fin-toast", (t) => t.innerText);
  out.steps.after = await page.$$eval("[data-export-ids]", (b) => b.map((x) => x.disabled));
  /* the usage batch of the export goes out when the view is left */
  await Promise.all([page.waitForRequest((r) => r.url().endsWith("/api/usage"), { timeout: 15000 }),
    page.evaluate(() => { Object.defineProperty(document, "visibilityState", { value: "hidden", configurable: true });
      document.dispatchEvent(new Event("visibilitychange")); })]);
  out.steps.monday = await page.evaluate(() => window.__monday.slice());
  out.steps.executed = await page.evaluate(() => window.__executed.slice());
  out.steps.stored = await page.evaluate(async () => ({ local: JSON.stringify(Object.assign({}, localStorage)),
    session: JSON.stringify(Object.assign({}, sessionStorage)), cookie: document.cookie,
    dbs: indexedDB.databases ? (await indexedDB.databases()).map((d) => d.name) : [] }));
  out.steps.text = await page.evaluate(() => document.body.innerText);
  await browser.close();
  console.log(JSON.stringify(out));
})().catch((e) => { console.log(JSON.stringify({ crashed: String(e && e.stack || e) })); process.exit(1); });
"""


def browser_snapshot():
    """Payments as the list shows them, and their leads: two leads on one row (the first empty), a name that opens with
    "=", a reference with leading zeros, a row with no lead, and one whose lead monday does not answer."""
    rows = [sc(1, invoice="", receipt="1", paid="3950.5", paid_date="2026-10-04", lead=["6101", "6102"]),
            sc(2, invoice="00123", created="2026-10-06T08:00:00Z", lead=["6103"]),
            dict(sc(3, created="2026-11-02T08:00:00Z", lead=["6104"]), name='=HYPERLINK("https://example.invalid","x")'),
            sc(4, st="4", refund_date="2026-11-02", created="2026-09-05T08:00:00Z", refund="4000", lead=[]),
            sc(5, created="2026-11-03T08:00:00Z", lead=["6109"])]
    leads = {"6101": "", "6102": "012345674", "6103": "000000018", "6104": "203456789"}
    return snap(rows, [b2(1)], leads=[lead(x) for x in ("6101", "6102", "6103", "6104", "6109")]), leads


@pytest.mark.skipif(PW_ROOT is None, reason="node with playwright is not installed")
def test_in_a_browser_the_file_holds_the_screens_rows_with_their_numbers_and_the_network_holds_none(desk, monkeypatch,  # noqa: F811
                                                                                                tmp_path):
    """This service's own app (`desk`: the gate, a monday of its own for the members, the reading served), the page as it
    is served, monday's SDK a fake holding the Leads board's numbers. One press: the file comes down, opened here with
    openpyxl - the screen's rows in its order, "ת.ז" after the student as text with its zeros, right to left, a name that
    opens with "=" a string. Every request the page made, to this service or anywhere else, is scanned for every number,
    and so are the usage events, the storage, the cookies and what the page shows."""
    import uvicorn

    from app import main
    from tests.desk_fixtures import served
    from tests.test_desk import session_token
    from tests.test_desk_browser import fake_sdk

    s, leads = browser_snapshot()
    served(monkeypatch, s)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(main.app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 15
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    assert server.started
    sdk = fake_sdk({LEADS_BOARD: {k: {ID_COLUMN: v} for k, v in leads.items()}}).replace("stub-session-token",
                                                                                          session_token(1))
    file = tmp_path / "with-ids.xlsx"
    try:
        run_ = subprocess.run([NODE, "-e", VISIT, "http://127.0.0.1:%d" % port, sdk, str(file)], capture_output=True,
                              text=True, timeout=180, env=dict(os.environ, NODE_PATH=PW_ROOT))
    finally:
        server.should_exit = True
        thread.join(timeout=10)
    assert run_.returncode == 0, (run_.stdout[-3000:], run_.stderr[-3000:])
    got = json.loads(run_.stdout.strip().splitlines()[-1])
    steps = got["steps"]
    assert got["errors"] == [], got["errors"]
    assert steps["button"] == {"text": "ייצוא לאקסל עם ת.ז", "disabled": False,
                               "title": "מספרי ת.ז נקראים ממאנדיי בשמך, כאן בדפדפן, ולא נשלחים לשום מקום"}
    # the file: the screen's rows in the screen's order, each with its number
    wb = openpyxl.load_workbook(file)
    ws = wb["תקבולים"]
    heads = [c.value for c in ws[1]]
    at, key = heads.index("ת.ז"), heads.index("מזהה במאנדיי")
    assert heads[at - 1] == "סטודנט/ית" and heads[at + 1] == "נציג מכירות"
    rows = [[c for c in ws[r]] for r in range(2, ws.max_row + 1) if ws.cell(row=r, column=key + 1).value]
    assert [r[key].value for r in rows] == steps["screen"] and sorted(steps["screen"]) == ["1", "2", "3", "4", "5"]
    ids = {r[key].value: r[at] for r in rows}
    assert {k: c.value for k, c in ids.items()} == {"1": "012345674", "2": "000000018", "3": "203456789", "4": None,
                                                     "5": None}
    assert all(ids[k].data_type == "s" and ids[k].number_format == "@" for k in ("1", "2", "3"))
    assert all(x.sheet_view.rightToLeft for x in wb) and ws.freeze_panes == "A2"
    name_cell = next(r[heads.index("סטודנט/ית")] for r in rows if r[key].value == "3")
    assert name_cell.value.startswith("=HYPERLINK(") and name_cell.data_type == "s"
    assert steps["name"].startswith("תקבולים-סטודנטים_הכל_") and steps["name"].endswith(".xlsx")
    assert steps["flash"] == "קובץ האקסל עם ת.ז יורד: 5 תקבולים. ל-2 מהם אין ת.ז, ובגיליון \"פרטים\" כתוב למה."
    assert steps["after"] == [False]
    # the reads as the person: the Leads board's ID column, by lead id, one page with monday's limit - nothing written
    of_row = {"1": ["6101", "6102"], "2": ["6103"], "3": ["6104"], "4": [], "5": ["6109"]}
    asked = list(dict.fromkeys(x for r in steps["screen"] for x in of_row[r]))
    assert [m["variables"] for m in steps["monday"]] == [{"i": asked, "n": 5, "c": [ID_COLUMN], "b": [LEADS_BOARD]}]
    assert all(m["query"].startswith("query ") for m in steps["monday"]) and steps["executed"] == []
    # the numbers: in the file, and nowhere else - no request of the page's (its address, its body, its headers), no usage
    # event, no storage, no cookie, nothing on the screen
    numbers = [v for v in leads.values() if v]
    requests = [dict(r, url=re.sub(r"(?:^|[?&])v=[0-9a-f]{12}(?=&|$)", "", r["url"])) for r in got["requests"]]
    carried = json.dumps({"requests": requests, "stored": steps["stored"], "text": steps["text"],
                          "monday": [m["variables"] for m in steps["monday"]], "executed": steps["executed"]},
                         ensure_ascii=False)
    for n in numbers:
        assert n not in carried, n
    paths = {re.sub(r"\?.*", "", r["url"]).replace("http://127.0.0.1:%d" % port, "") for r in got["requests"]}
    assert "/api/desk/export.json" in paths and not [p for p in paths if p.startswith("/api/finance")]
    assert all(p.startswith(("/desk", "/api/desk", "/api/usage")) for p in paths), paths
    usage = [e for r in got["requests"] if r["url"].endswith("/api/usage") for e in json.loads(r["body"])["events"]]
    assert [e["d"] for e in usage if e["k"] == "export_ids"] == [{"results": 5}]
    # the server's table, as the page asked it: the students' list, no number in it
    table = [r for r in got["requests"] if r["url"].endswith("/api/desk/export.json")]
    assert len(table) == 1 and table[0]["method"] == "POST" and json.loads(table[0]["body"])["tab"] == "b2c"
