"""The desk's page in node (D2): frontend/finance.js, ported from analiza-metrics at da671b2, and frontend/desk.js, the glue
that mounts it. Needs node; skipped without it.

Ported from analiza-metrics' tests/test_finance_js.py, test_finance_id_js.py and test_finance_actions_js.py: the tests
that hold the page to itself - its words, its return rule, its house style, the ID number read as the person, the
allowlist refusing before monday is asked - run here on the hand-made view of tests/desk_page_view.py. The usage events
are held to the server's vocabulary (app/desk/usage.py) since its port: driven here, and statically in
tests/test_desk_usage.py. The tests that hold the page to the rest of the server wait for its port: the students' and the
companies' filters against filter_b2c and filter_b2b, the exportable search alphabet against query_exportable, the refund
labels, the return rule against the refresh gap, the legend against every label and chip, the aging headers against the
Excel file, and the page's write allowlist (WRITABLE) against the server's actions (desk_writes).

New with the move: no path under /api/finance, every monday request through the one door, a refund's bank account and a
company's ח.פ read as the person only when shown, an answer with no view yet, and the
usage batches stopping quietly at the first failure. With D2's list (the desk move plan §1b): the search over every row
(1.2), the two actions on the row for one row or several (1.1), and a company's billing details edited from the browser as
the person, their Update only with updates:write (ADR-0188)."""
from __future__ import annotations

import copy
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.desk_page_view import SC_BOARD, B2_BOARD, answer, sc_row, view

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")
FRONT = Path(__file__).resolve().parents[1] / "frontend"
PAGE = FRONT / "finance.js"
GLUE = FRONT / "desk.js"
LEADS_BOARD, ACCOUNTS_BOARD = "5089697337", "5089697343"
ID_COLUMN, VAT_COLUMN = "text_mkzvem4", "text_mm3y3gm7"
BANK_COLUMNS = ["text_mm8cxm9y", "text_mm8cv3v3", "text_mm8cgc0d", "text_mm8ckgh8"]

# mount() in a document just big enough for it: a fake monday (cells by board and item, status labels, and per-request
# overrides), the server's answers in order per request ("GET <path>" / "POST <path>"; a request with no answer left never
# settles), a clock the test may move, and steps that click, type, return to the view, act and write
HARNESS = r"""
const vm = require("vm"), fs = require("fs");
const input = JSON.parse(fs.readFileSync(0, "utf8"));
let now = 1000000;
if (input.clock) { const RealDate = Date; global.Date = class extends RealDate { static now() { return now; } }; }
const toasts = [], copied = [], sent = [], calls = [], posts = [], usage = [], executed = [], handlers = {}, listeners = {};
const updates = [];
const search = { focus() {}, setSelectionRange() {}, value: "" };
global.document = { visibilityState: "visible", activeElement: null, body: { appendChild() {} },
  addEventListener(t, f) { (listeners[t] = listeners[t] || []).push(f); },
  getElementById(id) { return id === "fin-q" ? search : null; },
  createElement() { const el = { setAttribute() {}, remove() {}, click() {}, style: {}, select() {} };
    Object.defineProperty(el, "textContent", { set(v) { toasts.push(v); } }); return el; },
  execCommand() { return !!input.oldCopyWorks; } };
Object.defineProperty(globalThis, "navigator", { configurable: true, writable: true, value: input.clipboard === "none" ? {} :
  { clipboard: { writeText(t) { if (input.clipboard === "refuses") { return Promise.reject(new Error("denied")); }
    copied.push(t); return Promise.resolve(); } } } });
let scrolled = null;                                /* where a step scrolled the page last, if it did */
global.window = { scrollY: 0, scrollTo(x, y) { this.scrollY = y; scrolled = y; }, open() {}, location: { href: "https://desk.test/desk/" },
  addEventListener(t, f) { (listeners["window:" + t] = listeners["window:" + t] || []).push(f); },
  crypto: require("crypto").webcrypto };
let html = "", paints = 0;                          /* every drawing of the page, counted */
const root = { get innerHTML() { return html; }, set innerHTML(v) { html = v; paints += 1; },
  addEventListener(t, f) { handlers[t] = f; }, contains() { return true; },
  querySelector() { return null; }, querySelectorAll() { return []; } };
vm.runInThisContext(fs.readFileSync(process.argv[1], "utf8"));
const F = window.FinanceDesk;
const M = input.monday || {}, boards = M.boards || {}, labels = M.labels || {}, queue = M.answers || [];
const has = (o, k) => Object.prototype.hasOwnProperty.call(o || {}, k);
function cv(item, c) {
  const x = (item || {})[c];
  if (x && typeof x === "object") { return { id: c, text: x.text || "", value: null, index: x.index, label: x.label }; }
  return { id: c, text: x == null ? "" : String(x), value: null };
}
const monday = M.absent ? null : {
  get: (what) => what !== "context" ? Promise.resolve({ data: "" }) : input.context === "never" ? new Promise(() => {})
    : Promise.resolve({ data: input.context || {} }),
  execute: (what, args) => { executed.push({ what: what, args: args }); return Promise.resolve(); },
  api: (query, opts) => {
    const v = (opts || {}).variables || {};
    sent.push({ kind: query.trim().split(" ")[0], query: query, variables: v, apiVersion: (opts || {}).apiVersion });
    const a = queue.shift() || {};
    if (a.reject) { return Promise.reject(a.reject); }
    if (a.hold) { return new Promise(() => {}); }
    if (a.answer) { return Promise.resolve(a.answer); }
    if (query.trim().startsWith("mutation") && query.indexOf("create_update(") !== -1) {
      updates.push(v);
      return Promise.resolve({ data: { create_update: { id: "9001" } } });
    }
    if (query.trim().startsWith("mutation")) {
      const item = boards[v.b][v.i], vals = JSON.parse(v.v);
      Object.keys(vals).forEach((c) => {
        if ((M.keep || []).indexOf(c) !== -1) { return; }                     // monday took the write and kept its value
        const val = vals[c];
        if (val && typeof val === "object" && "index" in val) { const l = labels[v.b][c][String(val.index)]; item[c] = { text: l, index: val.index, label: l }; }
        else if (val && typeof val === "object" && "date" in val) { item[c] = val.date; }
        else if (val && typeof val === "object" && "ids" in val) {           // a dropdown: its label's name, by id
          const l = (labels[v.b][c] || []).filter((x) => x.id === val.ids[0])[0]; item[c] = l ? l.name : "";
        }
        else { item[c] = String(val); }
      });
      return Promise.resolve({ data: { change_multiple_column_values: { id: v.i, column_values: v.c.map((c) => cv(item, c)) } } });
    }
    const b = String((v.b || [])[0]), cells = boards[b] || {};
    const items = a.noItems ? [] : (v.i || []).filter((id) => has(cells, id)).map((id) => ({ id: id, name: cells[id].__name || "",
      board: { id: cells[id].__board || b }, column_values: (v.c || []).map((c) => cv(cells[id], c)) }));
    const columns = a.noColumn ? [] : (v.c || []).map((c) => ({ id: c, type: "x",
      settings_str: JSON.stringify({ labels: (labels[b] || {})[c] || {} }) }));
    return Promise.resolve({ data: { items: items, boards: a.noBoard ? [] : [{ id: b, columns: columns }] }, account_id: 1 });
  }
};
/* an answer marked `hold` lands only when a step says so ({"land": "<request>"}) */
const held = {};
function answer(key) {
  calls.push(key);
  const a = ((input.answers || {})[key] || []).shift();
  if (a && a.hold) {
    return new Promise((ok, no) => { (held[key] = held[key] || []).push(() => (a.reject ? no(a.reject) : ok(a.body))); });
  }
  return !a ? new Promise(() => {}) : a.reject ? Promise.reject(a.reject) : Promise.resolve(a.body);
}
const deps = { api: (p) => answer("GET " + p),
  post: (p, body, keep) => {
    if (p === "/api/usage") {
      usage.push({ body: body, keep: !!keep });
      return input.usageFails ? Promise.reject(input.usageFails) : Promise.resolve({ stored: body.events.length, refused: 0 });
    }
    posts.push([p, body === undefined ? null : body]); return answer("POST " + p);
  },
  monday: monday, board: input.board === undefined ? "5093448677" : input.board, later: (fn) => fn(), today: () => "2026-11-24",
  useFlushMs: input.useFlushMs, useSearchMs: input.useSearchMs, mondayWaitMs: input.mondayWaitMs };
let clicked = null;
function click(ds, cls) {
  const el = { dataset: ds, classList: { contains: (c) => (cls || []).indexOf(c) !== -1 }, closest: () => null,
    getAttribute: () => "", disabled: false, textContent: "" };
  clicked = el;
  handlers.click({ target: { closest: (sel) => sel.indexOf("[data-") !== -1 ? el : null }, preventDefault() {} });
}
const settle = async () => { for (let i = 0; i < 40; i++) { await new Promise((r) => setTimeout(r, 0)); } };
(async () => {
  await F.mount(root, deps);
  await settle();
  const first = root.innerHTML;
  const steps = [];
  for (const step of input.steps || []) {
    let refused = null;
    scrolled = null;
    if (step.scrollY !== undefined) { window.scrollY = step.scrollY; }      /* how far down she is before the step */
    if (step.mutate) { Object.keys(step.mutate).forEach((b) => Object.keys(step.mutate[b]).forEach((id) => Object.assign(boards[b][id], step.mutate[b][id]))); }
    /* the main button pressed on the desk, and (`release`, below) let go */
    if (step.press) { (handlers.pointerdown || (() => {}))({ button: 0 }); }
    if (step.show) { F._show(step.show); }
    if (step.click) { click(step.click[0], step.click[1]); }
    if (step.type !== undefined) { search.value = step.type; handlers.input({ target: { id: "fin-q", value: step.type, dataset: {} } }); }
    /* a value typed into a box of the page that is not the search ([dataset, value]), or chosen in a list */
    if (step.input) { handlers.input({ target: { id: "", value: step.input[1], dataset: step.input[0] } }); }
    if (step.choose) { handlers.change({ target: { id: "", value: step.choose[1], dataset: step.choose[0] } }); }
    if (step.change) { handlers.change({ target: { id: step.change[0], value: step.change[1], dataset: {} } }); }
    if (step.land) {
      const next = (held[step.land] || []).shift();
      if (!next) { throw new Error("nothing held for " + step.land); }
      next();
    }
    if (step.hide) { document.visibilityState = "hidden"; (listeners.visibilitychange || []).forEach((f) => f()); }
    if (step.back) {
      /* a return to the view: [ms away, how] - "visible" (another tab), "focus" (monday's item card closed) or "both" */
      now += step.back[0];
      const how = step.back[1] || "visible";
      document.visibilityState = "visible";
      if (how !== "focus") { (listeners.visibilitychange || []).forEach((f) => f()); }
      if (how !== "visible") { (listeners["window:focus"] || []).forEach((f) => f()); }
    }
    if (step.release) { (listeners["window:pointerup"] || []).forEach((f) => f({ button: 0 })); }
    if (step.act) { F._act(step.act[0], step.act[1], step.typed); }
    if (step.confirm) { F._confirm(); }
    if (step.write) { refused = await F._write(step.write[0], step.write[1], step.write[2]).then(() => "sent", (e) => e.code); }
    if (step.update) { refused = await F._update(step.update[0], step.update[1], step.update[2]).then(() => "sent", (e) => e.code); }
    if (step.wait) { await new Promise((r) => setTimeout(r, step.wait)); }
    await settle();
    const act = F._actState(), b = F._batchState();
    /* the element a step clicked, as the click left it (a button that says it is busy) */
    const pressed = step.click && clicked ? { text: clicked.textContent, disabled: clicked.disabled } : null;
    const drawn = paints;                             /* before the step's own picture below, which draws */
    steps.push({ html: step.raw ? root.innerHTML : F._show({}), paints: drawn, phase: act && act.phase, error: act && act.error, pressed: pressed,
      refused: refused, can: F._can(), sent: sent.length, scrolled: scrolled,
      plan: act && act.plan ? act.plan.lines.map((l) => [l.title, !!l.same, !!l.kept, l.to === undefined ? null : l.to]) : null,
      /* 1.1: the batch on the screen, row by row */
      batch: b ? { phase: b.phase, error: b.error || "", items: b.items.map((it) => ({ key: it.key, verdict: it.verdict || null,
        why: it.why || "", result: it.result || null })) } : null, picked: Object.keys(F._picked()).sort(),
      /* D2 (ADR-0188): a company's billing edit */
      bill: F._billState() ? { phase: F._billState().phase, error: F._billState().error || "", outcome: F._billState().outcome || null }
        : null });
  }
  /* written at once: console.log to a pipe is asynchronous, and process.exit would cut a long answer short */
  fs.writeSync(1, JSON.stringify({ steps: steps, first: first, sent: sent, calls: calls, posts: posts,
    usage: usage, toasts: toasts, copied: copied, executed: executed, updates: updates }));
  process.exit(0);
})();
"""

LOAD = """
const vm = require("vm"), fs = require("fs");
global.window = {};
vm.runInThisContext(fs.readFileSync(process.argv[1], "utf8"));
const input = JSON.parse(fs.readFileSync(0, "utf8"));
const F = window.FinanceDesk;
"""


def node(script: str, data, page: Path = PAGE) -> object:
    out = subprocess.run([NODE, "-e", script, str(page)], input=json.dumps(data, default=str, ensure_ascii=False),
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def drive(body: dict | None = None, steps=(), monday: dict | None = None, answers: dict | None = None, **extra) -> dict:
    data = {"answers": answers if answers is not None else {"GET /api/desk": [{"body": body if body is not None else answer()}]},
            "monday": monday or {}, "steps": list(steps), **extra}
    out = subprocess.run([NODE, "-e", HARNESS, str(PAGE)], input=json.dumps(data, ensure_ascii=False), capture_output=True,
                         text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def code_of(path: Path) -> str:
    """A file's code without its comments: a comment may tell the history, the code may not hold it."""
    return re.sub(r"/\*.*?\*/", "", path.read_text(encoding="utf-8"), flags=re.S)


def detail_of(html: str, row_id: str) -> str:
    start = html.index('data-row="%s"' % row_id)
    detail = html[html.index('<tr class="fin-detail">', start):]
    return detail[:detail.index("</td></tr>")]


def fact(html: str, title: str) -> str:
    """One fact's value in a details grid or the billing details: what follows <dt>title</dt>."""
    head = "<dt>%s</dt><dd>" % title
    return html[html.index(head) + len(head):].split("</dd>")[0]


def bank_box(html: str) -> str:
    """A refund's "פרטי חשבון" box in its details: what follows its label."""
    head = '<div class="fin-dx-box"><span class="fin-subtle">פרטי חשבון</span> '
    box = html[html.index(head) + len(head):]
    return box[:box.index("</div>")]


LEADS = {"6101": "000000018", "6102": "", "6103": "000000034", "6105": "000000042"}
BANK = {"text_mm8cxm9y": "בעל/ת החשבון", "text_mm8cv3v3": "12", "text_mm8cgc0d": "345", "text_mm8ckgh8": "1234567"}
VATS = {"7701": "500000001", "7702": "", "7703": "500000027"}


def world(leads=None, bank=None, vats=None, **more) -> dict:
    """The fake monday the page reads as the person: the Leads board's ID numbers, a refund's bank account on Student
    Charge, the companies' ח.פ on Accounts."""
    boards = {LEADS_BOARD: {k: {ID_COLUMN: v} for k, v in (LEADS if leads is None else leads).items()},
              SC_BOARD: {"3103": dict(BANK if bank is None else bank)},
              ACCOUNTS_BOARD: {k: {VAT_COLUMN: v} for k, v in (VATS if vats is None else vats).items()}}
    return {"boards": boards, **more}


REFUND_OPEN = {"tab": "b2c", "sc": {"state": "refund_to_execute", "period": "", "open": {"3103": True}}}
ALL_OPEN = {"tab": "b2c", "sc": {"state": "", "period": "", "open": {k: True for k in ("3101", "3102", "3103", "3104", "3105")}}}
LEDGER = {"tab": "b2b", "b2": {"state": "", "view": "list", "customer": "7703", "open": {}}}


# --- the paths and the one door (D2) ------------------------------------------------------------------------------------

def receipt_world() -> dict:
    """world(), and the payment 3102 as monday holds it for "שליחת קבלה: יצאה": its row, and the column's labels."""
    monday = world(labels={SC_BOARD: {"color_mm4cq1hw": {"0": "טרם", "1": "יצאה", "2": "לא רלוונטי"}}})
    monday["boards"][SC_BOARD]["3102"] = {"__name": "תלמיד/ה 2", "color_mm4cq1hw": {"text": "טרם", "index": 0, "label": "טרם"}}
    return monday


def test_the_page_never_calls_a_path_under_api_finance():
    """Every request the page can make, made: the open, the newer reading (`?fresh=1`), a return, the refresh, the totals,
    the Excel link, a write's refresh and the usage batch. None is under /api/finance, and no file of the page names one."""
    for name in ("finance.js", "desk.js", "check.js", "index.html"):
        assert "/api/finance" not in (FRONT / name).read_text(encoding="utf-8"), name
    later = answer(taken_at="2026-11-24T08:00:30+00:00")
    got = drive(answer(refreshing=True), [
        {"show": {"tab": "b2c", "sc": {"state": "receipt_pending", "period": "", "open": {"3102": True}}}},
        {"act": ["sc:3102", "receipt_sent"]}, {"confirm": True},
        {"click": [{"export": "b2c"}]},
        {"back": [60_000]},
        {"click": [{"refresh": "1"}]},
        {"hide": True}],
        monday=receipt_world(),
        answers={"GET /api/desk": [{"body": answer(refreshing=True)}], "GET /api/desk?fresh=1": [{"body": later}],
                 "POST /api/desk/refresh": [{"body": later}] * 4,
                 "POST /api/desk/export-link": [{"body": {"url": "/api/desk/export.xlsx?tab=b2c&sig=s"}}]},
        context={"permissions": {"approvedScopes": ["boards:read", "boards:write"]}}, clock=True)
    paths = [c.split(" ", 1)[1].split("?")[0] for c in got["calls"]] + ["/api/usage"] * len(got["usage"])
    assert set(paths) == {"/api/desk", "/api/desk/totals", "/api/desk/refresh", "/api/desk/export-link", "/api/usage"}
    assert "GET /api/desk?fresh=1" in got["calls"] and got["steps"][2]["phase"] == "done"
    assert got["executed"] == [{"what": "openLinkInTab", "args": {"url": "https://desk.test/api/desk/export.xlsx?tab=b2c&sig=s"}}]
    assert not [p for p in paths if p.startswith("/api/finance")]


def test_the_unfilter_button_and_the_link_to_ask_nadav_are_gone():
    """D2, 2.1 and 2.2 (the desk move plan §1b, 24.11): "חיפוש ללא סינון" (pressed 0 times; since 1.2 a search looks at every
    row by itself) and "חסר כאן משהו? פנייה לנדב" (no request came through its form since the desk was built) are gone -
    their words, their code, their hook and their style."""
    page = code_of(PAGE)
    for gone in ("data-unfilter", "unfilter(", "UNFILTER", "_unfilter", "data-ask", "fin-ask", "ASK_URL", "5098225215",
                 "51829235", "חסר כאן משהו", "פנייה לנדב", "חיפוש ללא סינון"):
        assert gone not in page, gone
    assert "fin-ask" not in (FRONT / "finance.css").read_text(encoding="utf-8")
    assert "_unfilter" not in node(LOAD + "console.log(JSON.stringify(Object.keys(F)));", None)
    got = drive(answer(), [
        {"show": {"tab": "b2c", "query": "zzz", "sc": {"state": "tasks", "period": "2026-11", "open": {}}}},
        {"show": {"tab": "b2b", "query": "zzz", "b2": {"state": "overdue", "view": "list", "customer": "7701", "open": {}}}}],
        monday=world())
    for html in [got["first"]] + [s["html"] for s in got["steps"]]:
        assert "פנייה לנדב" not in html and "data-ask" not in html and "data-unfilter" not in html
        assert '<div class="fin-foot"><section class="fin-health">' in html


def test_a_searchs_excel_file_and_totals_are_asked_with_no_tile_period_or_company():
    """D2, 1.2: a search looks at every row of its tab, so its Excel file and its totals are asked with no tile, period or
    company - the server's filter then picks exactly the rows on the screen (tests/test_desk_parity_js.py). Without a
    search, the open list's filters go as before."""
    from urllib.parse import parse_qs
    got = drive(answer(), [
        {"show": {"tab": "b2c", "query": "תלמיד/ה", "sc": {"state": "receipt_pending", "period": "2026-11", "open": {}}}},
        {"click": [{"export": "b2c"}]},
        {"show": {"tab": "b2b", "query": "חיוב", "b2": {"state": "overdue", "view": "list", "customer": "7701", "open": {}}}},
        {"click": [{"export": "b2b"}]},
        {"show": {"tab": "b2c", "query": "", "sc": {"state": "receipt_pending", "period": "2026-11", "open": {}}}},
        {"click": [{"export": "b2c"}]},
        {"show": {"tab": "b2b", "query": "", "b2": {"state": "overdue", "view": "list", "customer": "7701", "open": {}}}},
        {"click": [{"export": "b2b"}]}],
        monday=world(), answers={"GET /api/desk": [{"body": answer()}],
                                 "POST /api/desk/export-link": [{"body": {"url": "/api/desk/export.xlsx?x=1"}}] * 4})
    links = [p[1] for p in got["posts"] if p[0] == "/api/desk/export-link"]
    assert [(x["tab"], x["state"], x["period"], x["customer"], x["q"]) for x in links] == [
        ("b2c", "", "", "", "תלמיד/ה"), ("b2b", "", "", "", "חיוב"),
        ("b2c", "receipt_pending", "2026-11", "", ""), ("b2b", "overdue", "", "7701", "")]
    totals = [{k: v[0] for k, v in parse_qs(c.split("?", 1)[1], keep_blank_values=True).items() if k != "taken"}
              for c in got["calls"] if c.startswith("GET /api/desk/totals")]
    assert {"tab": "b2c", "state": "", "period": "", "q": "תלמיד/ה"} in totals
    assert {"tab": "b2b", "state": "", "customer": "", "q": "חיוב"} in totals
    assert {"tab": "b2c", "state": "receipt_pending", "period": "2026-11", "q": ""} in totals
    assert {"tab": "b2b", "state": "overdue", "customer": "7701", "q": ""} in totals


def heads_of(html: str) -> list:
    return [re.sub(r"<[^>]+>", "", x) for x in re.findall(r"<th[^>]*>(.*?)</th>", html, flags=re.S)]


def cells_of(html: str, row_id: str) -> dict:
    """A row's cells by their column key (k-...), as drawn."""
    row = html[html.index('data-row="%s"' % row_id):]
    row = row[:row.index("</tr>")]
    return {m.group(1): m.group(2) for m in re.finditer(r'<td class="k-(\w+)[^"]*">(.*?)</td>', row)}


def test_a_search_with_a_row_outside_the_open_list_is_drawn_with_the_general_columns():
    """QA 3 (24.11): in "החזר לביצוע", a name from "הושלם" was found and marked "● הושלם", but under the refund's columns -
    "תאריך בקשת ביטול" and "אופן החזר" with "-", and no payment date, channel or receipt. With any row outside the open list
    the search is drawn with the general columns, as a search from "לטיפול" is; with every row inside, the open list's
    own columns stay (§1b, 1.4). The same on the companies' tab."""
    refund = {"tab": "b2c", "sc": {"state": "refund_to_execute", "period": "", "open": {}}}
    got = drive(answer(), [{"show": dict(refund, query="תלמיד/ה 1")}, {"show": dict(refund, query="תלמיד/ה 3")},
                           {"show": dict(refund, query="")},
                           {"show": {"tab": "b2b", "query": "חיוב 3", "b2": {"state": "overdue", "view": "list", "customer": "",
                                                                             "open": {}}}},
                           {"show": {"tab": "b2b", "query": "חיוב 1", "b2": {"state": "overdue", "view": "list", "customer": "",
                                                                             "open": {}}}}], monday=world())
    outside, inside, plain, b2_outside, b2_inside = [s["html"] for s in got["steps"]]
    general = ["תאריך תקבול", "סטודנט/ית", "נציג מכירות", "מחזור", "סכום ששולם", "ערוץ תשלום", "אסמכתת חשבונית",
               "שליחת קבלה", "שיבוץ", "פתיחה במאנדיי"]
    assert [h for h in heads_of(outside) if h] == general
    assert cells_of(outside, "3101")["date"] == "03.11.26" and cells_of(outside, "3101")["receipt"] == "יצאה"
    assert "fin-where" in cells_of(outside, "3101")["student"]
    # every row found inside the open list: the refund's columns, as with no search
    assert [h for h in heads_of(inside) if h] == [h for h in heads_of(plain) if h]
    assert "תאריך בקשת ביטול" in heads_of(inside) and "ערוץ תשלום" not in heads_of(inside)
    # the companies: a partly paid charge found from "בפיגור" is drawn under the general columns, with its tile
    assert "שיבוץ" in heads_of(b2_outside) and "ימים בפיגור" not in heads_of(b2_outside)
    assert "ימים בפיגור" in heads_of(b2_inside) and "שיבוץ" not in heads_of(b2_inside)


def test_a_row_found_by_a_field_its_list_does_not_show_says_which_and_what_it_holds():
    """QA 3 (24.11): a search "976.19" found a row by its refund amount while the row showed only what was paid (₪1,000).
    Such a row says, under its name, which field found it and what that field holds - in the field's monday name; a row
    whose list shows the field, or found by its name, says nothing more."""
    import html as h
    v = with_row(view(), sc_row("3107", "תלמיד/ה 7", "refunded", status="תשלום הוחזר", status_idx="4", money_in="1000",
                                paid_typed="1000", net="23.81", refunded="976.19", refund_typed="976.19",
                                refund_date="2026-11-10", credit_approved="976.19"))
    v["b2b"]["rows"][2]["paid_so_far"] = "9000"
    shows = lambda tab, st, q, **f: {"show": dict({"tab": tab, "query": q}, **({"sc": dict({"state": st, "period": "", "open": {}}, **f)}
                                                                             if tab == "b2c" else
                                                                             {"b2": dict({"state": st, "view": "list", "customer": "",
                                                                                          "open": {}}, **f)}))}
    got = drive(answer(v), [shows("b2c", "refund_to_execute", "976.19"), shows("b2c", "refunded", "976.19"),
                            shows("b2c", "tasks", "סייבר"), shows("b2c", "refund_to_execute", "תלמיד/ה 7"),
                            shows("b2b", "overdue", "9000"), shows("b2b", "partial", "9000"), shows("b2c", "tasks", "")],
                monday=world())
    pages = [h.unescape(s["html"]) for s in got["steps"]]
    found = lambda page, rid: re.findall(r'<span class="fin-found">(.*?)</span>$', cells_of(page, rid).get(
        "student", cells_of(page, rid).get("charge", "")))
    # the general columns show what was paid, not the refund: which field found the row, and its value
    assert found(pages[0], "3107") == ["נמצא לפי: הוחזרו <bdi>976.19 ₪</bdi> · סכום לזיכוי <bdi>976.19 ₪</bdi>"]
    # "החזר בוצע" shows "הוחזרו": nothing to say
    assert found(pages[1], "3107") == [] and "fin-found" not in pages[1]
    # a cohort's title shows only on hover: the row says it was found by its cohort
    assert found(pages[2], "3101") == ["נמצא לפי: מחזור הגנת סייבר"]
    # found by its name, which the row shows: nothing more
    assert "fin-found" not in pages[3]
    # the companies: "סכום ששולם (כולל מע"מ)" is not a column of the general list, and is one of "שולם חלקית"'s
    assert found(pages[4], "9903") == ['נמצא לפי: סכום ששולם (כולל מע"מ) <bdi>9,000.00 ₪</bdi>']
    assert "fin-found" not in pages[5]
    # no search, no mark
    assert "fin-found" not in pages[6]


def tile_of(html: str, attr: str, key: str) -> str:
    """A state's tile or pill, as drawn: <div class="fin-state ..." ... data-<attr>="key" ...>...</div>."""
    m = re.search(r'<div class="(fin-state[^"]*)"[^>]*%s="%s"[^>]*>(.*?)</div>' % (attr, key), html)
    return (m.group(1) + " | " + re.sub(r"<[^>]+>", " ", m.group(2)).split()[0] + " " +
            " ".join(re.sub(r"<[^>]+>", " ", m.group(2)).split()[1:])) if m else ""


def test_an_empty_state_keeps_its_pill_with_its_0_as_an_empty_tile_keeps_its_tile():
    """QA 5 (24.11): the pill "ביקש החזר, טרם אושר" disappeared when empty, while an empty task's tile stays with its ✓. Every
    state stays, an empty one muted with its 0, on both tabs; pressed, its list says it is empty."""
    got = drive(answer(), [{"show": {"tab": "b2c", "sc": {"state": "tasks", "period": "", "open": {}}}},
                           {"click": [{"bstate": "refund_asked"}]},
                           {"show": {"tab": "b2b", "b2": {"state": "tasks", "view": "list", "customer": "", "open": {}}}}],
                monday=world())
    b2c, pressed, b2b = [s["html"] for s in got["steps"]]
    for key, label in (("refund_asked", "ביקש החזר, טרם אושר"), ("no_money", "ללא תקבול")):
        assert tile_of(b2c, "data-bstate", key).startswith("fin-state zero dot | 0 " + label), key
    for key in ("open", "planned", "pending", "paid", "cancelled"):
        assert tile_of(b2b, "data-b2state", key).startswith("fin-state zero dot | 0 "), key
    # a state with rows is drawn as before, and "הכל" closes the row
    assert tile_of(b2c, "data-bstate", "done").startswith("fin-state dot | 1 הושלם")
    assert re.findall(r'data-bstate="([^"]*)"', b2c)[-5:] == ["refund_asked", "done", "refunded", "no_money", ""]
    assert "אין כאן תקבולים." in pressed and 'aria-pressed="true" data-bstate="refund_asked"' in pressed


def test_a_refresh_on_its_way_has_one_word_on_the_button_and_beside_the_readings_time():
    """QA 10 (24.11): the refresh button said "מרענן…" and the header "מתעדכן" of the same thing; "מתעדכן" is kept."""
    assert "מרענן" not in code_of(PAGE)
    got = drive(steps=[{"click": [{"refresh": "1"}]}], monday=world(),
                answers={"GET /api/desk": [{"body": answer(refreshing=True)}]})
    assert got["steps"][0]["pressed"] == {"text": "מתעדכן...", "disabled": True}
    assert '<span class="fin-refreshing">מתעדכן</span>' in got["first"]


def test_a_date_that_was_not_typed_says_by_what_it_is_dated_and_an_empty_value_is_a_dash():
    """QA 10 (24.11): "יום היצירה" beside a payment date did not read without the legend - it says "(לפי יום היצירה)": in
    the details and the row's situation as text, in the list's cell drawn around the legend's term by finance.css (the
    cell's markup is held by tests/test_desk_parity_js.py). And in "החזר בוצע" an empty "תאריך החזר" was an empty cell
    while every other empty value is "-": so is it now, and every column's empty value."""
    v = view()
    for r in v["b2c"]["rows"]:
        if r["id"] == "3101":
            r["date_from"] = "created"
        if r["id"] == "3105":
            r["refund_date"] = ""
    for r in v["b2b"]["rows"]:
        if r["id"] == "9902":
            r.update(course="", ctx={"account": "חברה ג", "deal": ""})
    got = drive(answer(v), [{"show": {"tab": "b2c", "sc": {"state": "", "period": "", "open": {"3101": True}}}},
                            {"show": {"tab": "b2c", "sc": {"state": "refunded", "period": "", "open": {}}}},
                            {"show": {"tab": "b2b", "b2": {"state": "", "view": "list", "customer": "", "open": {}}}}],
                monday=world())
    every, refunded, b2_all = [s["html"] for s in got["steps"]]
    said = '03.11.26 <span class="fin-subtle">(לפי יום היצירה)</span>'
    assert fact(detail_of(every, "3101"), "תאריך תקבול") == said
    assert "שולם <bdi>4,000.00 ₪</bdi> ב-03.11.26 (לפי יום היצירה)" in detail_of(every, "3101")
    assert cells_of(every, "3101")["date"] == said  # the list's cell says it in full too, in its text (not drawn by css)
    dash = '<span class="fin-subtle">-</span>'
    assert cells_of(refunded, "3105")["refund_date"] == dash
    assert cells_of(b2_all, "9902")["due"] == dash and cells_of(b2_all, "9902")["deal"] == dash
    # no cell of a row is empty: a value, or "-" (the row's box and its button are no values)
    for page, rid in ((every, "3101"), (refunded, "3105"), (b2_all, "9902")):
        empty = [k for k, x in cells_of(page, rid).items() if not x.strip() and k not in ("pick", "rowact", "notes")]
        assert not empty, (rid, empty)


def test_a_pending_tile_says_no_amount_while_none_is_typed():
    """QA 10 (24.11): "ממתין לנתונים ₪0" said there was nothing to bill of charges whose amount is not known yet. With no
    amount typed the tile says none; a typed amount is summed and said (app/desk/view.py B2B_STATES: "a typed amount may
    sit here, and the tile sums it")."""
    def pending(amount):
        v = view()
        v["b2b"]["numbers"]["pending"] = {"count": 2, "money": amount}
        return drive(answer(v), [{"show": {"tab": "b2b", "b2": {"state": "tasks", "view": "list", "customer": "",
                                                                 "open": {}}}}], monday=world())["steps"][0]["html"]
    assert tile_of(pending("0"), "data-b2state", "pending") == "fin-state dot | 2 ממתין לנתונים"
    assert tile_of(pending("1500"), "data-b2state", "pending") == "fin-state dot | 2 ממתין לנתונים 1,500 ₪"


def test_a_receipt_sent_on_a_payment_that_brought_no_money_in_is_a_red_chip_and_a_sentence_in_its_details():
    """The server's new exception (QA 24.11, approved by Nadav; branch qa-server): a payment of "ללא תקבול" whose "שליחת קבלה"
    is "יצאה" sits in "חריג, לבדיקה" with the flag receipt_no_money. Until the page knew it, the row was there with no
    chip. The page draws it in the server's words - a contradiction between two columns, so red - in the server's order,
    and says it in the row's details; tests/test_desk_parity_js.py holds the words and the order to the server's."""
    v = with_row(view(), sc_row("3108", "תלמיד/ה 8", "exception", status="לא שולם", status_idx="0", money_in="0", counted=False,
                                paid_typed="4000", net="0", receipt="יצאה", receipt_idx="1", flags=["unlinked", "receipt_no_money"]))
    got = drive(answer(v), [{"show": {"tab": "b2c", "sc": {"state": "exception", "period": "", "open": {"3108": True}}}}],
                monday=world())
    html = got["steps"][0]["html"]
    assert cells_of(html, "3108")["notes"] == (
        '<div class="fin-chips"><span class="fin-chip bad">שליחת קבלה: יצאה, בסטטוס &quot;לא שולם&quot;</span>'
        '<span class="fin-chip q">בלי הרשמה ובלי ליד מקור</span></div>')
    assert ("<li>שליחת קבלה &quot;יצאה&quot;, וסטטוס תשלום &quot;לא שולם&quot;: קבלה יצאה על כסף שלא נכנס</li>"
            in detail_of(html, "3108"))
    flags = node(LOAD + "console.log(JSON.stringify(F._flags()));", None)
    assert flags["all"]["receipt_no_money"] == "שליחת קבלה: יצאה, בסטטוס {status}"
    order = json.loads(re.search(r"var FLAG_ORDER = (\[.*?\]);", PAGE.read_text(encoding="utf-8"), flags=re.S).group(1))
    assert order[order.index("refund_date_on_status") + 1] == "receipt_no_money"


def test_a_row_that_opens_its_details_says_so_with_a_chevron_at_its_start():
    """QA 6 (24.11): nothing said a row opens on a click (there is no "פרטים" button). Every row of a list that opens its
    details carries a chevron at its start - after the box where the row has one, first where the list has none - under
    an empty header, decorative for a screen reader (the row says aria-expanded); the companies by customer, whose rows
    open a company's ledger, have none."""
    got = drive(answer(receipts_view()), [
        {"show": TASKS}, {"show": {"tab": "b2c", "sc": {"state": "tasks", "period": "", "open": {"3102": True}}}},
        {"show": {"tab": "b2b", "b2": {"state": "", "view": "list", "customer": "", "open": {}}}},
        {"show": {"tab": "b2b", "b2": {"state": "", "view": "customers", "customer": "", "open": {}}}}],
        monday=receipts_world(), context=WRITES)
    tasks, opened, b2_all, customers = [s["html"] for s in got["steps"]]
    more = ('<td class="k-more"><span class="fin-more" title="פרטים"><svg viewBox="0 0 16 16" width="14" height="14" '
            'aria-hidden="true" focusable="false">')
    for html, first in ((tasks, ["k-pick", "k-more", "k-date"]), (b2_all, ["k-more", "k-demand"])):
        assert re.findall(r'<th class="(k-[a-z_]+)', html)[:len(first)] == first
        assert '<th class="k-more"></th>' in html
        rows = re.findall(r'<tr class="fin-rowbtn[^"]*" data-row="[^"]+"', html)
        assert len(rows) >= 3 and html.count(more) == len(rows)
    assert '<tr class="fin-rowbtn open" data-row="3102" tabindex="0" aria-expanded="true">' in opened
    assert "fin-more" not in customers and 'data-customer="7701"' in customers
    css = (FRONT / "finance.css").read_text(encoding="utf-8")
    assert "tr.fin-rowbtn.open .fin-more svg { transform: rotate(180deg); }" in css


def test_an_open_company_heads_its_tab_with_its_name_and_the_way_back():
    """QA 7 (24.11): with a company open, its name was only in the list's heading, far down; the sums, "פרטי חיוב" and the
    tiles above it are that company's alone, with no name. Its name heads the tab now, with the way back, above them; with
    no company open there is no such head."""
    got = drive(answer(), [{"show": LEDGER}, {"show": {"tab": "b2b", "b2": {"state": "overdue", "view": "list",
                                                                               "customer": "7701", "open": {}}}},
                           {"show": {"tab": "b2b", "b2": {"state": "", "view": "list", "customer": "", "open": {}}}},
                           {"show": {"tab": "b2b", "b2": {"state": "", "view": "customers", "customer": "", "open": {}}}}],
                monday=world(), board=B2_BOARD)
    ledger, one_tile, every, customers = [s["html"] for s in got["steps"]]
    head = ('<div class="fin-co"><h2 class="fin-co-name">חברה ג</h2><button class="fin-linkbtn" type="button" '
            'data-customer="">כל החברות</button></div>')
    assert head in ledger and ledger.index(head) < ledger.index('class="fin-figs"') < ledger.index("פרטי חיוב")
    assert ledger.index("פרטי חיוב") < ledger.index('class="fin-states"')
    assert '<h2 class="fin-co-name">חברה א</h2>' in one_tile
    assert "fin-co" not in every and "fin-co" not in customers


def test_the_way_back_from_a_company_returns_to_the_tile_the_view_and_the_place_she_came_from():
    """QA 8 (24.11): "כל החברות" went back to "הכל" and "רשימה", not where she came from ("לטיפול" and "לפי לקוח"). A company
    opened from "לפי לקוח", or from a list by its "אין תנאי תשלום", opens at its top (its name, QA 7); the way back returns
    to the tile, the view and the height of the page she left - a tile chosen inside the company changes none of them."""
    customers = {"tab": "b2b", "b2": {"state": "tasks", "view": "customers", "customer": "", "open": {}}}
    got = drive(answer(), [{"show": customers}, {"scrollY": 420, "click": [{"customer": "7703"}]},
                           {"click": [{"b2state": "to_bill"}]}, {"click": [{"customer": ""}]},
                           {"click": [{"b2view": "list"}]}], monday=world(), board=B2_BOARD)
    _shown, opened, inside, back, listed = got["steps"]
    assert opened["scrolled"] == 0 and '<h2 class="fin-co-name">חברה ג</h2>' in opened["html"]
    assert '<h2 class="fin-co-name">חברה ג</h2>' in inside["html"]
    assert back["scrolled"] == 420 and "fin-co" not in back["html"]
    assert "<h2>לפי לקוח</h2>" in back["html"] and 'aria-pressed="true" data-b2view="customers"' in back["html"]
    # and the tile she had chosen before "לפי לקוח" is still hers on the list
    assert "<h2>לטיפול</h2>" in listed["html"] and 'aria-pressed="true" data-b2state="tasks"' in listed["html"]
    # from a list's tile, by a company's "אין תנאי תשלום"
    got = drive(answer(), [{"show": {"tab": "b2b", "b2": {"state": "to_bill", "view": "list", "customer": "", "open": {}}}},
                           {"scrollY": 150, "click": [{"terms": "7703"}]}, {"click": [{"customer": ""}]}],
                monday=world(), board=B2_BOARD)
    _shown, opened, back = got["steps"]
    assert opened["scrolled"] == 0 and back["scrolled"] == 150
    assert "<h2>לחיוב החודש</h2>" in back["html"] and 'aria-pressed="true" data-b2state="to_bill"' in back["html"]


def totals_call(**params) -> str:
    """The page's request for a list's totals, as it asks it: its filter in its order, then the reading it shows."""
    from urllib.parse import quote
    from tests.desk_page_view import TAKEN
    return "GET /api/desk/totals?" + "&".join("%s=%s" % (k, quote(v, safe="")) for k, v in list(params.items()) + [("taken", TAKEN)])


def test_the_totals_say_the_count_of_tasks_by_customer_and_the_sum_of_every_row_where_the_server_made_it():
    """QA 11 (24.11): the total row of "לפי לקוח" had nothing under "לטיפול" (a count): it says the companies' tasks now.
    And the heading of "הכל" had no sum: on the students' tab it is the period's "התקבלו", a figure of the view; on the
    companies' tab, where the view has none, the server's total of that very list once it lands (the table's last row says
    the same) - never a sum made in the page, and none while it has not landed or failed."""
    customers = {"count": 3, "sums": {"to_bill": "20000", "partial_balance": "11650", "partial_count": 1, "partial_unknown": 0,
                                      "partial_unknown_amount": "0", "open": "22627.12", "overdue": "15000",
                                      **{"aging_" + k: "0" for k in ("current", "d1_30", "d31_60", "d61_90", "d90", "no_due")}}}
    every = {"count": 3, "sums": {"amount": "52500", "open_money": "22627.12", "paid_so_far": "9000", "balance": "11650",
                                  "partial_unknown": 0}}
    answers = {"GET /api/desk": [{"body": answer()}],
               totals_call(tab="b2b_customers", q=""): [{"body": customers}],
               totals_call(tab="b2b", state="", customer="", q=""): [{"body": every}],
               totals_call(tab="b2b", state="overdue", customer="", q=""): [{"reject": {"error": "http_500"}}]}
    got = drive(steps=[{"show": {"tab": "b2b", "b2": {"state": "", "view": "customers", "customer": "", "open": {}}}},
                       {"show": {"tab": "b2b", "b2": {"state": "", "view": "list", "customer": "", "open": {}}}},
                       {"show": {"tab": "b2b", "b2": {"state": "overdue", "view": "list", "customer": "", "open": {}}}},
                       {"show": {"tab": "b2c", "sc": {"state": "", "period": "", "open": {}}}},
                       {"show": {"tab": "b2c", "sc": {"state": "tasks", "period": "", "open": {}}}}],
                monday=world(), answers=answers)
    by_customer, b2_all, overdue, b2c_all, tasks = [s["html"] for s in got["steps"]]
    foot = by_customer[by_customer.index("<tfoot>"):]
    assert '<td><b>סה"כ</b> <span class="fin-subtle">3 חברות</span></td><td></td>' in foot
    tasks_at = [c["key"] for c in view()["b2b"]["aging_columns"]].index("tasks")
    assert re.findall(r"<td[^>]*>(.*?)</td>", foot)[tasks_at] == "3"
    head = lambda html: re.search(r'<div class="fin-listhead"><div><h2>.*?</h2><div class="fin-subtle">(.*?)</div>', html).group(1)
    assert head(b2_all) == '3 חיובים · <bdi>52,500 ₪</bdi> ללא מע"מ'
    assert head(overdue) == 'חיוב אחד · <bdi>15,000 ₪</bdi> ללא מע"מ'          # a state's own, as before
    assert head(b2c_all) == "5 תקבולים · <bdi>18,450 ₪</bdi>" and head(tasks) == "3 תקבולים"
    # the companies' "הכל" before its totals land, or when they failed: its count alone
    got = drive(steps=[{"show": {"tab": "b2b", "b2": {"state": "", "view": "list", "customer": "", "open": {}}}}], monday=world(),
                answers={"GET /api/desk": [{"body": answer()}],
                         totals_call(tab="b2b", state="", customer="", q=""): [{"reject": {"error": "http_500"}}]})
    assert head(got["steps"][0]["html"]) == "3 חיובים"


def test_the_excel_file_says_on_the_page_that_it_is_on_its_way_with_the_number_of_rows_of_its_list():
    """QA 2 (24.11): after "ייצוא לאקסל" the page said nothing - no word, no count (the files themselves were right). When the
    link opens - through monday's new tab, or outside monday by the page's own link - the page says so, with the rows of
    the list it exported: a tile, a search over every row, a company's list, the companies by customer."""
    def exported(monday):
        return drive(answer(), [
            {"show": {"tab": "b2c", "query": "", "sc": {"state": "tasks", "period": "", "open": {}}}},
            {"click": [{"export": "b2c"}]},
            {"show": {"tab": "b2c", "query": "תלמיד", "sc": {"state": "done", "period": "2026-11", "open": {}}}},
            {"click": [{"export": "b2c"}]},
            {"show": {"tab": "b2b", "query": "", "b2": {"state": "overdue", "view": "list", "customer": "", "open": {}}}},
            {"click": [{"export": "b2b"}]},
            {"show": {"tab": "b2b", "query": "", "b2": {"state": "", "view": "customers", "customer": "", "open": {}}}},
            {"click": [{"export": "b2b_customers"}]}],
            monday=monday, answers={"GET /api/desk": [{"body": answer()}],
                                    "POST /api/desk/export-link": [{"body": {"url": "/api/desk/export.xlsx?x=1"}}] * 4})
    said = ["קובץ האקסל יורד: 3 תקבולים.", "קובץ האקסל יורד: 5 תקבולים.", "קובץ האקסל יורד: חיוב אחד.",
            "קובץ האקסל יורד: 3 חברות."]
    inside = exported(world())
    assert inside["toasts"] == said and len(inside["executed"]) == 4
    outside = exported({"absent": True})
    assert outside["toasts"] == said and outside["executed"] == []
    # a link that was refused says why, and never that a file is on its way
    got = drive(answer(), [{"click": [{"export": "b2c"}]}], monday=world(),
                answers={"GET /api/desk": [{"body": answer()}],
                         "POST /api/desk/export-link": [{"reject": {"error": "not_a_board_member"}}]})
    assert got["toasts"] == ["התצוגה הזאת מראה את לוחות החיובים, ואין לך גישה לאף אחד מהם."]


def test_every_monday_request_of_the_page_goes_through_the_one_door():
    """finance.js calls monday.api in one place (mondayCall) and composes each of its two mutations in one place - the
    columns in mondayWrite, and since D2 (ADR-0188) a billing edit's Update in mondayUpdate; every read - a row before a
    write, the ID number, a bank account, a ח.פ, a company's billing details - goes through it at the pinned API version.
    The glue never calls monday.api; the self-check (/desk/?check=1, D1's) has its own one read, held by tests/test_desk.py."""
    page = code_of(PAGE)
    # every `.api(` of the page is this service's (deps.api) or monday's - and monday's is called in one place
    callers = re.findall(r"([\w.$]+)\s*\.\s*api\s*\(", page)
    assert set(callers) == {"deps", "deps.monday"} and callers.count("deps.monday") == 1
    assert not re.search(r"\[\s*[\"']api[\"']\s*\]", page)          # nor by its name in brackets
    door = page[page.index("function mondayCall("):page.index("function shapeOk(")]
    assert "deps.monday.api(query, { variables: variables, apiVersion: API_VERSION })" in door
    assert page.count('"mutation ') == 2 and page.count("mondayCall(WRITE_ROW") == 1 and page.count("mondayCall(WRITE_UPDATE") == 1
    assert sorted(re.findall(r"(?<!function )mondayCall\((\w+)", page)) == ["READ_CELLS", "READ_ROW", "WRITE_ROW", "WRITE_UPDATE"]
    update = page[page.index("function mondayUpdate("):page.index("function readRows(")]
    assert "mondayCall(WRITE_UPDATE" in update and "!CAN_UPDATE" in update and "UPDATE_BOARDS" in update
    assert ".api(" not in code_of(GLUE)
    assert code_of(FRONT / "check.js").count("monday.api(") == 1
    # and in a visit that makes every kind of request: one of the page's three queries, at the version the server reads
    got = drive(answer(), [{"show": ALL_OPEN}, {"show": REFUND_OPEN}, {"act": ["sc:3102", "receipt_sent"]},
                           {"show": LEDGER}], monday=world(), context={})
    kinds = {s["query"] for s in got["sent"]}
    assert len(got["sent"]) >= 5 and len(kinds) == 2 and {s["apiVersion"] for s in got["sent"]} == {"2026-07"}
    assert all(q.startswith("query ($i: [ID!], $c: [String!], $b: [ID!]) { items(ids: $i) { id ") for q in kinds)


# --- ported: the page's words, columns, return rule and house style (analiza-metrics tests/test_finance_js.py) ----------

def test_the_page_words_a_refunds_lateness_as_innas_app_does():
    got = node(LOAD + "console.log(JSON.stringify(input.map(F._lastDayText)));", [-12, -2, -1, 0, 1, 2, 14])
    assert got == ["באיחור של 12 ימים", "באיחור של 2 ימים", "באיחור של יום אחד", "היום הוא היום האחרון", "נותר יום אחד",
                   "נותרו 2 ימים", "נותרו 14 ימים"]


def test_the_page_heads_mondays_columns_by_their_monday_names_and_lays_out_the_refund_and_open_lists():
    """The page's half of the old test; that its states are the server's (B2C_STATES, B2B_STATES) waits for the port."""
    cols = node(LOAD + "console.log(JSON.stringify(F._columns()));", None)
    heads = cols["heads"]
    assert {k: heads[k] for k in ("received", "receipt", "to_refund", "credit_ref", "invoice", "method",
                                  "refund_date", "asked", "last_day", "planned", "demand", "amount", "paid_so_far",
                                  "account", "course", "status", "payment", "open")} == {
        "received": "סכום ששולם", "receipt": "שליחת קבלה", "to_refund": "סכום לזיכוי",
        "credit_ref": "אסמכתת זיכוי", "invoice": "אסמכתת חשבונית", "method": "אופן החזר", "refund_date": "תאריך החזר",
        "asked": "תאריך בקשת ביטול", "last_day": "מועד אחרון להחזר", "planned": "מועד העברה מתוכנן",
        "demand": "מועד דרישת תשלום", "amount": 'סכום לחיוב (ללא מע"מ)', "paid_so_far": 'סכום ששולם (כולל מע"מ)',
        "account": "חברה", "course": "קוד קורס", "status": "מצב חיוב", "payment": "סטטוס תשלום", "open": "פתיחה במאנדיי"}
    # the refund counted is the desk's own word, as the tab's figure and the Excel file call it (QA 24.11): under "סכום
    # החזר" it read as monday's column, which a refund's details show as it is
    assert heads["refunded"] == "הוחזרו" and "סכום החזר" not in heads.values()
    refund = cols["sc"]["refund_to_execute"]
    i = refund.index("last_day")
    assert refund[i - 1:i + 2] == ["asked", "last_day", "planned"]              # the two dates side by side
    done = cols["sc"]["refunded"]
    assert done[done.index("last_day") + 1] == "planned"
    assert cols["b2"]["open"][0] == "due" and "to_bill" in cols["b2"] and "approval" not in cols["b2"]
    # D2: the list "החזר לביצוע" has no bank account column - the page reads one only when a refund's details open
    assert "bank" not in refund and "bank" not in heads


def test_the_page_asks_again_on_return_only_after_5_seconds_and_never_inside_the_refresh_gap():
    cases = [  # body, landed at (ms), now (ms), not before (ms), busy
        [{"age_seconds": 0}, 0, 4_000, 0, False], [{"age_seconds": 0}, 0, 6_000, 0, False],
        [{"age_seconds": 3}, 1_000, 4_000, 0, False],                           # 3s old when it came, 3s since: 6s
        [{"age_seconds": 60}, 0, 1_000, 5_000, False],                         # the server said not before 5s
        [{"age_seconds": 60}, 0, 1_000, 0, True],                              # a newer reading is already on its way
        [{"age_seconds": 60, "refreshing": True}, 0, 1_000, 0, False], [None, 0, 99_000, 0, False]]
    got = node(LOAD + "console.log(JSON.stringify(input.map(c => F._refetchDue.apply(null, c))));", cases)
    assert got == [False, True, True, False, False, False, False]
    source = PAGE.read_text(encoding="utf-8")
    assert 'document.addEventListener("visibilitychange", onVisible)' in source
    assert 'window.addEventListener("focus", onFocus)' in source and "setInterval" not in source


def test_the_page_has_no_retired_term_and_keeps_the_house_style():
    source = code_of(PAGE)
    retired = ["חשבונית זיכוי", "מספר חשבונית", "להפקה", "הפקת", "לפני מע\\\"מ", "להחזיר עד", "היום האחרון להחזר",
               "פתוח לגבייה", "שולם עד כה", "נותר לגבות", "סטטוס במאנדיי", "מצב חיוב במאנדיי", "סטטוס תשלום במאנדיי",
               "מועד דרישה\"", "מועד דרישה ", "מועד הדרישה",
               "מסלול מימון", "\"לחשבון\"", "\"קבלה\"", "\"תקבול\"", "היקף העסקה", "המשלם בעסקה", "הסכום ששולם",
               "כל הלקוחות", "\"לקוח אחד\"", "לקוחות עם משהו פתוח", "בכל השיבוצים", "בשאר השיבוצים",
               # D2: the ID number is in no Excel file of this service's
               "נמצאת גם בקובץ האקסל"]
    found = [t for t in retired if t in source]
    assert not found, found
    for name in ("finance.js", "finance.css", "desk.js", "desk.css", "check.js", "index.html"):
        text = (FRONT / name).read_text(encoding="utf-8")
        assert "—" not in text and "→" not in text, name


def with_row(v: dict, row: dict) -> dict:
    v = copy.deepcopy(v)
    v["b2c"]["rows"].append(row)
    return v


def newer_body(taken: str, age: int, marker: str = "שורה-חדשה") -> dict:
    """A newer reading holding one more payment waiting for its receipt: on the list the page opens on ("לטיפול")."""
    return answer(with_row(view(), sc_row("3199", marker, "receipt_pending", receipt="טרם", receipt_idx="0")),
                  taken_at=taken, age_seconds=age)


def visit(answers: dict, returns: list) -> dict:
    steps = [{"back": r if isinstance(r, list) else [r, "visible"], "raw": True} for r in returns]
    got = drive(steps=steps, answers=answers, monday={"absent": True}, clock=True)
    return {"calls": [c for c in got["calls"] if "/totals" not in c], "seen": ["שורה-חדשה" in s["html"] for s in got["steps"]]}


def test_a_return_that_meets_the_refresh_gap_still_shows_the_reading_the_server_already_holds():
    older = {"body": answer()}
    newer = {"body": newer_body("2026-11-24T08:00:40.250000+00:00", 4)}
    not_yet = {"reject": {"error": "rate_limited", "retryAfter": 20}}
    got = visit({"GET /api/desk": [older, newer], "POST /api/desk/refresh": [not_yet]}, [25_000])
    assert got == {"calls": ["GET /api/desk", "POST /api/desk/refresh", "GET /api/desk"], "seen": [True]}
    got = visit({"GET /api/desk": [older, older], "POST /api/desk/refresh": [not_yet, newer]}, [25_000, 5_000, 20_000])
    assert got == {"calls": ["GET /api/desk", "POST /api/desk/refresh", "GET /api/desk", "POST /api/desk/refresh"],
                   "seen": [False, False, True]}
    newest = {"body": newer_body("2026-11-24T08:00:40.250000+00:00", 0)}
    got = visit({"GET /api/desk": [older], "POST /api/desk/refresh": [newest, newest]}, [25_000, 3_000, 3_000])
    assert got == {"calls": ["GET /api/desk", "POST /api/desk/refresh", "POST /api/desk/refresh"], "seen": [True, True, True]}


def test_the_page_asks_again_when_its_window_takes_the_focus_back_after_mondays_item_card():
    older = {"body": answer()}
    newer = {"body": newer_body("2026-11-24T08:00:40.250000+00:00", 0)}
    got = visit({"GET /api/desk": [older], "POST /api/desk/refresh": [newer]}, [[6_000, "focus"]])
    assert got == {"calls": ["GET /api/desk", "POST /api/desk/refresh"], "seen": [True]}
    got = visit({"GET /api/desk": [older], "POST /api/desk/refresh": [newer, newer]}, [[3_000, "focus"], [3_000, "both"]])
    assert got == {"calls": ["GET /api/desk", "POST /api/desk/refresh"], "seen": [False, True]}
    not_yet = {"reject": {"error": "rate_limited", "retryAfter": 5}}
    got = visit({"GET /api/desk": [older, older], "POST /api/desk/refresh": [not_yet, newer]},
                [[6_000, "focus"], [2_000, "focus"], [4_000, "focus"]])
    assert got == {"calls": ["GET /api/desk", "POST /api/desk/refresh", "GET /api/desk", "POST /api/desk/refresh"],
                   "seen": [False, False, True]}


# --- the QA of 24.11, its second pass: a drawing she did not ask for keeps what she typed and what she pressed ---------

REFRESHING = 'class="fin-refreshing"'
NEWER = "2026-11-24T08:00:40.250000+00:00"


def search_box(html: str) -> str:
    """What the search box holds in a drawing of the page."""
    import html as h
    return h.unescape(re.search(r'id="fin-q"[^>]*value="([^"]*)"', html).group(1))


@pytest.mark.parametrize("drawing", [
    {"land": "GET /api/desk?fresh=1"},           # the page's own second request lands: "מתעדכן" goes
    {"click": [{"bstate": "done"}]},               # a tile chosen before the search settled (a redraw)
    {"click": [{"legend": "1"}]},                  # "מקרא" opened before it settled (a drawing of its own)
])
def test_a_drawing_before_the_search_settles_keeps_what_was_typed_in_the_box(drawing):
    """QA 24.11 (1): text typed into the search box was erased while "מתעדכן" showed. What is typed reaches state.query
    when the search settles, 250ms after the last key, and the box was read back only while it had the focus: a drawing
    in between drew the box from state.query. Here the box has no focus (the harness's document has none, as when
    monday's frame holds it), and every drawing keeps what it holds - the search is drawn on it at once."""
    import html as h
    first = {"body": answer(refreshing=True, age_seconds=40)}
    newer = {"hold": True, "body": newer_body(NEWER, 0)}
    got = drive(answers={"GET /api/desk": [first], "GET /api/desk?fresh=1": [newer]}, monday={"absent": True},
                steps=[{"type": "תלמיד/ה 2", "raw": True}, dict(drawing, raw=True), {"wait": 400, "raw": True}])
    _typed, drawn, settled = got["steps"]
    assert search_box(drawn["html"]) == "תלמיד/ה 2" and "חיפוש: תלמיד/ה 2" in h.unescape(drawn["html"])
    if "land" in drawing:
        assert REFRESHING not in drawn["html"]                     # the newer reading is the one on the screen
    assert search_box(settled["html"]) == "תלמיד/ה 2" and "חיפוש: תלמיד/ה 2" in h.unescape(settled["html"])


@pytest.mark.parametrize("landing", [
    {"hold": True, "body": newer_body(NEWER, 0)},
    {"hold": True, "reject": {"error": "rate_limited", "retryAfter": 5}},   # "not yet": the reading on the screen stays
])
def test_the_refresh_pressed_keeps_what_was_typed_while_it_was_on_its_way(landing):
    """QA 24.11 (1): "רענון" drew its answer with render() from state.query, so a search typed while it was on its way was
    drawn over. Its answer, a newer reading or a "not yet", is drawn as every answer is now."""
    got = drive(answers={"GET /api/desk": [{"body": answer()}], "POST /api/desk/refresh": [landing]}, monday={"absent": True},
                steps=[{"click": [{"refresh": "1"}]}, {"type": "תלמיד/ה 2", "land": "POST /api/desk/refresh", "raw": True}])
    assert search_box(got["steps"][1]["html"]) == "תלמיד/ה 2"
    assert ("rate_limited" in json.dumps(landing)) == ("אפשר לרענן שוב בעוד 5 שניות." in got["toasts"])


def test_a_drawing_that_lands_while_the_desk_is_pressed_waits_for_the_release_and_is_drawn_once():
    """QA 24.11 (1): a click on a tile was not taken while "מתעדכן" showed. A drawing between a press and its release builds
    what was pressed anew, and the browser then takes no click (tests/test_desk_browser.py presses one). An answer that
    lands while a pointer is down on the desk is drawn after the release, once; a choice of her own draws at once, and what
    landed with it; and a release the page never hears holds a drawing PRESS_MAX_MS (1.5 s) at most."""
    first = lambda: {"body": answer(refreshing=True, age_seconds=40)}
    newer = lambda: {"hold": True, "body": newer_body(NEWER, 0)}
    answers = lambda: {"GET /api/desk": [first()], "GET /api/desk?fresh=1": [newer()]}
    pressed_landing = {"press": True, "land": "GET /api/desk?fresh=1", "raw": True}
    got = drive(answers=answers(), monday={"absent": True},
                steps=[{"raw": True}, pressed_landing, {"release": True, "raw": True}, {"wait": 50, "raw": True}])
    before, pressed, released, after = got["steps"]
    assert "שורה-חדשה" not in pressed["html"] and REFRESHING in pressed["html"] and pressed["paints"] == before["paints"]
    assert "שורה-חדשה" in released["html"] and REFRESHING not in released["html"]
    assert released["paints"] == before["paints"] + 1 and after["paints"] == released["paints"]
    # her own choice while pressed (a select's list): drawn at once, the reading that landed with it, and nothing owed after
    got = drive(answers=answers(), monday={"absent": True},
                steps=[{"raw": True}, pressed_landing, {"change": ["fin-period", "2026-11"], "raw": True},
                       {"release": True, "raw": True}])
    before, pressed, chosen, released = got["steps"]
    assert pressed["paints"] == before["paints"] and chosen["paints"] == before["paints"] + 1 and "שורה-חדשה" in chosen["html"]
    assert released["paints"] == chosen["paints"]
    # a press whose release never comes: the reading is drawn after PRESS_MAX_MS all the same
    got = drive(answers=answers(), monday={"absent": True}, steps=[pressed_landing, {"wait": 1700, "raw": True}])
    assert "שורה-חדשה" not in got["steps"][0]["html"] and "שורה-חדשה" in got["steps"][1]["html"]


# --- ported: the allowlist and one action end to end (analiza-metrics tests/test_finance_actions_js.py) ------------------

@pytest.mark.parametrize("board, item, values", [
    (ACCOUNTS_BOARD, "1", {VAT_COLUMN: "512345678"}),                               # another board
    ("5089697338", "1", {"deal_stage": {"index": 1}}),
    (SC_BOARD, "1", {"text_mm8ckgh8": "123"}),                                       # a column the desk never writes
    (SC_BOARD, "1", {"numeric_mm8cvnsf": "100"}),                                  # "סכום לזיכוי"
    (B2_BOARD, "1", {"color_mm1kxpvt": {"index": 4}}),                              # a column of the other board
    (SC_BOARD, "1", {"color_mm1kxpvt": {"label": "תשלום הוחזר"}}),                  # never by a label's text
    (SC_BOARD, "1", {"color_mm1kxpvt": {"index": "4"}}),
    (SC_BOARD, "1", {"color_mm1kxpvt": {"index": 4, "label": "תשלום הוחזר"}}),
    (SC_BOARD, "1", {"color_mm1kxpvt": {"index": 3}}),                              # an index no action sets
    (SC_BOARD, "1", {"numeric_mm62za1f": "12a"}), (SC_BOARD, "1", {"numeric_mm62za1f": 500}),
    (B2_BOARD, "1", {"date_mm4a6y8y": {"date": "11.11.2026"}}), (B2_BOARD, "1", {"date_mm4a6y8y": "2026-11-11"}),
    (SC_BOARD, "1", {"text_mm8fjzfj": "ZK\n1"}), (SC_BOARD, "1", {"text_mm8fjzfj": "x" * 61}),
    (SC_BOARD, "1", {"text_mm8fjzfj": "ZK؜1"}),                                # the Arabic letter mark
    (SC_BOARD, "1", {}), (SC_BOARD, "abc", {"text_mm8fjzfj": "ZK-1"}),
    (SC_BOARD, "1", {"text_mm8fjzfj": "ZK-1", "date_mm8cp8gj": {"date": "2026-11-11"}}),   # one bad column of two
])
def test_a_write_outside_the_allowlist_is_refused_before_monday_is_asked(board, item, values):
    got = drive(answer(), [{"write": [board, item, values]}], monday=world(), context={})
    assert got["steps"][0]["refused"] == "write_refused"
    assert got["sent"] == []


def test_the_page_composes_a_mutation_in_one_place_and_calls_monday_api_in_one():
    """Since D2 (ADR-0188) two mutations, each composed once: the columns, and a billing edit's Update (create_update)."""
    source = code_of(PAGE)
    assert source.count('"mutation ') == 2 and source.count("monday.api(") == 1   # deps.api is this service, not monday
    assert source.count("change_multiple_column_values(") == 1 and source.count("create_update(") == 1
    assert "change_simple_column_value" not in source and "create_labels_if_missing: false" in source


def test_an_action_reads_shows_the_plan_writes_the_index_and_asks_this_services_refresh():
    """"שליחת קבלה: יצאה", end to end on the hand-made view: the row read live, the plan, one write by index, then a
    reading that started after it - POST /api/desk/refresh - and the row placed by that reading."""
    after = view()
    row = [r for r in after["b2c"]["rows"] if r["id"] == "3102"][0]
    row.update(state="done", receipt="יצאה", receipt_idx="1", actions=[])
    got = drive(answer(), [{"act": ["sc:3102", "receipt_sent"]}, {"confirm": True}],
                monday=receipt_world(), context={"permissions": {"approvedScopes": ["boards:read", "boards:write"]}},
                answers={"GET /api/desk": [{"body": answer()}],
                         "POST /api/desk/refresh": [{"body": answer(after, taken_at="2026-11-24T08:00:09+00:00")}]})
    plan, wrote = got["steps"]
    assert plan["phase"] == "confirm" and plan["plan"] == [["שליחת קבלה", False, False, "יצאה"]]
    read = got["sent"][0]
    assert read["kind"] == "query" and read["variables"] == {"i": ["3102"], "c": ["color_mm4cq1hw"], "b": [SC_BOARD]}
    writes = [s for s in got["sent"] if s["kind"] == "mutation"]
    assert [(w["variables"]["b"], w["variables"]["i"], json.loads(w["variables"]["v"])) for w in writes] == [
        (SC_BOARD, "3102", {"color_mm4cq1hw": {"index": 1}})]                         # by index, never by text
    assert wrote["phase"] == "done" and ["/api/desk/refresh", None] in got["posts"]
    assert "נכתב במאנדיי בשמך: שליחת קבלה \"יצאה\". המסך מתעדכן." in got["toasts"]


# --- 1.1 (D2, the desk move plan §1b): two actions on the row itself, for one row or several at once ---------------------

RECEIPT_LABELS = {"0": "טרם", "1": "יצאה", "2": "לא רלוונטי"}
PAY_LABELS = {"0": "לא שולם", "1": "שולם", "2": "יש בעיה", "3": "מלגת אנליזה", "4": "תשלום הוחזר", "5": "הוחזר חלקי",
              "9": "ביקש החזר"}
WRITES = {"permissions": {"approvedScopes": ["boards:read", "boards:write"]}}
TASKS = {"tab": "b2c", "sc": {"state": "tasks", "period": "", "open": {}}}
RECEIPT_COL, STATUS_COL = "color_mm4cq1hw", "color_mm1kxpvt"


def receipts_view() -> dict:
    """The hand-made view with a second payment waiting for its receipt (3106), beside 3102."""
    return with_row(view(), sc_row("3106", "תלמיד/ה 6", "receipt_pending", invoice="30006", receipt="טרם", receipt_idx="0",
                                   money_in="5000", paid_typed="5000", net="5000", actions=["receipt_sent"]))


def receipts_world(answers=None, **cells) -> dict:
    """monday as the person reads it: the two payments waiting for a receipt, the refund to execute, their labels."""
    monday = world(labels={SC_BOARD: {RECEIPT_COL: dict(RECEIPT_LABELS), STATUS_COL: dict(PAY_LABELS)}},
                   answers=list(answers or []))
    board = monday["boards"][SC_BOARD]
    for rid in ("3102", "3106"):
        board[rid] = {"__name": "תלמיד/ה " + rid[-1], RECEIPT_COL: {"text": "טרם", "index": 0, "label": "טרם"}}
    board["3103"].update({"__name": "תלמיד/ה 3", STATUS_COL: {"text": "שולם", "index": 1, "label": "שולם"}})
    for rid, cell in cells.items():
        board[rid].update(cell)
    return monday


def receipts_done(v: dict | None = None) -> dict:
    """The reading after both receipts were written: both "הושלם"."""
    v = copy.deepcopy(v or receipts_view())
    for r in v["b2c"]["rows"]:
        if r["id"] in ("3102", "3106"):
            r.update(state="done", receipt="יצאה", receipt_idx="1", actions=[])
    return v


def status_cell(labels: dict, idx: str) -> dict:
    return {"text": labels[idx], "index": int(idx), "label": labels[idx]}


def writes_of(got: dict) -> list:
    return [(s["variables"]["b"], s["variables"]["i"], json.loads(s["variables"]["v"])) for s in got["sent"]
            if s["kind"] == "mutation"]


def batch_answers(v: dict, refreshed: dict | None = None) -> dict:
    return {"GET /api/desk": [{"body": answer(v)}],
            "POST /api/desk/refresh": [{"body": answer(refreshed or receipts_done(v), taken_at="2026-11-24T08:00:09+00:00")}]}


def test_two_receipts_picked_are_written_with_one_confirmation_each_read_before_its_write():
    """Select two rows, press once, one confirmation listing each row's write; on "כתיבה" each row is read again right
    before its own write, written by index and checked against monday's answer; one refresh after the batch."""
    import html as h
    v = receipts_view()
    got = drive(steps=[{"show": TASKS}, {"click": [{"pick": "sc:3102"}]}, {"click": [{"pick": "sc:3106"}]},
                       {"click": [{"batch": "receipt_sent"}]}, {"click": [{"batchGo": "1"}]}],
                monday=receipts_world(), context=WRITES, answers=batch_answers(v))
    shown, _one, two, plan, done = got["steps"]
    # the box and the button only on the rows that offer one of the two: the two receipts and the refund to execute
    assert sorted(re.findall(r'data-pick="(sc:\d+)"', shown["html"])) == ["sc:3102", "sc:3103", "sc:3106"]
    assert sorted(re.findall(r'data-row-act="([^"]+)"', shown["html"])) == [
        "sc:3102:receipt_sent", "sc:3103:refund_done", "sc:3106:receipt_sent"]
    assert 'data-pick-all="1"' in shown["html"] and "<th class=\"k-rowact\">פעולות</th>" in shown["html"]
    assert shown["sent"] == 0                                                       # nothing read before a press
    # the bar: what is picked, and one button per action they offer, with its count
    bar = h.unescape(two["html"])
    assert "נבחרו 2 שורות" in bar and 'data-batch="receipt_sent">שליחת קבלה: "יצאה" <span class="fin-subtle">(2)</span>' in bar
    # one confirmation, from the two rows read in one request, before anything is written
    assert plan["batch"]["phase"] == "confirm" and [i["verdict"] for i in plan["batch"]["items"]] == ["write", "write"]
    page = h.unescape(plan["html"])
    assert "ייכתב במאנדיי בשמך, ב-2 שורות:" in page
    assert '<li><b>תלמיד/ה 2</b> · שליחת קבלה: עכשיו "טרם", ייכתב "יצאה"</li>' in page
    assert '<li><b>תלמיד/ה 6</b> · שליחת קבלה: עכשיו "טרם", ייכתב "יצאה"</li>' in page
    assert 'data-batch-go="1">כתיבה</button>' in page and plan["sent"] == 1
    assert got["sent"][0]["kind"] == "query" and got["sent"][0]["variables"] == {
        "i": ["3102", "3106"], "c": [RECEIPT_COL], "b": [SC_BOARD]}
    # each row read right before its write, written by index, one after the other
    assert [(s["kind"], s["variables"]["i"]) for s in got["sent"][1:]] == [
        ("query", ["3102"]), ("mutation", "3102"), ("query", ["3106"]), ("mutation", "3106")]
    assert writes_of(got) == [(SC_BOARD, "3102", {RECEIPT_COL: {"index": 1}}), (SC_BOARD, "3106", {RECEIPT_COL: {"index": 1}})]
    assert done["batch"]["phase"] == "done" and [i["result"] for i in done["batch"]["items"]] == ["written", "written"]
    assert "נכתב במאנדיי בשמך ב-2 שורות. המסך מתעדכן." in got["toasts"]
    # one refresh, as after one action; the picks are the batch's and are cleared
    assert [p for p in got["posts"] if p[0] == "/api/desk/refresh"] == [["/api/desk/refresh", None]]
    assert done["picked"] == [] and "data-row=\"3102\"" not in done["html"]          # the reading placed them in "הושלם"


def test_a_rows_own_button_is_one_press_to_its_confirmation_and_says_what_follows():
    """One row: its button reads it and shows its one confirmation, with what the refunds app does after (MON-86)."""
    import html as h
    v = receipts_view()
    refunded = copy.deepcopy(v)
    for r in refunded["b2c"]["rows"]:
        if r["id"] == "3103":
            r.update(state="refunded", status="תשלום הוחזר", status_idx="4", actions=[])
    got = drive(steps=[{"show": TASKS}, {"click": [{"rowAct": "sc:3103:refund_done"}]}, {"click": [{"batchGo": "1"}]}],
                monday=receipts_world(), context=WRITES, answers=batch_answers(v, refunded))
    plan, done = got["steps"][1], got["steps"][2]
    page = h.unescape(plan["html"])
    assert 'ייכתב במאנדיי בשורה "תלמיד/ה 3", בשמך:' in page
    assert '<li><b>תלמיד/ה 3</b> · סטטוס תשלום: עכשיו "שולם", ייכתב "תשלום הוחזר"</li>' in page
    assert "אחרי הכתיבה אפליקציית ההחזרים סוגרת את הבקשה." in page                # the action's `after`, from the server
    assert writes_of(got) == [(SC_BOARD, "3103", {STATUS_COL: {"index": 4}})]
    assert done["batch"]["items"] == [{"key": "sc:3103", "verdict": "write", "why": "", "result": "written"}]


@pytest.mark.parametrize("now, why", [
    # the desk read "טרם"; monday holds another value now: not written over
    (status_cell(RECEIPT_LABELS, "2"), 'השתנתה במאנדיי מאז הקריאה, ועכשיו שליחת קבלה "לא רלוונטי"'),
    # monday already holds it
    (status_cell(RECEIPT_LABELS, "1"), 'כבר "יצאה" במאנדיי'),
])
def test_a_row_changed_in_monday_since_the_reading_is_not_written_and_the_confirmation_says_why(now, why):
    import html as h
    got = drive(steps=[{"show": TASKS}, {"click": [{"pick": "sc:3102"}]}, {"click": [{"pick": "sc:3106"}]},
                       {"click": [{"batch": "receipt_sent"}]}, {"click": [{"batchGo": "1"}]}],
                monday=receipts_world(**{"3106": {RECEIPT_COL: now}}), context=WRITES, answers=batch_answers(receipts_view()))
    plan, done = got["steps"][3], got["steps"][4]
    assert [(i["key"], i["verdict"], i["why"]) for i in plan["batch"]["items"]] == [
        ("sc:3102", "write", ""), ("sc:3106", "skip", why)]
    page = h.unescape(plan["html"])
    assert 'ייכתב במאנדיי בשורה "תלמיד/ה 2", בשמך:' in page and "<p>לא ייכתב:</p><ul><li><b>תלמיד/ה 6</b> · %s</li>" % why in page
    assert writes_of(got) == [(SC_BOARD, "3102", {RECEIPT_COL: {"index": 1}})]
    assert "לא נכתב:" in h.unescape(done["html"]) and why in h.unescape(done["html"])


def test_a_row_changed_after_the_confirmation_was_shown_is_not_written_and_the_rest_is():
    import html as h
    got = drive(steps=[{"show": TASKS}, {"click": [{"pick": "sc:3102"}]}, {"click": [{"pick": "sc:3106"}]},
                       {"click": [{"batch": "receipt_sent"}]},
                       {"mutate": {SC_BOARD: {"3102": {RECEIPT_COL: status_cell(RECEIPT_LABELS, "2")}}}},
                       {"click": [{"batchGo": "1"}]}],
                monday=receipts_world(), context=WRITES, answers=batch_answers(receipts_view()))
    done = got["steps"][5]["batch"]
    assert [(i["key"], i["result"]) for i in done["items"]] == [("sc:3102", "skipped"), ("sc:3106", "written")]
    assert done["items"][0]["why"] == 'השתנתה במאנדיי מאז שהתוכנית הוצגה, ועכשיו שליחת קבלה "לא רלוונטי"'
    assert writes_of(got) == [(SC_BOARD, "3106", {RECEIPT_COL: {"index": 1}})]
    report = h.unescape(got["steps"][5]["html"])
    assert "נכתב במאנדיי בשמך בשורה אחת." in report and "<li><b>תלמיד/ה 6</b></li>" in report
    assert "<p>לא נכתב:</p>" in report


def test_a_row_that_fails_is_said_with_why_and_the_others_are_written():
    denied = {"reject": {"message": "User unauthorized to perform action"}}
    got = drive(steps=[{"show": TASKS}, {"click": [{"pick": "sc:3102"}]}, {"click": [{"pick": "sc:3106"}]},
                       {"click": [{"batch": "receipt_sent"}]}, {"click": [{"batchGo": "1"}]}],
                monday=receipts_world(answers=[{}, {}, denied]), context=WRITES, answers=batch_answers(receipts_view()))
    done = got["steps"][4]
    assert [(i["key"], i["result"], i["why"]) for i in done["batch"]["items"]] == [
        ("sc:3102", "failed", "מאנדיי לא מרשה לך לכתוב בשורה הזאת. לא נכתב כלום."), ("sc:3106", "written", "")]
    assert "נכתב במאנדיי בשמך בשורה אחת, ובשאר לא: הפירוט ברשימה. המסך מתעדכן." in got["toasts"]
    assert 'class="fin-act-msg bad" role="alert"' in done["html"] and "<p>נכשל:</p>" in done["html"]
    assert len([p for p in got["posts"] if p[0] == "/api/desk/refresh"]) == 1


def test_a_missing_permission_stops_the_batch_at_its_first_write_and_nothing_else_is_sent():
    scope = {"reject": {"message": "Your app is missing the required scopes: boards:write"}}
    got = drive(steps=[{"show": TASKS}, {"click": [{"pick": "sc:3102"}]}, {"click": [{"pick": "sc:3106"}]},
                       {"click": [{"batch": "receipt_sent"}]}, {"click": [{"batchGo": "1"}]}],
                monday=receipts_world(answers=[{}, {}, scope]), context={}, answers=batch_answers(receipts_view()))
    done = got["steps"][4]
    assert [(i["result"], i["why"]) for i in done["batch"]["items"]] == [
        ("failed", "לאפליקציה אין הרשאה לכתוב בלוח הזה. לא נכתב כלום."), ("failed", "לאפליקציה אין הרשאה לכתוב בלוח הזה. לא נשלח.")]
    assert [s["kind"] for s in got["sent"]] == ["query", "query", "mutation"] and done["can"]["state"] == "no"
    assert "לא נכתב כלום: הפירוט ברשימה." in got["toasts"] and not [p for p in got["posts"] if p[0] == "/api/desk/refresh"]
    assert "data-row-act" not in done["html"] and "data-pick" not in done["html"]   # no button left where it cannot write


def test_a_label_renamed_in_monday_stops_the_whole_batch_before_anything_is_written():
    monday = receipts_world()
    monday["labels"][SC_BOARD][RECEIPT_COL]["1"] = "נשלחה"
    got = drive(steps=[{"show": TASKS}, {"click": [{"pick": "sc:3102"}]}, {"click": [{"batch": "receipt_sent"}]}],
                monday=monday, context=WRITES, answers=batch_answers(receipts_view()))
    failed = got["steps"][2]["batch"]
    assert failed["phase"] == "failed" and failed["error"] == "תווית שהפעולה כותבת השתנתה במאנדיי; נדב צריך לבדוק. לא נכתב כלום."
    assert writes_of(got) == [] and "data-batch-go" not in got["steps"][2]["html"]


@pytest.mark.parametrize("context, monday", [
    ({"permissions": {"approvedScopes": ["boards:read"], "requiredScopes": ["boards:read"]}}, None),
    (dict(WRITES, user={"isViewOnly": True}), None),
    ({}, {"absent": True}),
])
def test_where_the_person_cannot_write_the_row_carries_no_box_and_no_button(context, monday):
    got = drive(answer(receipts_view()), [{"show": TASKS}], monday=monday or receipts_world(), context=context)
    html = got["steps"][0]["html"]
    assert "data-pick" not in html and "data-row-act" not in html and "k-rowact" not in html and "k-pick" not in html


def test_the_bar_offers_each_action_its_picked_rows_offer_and_a_batch_takes_only_the_rows_of_its_action():
    import html as h
    got = drive(steps=[{"show": TASKS}, {"click": [{"pick": "sc:3102"}]}, {"click": [{"pick": "sc:3103"}]},
                       {"click": [{"batch": "refund_done"}]}],
                monday=receipts_world(), context=WRITES, answers=batch_answers(receipts_view()))
    bar = h.unescape(got["steps"][2]["html"])
    assert 'data-batch="receipt_sent">שליחת קבלה: "יצאה" <span class="fin-subtle">(1)</span>' in bar
    assert 'data-batch="refund_done">סימון "תשלום הוחזר" <span class="fin-subtle">(1)</span>' in bar
    assert [i["key"] for i in got["steps"][3]["batch"]["items"]] == ["sc:3103"]
    # a pick in a list that is not on the screen is not in its bar, nor in its batch: that list's bar says none is picked
    got = drive(steps=[{"show": TASKS}, {"click": [{"pick": "sc:3102"}]},
                       {"show": {"tab": "b2c", "sc": {"state": "refund_to_execute", "period": "", "open": {}}}}],
                monday=receipts_world(), context=WRITES, answers=batch_answers(receipts_view()))
    assert "נבחרה שורה אחת" in got["steps"][1]["html"]
    assert "נבחרה שורה אחת" not in got["steps"][2]["html"] and "fin-batchbar idle" in got["steps"][2]["html"]


def test_the_bar_holds_its_place_wherever_the_list_offers_a_box_and_says_what_the_box_is_for():
    """QA 1 (24.11): ticking the first row's box inserted the bar above the table, the rows moved down ~58px, and the click
    aimed at the second row's box unticked the first. The bar is there before any pick, quiet, wherever a row of the list
    carries a box - and only there: a list with no box, or a person who may not write, has none."""
    hint = "לא נבחרו שורות. אפשר לבחור כמה בתיבה שבתחילת השורה, ולסמן בכולן יחד."
    got = drive(steps=[{"show": TASKS}, {"click": [{"pick": "sc:3102"}]}, {"click": [{"pick": "sc:3102"}]},
                       {"show": {"tab": "b2c", "sc": {"state": "done", "period": "", "open": {}}}}],
                monday=receipts_world(), context=WRITES, answers=batch_answers(receipts_view()))
    idle, one, none, no_box = [s["html"] for s in got["steps"]]
    bar = '<div class="fin-batch fin-batchbar idle" role="group" aria-label="השורות שנבחרו"><span>%s</span></div>' % hint
    assert bar in idle and bar in none and hint not in one
    assert 'class="fin-batch fin-batchbar" role="group"' in one and "נבחרה שורה אחת" in one
    # the bar sits where it did, between the list's head and its table, in both
    for html in (idle, one):
        assert html.index("fin-listhead") < html.index("fin-batchbar") < html.index('<table class="fin-t">')
    assert "fin-batchbar" not in no_box and "data-pick" not in no_box
    got = drive(answer(receipts_view()), [{"show": TASKS}], monday={"absent": True})
    assert "fin-batchbar" not in got["steps"][0]["html"]


def test_a_new_pick_after_a_batch_drops_its_report_and_shows_the_bar_and_a_confirmation_cancelled_writes_nothing():
    got = drive(steps=[{"show": TASKS}, {"click": [{"pick": "sc:3102"}]}, {"click": [{"batch": "receipt_sent"}]},
                       {"click": [{"batchClose": "1"}]}, {"click": [{"rowAct": "sc:3106:receipt_sent"}]},
                       {"click": [{"pick": "sc:3103"}]}],
                monday=receipts_world(), context=WRITES, answers=batch_answers(receipts_view()))
    plan, cancelled, single, picked = got["steps"][2:]
    assert plan["batch"]["phase"] == "confirm" and cancelled["batch"] is None and writes_of(got) == []
    assert cancelled["picked"] == ["sc:3102"] and "נבחרה שורה אחת" in cancelled["html"]       # the pick stays for later
    assert [i["key"] for i in single["batch"]["items"]] == ["sc:3106"]                       # the row's own: that row only
    assert picked["batch"] is None and "נבחרו 2 שורות" in picked["html"]


def test_every_row_picks_at_most_one_batchs_worth_and_a_pick_past_it_is_said():
    v = view()
    for i in range(30):
        v = with_row(v, sc_row(str(4000 + i), "תלמיד/ה %d" % i, "receipt_pending", receipt="טרם", receipt_idx="0",
                                actions=["receipt_sent"]))
    got = drive(answer(v), [{"show": {"tab": "b2c", "sc": {"state": "receipt_pending", "period": "", "open": {}}}},
                            {"click": [{"pickAll": "1"}]}, {"click": [{"pick": "sc:4029"}]}, {"click": [{"pickAll": "1"}]}],
                monday=receipts_world(), context=WRITES)
    assert len(got["steps"][1]["picked"]) == 25 and "sc:4029" not in got["steps"][2]["picked"]
    assert "אפשר לבחור עד 25 שורות לכתיבה אחת." in got["toasts"]
    assert got["steps"][3]["picked"] == []                       # every row it can was picked: the second press lets go


def test_a_batch_floats_beside_the_bar_which_keeps_its_place_while_it_is_on_the_screen():
    """QA2 3 (24.11): the batch's box took the bar's place above the table, taller than it, and every row moved down. The bar
    is drawn in its place while a batch is on the screen - picked rows and all - and the batch's box floats (fin-pop, placed
    in the view by placePop; tests/test_desk_browser.py measures that no row moves). Where the batch's rows offer no box any
    more (written), the bar's place is held, empty, until the report closes; then the list has no bar, as before."""
    receipts = {"tab": "b2c", "sc": {"state": "receipt_pending", "period": "", "open": {}}}
    got = drive(steps=[{"show": receipts}, {"click": [{"pick": "sc:3102"}]}, {"click": [{"pick": "sc:3106"}]},
                       {"click": [{"batch": "receipt_sent"}]}, {"click": [{"batchGo": "1"}]}, {"click": [{"batchClose": "1"}]}],
                monday=receipts_world(), context=WRITES, answers=batch_answers(receipts_view()))
    confirm, done, closed = (s["html"] for s in got["steps"][3:])
    assert (confirm.index("fin-listhead") < confirm.index('class="fin-batch fin-batchbar" role="group"') <
            confirm.index('class="fin-batch fin-act-plan fin-batch-plan fin-pop"') < confirm.index('<table class="fin-t">'))
    assert "נבחרו 2 שורות" in confirm and confirm.count('class="fin-batch') == 2
    # written, both rows offer no box: the bar's place held, empty and unread, beside the floating report
    assert '<div class="fin-batch fin-batchbar idle fin-hold" aria-hidden="true"></div>' in done
    assert 'class="fin-batch fin-batch-done fin-pop"' in done
    assert "fin-batchbar" not in closed and "fin-pop" not in closed


CARD_NOTE = ("נכתב במאנדיי, כפי שמוצג כאן. כרטיס הפריט בלשונית הזאת עשוי להציג את הערך שלפני הכתיבה, עד שהדף ייטען "
             "מחדש בדפדפן.")


def test_opening_the_card_of_a_row_written_here_says_once_that_mondays_card_may_show_the_value_from_before_it():
    """QA2 2 (24.11): after "שליחת קבלה: יצאה" was written from the desk, "פתיחה במאנדיי" still showed "טרם" - closed and
    opened again, after "רענון" too - and only reloading the page showed "יצאה" (the item's page in a new tab showed it at
    once). The write was right; monday's card in that tab keeps what it read before. Opening over the view the card of a
    row written in this visit says so, once for that row, by any of the three ways to it - and never with the write
    itself, nor for a row not written."""
    only_3102 = copy.deepcopy(receipts_view())
    [r for r in only_3102["b2c"]["rows"] if r["id"] == "3102"][0].update(state="done", receipt="יצאה", receipt_idx="1",
                                                                          actions=[])
    answers = {"GET /api/desk": [{"body": answer(receipts_view())}],
               "POST /api/desk/refresh": [{"body": answer(only_3102, taken_at="2026-11-24T08:00:09+00:00")},
                                          {"body": answer(receipts_done(), taken_at="2026-11-24T08:00:19+00:00")}]}
    got = drive(steps=[{"show": TASKS}, {"click": [{"open": "3106"}, ["fin-name"]]},              # not written: nothing said
                       {"click": [{"rowAct": "sc:3102:receipt_sent"}]}, {"click": [{"batchGo": "1"}]},
                       {"click": [{"open": "3102"}, ["fin-dx-open"]]},                           # written here: said
                       {"click": [{"open": "3102"}, ["fin-name"]]},                              # once for the row
                       {"act": ["sc:3106", "receipt_sent"]}, {"confirm": True},                  # the details' action
                       {"click": [{"open": "3106"}]}],                                           # the quiet icon
                monday=receipts_world(), context=WRITES, answers=answers)
    assert writes_of(got) == [(SC_BOARD, "3102", {RECEIPT_COL: {"index": 1}}), (SC_BOARD, "3106", {RECEIPT_COL: {"index": 1}})]
    assert [e["args"]["itemId"] for e in got["executed"] if e["what"] == "openItemCard"] == [3106, 3102, 3102, 3106]
    assert got["toasts"] == ["נכתב במאנדיי בשמך בשורה אחת. המסך מתעדכן.", CARD_NOTE,
                             "נכתב במאנדיי בשמך: שליחת קבלה \"יצאה\". המסך מתעדכן.", CARD_NOTE]
    # outside monday a row's link opens its page in a new tab, which shows the value at once: nothing to say
    source = code_of(PAGE)
    assert source.index("cardNote(el.dataset.open)") > source.index("if (deps.monday) {", source.index("if (el.dataset.open) {"))


# --- ported: the ID number, read as the person (analiza-metrics tests/test_finance_id_js.py) ----------------------------

def test_a_payments_details_show_its_leads_id_number_read_as_the_person_never_from_the_server():
    body = answer()
    text = json.dumps(body, ensure_ascii=False)
    assert "000000018" not in text and "id_number" not in text
    v = view()
    for r in v["b2c"]["rows"]:
        r["lead_ids"] = {"3101": ["6101"], "3102": ["6102", "6103"], "3103": [], "3104": ["6102"], "3105": []}[r["id"]]
    got = drive(answer(v), [{"show": ALL_OPEN}, {"show": {}}], monday=world())
    html = got["steps"][0]["html"]
    cell = fact(detail_of(html, "3101"), "ת.ז")
    assert cell.startswith('<bdi dir="ltr" class="fin-idnum">000000018</bdi> <button type="button" class="fin-iconbtn" '
                           'data-id-copy="3101" aria-label="העתקת ת.ז" title="העתקת ת.ז"><svg ')
    # two leads: the first that holds one; no lead: nothing to read; a lead without one: said so
    assert "000000034" in fact(detail_of(html, "3102"), "ת.ז")
    assert fact(detail_of(html, "3103"), "ת.ז") == '<span class="fin-subtle">-</span>'
    assert fact(detail_of(html, "3104"), "ת.ז") == '<span class="fin-subtle">אין בליד</span>'
    ids = [s["variables"] for s in got["sent"] if s["variables"]["b"] == [LEADS_BOARD]]
    assert sorted(ids, key=lambda x: x["i"]) == [{"i": ["6101"], "c": [ID_COLUMN], "b": [LEADS_BOARD]},
                                                 {"i": ["6102"], "c": [ID_COLUMN], "b": [LEADS_BOARD]},
                                                 {"i": ["6102", "6103"], "c": [ID_COLUMN], "b": [LEADS_BOARD]}]
    # drawn again: read once per payment, kept in the page's memory only, and the server never sees a number
    assert got["steps"][1]["html"] == html and len([s for s in got["sent"] if s["variables"]["b"] == [LEADS_BOARD]]) == 3
    assert "000000018" not in json.dumps(got["posts"] + got["calls"] + got["usage"])


@pytest.mark.parametrize("first, words", [
    # no CRM seat: the board answers nothing - and since D2 no Excel file holds the number either
    ({"noBoard": True}, "אין לך גישה ללוח הלידים במאנדיי"),
    ({"noColumn": True}, "העמודה ת.ז לא נמצאה בלוח הלידים; נדב צריך לבדוק"),
    ({"noItems": True}, "הליד לא נמצא בלוח הלידים"),
    ({"reject": {"message": "Unauthorized field or type"}}, "לאפליקציה אין הרשאה לקרוא את לוח הלידים"),
    ({"reject": {"message": "User unauthorized to perform action"}}, "אין לך גישה ללוח הלידים במאנדיי"),
    ({"reject": {"message": "Rate limit exceeded"}}, "מאנדיי ביקשה להאט; לנסות שוב בעוד דקה"),
])
def test_when_monday_does_not_give_the_id_number_the_details_say_why_and_offer_to_try_again(first, words):
    one = {"tab": "b2c", "sc": {"state": "", "period": "", "open": {"3101": True}}}
    got = drive(answer(), [{"show": one}, {"click": [{"idRetry": "3101"}]}], monday=world(answers=[first]))
    failed, again = got["steps"]
    assert fact(detail_of(failed["html"], "3101"), "ת.ז") == (
        '<span class="fin-badtext">%s</span> <button type="button" class="fin-linkbtn" data-id-retry="3101">לנסות שוב</button>'
        % words)
    assert "000000018" in fact(detail_of(again["html"], "3101"), "ת.ז") and len(got["sent"]) == 2


def test_an_item_monday_answers_from_another_board_is_a_stranger_and_never_shown():
    """The adversarial review of 62fd993 asked that a live read refuse an item monday answered from another board, as the
    server's reader drops a stranger. readCells asks every item's `board { id }` and keeps only the board it asked; this
    holds it for the three reads: a lead's ID number, a refund's bank account and a company's ח.פ."""
    v = view()
    for r in v["b2c"]["rows"]:
        if r["id"] == "3102":
            r["lead_ids"] = ["6101", "6103"]                       # a stranger first, then a lead of the Leads board
    monday = world()
    monday["boards"][LEADS_BOARD]["6101"]["__board"] = "9999001"
    monday["boards"][SC_BOARD]["3103"]["__board"] = "9999002"
    monday["boards"][ACCOUNTS_BOARD]["7703"]["__board"] = "9999003"
    one = {"tab": "b2c", "sc": {"state": "", "period": "", "open": {"3101": True, "3102": True, "3103": True}}}
    got = drive(answer(v), [{"show": one}, {"show": LEDGER}], monday=monday, board=B2_BOARD)
    html, ledger = got["steps"][0]["html"], got["steps"][1]["html"]
    # the only lead is a stranger: not found; a stranger beside a lead of the board: the lead's number, never the stranger's
    assert fact(detail_of(html, "3101"), "ת.ז").startswith('<span class="fin-badtext">הליד לא נמצא בלוח הלידים</span>')
    assert "000000034" in fact(detail_of(html, "3102"), "ת.ז") and "000000018" not in html
    assert bank_box(html).startswith('<span class="fin-badtext">התקבול לא נמצא בלוח Student Charge</span>')
    assert "1234567" not in html
    assert fact(ledger, "ח.פ").startswith('<span class="fin-badtext">החברה לא נמצאה בלוח החברות</span>')
    assert "500000027" not in ledger
    assert all("board { id }" in s["query"] for s in got["sent"])


def test_a_newer_reading_reads_the_number_again_and_the_last_one_stays_on_the_screen_meanwhile():
    one = {"tab": "b2c", "sc": {"state": "", "period": "", "open": {"3101": True}}}
    got = drive(answer(), [{"show": one}, {"show": {}}, {"show": {}}], monday=world())
    assert len(got["sent"]) == 1
    newer = answer(taken_at="2026-11-24T08:00:30+00:00")
    got = drive(steps=[{"show": one}, {"click": [{"refresh": "1"}]}], monday=world(answers=[{}, {"hold": True}]),
                answers={"GET /api/desk": [{"body": answer()}], "POST /api/desk/refresh": [{"body": newer}]})
    assert len(got["sent"]) == 2 and "POST /api/desk/refresh" in got["calls"]
    shown = got["steps"][1]["html"]
    assert "000000018" in fact(detail_of(shown, "3101"), "ת.ז") and "קורא ממאנדיי" not in shown


def test_outside_monday_the_id_number_is_not_read_and_the_details_say_where_it_is():
    got = drive(answer(), [{"show": {"tab": "b2c", "sc": {"state": "", "period": "", "open": {"3101": True}}}}],
                monday={"absent": True})
    assert fact(detail_of(got["steps"][0]["html"], "3101"), "ת.ז") == \
        '<span class="fin-subtle">מוצגת רק כשהתצוגה פתוחה מתוך מאנדיי</span>'
    assert got["sent"] == []


def test_the_copy_button_puts_the_number_on_the_clipboard_and_says_so():
    one = {"tab": "b2c", "sc": {"state": "", "period": "", "open": {"3101": True}}}
    got = drive(answer(), [{"show": one}, {"click": [{"idCopy": "3101"}]}], monday=world())
    assert got["copied"] == ["000000018"] and "ת.ז הועתקה." in got["toasts"]
    got = drive(answer(), [{"show": one}, {"click": [{"idCopy": "3101"}]}], monday=world(), clipboard="refuses",
                oldCopyWorks=True)
    assert got["copied"] == [] and "ת.ז הועתקה." in got["toasts"]
    got = drive(answer(), [{"show": one}, {"click": [{"idCopy": "3101"}]}], monday=world(), clipboard="none",
                oldCopyWorks=False)
    assert "לא הצלחנו להעתיק: לסמן את המספר ולהעתיק ידנית." in got["toasts"]


def test_the_id_number_is_the_same_field_in_the_same_place_on_every_payment():
    v = view()
    for r in v["b2c"]["rows"]:
        r["state"] = "done"                       # one list's rows: the same details, refund part or not by its fields
        r["credit_approved"], r["refund_typed"], r["refund_date"], r["status_idx"] = "0", None, "", "1"
    got = drive(answer(v), [{"show": ALL_OPEN}], monday=world())
    details = re.findall(r'<tr class="fin-detail">(.*?)</td></tr>', got["steps"][0]["html"])
    fields = [re.findall(r"<dt>([^<]+)</dt>", x) for x in details]
    assert len(fields) == 5 and all(f == fields[0] for f in fields)
    assert fields[0][fields[0].index("אסמכתת חשבונית") + 1:][:2] == ["ת.ז", "נציג מכירות"]


# --- D2: a refund's bank account and a company's ח.פ, read as the person only when shown ---------------------------------

def test_a_refund_to_executes_bank_account_is_read_as_the_person_only_when_its_details_open():
    body = answer()
    assert not re.search(r'"bank', json.dumps(body))                         # the server sends no bank account
    closed = {"tab": "b2c", "sc": {"state": "refund_to_execute", "period": "", "open": {}}}
    got = drive(body, [{"show": closed}, {"show": REFUND_OPEN}, {"show": {}}], monday=world())
    listed, opened, again = got["steps"]
    # the list reads nothing and shows no account; its details read it once (and the ID number), and a redraw reads nothing
    assert "פרטי חשבון" not in listed["html"] and [listed["sent"], opened["sent"], again["sent"]] == [0, 2, 2]
    bank_reads = [s["variables"] for s in got["sent"] if s["variables"]["b"] == [SC_BOARD]]
    assert bank_reads == [{"i": ["3103"], "c": BANK_COLUMNS, "b": [SC_BOARD]}]
    assert bank_box(opened["html"]) == ('בנק <bdi dir="ltr">12</bdi> · סניף <bdi dir="ltr">345</bdi> · מספר חשבון '
                                        '<bdi dir="ltr">1234567</bdi> · בעל/ת החשבון')
    assert again["html"] == opened["html"]
    # only a refund to execute: the other rows' details hold no account and read none
    got = drive(body, [{"show": ALL_OPEN}], monday=world())
    assert [s["variables"]["i"] for s in got["sent"] if s["variables"]["b"] == [SC_BOARD]] == [["3103"]]
    assert got["steps"][0]["html"].count("פרטי חשבון") == 1
    # and never to the server: no request to it carries a cell the page read
    assert not [x for x in BANK.values() if x in json.dumps(got["posts"] + got["calls"] + got["usage"], ensure_ascii=False)]


@pytest.mark.parametrize("first, words", [
    ({"noBoard": True}, "אין לך גישה לפרטי החשבון במאנדיי"),
    ({"noColumn": True}, "עמודה של פרטי החשבון לא נמצאה בלוח Student Charge; נדב צריך לבדוק"),
    ({"noItems": True}, "התקבול לא נמצא בלוח Student Charge"),
    ({"reject": {"message": "Unauthorized field or type"}}, "לאפליקציה אין הרשאה לקרוא את לוח Student Charge"),
    ({"reject": {"message": "Rate limit exceeded"}}, "מאנדיי ביקשה להאט; לנסות שוב בעוד דקה"),
    ({"reject": {"message": "Internal server error"}}, "לא נקראו ממאנדיי"),
])
def test_when_monday_does_not_give_the_bank_account_the_details_say_why_and_offer_to_try_again(first, words):
    # the refund's part of the details is drawn first: its bank account is the first read, and meets the refusal
    got = drive(answer(), [{"show": REFUND_OPEN}, {"click": [{"bankRetry": "3103"}]}], monday=world(answers=[first]))
    failed, again = got["steps"]
    assert bank_box(failed["html"]) == ('<span class="fin-badtext">%s</span> <button type="button" class="fin-linkbtn" '
                                        'data-bank-retry="3103">לנסות שוב</button>' % words)
    assert "1234567" in bank_box(again["html"])


def test_a_bank_account_monday_holds_empty_outside_monday_and_while_it_is_read_are_each_said_as_they_are():
    empty = drive(answer(), [{"show": REFUND_OPEN}], monday=world(bank={c: "" for c in BANK_COLUMNS}))
    assert bank_box(empty["steps"][0]["html"]) == '<span class="fin-subtle">-</span>'
    outside = drive(answer(), [{"show": REFUND_OPEN}], monday={"absent": True})
    assert bank_box(outside["steps"][0]["html"]) == '<span class="fin-subtle">מוצגים רק כשהתצוגה פתוחה מתוך מאנדיי</span>'
    assert outside["sent"] == []
    held = drive(answer(), [{"show": REFUND_OPEN}], monday=world(answers=[{"hold": True}]))
    assert bank_box(held["steps"][0]["html"]) == '<span class="fin-subtle">קורא ממאנדיי...</span>'


def test_a_companys_vat_id_is_read_as_the_person_only_when_its_ledger_shows_the_billing_details():
    body = answer()
    assert "vat_id" not in json.dumps(body["view"]["b2b"]["by_customer"])     # the server never reads it
    got = drive(body, [{"show": {"tab": "b2b", "b2": {"state": "", "view": "customers", "customer": "", "open": {}}}},
                       {"show": {"tab": "b2b", "b2": {"state": "", "view": "list", "customer": "", "open": {}}}},
                       {"show": LEDGER}, {"show": {}}], monday=world(), board=B2_BOARD)
    aging, listed, ledger, again = got["steps"]
    assert got["sent"] == [s for s in got["sent"] if s["variables"] == {"i": ["7703"], "c": [VAT_COLUMN], "b": [ACCOUNTS_BOARD]}]
    assert len(got["sent"]) == 1 and "500000027" not in aging["html"] + listed["html"]
    assert fact(ledger["html"], "ח.פ") == '<bdi dir="ltr">500000027</bdi>' and again["html"] == ledger["html"]
    # whatever an answer might carry under the key, the ח.פ shown is the one monday gives the person now
    v = view()
    v["b2b"]["by_customer"]["7703"]["billing"]["vat_id"] = "999999999"
    got = drive(answer(v), [{"show": LEDGER}], monday=world(), board=B2_BOARD)
    assert fact(got["steps"][0]["html"], "ח.פ") == '<bdi dir="ltr">500000027</bdi>' and "999999999" not in got["steps"][0]["html"]
    assert not [x for x in VATS.values() if x and x in json.dumps(got["posts"] + got["calls"] + got["usage"])]


@pytest.mark.parametrize("monday, words", [
    ({"answers": [{"noBoard": True}]}, "אין לך גישה ללוח החברות במאנדיי"),
    ({"answers": [{"noColumn": True}]}, "העמודה ח.פ לא נמצאה בלוח החברות; נדב צריך לבדוק"),
    ({"answers": [{"noItems": True}]}, "החברה לא נמצאה בלוח החברות"),
    ({"answers": [{"reject": {"message": "Your app is missing the required scopes: boards:read"}}]},
     "לאפליקציה אין הרשאה לקרוא את לוח החברות"),
    ({"answers": [{"reject": {"message": "complexity budget exhausted"}}]}, "מאנדיי ביקשה להאט; לנסות שוב בעוד דקה"),
])
def test_when_monday_does_not_give_the_vat_id_the_billing_details_say_why_and_offer_to_try_again(monday, words):
    got = drive(answer(), [{"show": LEDGER}, {"click": [{"vatRetry": "7703"}]}], monday=world(**monday), board=B2_BOARD)
    failed, again = got["steps"]
    assert fact(failed["html"], "ח.פ") == ('<span class="fin-badtext">%s</span> <button type="button" class="fin-linkbtn" '
                                           'data-vat-retry="7703">לנסות שוב</button>' % words)
    assert fact(again["html"], "ח.פ") == '<bdi dir="ltr">500000027</bdi>'


def test_a_vat_id_monday_holds_empty_or_cannot_be_read_outside_monday_is_said_as_it_is():
    ledger = {"tab": "b2b", "b2": {"state": "", "view": "list", "customer": "7702", "open": {}}}
    empty = drive(answer(), [{"show": ledger}], monday=world(), board=B2_BOARD)
    assert fact(empty["steps"][0]["html"], "ח.פ") == '<span class="fin-subtle">-</span>'
    outside = drive(answer(), [{"show": ledger}], monday={"absent": True}, board=B2_BOARD)
    assert fact(outside["steps"][0]["html"], "ח.פ") == '<span class="fin-subtle">מוצג רק כשהתצוגה פתוחה מתוך מאנדיי</span>'
    assert outside["sent"] == []


# --- D2 (ADR-0188, the desk move plan §1b item 4): a company's billing details, written from the browser as the person ------

HASH_COL, NAME_COL, TERMS_COL = "text_mm3yprqa", "text_mm3yfrew", "dropdown_mm3yd36d"
BILL_COLS = [VAT_COLUMN, HASH_COL, NAME_COL, TERMS_COL]
TERMS_LABELS = [{"id": 1, "name": "שוטף"}, {"id": 2, "name": "שוטף+30"}, {"id": 3, "name": "שוטף+60"}, {"id": 4, "name": "אחר"},
                {"id": 5, "name": "שוטף+45"}]
UPDATES = {"permissions": {"approvedScopes": ["boards:read", "boards:write", "updates:write"]}, "user": {"id": 1}}


def billing_world(answers=None, labels=None, **company) -> dict:
    """monday as the person reads Accounts: company 7703 ("חברה ג", on the desk) with its four details, and the terms'
    labels by id; `company` sets its cells."""
    monday = world(labels={ACCOUNTS_BOARD: {TERMS_COL: labels or TERMS_LABELS}}, answers=list(answers or []))
    monday["boards"][ACCOUNTS_BOARD]["7703"].update({"__name": "חברה ג", VAT_COLUMN: "", HASH_COL: "H7703",
                                                     NAME_COL: 'חברה ג בע"מ', TERMS_COL: ""}, **company)
    return monday


def bill_steps(*edits, save=True):
    """The ledger of 7703, "עריכה", the edits ({"vat_id": ...} typed, {"terms": id} chosen), "המשך", and "שמירה"."""
    steps = [{"show": LEDGER}, {"click": [{"billEdit": "7703"}]}]
    for key, value in edits:
        steps.append({"choose": [{"bill": key}, value]} if key == "terms" else {"input": [{"bill": key}, value]})
    steps.append({"click": [{"billGo": "1"}]})
    return steps + ([{"click": [{"billSave": "1"}]}] if save else [])


def bill_reads(got) -> list:
    return [s["variables"] for s in got["sent"] if s["kind"] == "query" and s["variables"]["c"] == BILL_COLS]


def test_a_companys_billing_details_are_edited_from_the_browser_as_the_person_read_before_the_write_and_checked_after():
    """"עריכה" reads the four live as the person; "המשך" shows before and after; "שמירה" reads them again, writes the
    changed ones through mondayWrite (the terms by id), checks monday's answer, and asks for one refresh. Without
    updates:write - the app's scopes today - no Update is tried, and the confirmation says the activity log keeps the
    before and the after. The ח.פ reaches monday only."""
    import html as h
    got = drive(steps=bill_steps(("vat_id", " 512345678 "), ("terms", "2")), monday=billing_world(), board=B2_BOARD,
                context=WRITES, answers={"GET /api/desk": [{"body": answer()}]})
    shown, form, _vat, _terms, plan, done = got["steps"]
    assert 'data-bill-edit="7703">עריכה</button>' in shown["html"] and "תחזור בקרוב" not in shown["html"]
    assert form["bill"]["phase"] == "edit" and 'data-bill="vat_id" dir="ltr" inputmode="numeric" maxlength="9" value=""' in form["html"]
    assert 'במאנדיי עכשיו: <bdi dir="ltr">H7703</bdi>' in form["html"] and ">שוטף+30</option>" in form["html"]
    page = h.unescape(plan["html"])
    assert plan["bill"]["phase"] == "confirm" and 'ייכתב בלוח החברות, בחברה "חברה ג", בשמך:' in page
    assert '<li><b>ח.פ</b>: עכשיו ריק, ייכתב "512345678"</li><li><b>תנאי תשלום</b>: עכשיו ריק, ייכתב "שוטף+30"</li>' in page
    assert ("יומן הפעילות של לוח החברות שומר מי כתב, מה היה ומה נכתב. עדכון על החברה לא ייכתב: לאפליקציה אין הרשאה לכתוב "
            "עדכונים.") in page
    assert [x for x in got["sent"][:plan["sent"]] if x["kind"] == "mutation"] == []       # nothing written before "שמירה"
    # read when the form opened and again right before the write, then one write of the two that changed, by the terms' id
    assert bill_reads(got) == [{"i": ["7703"], "c": BILL_COLS, "b": [ACCOUNTS_BOARD]}] * 2
    assert writes_of(got) == [(ACCOUNTS_BOARD, "7703", {VAT_COLUMN: "512345678", TERMS_COL: {"ids": [2]}})]
    assert got["updates"] == [] and done["bill"]["outcome"] == "no_update"
    after = h.unescape(done["html"])
    assert "נכתב בלוח החברות בשמך. יומן הפעילות של הלוח שומר מה היה ומה נכתב." in after
    assert fact(done["html"], "תנאי תשלום") == "<bdi>שוטף+30</bdi>" and fact(done["html"], "ח.פ") == '<bdi dir="ltr">512345678</bdi>'
    assert [p for p in got["posts"] if p[0] == "/api/desk/refresh"] == [["/api/desk/refresh", None]]
    assert "512345678" not in json.dumps(got["posts"] + got["calls"] + got["usage"])


def test_with_updates_write_the_old_apps_update_is_written_on_the_company_with_before_and_after():
    import html as h
    got = drive(steps=bill_steps(("vat_id", "512345678"), ("terms", "2")), monday=billing_world(), board=B2_BOARD,
                context=UPDATES, answers={"GET /api/desk": [{"body": answer()}]})
    plan, done = got["steps"][4], got["steps"][5]
    assert "על החברה ייכתב גם עדכון בשמך: מה היה ומה נכתב." in h.unescape(plan["html"])
    assert [s["kind"] for s in got["sent"]][-2:] == ["mutation", "mutation"]             # the columns, then the Update
    assert got["updates"] == [{"i": "7703", "body": (
        "עדכון פרטי חיוב מתוך שולחן הכספים, בשם מי שכתב את העדכון הזה (משתמש 1).<br>"
        "ח.פ: היה ריק, נכתב &quot;512345678&quot;.<br>תנאי תשלום: היה ריק, נכתב &quot;שוטף+30&quot;.<br>"
        "הכתיבה נעשתה מהדפדפן בשם מי שכתב, ויומן הפעילות של לוח החברות רושם אותה גם הוא.")}]
    assert done["bill"]["outcome"] == "posted"
    assert "נכתב בלוח החברות בשמך, ועל החברה נכתב עדכון: מה היה ומה נכתב." in h.unescape(done["html"])


@pytest.mark.parametrize("edits, words", [
    ((("vat_id", "51234567"),), "ח.פ: 9 ספרות בדיוק, בלי מקפים ובלי רווחים."),
    ((("vat_id", "51-234567-8"),), "ח.פ: 9 ספרות בדיוק, בלי מקפים ובלי רווחים."),
    ((("hash_key", "מפתח"),), "מפתח חשבשבת: עד 15 אותיות באנגלית או ספרות."),
    ((("hash_key", "1" * 16),), "מפתח חשבשבת: עד 15 אותיות באנגלית או ספרות."),
    ((("invoice_name", "א" * 121),), "חשבונית על שם: עד 120 תווים, בשורה אחת."),
    ((("invoice_name", "חברה\u061c"),), "חשבונית על שם: עד 120 תווים, בשורה אחת."),     # the Arabic letter mark
    ((), "אין שינוי: כל הפרטים כבר כך."),
    ((("hash_key", " H7703 "),), "אין שינוי: כל הפרטים כבר כך."),
])
def test_the_billing_form_says_what_is_wrong_before_anything_is_written(edits, words):
    got = drive(steps=bill_steps(*edits, save=False), monday=billing_world(), board=B2_BOARD, context=WRITES)
    last = got["steps"][-1]
    assert last["bill"] == {"phase": "edit", "error": words, "outcome": None} and words in last["html"]
    assert writes_of(got) == [] and len(bill_reads(got)) == 1


def test_a_detail_monday_holds_that_the_rules_would_refuse_never_blocks_another_and_an_empty_one_clears():
    """review 11.11: a ח.פ with dashes kept in monday blocked setting the company's terms; a key typed in error is cleared
    (MON-43); the terms are never cleared from here."""
    monday = billing_world(**{VAT_COLUMN: "51-234567-8", HASH_COL: "10 269", TERMS_COL: "שוטף"})
    got = drive(steps=bill_steps(("terms", "3")), monday=monday, board=B2_BOARD, context=WRITES)
    assert writes_of(got) == [(ACCOUNTS_BOARD, "7703", {TERMS_COL: {"ids": [3]}})]
    got = drive(steps=bill_steps(("hash_key", ""), ("terms", "")), monday=billing_world(**{TERMS_COL: "שוטף"}), board=B2_BOARD,
                context=WRITES)
    assert writes_of(got) == [(ACCOUNTS_BOARD, "7703", {HASH_COL: ""})]


def test_details_changed_in_monday_after_the_form_was_read_are_shown_and_never_written_over():
    """Monday holds other terms by "שמירה" (and another ח.פ, a box she did not touch): nothing is written, the form shows
    what monday holds now and keeps what she chose; the untouched box takes the new value, so the next "שמירה" never
    writes the old one back (review 11.11). Only what is being written must still be what was shown: a change elsewhere
    is not written over, and does not stop the write."""
    import html as h
    steps = bill_steps(("terms", "2"), save=False) + [
        {"mutate": {ACCOUNTS_BOARD: {"7703": {VAT_COLUMN: "520034620", TERMS_COL: "שוטף"}}}}, {"click": [{"billSave": "1"}]},
        {"click": [{"billGo": "1"}]}, {"click": [{"billSave": "1"}]}]
    got = drive(steps=steps, monday=billing_world(), board=B2_BOARD, context=WRITES)
    moved, again, saved = got["steps"][-3:]
    assert moved["bill"]["phase"] == "edit" and "בינתיים פרטי החברה השתנו במאנדיי" in moved["bill"]["error"]
    form = h.unescape(moved["html"])
    assert 'data-bill="vat_id" dir="ltr" inputmode="numeric" maxlength="9" value="520034620"' in form
    assert 'במאנדיי עכשיו: <bdi dir="ltr">520034620</bdi>' in form and '<option value="2" selected>' in form
    assert writes_of(got) == [(ACCOUNTS_BOARD, "7703", {TERMS_COL: {"ids": [2]}})] and saved["bill"]["phase"] == "done"


def test_terms_whose_labels_moved_in_monday_are_never_written_and_the_other_details_still_are():
    renamed = [dict(x, name="30 יום") if x["id"] == 2 else x for x in TERMS_LABELS]
    got = drive(steps=bill_steps(("terms", "2"), save=False), monday=billing_world(labels=renamed), board=B2_BOARD, context=WRITES)
    assert got["steps"][-1]["bill"]["error"] == "התוויות של תנאי תשלום השתנו במאנדיי, ולכן לא נכתב כלום. נדב צריך לבדוק."
    got = drive(steps=bill_steps(("hash_key", "10269")), monday=billing_world(labels=renamed), board=B2_BOARD, context=WRITES)
    assert writes_of(got) == [(ACCOUNTS_BOARD, "7703", {HASH_COL: "10269"})]


@pytest.mark.parametrize("context, monday, why", [
    (dict(WRITES, user={"isViewOnly": True}), None, "במאנדיי יש לך הרשאת צפייה בלבד, ולכן אי אפשר לערוך מכאן."),
    ({"permissions": {"approvedScopes": ["boards:read"], "requiredScopes": ["boards:read"]}}, None,
     "לאפליקציה עוד אין הרשאת כתיבה ללוחות במאנדיי, ולכן אי אפשר לערוך מכאן."),
    ({}, {"absent": True}, "עריכת פרטי החיוב נכתבת במאנדיי בשמך, ולכן היא זמינה רק כשהתצוגה פתוחה מתוך מאנדיי."),
])
def test_who_may_not_write_sees_the_details_with_why_and_no_edit(context, monday, why):
    got = drive(answer(), [{"show": LEDGER}, {"click": [{"billEdit": "7703"}]}, {"click": [{"terms": "7703"}]}],
                monday=monday or billing_world(), board=B2_BOARD, context=context)
    for step in got["steps"]:
        assert "data-bill-edit" not in step["html"] and "data-bill=" not in step["html"] and step["bill"] is None
        assert '<p class="fin-act-why">%s</p>' % why in step["html"]
    assert writes_of(got) == [] and bill_reads(got) == []
    assert "לעריכת תנאי תשלום" not in got["steps"][0]["html"]


def test_no_terms_beside_a_company_opens_its_ledger_with_the_form_on_the_terms():
    got = drive(answer(), [{"show": {"tab": "b2b", "b2": {"state": "", "view": "customers", "customer": "", "open": {}}}},
                           {"click": [{"terms": "7703"}]}], monday=billing_world(), board=B2_BOARD, context=WRITES)
    listed, ledger = got["steps"]
    assert ('<button type="button" class="fin-chip fin-chiplink" data-terms="7703" title="פתיחת הכרטסת של החברה, לעריכת '
            'תנאי תשלום">אין תנאי תשלום</button>') in listed["html"]
    assert ledger["bill"]["phase"] == "edit" and 'data-bill="terms"' in ledger["html"] and len(bill_reads(got)) == 1


@pytest.mark.parametrize("answers, words, maybe", [
    ([{}, {}, {}, {"reject": {"message": "User unauthorized to perform action"}}],
     "מאנדיי לא מרשה לך לכתוב בלוח החברות. לא נכתב כלום.", False),
    ([{}, {}, {}, {"reject": {"message": "Your app is missing the required scopes: boards:write"}}],
     "לאפליקציה אין הרשאה לכתוב בלוח החברות. לא נכתב כלום.", False),
    ([{}, {}, {}, {"hold": True}], "מאנדיי לא ענתה על הכתיבה בזמן, ולכן לא ידוע אם נכתב. לבדוק את החברה במאנדיי לפני שמנסים שוב.",
     True),
])
def test_a_billing_write_that_failed_is_said_in_words_and_one_that_may_have_landed_is_read_again(answers, words, maybe):
    """The first read is the ledger's ח.פ, then the form's, the one before the write, and the write."""
    got = drive(steps=bill_steps(("vat_id", "512345678")), monday=billing_world(answers=answers), board=B2_BOARD,
                context=WRITES, mondayWaitMs=5)
    done = got["steps"][-1]
    assert done["bill"]["phase"] == "failed" and done["bill"]["error"] == words and words in got["toasts"]
    assert (["/api/desk/refresh", None] in got["posts"]) == maybe


def test_a_value_monday_kept_otherwise_is_said_and_read_again():
    monday = billing_world()
    monday["keep"] = [TERMS_COL]
    got = drive(steps=bill_steps(("terms", "2")), monday=monday, board=B2_BOARD, context=WRITES)
    done = got["steps"][-1]
    assert done["bill"]["error"] == "מאנדיי ענתה, אבל הערך שנשמר אינו מה שנשלח. לבדוק את החברה במאנדיי."
    assert "לא נכתב כלום" not in done["bill"]["error"] and ["/api/desk/refresh", None] in got["posts"]


@pytest.mark.parametrize("step, context, sent", [
    ({"write": [ACCOUNTS_BOARD, "7701", {VAT_COLUMN: "512345678"}]}, WRITES, True),     # a company the desk shows
    ({"write": [ACCOUNTS_BOARD, "7701", {TERMS_COL: {"ids": [5]}, HASH_COL: "", NAME_COL: "חברה"}]}, WRITES, True),
    ({"write": [ACCOUNTS_BOARD, "1", {VAT_COLUMN: "512345678"}]}, WRITES, False),       # an account the desk does not show
    ({"write": [ACCOUNTS_BOARD, "none", {VAT_COLUMN: "512345678"}]}, WRITES, False),
    ({"write": [ACCOUNTS_BOARD, "7701", {VAT_COLUMN: "51234567"}]}, WRITES, False),     # a value of another shape
    ({"write": [ACCOUNTS_BOARD, "7701", {VAT_COLUMN: 512345678}]}, WRITES, False),
    ({"write": [ACCOUNTS_BOARD, "7701", {HASH_COL: "מפתח"}]}, WRITES, False),
    ({"write": [ACCOUNTS_BOARD, "7701", {NAME_COL: " חברה"}]}, WRITES, False),
    ({"write": [ACCOUNTS_BOARD, "7701", {NAME_COL: "א" * 121}]}, WRITES, False),
    ({"write": [ACCOUNTS_BOARD, "7701", {TERMS_COL: {"ids": [9]}}]}, WRITES, False),
    ({"write": [ACCOUNTS_BOARD, "7701", {TERMS_COL: {"ids": ["2"]}}]}, WRITES, False),
    ({"write": [ACCOUNTS_BOARD, "7701", {TERMS_COL: {"ids": [2, 3]}}]}, WRITES, False),
    ({"write": [ACCOUNTS_BOARD, "7701", {TERMS_COL: {"labels": ["שוטף"]}}]}, WRITES, False),
    ({"write": [ACCOUNTS_BOARD, "7701", {TERMS_COL: ""}]}, WRITES, False),              # the terms are never cleared
    ({"write": [ACCOUNTS_BOARD, "7701", {"text_mm3yxxxx": "x"}]}, WRITES, False),       # a fifth column
    ({"update": [ACCOUNTS_BOARD, "7701", "עדכון"]}, UPDATES, True),                     # with updates:write
    ({"update": [ACCOUNTS_BOARD, "7701", "עדכון"]}, WRITES, False),                     # without it: never tried
    ({"update": [ACCOUNTS_BOARD, "1", "עדכון"]}, UPDATES, False),
    ({"update": [SC_BOARD, "3101", "עדכון"]}, UPDATES, False),
    ({"update": [ACCOUNTS_BOARD, "7701", "עד\u202eכון"]}, UPDATES, False),
    ({"update": [ACCOUNTS_BOARD, "7701", ""]}, UPDATES, False),
])
def test_the_billing_writes_and_the_update_go_out_only_inside_the_allowlist_and_the_gate(step, context, sent):
    """The four columns are on the allowlist with their rules, on a company the desk shows only; the Update only on such a
    company, and only when monday's context says the app holds updates:write. A refusal sends nothing."""
    got = drive(answer(), [step], monday=billing_world(), board=B2_BOARD, context=context)
    assert got["steps"][0]["refused"] == ("sent" if sent else "write_refused")
    assert len([s for s in got["sent"] if s["kind"] == "mutation"]) == (1 if sent else 0)


# --- D2: an answer with no view yet ---------------------------------------------------------------------------------------

def test_an_answer_without_a_view_says_the_data_was_not_read_yet_and_offers_the_refresh():
    failed = answer(None, last_attempt={"taken_at": "2026-11-24T07:59:00+00:00", "ok": False, "error": "deadline"})
    got = drive(steps=[{"click": [{"refresh": "1"}], "raw": True}], monday=world(),
                answers={"GET /api/desk": [{"body": answer(None)}], "POST /api/desk/refresh": [{"body": answer()}]})
    first = got["first"]
    assert '<p class="fin-msg">הנתונים עוד לא נקראו</p>' in first and 'data-refresh="1">רענון</button>' in first
    assert "נכשלה" not in first and "fin-tabs" not in first
    assert got["calls"][:2] == ["GET /api/desk", "POST /api/desk/refresh"] and "fin-tabs" in got["steps"][0]["html"]
    got = drive(failed, [], monday=world())
    assert '<p class="fin-msg">הקריאה האחרונה נכשלה.</p>' in got["first"] and "הנתונים עוד לא נקראו" in got["first"]
    # a reading on its way (`refreshing`) is asked for by the page's own second request, as for a view
    got = drive(steps=[{"raw": True}], monday=world(),
                answers={"GET /api/desk": [{"body": answer(None, refreshing=True)}], "GET /api/desk?fresh=1": [{"body": answer()}]})
    assert got["calls"][:2] == ["GET /api/desk", "GET /api/desk?fresh=1"] and "fin-tabs" in got["steps"][0]["html"]


def test_a_reading_whose_view_could_not_be_built_is_said_as_the_servers_fault_with_no_refresh_offered():
    """The adversarial review of 62fd993: the boards were read, the view crashed in its build (`view_error`), and the page
    said "הנתונים עוד לא נקראו" with a refresh that reads the boards again and crashes the same way."""
    crashed = answer(None, view_error="crashed", taken_at="2026-11-24T08:00:00+00:00",
                     last_attempt={"taken_at": "2026-11-24T08:00:00+00:00", "ok": True, "error": None})
    first = drive(crashed, [], monday=world())["first"]
    assert '<p class="fin-msg">הנתונים נקראו ממאנדיי, אבל השולחן לא נבנה מהם. זו תקלה בשרת, ונדב צריך לבדוק אותה.</p>' in first
    assert "הנתונים עוד לא נקראו" not in first and "data-refresh" not in first and "<button" not in first
    assert "fin-tabs" not in first
    # a view on the screen: a refresh answered with a view that could not be built keeps it, and says why
    got = drive(steps=[{"click": [{"refresh": "1"}], "raw": True}], monday=world(),
                answers={"GET /api/desk": [{"body": answer()}], "POST /api/desk/refresh": [{"body": crashed}]})
    assert "fin-tabs" in got["steps"][0]["html"] and "השולחן לא נבנה" not in got["steps"][0]["html"]
    assert got["toasts"] == ["הנתונים החדשים נקראו ממאנדיי, אבל השולחן לא נבנה מהם, ולכן מוצגים הנתונים הקודמים. זו תקלה "
                             "בשרת, ונדב צריך לבדוק אותה."]
    # and an answer with no reading at all is still "not read yet", with its refresh
    assert "הנתונים עוד לא נקראו" in drive(answer(None), [], monday=world())["first"]


def test_an_answer_without_a_view_never_replaces_the_view_on_the_screen():
    """An instance that has not read yet may answer a return or a refresh: the reading on the screen stays."""
    none = {"body": answer(None)}
    got = drive(steps=[{"back": [25_000], "raw": True}, {"click": [{"refresh": "1"}], "raw": True}], monday={"absent": True},
                clock=True, answers={"GET /api/desk": [{"body": answer()}], "POST /api/desk/refresh": [none, none]})
    assert [c for c in got["calls"] if "/totals" not in c] == ["GET /api/desk", "POST /api/desk/refresh", "POST /api/desk/refresh"]
    for step in got["steps"]:
        assert "fin-tabs" in step["html"] and "הנתונים עוד לא נקראו" not in step["html"]
        assert "fin-refreshing" not in step["html"]


# --- D2: the usage batches -----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("failure", [{"status": 404, "error": "http_404"}, {"status": 405, "error": "http_405"},
                                     {"blocked": True, "error": "unauthorized"}, {"message": "Failed to fetch"},
                                     {"status": 503, "error": "usage_unavailable"},
                                     {"status": 429, "error": "rate_limited", "retryAfter": 60}])
def test_the_usage_batches_stop_quietly_at_the_first_failure(failure):
    """The first batch that fails - nowhere to keep it (503 usage_unavailable), its own limit (429), a token monday no
    longer honours, a server without the route (D2 before POST /api/usage came) - ends the sending for this visit, and
    the failure is never said on the screen."""
    steps = [{"click": [{"bstate": "done"}]}, {"wait": 30}, {"click": [{"bstate": "refunded"}]}, {"wait": 30},
             {"click": [{"legend": "1"}]}, {"hide": True}, {"wait": 30}]
    got = drive(answer(), steps, monday=world(), useFlushMs=5, usageFails=failure)
    assert len(got["usage"]) == 1 and got["toasts"] == []
    assert all("usage" not in s["html"] and "http_" not in s["html"] for s in got["steps"])


def test_the_page_sends_its_usage_in_closed_words_and_never_a_name_an_amount_a_search_or_an_id_number():
    """Every event the page sends passes the server's check (app/desk/usage.py) untouched, and no batch holds the student's
    name, the amount, the ID number or the text she searched for - only the search's shape (analiza-metrics
    tests/test_finance_id_js.py at da671b2, on this view: payment 3101 named and paid as there, its lead 6101 holding
    000000018)."""
    from app.desk import usage

    v = view()
    v["b2c"]["rows"][0].update(name="דנה כהן", money_in="4321.5", paid_typed="4321.5", net="4321.5")
    steps = [{"show": {"tab": "b2c", "sc": {"state": "", "period": "", "open": {}}}},
             {"click": [{"bstate": "receipt_pending"}]},
             {"click": [{"row": "3102"}]},
             {"type": "דנה כהן", "wait": 400},                    # settles after the page's 250ms and the usage's 60ms
             {"type": "000000018", "wait": 400},                  # an ID number typed into the search
             {"type": "4321.50", "wait": 400},
             {"show": {"sc": {"open": {"3101": True}}}},          # the details read the ID number ("חיפוש ללא סינון" retired, 2.1)
             {"click": [{"idCopy": "3101"}]},
             {"click": [{"legend": "1"}]},
             {"click": [{"open": "3101"}, ["fin-name"]]},
             {"click": [{"export": "b2c"}]},
             {"change": ["fin-period", "2026-11"]},
             {"click": [{"fintab": "b2b"}]},
             {"hide": True}]
    got = drive(answer(v), steps, monday=world(), useSearchMs=60)
    batches = got["usage"]
    assert batches and batches[-1]["keep"] is True                       # the last one sent as the view went out of sight
    events = [e for b in batches for e in b["body"]["events"]]
    assert {b["body"]["desk"] for b in batches} == {"finance"} and len({b["body"]["session"] for b in batches}) == 1
    # the visit's id is sixteen letters, never a digit (D2's review: the server refuses any other)
    assert re.fullmatch(r"[a-z]{16}", batches[0]["body"]["session"]) and usage.SESSION.fullmatch(batches[0]["body"]["session"])
    kinds = [e["k"] for e in events]
    for k in ("open", "state", "row", "search", "id_shown", "id_copied", "legend", "open_item", "export",
              "period", "tab", "hide"):
        assert k in kinds, (k, kinds)
    # every event as the server checks it: none refused, each kept as it was sent
    rows_kept, refused = usage.clean_batch(dict(batches[0]["body"], events=events), 7)
    assert refused == 0 and [r["kind"] for r in rows_kept] == kinds
    assert [r["detail"] for r in rows_kept] == [e.get("d", {}) for e in events]
    searches = [e["d"] for e in events if e["k"] == "search"]
    assert [s["kind"] for s in searches] == ["hebrew", "digits", "amount"] and [s["len"] for s in searches] == [7, 9, 7]
    assert searches[0]["results"] == 0 and searches[0]["unfiltered"] == 1    # she searched inside a list that hid it
    # the page's age (`t`) and the first drawing's `ms` are clocks, not values: left out, so a slow machine's 4321 ms is no
    # false alarm
    text = json.dumps([dict({k: x for k, x in e.items() if k != "t"}, d={k: x for k, x in e.get("d", {}).items() if k != "ms"})
                       for e in events], ensure_ascii=False)
    for secret in ("דנה", "כהן", "000000018", "4321", "6101", "3101"):
        assert secret not in text, secret


# --- D2: nothing from an answer, or from monday, is ever parsed as HTML unescaped ----------------------------------------

def test_every_value_an_answer_or_monday_gives_is_escaped_into_the_page():
    bad = '<img src=x onerror="alert(1)">'
    v = view()
    for r in v["b2c"]["rows"]:
        r.update(name=bad, cohort_code=bad, cohort_title=bad, invoice=bad, channel=bad, rep=bad, refund_method=bad,
                 flow=bad, status=bad, credit_ref=bad)
    for r in v["b2b"]["rows"]:
        r.update(name=bad, account=bad, deal=bad, course=bad, invoice=bad, ctx={"account": bad, "deal": bad}, payer=bad,
                 deal_stage=bad, account_terms=bad)
    for c in v["b2b"]["customers"]:
        c["name"] = bad
    for part in (v["b2c"], v["b2b"]):
        part["tasks_about"] = bad
        part["legend"][0]["items"][0].update(term=bad, text=bad)
        for s in part["states"]:
            s.update(label=bad, about=bad)
    v["b2b"]["by_customer"]["7703"].update(name=bad, billing={"hash_key": bad, "invoice_name": bad, "terms": ""})
    v["b2c"]["periods"][1]["label"] = bad
    monday = world(leads={"6101": bad}, bank={c: bad for c in BANK_COLUMNS}, vats={"7703": bad})
    cases = [dict(ALL_OPEN, legend=True), REFUND_OPEN, {"tab": "b2c", "sc": {"state": "exception", "open": {"3104": True}}},
             {"tab": "b2b", "legend": True, "b2": {"state": "", "view": "list", "customer": "", "open": {"9901": True, "9903": True}}},
             {"tab": "b2b", "b2": {"state": "", "view": "customers", "customer": "", "open": {}}}, LEDGER]
    got = drive(answer(v), [{"show": c} for c in cases], monday=monday, board=B2_BOARD)
    for step in got["steps"]:
        html = step["html"]
        assert "<img" not in html and "onerror=\"" not in html
        assert "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;" in html
    assert len(got["sent"]) >= 3                                                 # the ID, the bank account, the ח.פ


def test_an_id_that_is_not_digits_is_escaped_in_a_retry_key_and_in_an_items_link():
    """The adversarial review of 62fd993: liveFailed's retry key goes into an attribute (escaped already, and pinned now);
    the same review's question asked of every id in an attribute found one more - the item's link in `href`, now escaped.
    The ids are monday's digits today, so this is defensive."""
    bad = '3101"><img src=x onerror="alert(1)">'
    v = view()
    for r in v["b2c"]["rows"]:
        if r["id"] == "3101":
            r["id"] = bad
    got = drive(answer(v), [{"show": {"tab": "b2c", "sc": {"state": "", "period": "", "open": {bad: True}}}}],
                monday=world(answers=[{"noBoard": True}]))
    html = got["steps"][0]["html"]
    assert "<img" not in html and 'onerror="' not in html
    safe = "3101&quot;&gt;&lt;img src=x onerror=&quot;alert(1)&quot;&gt;"
    assert ('<span class="fin-badtext">אין לך גישה ללוח הלידים במאנדיי</span> <button type="button" class="fin-linkbtn" '
            'data-id-retry="%s">לנסות שוב</button>' % safe) in html
    assert html.count('href="https://analiza-college-group.monday.com/boards/5093448677/pulses/%s"' % safe) == 3


# --- the glue (desk.js) --------------------------------------------------------------------------------------------------

GLUE_HARNESS = r"""
const vm = require("vm"), fs = require("fs");
const input = JSON.parse(fs.readFileSync(0, "utf8"));
const nodes = {}, mounted = [], ran = [], fetches = [], listened = [], results = [];
function nodeOf(id) { if (!nodes[id]) { nodes[id] = { id: id, hidden: id !== "screen-loading", textContent: "" }; } return nodes[id]; }
const body = { attrs: {}, classes: [], setAttribute(k, v) { body.attrs[k] = v; }, classList: { add(c) { body.classes.push(c); } } };
global.document = { getElementById: nodeOf, body: body, readyState: "complete", addEventListener() {} };
const tokens = (input.tokens || ["tok-1"]).slice();
const sdk = {
  get(what) {
    if (what === "context") { return Promise.resolve({ data: input.context === undefined ? {} : input.context }); }
    if (what === "sessionToken") { return Promise.resolve({ data: tokens.length > 1 ? tokens.shift() : tokens[0] }); }
    return Promise.resolve({ data: null });
  },
  listen(what) { listened.push(what); },
  api() { throw new Error("the glue never calls monday.api"); }
};
const queue = (input.fetch || []).slice();
global.fetch = function (path, init) {
  fetches.push({ path: path, init: init });
  const a = queue.shift() || { status: 200, body: {} };
  return Promise.resolve({ status: a.status, ok: a.status >= 200 && a.status < 300,
    json: () => a.body === undefined ? Promise.reject(new Error("no json")) : Promise.resolve(a.body),
    headers: { get: (h) => (a.headers || {})[h] || null } });
};
global.window = { location: { search: input.search || "" }, matchMedia: () => ({ matches: false }),
  mondaySdk: input.noSdk ? undefined : function () { return sdk; },
  FinanceDesk: input.noDesk ? undefined : { mount(root, deps) {
    mounted.push({ root: root.id, monday: deps.monday === sdk ? "sdk" : deps.monday, board: deps.board, keys: Object.keys(deps).sort() });
    let p = Promise.resolve();
    (input.mountCalls || []).forEach((c) => { p = p.then(() => (c[0] === "GET" ? deps.api(c[1]) : deps.post(c[1], c[2], c[3]))
      .then((b) => { results.push({ ok: b }); }, (e) => { results.push({ err: e }); })); });
    return p;
  } },
  FinanceDeskCheck: { run(s) { ran.push(s === sdk ? "sdk" : s); } } };
vm.runInThisContext(fs.readFileSync(process.argv[1], "utf8"));
setTimeout(() => {
  const shown = Object.keys(nodes).filter((k) => k.indexOf("screen-") === 0 && !nodes[k].hidden);
  fs.writeSync(1, JSON.stringify({ mounted: mounted, ran: ran, fetches: fetches, listened: listened, results: results,
    shown: shown, blocked: nodeOf("blocked-msg").textContent, check: nodeOf("check").hidden, theme: body.attrs["data-theme"],
    classes: body.classes }));
  process.exit(0);
}, 100);
"""


def glue(**data) -> dict:
    return node(GLUE_HARNESS, data, page=GLUE)


@pytest.mark.parametrize("context, board", [
    ({"boardId": 9999001, "theme": "dark", "user": {"id": "1"}}, ""),          # a private board: monday, no charge board
    ({"boardId": 5097721228}, "5097721228"), ({"boardIds": [5093448677]}, "5093448677"),
])
def test_the_glue_passes_monday_whenever_monday_supplied_a_context_and_the_board_only_when_it_is_a_charge_board(context, board):
    got = glue(context=context)
    assert got["mounted"] == [{"root": "fin-root", "monday": "sdk", "board": board, "keys": ["api", "board", "monday", "post"]}]
    assert got["shown"] == ["screen-finance"] and got["listened"] == ["context"] and got["ran"] == []
    assert got.get("theme") == ("dark" if context.get("theme") == "dark" else None)


def test_without_a_context_the_desk_gets_no_monday_and_without_a_token_the_view_says_where_to_open_it():
    got = glue(context={})
    assert got["mounted"][0]["monday"] is None and got["mounted"][0]["board"] == ""
    got = glue(noSdk=True, search="?sessionToken=local-dev")
    assert got["mounted"][0]["monday"] is None
    got = glue(noSdk=True)
    assert got["mounted"] == [] and got["shown"] == ["screen-blocked"]
    assert got["blocked"] == "פתחו את התצוגה מתוך monday - שם היא מקבלת את הזהות שלכם."
    got = glue(noDesk=True, context={"boardId": 1})
    assert got["blocked"] == "קובץ התצוגה של הכספים לא נטען. רעננו את הדף."


def test_the_glue_asks_a_fresh_token_once_on_token_expired_and_carries_a_429_and_keepalive():
    expired = {"status": 401, "body": {"error": "token_expired"}}
    got = glue(context={"boardId": 1}, tokens=["tok-1", "tok-2", "tok-3"], mountCalls=[["GET", "/api/desk"]],
               fetch=[expired, {"status": 200, "body": {"view": None}}])
    assert [f["init"]["headers"]["Authorization"] for f in got["fetches"]] == ["Bearer tok-1", "Bearer tok-2"]
    assert got["results"] == [{"ok": {"view": None}}]
    # once: a second token_expired is a refusal, and a 401 of another kind is not asked again
    got = glue(context={"boardId": 1}, tokens=["tok-1", "tok-2"], mountCalls=[["GET", "/api/desk"], ["GET", "/api/desk"]],
               fetch=[expired, expired, {"status": 401, "body": {"error": "unauthorized"}}])
    assert len(got["fetches"]) == 3
    assert [r["err"]["error"] for r in got["results"]] == ["token_expired", "unauthorized"]
    assert all(r["err"]["blocked"] is True for r in got["results"])
    got = glue(context={"boardId": 1}, mountCalls=[["POST", "/api/desk/refresh"], ["POST", "/api/usage", {"events": []}, True],
                                                   ["GET", "/api/desk/totals?tab=b2c"]],
               fetch=[{"status": 429, "body": {"error": "rate_limited", "retry_after_seconds": 17}, "headers": {"Retry-After": "20"}},
                      {"status": 404, "body": {"detail": "Not Found"}}, {"status": 503, "body": {"error": "app_not_configured"}}])
    rate, usage, conf = got["results"]
    assert rate["err"]["error"] == "rate_limited" and rate["err"]["retryAfter"] == 17
    assert usage["err"]["error"] == "http_404" and usage["err"]["status"] == 404
    assert conf["err"] == {"blocked": True, "error": "app_not_configured", "status": 503, "body": {"error": "app_not_configured"}}
    post = got["fetches"][1]["init"]
    assert post["method"] == "POST" and post["keepalive"] is True and post["headers"]["Content-Type"] == "application/json"
    assert json.loads(post["body"]) == {"events": []} and "keepalive" not in got["fetches"][0]["init"]


def test_check_1_draws_the_self_check_with_the_pages_sdk_and_never_mounts_the_desk():
    got = glue(search="?check=1", context={"boardId": 1})
    assert got["ran"] == ["sdk"] and got["mounted"] == [] and got["fetches"] == []
    assert got["check"] is False and got["shown"] == [] and got["classes"] == ["is-check"]
