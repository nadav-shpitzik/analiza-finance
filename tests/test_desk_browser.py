"""The desk's page in a real browser (D2): /desk/ served by this service's own routes, its API a stub answering the
hand-made view (tests/desk_page_view.py), monday a fake SDK. Chromium through Playwright for node; skipped without them.

One visit, the way Chava works: the students tab opens on the view's board, a refund to execute is opened (its bank
account and the ID number are read from monday as the person, then and only then), the companies tab, one company's
ledger (its ח.פ read the same way, its billing details with their edit, ADR-0188), the Excel link, a refresh; and
/desk/?check=1 draws D1's six lines. Set DESK_SCREENSHOT=<path.png> to keep a screenshot of the refund's details."""
from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time
from decimal import Decimal

import pytest

from tests.desk_page_view import answer, b2_row, sc_row, view

NODE = shutil.which("node")


def _playwright_root() -> str | None:
    """The global node_modules that holds playwright, or None."""
    if NODE is None:
        return None
    npm = shutil.which("npm")
    roots = [subprocess.run([npm, "root", "-g"], capture_output=True, text=True).stdout.strip()] if npm else []
    for root in [r for r in roots if r] + [os.environ.get("NODE_PATH", "")]:
        if root and os.path.isdir(os.path.join(root, "playwright")):
            return root
    return None


PW_ROOT = _playwright_root()
pytestmark = pytest.mark.skipif(PW_ROOT is None, reason="node with playwright is not installed")

# what the fake monday holds for the reads the page makes as the person (invented, never a real number)
LEADS = {"6101": "000000018", "6102": "000000026", "6103": "000000034", "6105": ""}
BANK = {"text_mm8cxm9y": "בעל/ת החשבון", "text_mm8cv3v3": "12", "text_mm8cgc0d": "345", "text_mm8ckgh8": "1234567"}
VAT = {"7701": "500000001", "7702": "", "7703": "500000027"}


def stub_app(log: list, v: dict | None = None, refreshed: dict | None = None, opened: dict | None = None,
             fresher: dict | None = None):
    """This service's page routes, behind a stub of D2's API that records every request it is asked: the hand-made view,
    or `v`; a refresh answers `refreshed` when given (the reading after a write). `opened`: what the plain GET's answer says
    besides (`{"refreshing": True}`: a held reading, so the page asks `?fresh=1` for the next); `fresher`: the view a
    `?fresh=1` answers, a newer reading."""
    from fastapi import FastAPI, Request
    from fastapi.responses import Response

    from app.desk.routes import router as desk_router
    from app.main import CSP, gzip_answers

    app = FastAPI()
    # QA 26.11 (B): served gzipped as this service serves it (inside the recording, as inside main's own middleware), so
    # the page's fetches here are the ones Chava's browser makes
    gzip_answers(app)
    count = {"n": 0}
    held = {"view": v if v is not None else view()}       # the reading this stub holds: the latest it answered

    @app.middleware("http")
    async def record(request: Request, call_next):
        body = (await request.body()).decode("utf-8", "replace")
        log.append({"method": request.method, "path": request.url.path, "query": request.url.query, "body": body,
                    "auth": request.headers.get("authorization", "")})
        resp = await call_next(request)
        resp.headers["Content-Security-Policy"] = CSP
        return resp

    @app.get("/api/desk")
    def desk(fresh: str = ""):
        if fresh and fresher is not None:
            held["view"] = fresher
            return answer(fresher, taken_at="2026-11-24T08:00:30+00:00")
        base = answer(v) if v is not None else answer()
        return dict(base, **(opened or {})) if not fresh else base

    @app.post("/api/desk/refresh")
    def refresh():
        count["n"] += 1
        taken = "2026-11-24T08:00:%02d+00:00" % (10 + count["n"])
        if refreshed is not None:
            held["view"] = refreshed
        return answer(held["view"], taken_at=taken)

    @app.get("/api/desk/totals")
    def totals(tab: str = "b2c", state: str = ""):
        shown = held["view"]
        if tab == "b2b_customers":
            rows = shown["b2b"]["customers"]
            keys = ["to_bill", "partial_balance", "open"] + ["aging_" + b["key"] for b in shown["b2b"]["bands"]]
            sums = {k: str(sum(Decimal((c["aging"][k[6:]] if k.startswith("aging_") else c[k]) or 0) for c in rows))
                    for k in keys}
            sums.update(partial_count=sum(c["partial_count"] for c in rows), partial_unknown=0)
            return {"count": len(rows), "sums": sums}
        part = shown["b2c"] if tab == "b2c" else shown["b2b"]
        tasks = {s["key"] for s in part["states"] if s["task"]}
        rows = [r for r in part["rows"] if not state or (r["state"] in tasks if state == "tasks" else r["state"] == state)]
        fields = ("money_in", "refunded", "credit_approved") if tab == "b2c" else ("amount", "open_money", "paid_so_far", "balance")
        return {"count": len(rows), "sums": {f: str(sum(Decimal(r[f] or 0) for r in rows)) for f in fields}}

    @app.post("/api/desk/export-link")
    def export_link():
        return {"url": "/api/desk/export.xlsx?tab=b2b&exp=1&sig=stub", "expires_in": 120}

    @app.get("/api/desk/export.xlsx")
    def export_file():
        return Response(b"PK-stub", media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    app.include_router(desk_router)
    return app


FAKE_SDK = r"""
window.__monday = [];
window.__executed = [];
window.mondaySdk = function () {
  var store = %(store)s, labels = %(labels)s;
  /* a cell as monday answers it: a text, or a status ({text, index, label}) */
  function cell(x, c) {
    var v = (x || {})[c], out = { id: c, text: v && typeof v === "object" ? v.text || "" : v || "", value: null };
    if (v && typeof v === "object" && v.index !== undefined) { out.index = v.index; out.label = v.label; }
    return out;
  }
  return {
    get: function (what) {
      if (what === "context") {
        return Promise.resolve({ data: { boardId: 5093448677, instanceType: "board_view", theme: "light",
          user: { id: "1", isViewOnly: false, isGuest: false }, appVersion: { name: "5", status: "draft" },
          permissions: { approvedScopes: ["boards:read", "boards:write"], requiredScopes: ["boards:read", "boards:write"] } } });
      }
      if (what === "sessionToken") { return Promise.resolve({ data: "stub-session-token" }); }
      return Promise.resolve({ data: null });
    },
    api: function (query, opts) {
      var v = (opts || {}).variables || {};
      window.__monday.push({ query: query, variables: v, apiVersion: (opts || {}).apiVersion });
      /* D4's review: the rows of a board whose cell in a column is one of the values, by its whole text */
      if (query.indexOf("items_page_by_column_values(") !== -1) {
        var on = store[String(v.b)] || {};
        return Promise.resolve({ data: { items_page_by_column_values: { cursor: null, items: Object.keys(on).filter(function (id) {
          return (v.v || []).indexOf(cell(on[id], v.c).text) !== -1;
        }).map(function (id) { return { id: id, name: on[id].__name || "" }; }) } } });
      }
      var board = String((v.b || [])[0]), cells = store[board] || {};
      var items = (v.i || []).filter(function (id) { return Object.prototype.hasOwnProperty.call(cells, id); }).map(function (id) {
        return { id: id, name: (cells[id] || {}).__name || "", board: { id: board },
          column_values: (v.c || []).map(function (c) { return cell(cells[id], c); }) };
      });
      return Promise.resolve({ data: { items: items, boards: [{ id: board, columns: (v.c || []).map(function (c) {
        return { id: c, type: "x", settings_str: JSON.stringify({ labels: (labels[board] || {})[c] || {} }) };
      }) }] } });
    },
    execute: function (what, args) { window.__executed.push({ what: what, args: args }); return Promise.resolve({}); },
    listen: function () {}
  };
};
"""


def fake_sdk(store: dict, labels: dict | None = None) -> str:
    """The fake SDK, holding `store` ({board: {item: {column: text or {text, index, label}, "__name": name}}}) and the
    status columns' `labels` ({board: {column: {index: label}}})."""
    return FAKE_SDK % {"store": json.dumps(store, ensure_ascii=False), "labels": json.dumps(labels or {}, ensure_ascii=False)}


VISIT = r"""
const { chromium } = require("playwright");
const [base, sdk, shot] = process.argv.slice(1);
(async () => {
  const browser = await chromium.launch();
  const out = { errors: [], steps: {} };
  const page = await browser.newPage({ viewport: { width: 1434, height: 900 }, deviceScaleFactor: 1 });
  page.on("pageerror", (e) => out.errors.push("pageerror: " + e));
  page.on("console", (m) => { if (m.type() === "error") { out.errors.push("console: " + m.text()); } });
  await page.route("**/desk/vendor/monday-sdk.js*", (r) => r.fulfill({ contentType: "application/javascript", body: sdk }));
  const monday = () => page.evaluate(() => window.__monday.length);
  const text = (sel) => page.$eval(sel, (el) => el.innerText);

  await page.goto(base + "/desk/");
  await page.waitForSelector("#screen-finance:not([hidden]) .fin-tabs", { timeout: 15000 });
  out.steps.opened = { tab: await text(".fin-tab.on"), title: await page.title(), monday: await monday(),
    shown: await page.$$eval("section[id^=screen-]", (s) => s.filter((x) => !x.hidden).map((x) => x.id)) };

  await page.click('[data-bstate="refund_to_execute"]');
  await page.waitForSelector('tr[data-row="3103"]');
  out.steps.list = { heads: await page.$$eval(".fin-t thead th", (t) => t.map((x) => x.innerText)), monday: await monday() };
  await page.click('tr[data-row="3103"] td.k-to_refund');
  await page.waitForFunction(() => /מספר חשבון/.test((document.querySelector(".fin-dx-box") || {}).innerText || "") &&
    /000000034/.test((document.querySelector(".fin-detail") || {}).innerText || ""), null, { timeout: 15000 });
  out.steps.refund = { bank: await text(".fin-dx-box"), monday: await page.evaluate(() => window.__monday.slice()) };
  if (shot) { await page.screenshot({ path: shot, fullPage: true }); }

  await page.click('[data-fintab="b2b"]');
  await page.click('[data-b2view="customers"]');
  await page.click('tr[data-customer="7703"]');
  await page.waitForFunction(() => /500000027/.test((document.querySelector(".fin-billing") || {}).innerText || ""), null,
    { timeout: 15000 });
  out.steps.ledger = { billing: await text(".fin-billing"), edit: await page.$$eval("[data-bill-edit],.fin-billing button",
    (b) => b.length), monday: await monday() };

  await page.click('[data-export="b2b"]');
  await page.waitForFunction(() => window.__executed.length > 0, null, { timeout: 15000 });
  out.steps.exported = await page.evaluate(() => window.__executed);

  await page.click("[data-refresh]");
  await page.waitForFunction((n) => window.__monday.length > n, out.steps.ledger.monday, { timeout: 15000 });
  await page.waitForFunction(() => !document.querySelector(".fin-refreshing"), null, { timeout: 15000 });
  out.steps.refreshed = { monday: await page.evaluate(() => window.__monday.slice(-1)), asof: await text(".fin-asof") };

  const check = await browser.newPage({ viewport: { width: 1200, height: 700 } });
  check.on("pageerror", (e) => out.errors.push("check pageerror: " + e));
  await check.route("**/desk/vendor/monday-sdk.js*", (r) => r.fulfill({ contentType: "application/javascript", body: sdk }));
  await check.goto(base + "/desk/?check=1");
  await check.waitForFunction(() => document.querySelectorAll(".check").length === 6 && !document.querySelector(".check.is-wait"),
    null, { timeout: 15000 });
  out.steps.check = { lines: await check.$$eval(".check", (ls) => ls.map((l) => l.className)),
    desk: await check.$eval("#fin-root", (r) => r.innerHTML.length),
    shown: await check.$$eval("section[id^=screen-]", (s) => s.filter((x) => !x.hidden).map((x) => x.id)) };
  await browser.close();
  console.log(JSON.stringify(out));
})().catch((e) => { console.log(JSON.stringify({ crashed: String(e && e.stack || e) })); process.exit(1); });
"""


# QA 26.11 (10): the fake SDK, its reads waiting until the page's window.__go() lets them through - a read on its way
HOLD_READS = r"""
(function () {
  var make = window.mondaySdk;
  window.__held = []; window.__hold = true;
  window.mondaySdk = function () {
    var sdk = make(), real = sdk.api;
    sdk.api = function (q, o) {
      if (!window.__hold) { return real(q, o); }
      return new Promise(function (ok) { window.__held.push(function () { ok(real(q, o)); }); });
    };
    return sdk;
  };
  window.__go = function () { window.__hold = false; window.__held.splice(0).forEach(function (f) { f(); }); };
})();
"""


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def served():
    import uvicorn

    log: list = []
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(stub_app(log), host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 15
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    assert server.started
    yield "http://127.0.0.1:%d" % port, log
    server.should_exit = True
    thread.join(timeout=10)


def test_the_desk_in_a_browser_reads_what_the_server_never_sends_only_when_shown(served):
    base, log = served
    store = {"5089697337": {k: {"text_mkzvem4": v} for k, v in LEADS.items()},
             "5093448677": {"3103": BANK}, "5089697343": {k: {"text_mm3y3gm7": v} for k, v in VAT.items()}}
    sdk = fake_sdk(store)
    shot = os.environ.get("DESK_SCREENSHOT", "")
    env = dict(os.environ, NODE_PATH=PW_ROOT)
    run = subprocess.run([NODE, "-e", VISIT, base, sdk, shot], capture_output=True, text=True, timeout=120, env=env)
    assert run.returncode == 0, (run.stdout[-2000:], run.stderr[-2000:])
    got = json.loads(run.stdout.strip().splitlines()[-1])
    steps = got["steps"]
    assert got["errors"] == []

    # the view's board is Student Charge: the students tab, the desk's own screen, and nothing read from monday yet
    assert steps["opened"] == {"tab": "סטודנטים", "title": "כספים - אנליזה", "monday": 0, "shown": ["screen-finance"]}
    # "החזר לביצוע" lists no bank account (D2), and choosing the list reads nothing
    assert "פרטי חשבון" not in steps["list"]["heads"] and steps["list"]["monday"] == 0
    # its details open: the bank account and the ID number, each read once, as the person, at the pinned version
    reads = steps["refund"]["monday"]
    assert sorted(json.dumps(r["variables"], sort_keys=True) for r in reads) == sorted(json.dumps(x, sort_keys=True) for x in [
        {"i": ["3103"], "c": ["text_mm8cxm9y", "text_mm8cv3v3", "text_mm8cgc0d", "text_mm8ckgh8"], "b": ["5093448677"]},
        {"i": ["6103"], "c": ["text_mkzvem4"], "b": ["5089697337"]}])
    assert all(r["query"].startswith("query ") and "mutation" not in r["query"] and r["apiVersion"] == "2026-07" for r in reads)
    assert steps["refund"]["bank"] == "פרטי חשבון בנק 12 · סניף 345 · מספר חשבון 1234567 · בעל/ת החשבון"

    # one company's ledger: its ח.פ read when its billing details show, and their edit (ADR-0188: from the browser, as the
    # person) one press away - nothing more is read until it is pressed
    billing = steps["ledger"]["billing"]
    assert "500000027" in billing and "תחזור בקרוב" not in billing and steps["ledger"]["edit"] == 1
    assert steps["ledger"]["monday"] == 3
    # the Excel file: the link this service signs, opened in a new tab through monday
    assert steps["exported"] == [{"what": "openLinkInTab", "args": {"url": base + "/api/desk/export.xlsx?tab=b2b&exp=1&sig=stub"}}]
    # "רענון": a newer reading, and the ח.פ on the screen read again with it
    assert steps["refreshed"]["monday"][0]["variables"] == {"i": ["7703"], "c": ["text_mm3y3gm7"], "b": ["5089697343"]}

    # /desk/?check=1: D1's six lines, and no desk
    assert len(steps["check"]["lines"]) == 6 and steps["check"]["desk"] == 0 and steps["check"]["shown"] == []

    # the server: only this service's desk routes, never a path under /api/finance, the token on every API request,
    # and never a value the page read from monday
    api = [x for x in log if x["path"].startswith("/api/")]
    assert {x["path"] for x in api} <= {"/api/desk", "/api/desk/refresh", "/api/desk/totals", "/api/desk/export-link",
                                        "/api/usage"}
    assert not [x for x in log if x["path"].startswith("/api/finance")]
    assert all(x["auth"] == "Bearer stub-session-token" for x in api)
    # (the page's own files are asked by their content's hash, `?v=` and twelve hex digits: a bank code of two digits is in
    # one of them by chance - 19cb26cc1012 - so the hash is left out of what is searched, never a request the page makes)
    sent = json.dumps([dict(x, query=re.sub(r"(?:^|&)v=[0-9a-f]{12}(?=&|$)", "", x["query"])) for x in log],
                      ensure_ascii=False)
    for secret in list(LEADS.values()) + list(BANK.values()) + list(VAT.values()):
        assert not secret or secret not in sent, secret


def test_the_first_details_read_shows_its_own_spinner_and_nothing_of_the_page_covers_the_desk(serve):
    """QA 26.11 (10): the first time a row's details opened, the whole desk went grey for about a second. Opened here with
    the first read of the visit held on its way (the bank account and the ID number, as the person): nothing of the page is
    over the desk - every point of the view is the desk's own, no fixed or absolute element covers it, the page's colours and
    opacity are what they were - and the cells being read show their own small spinner, gone when the values land. The grey
    is not drawn by the page: it is monday's, around the view."""
    base, _log = serve()
    store = {"5089697337": {k: {"text_mkzvem4": v} for k, v in LEADS.items()}, "5093448677": {"3103": BANK}}
    got = qa_visit(r"""
  const look = () => page.evaluate(() => {
    const vw = window.innerWidth, vh = window.innerHeight, root = document.getElementById("fin-root");
    const over = [...document.querySelectorAll("body *")].filter((el) => {
      const st = getComputedStyle(el), r = el.getBoundingClientRect();
      return (st.position === "fixed" || st.position === "absolute") && st.display !== "none" && st.visibility !== "hidden"
        && r.width * r.height > vw * vh * 0.25;
    }).map((el) => el.tagName + "." + el.className);
    const points = [[0.5, 0.5], [0.25, 0.25], [0.75, 0.25], [0.25, 0.75], [0.75, 0.75]].map(([x, y]) => {
      const el = document.elementFromPoint(vw * x, vh * y);
      return !!el && (root.contains(el) || el === document.body || el === document.documentElement || !!el.closest("#screen-finance"));
    });
    const screen = getComputedStyle(document.getElementById("screen-finance"));
    return { over: over, points: points, body: getComputedStyle(document.body).backgroundColor, opacity: screen.opacity,
      filter: screen.filter, spins: document.querySelectorAll(".fin-detail .fin-spin").length,
      id: /000000034/.test((document.querySelector(".fin-detail") || {}).innerText || "") };
  });
  await page.click('[data-bstate="refund_to_execute"]');
  await page.waitForSelector('tr[data-row="3103"]');
  out.steps.before = await look();
  await page.click('tr[data-row="3103"] td.k-to_refund');
  await page.waitForSelector(".fin-detail .fin-spin");
  await page.waitForTimeout(300);
  out.steps.reading = await look();
  out.steps.held = await page.evaluate(() => window.__held.length);
  await page.evaluate(() => window.__go());
  await page.waitForFunction(() => /000000034/.test((document.querySelector(".fin-detail") || {}).innerText || ""), null,
    { timeout: 15000 });
  out.steps.landed = await look();
""", base, fake_sdk(store) + HOLD_READS)
    assert got["errors"] == []
    before, reading, landed = (got["steps"][k] for k in ("before", "reading", "landed"))
    assert got["steps"]["held"] == 2                                   # the bank account and the ID number, on their way
    for step in (before, reading, landed):
        assert step["over"] == [] and all(step["points"]), step
        assert (step["body"], step["opacity"], step["filter"]) == (before["body"], "1", "none"), step
    assert reading["spins"] == 2 and not reading["id"]                 # a spinner in each cell being read
    assert landed["spins"] == 0 and landed["id"]


# --- the QA of 24.11: the page in monday as Chava sees it, each finding by its number in the QA report -------------------

@pytest.fixture
def serve():
    """Start the stub on a free port with a given view (and the reading a refresh answers); stop it after the test."""
    import uvicorn

    started = []

    def start(v=None, refreshed=None, opened=None, fresher=None):
        log: list = []
        port = _free_port()
        server = uvicorn.Server(uvicorn.Config(stub_app(log, v, refreshed, opened, fresher), host="127.0.0.1", port=port,
                                               log_level="warning"))
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


# `sizes`: the viewports to open the page at, one after the other ([width, height], monday's iframe); `script` runs for
# each, with `size` the one open. Every console error and uncaught exception of the page is kept in `errors`.
QA_PRELUDE = r"""
const { chromium } = require("playwright");
const [base, sdk, sizes] = process.argv.slice(1);
(async () => {
  const browser = await chromium.launch();
  const out = { errors: [], steps: {} };
  for (const size of JSON.parse(sizes)) {
  const page = await browser.newPage({ viewport: { width: size[0], height: size[1] }, deviceScaleFactor: 1 });
  page.on("pageerror", (e) => out.errors.push("pageerror: " + e));
  page.on("console", (m) => { if (m.type() === "error") { out.errors.push("console: " + m.text()); } });
  await page.route("**/desk/vendor/monday-sdk.js*", (r) => r.fulfill({ contentType: "application/javascript", body: sdk }));
  const text = (sel) => page.$eval(sel, (el) => el.innerText);
  const at = size.join("x");
  /*WHATS_NEW*/
  /*SETUP*/
  await page.goto(base + "/desk/");
  await page.waitForSelector("#screen-finance:not([hidden]) .fin-tabs", { timeout: 15000 });
"""
QA_CODA = r"""
  await page.close();
  }
  await browser.close();
  console.log(JSON.stringify(out));
})().catch((e) => { console.log(JSON.stringify({ crashed: String(e && e.stack || e) })); process.exit(1); });
"""


# QA 26.11 (H): "מה השתנה" shows on a first visit for a few weeks; a test of anything else opens the desk as one who closed it
WHATS_NEW_CLOSED = r"""
  await page.addInitScript(() => { try { window.localStorage.setItem("fin-whats-new-2026-11", "closed"); } catch (e) { /* none */ } });
"""


def qa_visit(script: str, base: str, sdk: str, sizes=((1434, 900),), setup: str = "", scrollbars: bool = False,
             whats_new: bool = False) -> dict:
    """`setup` runs before the page opens (a route that holds a request, a clock). `scrollbars`: the page's scrollbars take
    their room, as in Chava's browser - Playwright's headless Chromium hides them by default (`--hide-scrollbars`).
    `whats_new`: the page as a first visit shows it, "מה השתנה" open; otherwise as one who closed it."""
    prelude = QA_PRELUDE.replace("/*SETUP*/", setup).replace("/*WHATS_NEW*/", "" if whats_new else WHATS_NEW_CLOSED)
    if scrollbars:
        prelude = prelude.replace("chromium.launch()", 'chromium.launch({ ignoreDefaultArgs: ["--hide-scrollbars"] })')
    run = subprocess.run([NODE, "-e", prelude + script + QA_CODA, base, sdk, json.dumps([list(s) for s in sizes])],
                         capture_output=True, text=True, timeout=180, env=dict(os.environ, NODE_PATH=PW_ROOT))
    assert run.returncode == 0, (run.stdout[-3000:], run.stderr[-3000:])
    got = json.loads(run.stdout.strip().splitlines()[-1])
    assert "crashed" not in got, got
    return got


def with_counts(v: dict) -> dict:
    """The hand-made view's tile counts, from its rows (the students' tab)."""
    for p in v["b2c"]["periods"]:
        for key, st in p["states"].items():
            st["count"] = len([r for r in v["b2c"]["rows"] if r["state"] == key])
        p["count"] = len(v["b2c"]["rows"])
    return v


def two_receipts() -> dict:
    """The hand-made view with a second payment waiting for its receipt (3106), beside 3102."""
    v = view()
    v["b2c"]["rows"].append(sc_row("3106", "תלמיד/ה 6", "receipt_pending", invoice="30006", receipt="טרם", receipt_idx="0",
                                   money_in="5000", paid_typed="5000", net="5000", actions=["receipt_sent"]))
    return with_counts(v)


def test_ticking_a_rows_box_never_moves_the_rows_and_the_next_click_ticks_the_next_row(serve):
    """QA 1 (24.11): in "שולם, קבלה לטיפול", ticking the first row's box inserted a bar above the table and pushed the
    rows ~58px down; the click aimed at the second row's box then landed on the first row's and unticked it. Each click
    here is aimed where the box was BEFORE the first click, as hers was; every row stays where it was."""
    base, _log = serve(two_receipts())
    got = qa_visit(r"""
  await page.click('[data-bstate="receipt_pending"]');
  await page.waitForSelector('tr[data-row="3106"]');
  const where = () => page.evaluate(() => ({ y: window.scrollY, bar: (document.querySelector(".fin-batchbar") || {}).innerText || "",
    rows: [...document.querySelectorAll("tr[data-row]")].map((r) => [r.dataset.row, Math.round(r.getBoundingClientRect().top)]),
    picked: [...document.querySelectorAll("[data-pick]:checked")].map((b) => b.dataset.pick) }));
  const center = (sel) => page.$eval(sel, (el) => { const b = el.getBoundingClientRect(); return [b.x + b.width / 2, b.y + b.height / 2]; });
  const first = await center('[data-pick="sc:3102"]'), second = await center('[data-pick="sc:3106"]');
  out.steps.before = await where();
  await page.mouse.click(first[0], first[1]);
  out.steps.one = await where();
  await page.mouse.click(second[0], second[1]);
  out.steps.two = await where();
  await page.mouse.click(first[0], first[1]);
  await page.mouse.click(second[0], second[1]);
  out.steps.none = await where();
""", base, fake_sdk({}))
    assert got["errors"] == []
    steps = got["steps"]
    before = steps["before"]
    assert [r[0] for r in before["rows"]] == ["3102", "3106"] and before["picked"] == []
    # every click left every row where it was, and ticked or unticked exactly the box it was aimed at
    for name, picked in (("one", ["sc:3102"]), ("two", ["sc:3102", "sc:3106"]), ("none", [])):
        step = steps[name]
        assert step["rows"] == before["rows"] and step["y"] == before["y"], (name, step["rows"], before["rows"])
        assert step["picked"] == picked, (name, step)
    # and the bar, in its place all along, says what is picked - and before any pick, what the box is for
    assert before["bar"] == "לא נבחרו שורות. אפשר לבחור כמה בתיבה שבתחילת השורה, ולסמן בכולן יחד."
    assert [steps[n]["bar"].split(" ", 2)[:2] for n in ("one", "two", "none")] == [
        ["נבחרה", "שורה"], ["נבחרו", "2"], ["לא", "נבחרו"]]


def long_view(copies: int = 3) -> dict:
    """The hand-made view with rows as long as real ones run (generic and invented, never a real name): long names, a
    sales rep, a date that was not typed, every state of both tabs, the two actions on the row - so the lists' widest
    cells are as wide as at Chava's desk."""
    v = view()
    states = ["receipt_pending", "refund_to_execute", "exception", "done", "refunded", "refund_asked", "no_money"]
    rows = []
    for i, st in enumerate(states * copies):
        kw = dict(invoice="3000%02d" % i, rep="נציג/ה לדוגמה ארוך", channel="העברה בנקאית", date_from="created",
                  money_in="12500", paid_typed="12500", lead_ids=[])
        if st == "receipt_pending":
            kw.update(receipt="טרם", receipt_idx="0", actions=["receipt_sent"])
        if st == "refund_to_execute":
            kw.update(credit_approved="3950", planned="2026-11-27", asked_date="2026-11-12", refund_last_day="2026-11-26",
                      refund_method="העברה בנקאית", flags=["refund_to_execute"], actions=["refund_done"])
        rows.append(sc_row(str(5000 + i), "תלמיד/ה לדוגמה בשם ארוך-ארוך %d" % i, st, **kw))
    v["b2c"]["rows"] = rows
    b2 = []
    kinds = [("overdue", dict(due="2026-10-31", days_late=24, flags=["overdue"], actions=["b2b_paid"])),
             ("to_bill", dict(billing="מאושר לחיוב", billing_idx="1", due="", flags=["terms_missing"])),
             ("partial", dict(payment="שולם חלקית", payment_idx="0", paid_so_far="9000", balance="11650")),
             ("open", dict(due="2026-12-31")), ("exception", dict(flags=["amount_missing"])), ("planned", dict(demand="2027-01-01")),
             ("paid", {}), ("pending", dict(billing="")), ("cancelled", {})]
    for i, (st, kw) in enumerate(kinds * 2):
        acc = ("7701", "7702", "7703")[i % 3]
        terms = {"7701": "שוטף+30", "7702": "שוטף+60", "7703": ""}[acc]
        ctx = {"account": "חברה לדוגמה בעמ לפיתוח %d" % i, "deal": "עסקה לדוגמה מחזור סייבר %d" % i}
        b2.append(b2_row(str(9000 + i), "פעימה %d · 2026-10 · A651" % i, st, acc, ctx["account"], terms, amount="125000",
                         invoice="50%03d" % i, ctx=ctx, deal=ctx["deal"], **kw))
    v["b2b"]["rows"] = b2
    return with_counts(v)


def test_the_page_takes_the_desks_answers_gzipped_and_draws_them(serve):
    """QA 26.11 (B): the page's own fetches - the desk, its totals - arrive gzipped (served as this service serves them, the
    stub using main's `gzip_answers`) and are drawn as before; the Excel file's link is untouched."""
    base, _log = serve(long_view())
    got = qa_visit(r"""
  await page.waitForSelector("tr[data-row]");
  await page.click('[data-bstate=""]');
  await page.waitForSelector('tr[data-row="5020"]');
  await page.waitForTimeout(300);
  out.steps.rows = await page.$$eval("tr[data-row]", (r) => r.length);
  out.steps.answers = seen;
""", base, fake_sdk({}), setup=r"""
  const seen = [];
  page.on("response", (r) => { const u = new URL(r.url());
    if (u.pathname.startsWith("/api/desk")) { seen.push([u.pathname, r.headers()["content-encoding"] || ""]); } });
""")
    assert got["errors"] == []
    steps = got["steps"]
    assert steps["rows"] == 21 and ["/api/desk", "gzip"] in steps["answers"], steps


def test_what_changed_shows_on_a_first_visit_and_once_closed_not_again_in_this_browser(serve):
    """QA 26.11 (H): "מה השתנה" on a first visit, its lines under the top bar (seven since the UX review of 26.11); "סגירה"
    closes it, and the page loaded again in the same browser opens without it (localStorage)."""
    base, _log = serve()
    got = qa_visit(r"""
  const seen = () => page.evaluate(() => { const b = document.querySelector(".fin-new");
    return b ? { lines: b.querySelectorAll("li").length, above: b.getBoundingClientRect().bottom <=
      document.querySelector(".fin-tabbar").getBoundingClientRect().top } : null; });
  out.steps.first = await seen();
  await page.click('[data-whats-new="close"]');
  await page.waitForSelector(".fin-new", { state: "detached" });
  out.steps.closed = await seen();
  await page.reload();
  await page.waitForSelector("#screen-finance:not([hidden]) .fin-tabs", { timeout: 15000 });
  out.steps.again = await seen();
""", base, fake_sdk({}), whats_new=True)
    assert got["errors"] == []
    steps = got["steps"]
    assert steps["first"] == {"lines": 7, "above": True} and steps["closed"] is None and steps["again"] is None


def test_the_desk_holds_its_place_sideways_when_the_page_gets_shorter_and_its_scrollbar_goes(serve):
    """QA 26.11 (6): when the page got shorter - a search with few results, "ללא תקבול", a cohort closed - its scrollbar went
    and the whole desk moved sideways by about 7 pixels (half the bar: the desk is centered), so a click aimed at the
    search's X missed it. The page keeps the bar's room (desk.css: `scrollbar-gutter: stable` on the root, which scrolls
    the view's frame), and the desk stays where it was. Measured with the scrollbars Chava has, which take room."""
    base, _log = serve(long_view())
    got = qa_visit(r"""
  const where = () => page.evaluate(() => ({ q: document.getElementById("fin-q").getBoundingClientRect().left,
    wrap: document.querySelector(".fin-wrap").getBoundingClientRect().left,
    right: document.querySelector(".fin-wrap").getBoundingClientRect().right,
    bar: window.innerWidth - document.documentElement.clientWidth,
    scrolls: document.documentElement.scrollHeight > window.innerHeight }));
  await page.click('[data-bstate=""]');
  await page.waitForSelector('tr[data-row="5020"]');
  out.steps[at] = { long: await where() };
  await page.click('[data-bstate="no_money"]');
  await page.waitForFunction(() => document.querySelectorAll("tr[data-row]").length === 3, null, { timeout: 15000 });
  out.steps[at].tile = await where();
  await page.click('[data-bstate=""]');
  await page.waitForSelector('tr[data-row="5020"]');
  await page.fill("#fin-q", "ארוך-ארוך 20");
  await page.waitForFunction(() => document.querySelectorAll("tr[data-row]").length === 1, null, { timeout: 15000 });
  out.steps[at].search = await where();
""", base, fake_sdk({}), sizes=((1280, 1000), (1400, 1000)), scrollbars=True)
    assert got["errors"] == []
    for size, steps in got["steps"].items():
        long = steps["long"]
        # the long list scrolls, and the bar takes room, as in Chava's browser
        assert long["scrolls"] and long["bar"] > 0, (size, long)
        for name in ("tile", "search"):
            short = steps[name]
            assert not short["scrolls"], (size, name, short)           # the page got shorter than the view: no bar
            assert (short["q"], short["wrap"], short["right"]) == (long["q"], long["wrap"], long["right"]), (size, name, long, short)


def test_every_column_of_a_general_list_fits_its_frame_at_chavas_width(serve):
    """QA 4 (24.11): at full width (a 1707-pixel window with monday's sidebar open) "לטיפול" and "הכל" ran about 40 pixels
    past their frame: "פתיחה במאנדיי" showed only "פר" and was reached only by scrolling sideways. At monday's view widths of
    1280 and 1400, with rows as long as real ones, every column of those lists - on both tabs - is inside its frame. And
    the date that was not typed reads "(לפי תאריך יצירה)" in its cell (QA 10: finance.css draws the words around the term)."""
    base, _log = serve(long_view())
    got = qa_visit(r"""
  const fit = (tile) => page.evaluate((tile) => {
    const tw = document.querySelector(".fin-tw"), t = tw.querySelector("table"), open = t.querySelector("thead th.k-open");
    const f = tw.getBoundingClientRect(), o = open.getBoundingClientRect();
    return { tile: tile, frame: Math.round(f.width), table: Math.round(t.getBoundingClientRect().width),
      open: [Math.round(o.left - f.left), Math.round(f.right - o.right)], sideways: tw.scrollWidth - tw.clientWidth };
  }, tile);
  out.steps[at] = [];
  for (const [tab, tile] of [["b2c", '[data-bstate="tasks"]'], ["b2c", '[data-bstate=""]'], ["b2b", '[data-b2state="tasks"]'],
                             ["b2b", '[data-b2state=""]']]) {
    await page.click('[data-fintab="' + tab + '"]');
    await page.click(tile);
    await page.waitForSelector(".fin-tw thead th.k-open");
    out.steps[at].push(await fit(tab + " " + tile));
  }
  await page.click('[data-fintab="b2c"]');
  await page.click('[data-bstate=""]');
  out.steps[at + " created"] = await page.$eval("tbody td.k-date > .fin-subtle", (el) => [getComputedStyle(el, "::before").content,
    el.textContent, getComputedStyle(el, "::after").content, getComputedStyle(el).display]);
""", base, fake_sdk({}), sizes=((1280, 748), (1400, 748)))
    assert got["errors"] == []
    for size in ("1280x748", "1400x748"):
        for x in got["steps"][size]:
            # the table no wider than its frame, nothing to scroll sideways, "פתיחה במאנדיי" wholly inside it
            assert x["table"] <= x["frame"] and x["sideways"] <= 0 and min(x["open"]) >= 0, (size, x)
        assert got["steps"][size + " created"] == ["none", "(לפי תאריך יצירה)", "none", "block"]


def test_a_row_says_it_opens_its_details_by_its_pointer_its_hover_and_a_chevron_that_turns(serve):
    """QA 6 (24.11): there is no "פרטים" button and nothing said a click on a row opens its details. A row shows the
    pointer, lights its chevron when pointed at, and turns it while its details are open; the totals' label stays out of
    the chevron's narrow column."""
    base, _log = serve(two_receipts())
    got = qa_visit(r"""
  await page.click('[data-bstate="receipt_pending"]');
  await page.waitForSelector('tr[data-row="3102"] .fin-more');
  const look = () => page.$eval('tr[data-row="3102"]', (tr) => {
    const m = tr.querySelector(".fin-more"), b = m.getBoundingClientRect();
    return { cursor: getComputedStyle(tr).cursor, size: [Math.round(b.width), Math.round(b.height)], color: getComputedStyle(m).color,
      turn: getComputedStyle(m.querySelector("svg")).transform };
  });
  await page.mouse.move(5, 5);
  out.steps.still = await look();
  await page.hover('tr[data-row="3102"] td.k-received');
  out.steps.pointed = await look();
  await page.click('tr[data-row="3102"] td.k-received');
  await page.waitForSelector('tr[data-row="3102"][aria-expanded="true"]');
  await page.waitForTimeout(400);
  out.steps.open = await look();
  await page.waitForSelector("tfoot .fin-total");
  out.steps.total = await page.$$eval("tfoot td", (t) => t.map((x) => [x.className, x.innerText.trim()]).filter((x) => x[1]));
""", base, fake_sdk({}))
    assert got["errors"] == []
    still, pointed, opened = got["steps"]["still"], got["steps"]["pointed"], got["steps"]["open"]
    assert still["cursor"] == "pointer" and still["size"][0] >= 12 and still["turn"] == "none"
    assert pointed["color"] != still["color"]                                   # the chevron lights while the row is pointed at
    assert opened["turn"].startswith("matrix(-1, ") and opened["turn"].endswith("0, 0)")    # turned half a circle
    assert got["steps"]["total"][0][0] == "k-date" and got["steps"]["total"][0][1].startswith('סה"כ')


def test_one_scroll_a_table_that_fits_scrolls_with_the_page_and_one_wider_than_its_frame_takes_the_views_height(serve):
    """QA 9 (24.11): on a 748-pixel-high screen the table scrolled inside a short frame (about six rows) inside a page that
    scrolls too. Where the table fits its frame, the page has the one scroll and the table's header stays at the top of the
    view as the page scrolls; at a narrow width (half of 1366), where the table is wider than its frame, the frame that
    scrolls - sideways too - is as tall as the view."""
    base, _log = serve(long_view())
    got = qa_visit(r"""
  await page.click('[data-bstate=""]');
  await page.waitForSelector(".fin-tw tbody tr");
  out.steps[at] = await page.evaluate(() => {
    const tw = document.querySelector(".fin-tw"), t = tw.querySelector("table");
    return { inner: tw.scrollHeight > tw.clientHeight + 1, overflowY: getComputedStyle(tw).overflowY,
      sideways: tw.scrollWidth > tw.clientWidth + 1, frame: tw.clientHeight, table: Math.round(t.getBoundingClientRect().height),
      view: window.innerHeight, page: document.documentElement.scrollHeight > window.innerHeight };
  });
  await page.waitForSelector("tfoot .fin-total");
  out.steps[at + " total"] = await page.$eval("tfoot td.k-date", (td) => {
    const b = td.querySelector("b"), n = td.querySelector(".fin-subtle");
    /* its lines, by their tops: a number and a word are two bidi runs, two boxes on one line */
    const lines = new Set([...n.getClientRects()].map((r) => Math.round(r.top))).size;
    return { count: n.innerText, lines: lines, below: n.getBoundingClientRect().top > b.getBoundingClientRect().top + 2 };
  });
  const top = await page.$eval(".fin-tw", (tw) => tw.getBoundingClientRect().top + window.scrollY);
  await page.evaluate((y) => window.scrollTo(0, y), top + 200);
  await page.waitForTimeout(100);
  out.steps[at + " scrolled"] = await page.evaluate(() => ({
    head: Math.round(document.querySelector(".fin-tw thead").getBoundingClientRect().top),
    frame: Math.round(document.querySelector(".fin-tw").getBoundingClientRect().top) }));
  /* the view narrowed and widened again (monday's sidebar, a resize): the frame follows, with no new drawing */
  const mode = () => page.$eval(".fin-tw", (tw) => getComputedStyle(tw).overflowY);
  await page.setViewportSize({ width: 683, height: size[1] });
  await page.waitForTimeout(150);
  const narrowed = await mode();
  await page.setViewportSize({ width: size[0], height: size[1] });
  await page.waitForTimeout(150);
  out.steps[at + " resized"] = [narrowed, await mode()];
""", base, fake_sdk({}), sizes=((1280, 748), (683, 748)))
    assert got["errors"] == []
    wide, narrow = got["steps"]["1280x748"], got["steps"]["683x748"]
    # wide: the table taller than the view scrolls with the page - no frame that scrolls - and its header stays on top
    assert wide["table"] > wide["view"] and wide["page"], wide
    assert not wide["inner"] and wide["overflowY"] == "visible" and not wide["sideways"], wide
    scrolled = got["steps"]["1280x748 scrolled"]
    assert scrolled["frame"] < 0 and scrolled["head"] == 0, scrolled
    # the view narrowed, then widened back: the frame comes and goes with it
    assert got["steps"]["1280x748 resized"] == ["auto", "visible"], got["steps"]["1280x748 resized"]
    # narrow: the table wider than its frame scrolls in a frame as tall as the view (it was 78% of it)
    assert narrow["sideways"] and narrow["overflowY"] == "auto" and narrow["frame"] >= 0.9 * narrow["view"], narrow
    # at its narrowest the totals' label wraps (QA 4) between "סה"כ" and its count, never inside "21 תקבולים"
    assert got["steps"]["683x748 total"] == {"count": "21 תקבולים", "lines": 1, "below": True}, got["steps"]["683x748 total"]


def test_the_page_logs_no_console_error_and_throws_nothing_while_it_loads_opens_details_confirms_and_refreshes(serve):
    """QA 12 (24.11): the QA could not read the console of the view's iframe. The page, in a browser, logs no console error
    and raises no uncaught exception while it loads, opens a row's details (the ID number and the bank account read as the
    person), shows an action's confirmation in the details and the row's own one, and refreshes."""
    base, _log = serve()
    store = {"5093448677": {"3103": dict(BANK, __name="תלמיד/ה 3", color_mm1kxpvt={"text": "שולם", "index": 1, "label": "שולם"})},
             "5089697337": {k: {"text_mkzvem4": v} for k, v in LEADS.items()}}
    labels = {"5093448677": {"color_mm1kxpvt": {"1": "שולם", "4": "תשלום הוחזר", "5": "הוחזר חלקי"}}}
    got = qa_visit(r"""
  out.steps.loaded = await page.$$eval("tr[data-row]", (t) => t.length);
  await page.click('[data-bstate="refund_to_execute"]');
  await page.click('tr[data-row="3103"] td.k-to_refund');
  await page.waitForFunction(() => /מספר חשבון/.test((document.querySelector(".fin-dx-box") || {}).innerText || ""), null,
    { timeout: 15000 });
  await page.click('[data-act="sc:3103:refund_done"]');
  await page.waitForSelector(".fin-acts .fin-act-plan", { timeout: 15000 });
  out.steps.details = await page.$eval(".fin-acts .fin-act-plan", (el) => el.innerText);
  await page.click('[data-act-close="1"]');
  await page.click('[data-row-act="sc:3103:refund_done"]');
  await page.waitForSelector(".fin-batch-plan", { timeout: 15000 });
  out.steps.row = await page.$eval(".fin-batch-plan", (el) => el.innerText);
  await page.click('[data-batch-close="1"]');
  await page.click("[data-refresh]");
  await page.waitForFunction(() => !document.querySelector(".fin-refreshing") && !document.querySelector("[data-refresh][disabled]"),
    null, { timeout: 15000 });
  out.steps.refreshed = await page.$eval(".fin-asof", (el) => el.innerText);
  out.steps.mutations = await page.evaluate(() => window.__monday.filter((m) => m.query.trim().indexOf("mutation") === 0).length);
""", base, fake_sdk(store, labels))
    steps = got["steps"]
    assert got["errors"] == [], got["errors"]
    # every step was reached: the list, the details' plan, the row's own confirmation, a newer reading - and nothing written
    assert steps["loaded"] > 0 and steps["mutations"] == 0
    assert 'סטטוס תשלום: עכשיו "שולם", ייכתב "תשלום הוחזר"' in steps["details"]
    assert 'יעודכן במאנדיי בשורה "תלמיד/ה 3", בשמך:' in steps["row"] and steps["refreshed"].startswith("נכון ל-")


# --- the QA of 24.11, its second pass ------------------------------------------------------------------------------------

def newer_view() -> dict:
    """The hand-made view with one more payment waiting for its receipt: a newer reading than the one the page opened on."""
    v = view()
    v["b2c"]["rows"].append(sc_row("3199", "שורה-חדשה", "receipt_pending", invoice="30099", receipt="טרם", receipt_idx="0"))
    return with_counts(v)


# the page's own second request (`?fresh=1`) and the refresh pressed, each held until the script lets it through
HOLD = r"""
  const holding = {};
  const hold = (name, match) => {
    holding[name] = {};
    holding[name].came = new Promise((ok) => { holding[name].ok = ok; });
    return page.route(match, (route) => { holding[name].route = route; holding[name].ok(); });
  };
  const release = async (name, part) => {
    const answered = page.waitForResponse((r) => r.url().indexOf(part) !== -1);
    await holding[name].route.continue();
    await answered;
  };
  await hold("fresh", (u) => u.pathname === "/api/desk" && u.searchParams.get("fresh") === "1");
  await hold("refresh", (u) => u.pathname === "/api/desk/refresh");
"""
SEARCH = r"""
  const search = () => page.evaluate(() => { const q = document.getElementById("fin-q");
    return { value: q.value, focused: document.activeElement === q, caret: q.selectionStart,
      line: /חיפוש: /.test(document.querySelector(".fin-listhead").innerText),
      refreshing: !!document.querySelector(".fin-refreshing") }; });
"""


def test_a_press_on_a_tile_is_taken_while_the_newer_reading_lands_under_it(serve):
    """QA 24.11 (1): "right after the page loaded, a click on a tile was not taken ... after 'מתעדכן' went away, everything
    worked". The page opens on a held reading and asks for the newer one, which lands while the tile is pressed - between
    the press and its release. A drawing there builds the tile anew and the browser takes no click; the drawing now waits
    for the release, and the tile is taken."""
    base, _log = serve(opened={"refreshing": True, "age_seconds": 40}, fresher=newer_view())
    got = qa_visit(r"""
  await holding.fresh.came;
  const tile = () => page.$eval('[data-bstate="done"]', (t) => t.getAttribute("aria-pressed"));
  out.steps.before = { pressed: await tile(), refreshing: !!(await page.$(".fin-refreshing")) };
  const spot = await page.$eval('[data-bstate="done"]', (el) => { const b = el.getBoundingClientRect(); return [b.x + b.width / 2, b.y + b.height / 2]; });
  await page.mouse.move(spot[0], spot[1]);
  await page.mouse.down();
  await release("fresh", "fresh=1");
  await page.waitForTimeout(300);
  out.steps.held = { refreshing: !!(await page.$(".fin-refreshing")) };
  await page.mouse.up();
  await page.waitForTimeout(300);
  out.steps.after = { pressed: await tile(), refreshing: !!(await page.$(".fin-refreshing")),
    head: await page.$eval(".fin-listhead h2", (h) => h.innerText) };
""", base, fake_sdk({}), setup=HOLD)
    assert got["errors"] == []
    steps = got["steps"]
    assert steps["before"] == {"pressed": "false", "refreshing": True}
    assert steps["held"] == {"refreshing": True}                  # the newer reading waited while the tile was pressed
    assert steps["after"]["pressed"] == "true" and steps["after"]["refreshing"] is False, steps["after"]


def test_what_is_typed_into_the_search_stays_when_an_answer_lands_before_it_settles(serve):
    """QA 24.11 (1): "text I typed into the search box was erased". (a) "רענון" pressed and the search typed while it is on
    its way: its answer keeps the text, the focus and the caret, and the next keys go on in the box. (b) The search typed,
    the focus gone to monday's frame, and the page's own newer reading lands before the search settles (the page's clock
    held, so the search cannot settle first): the box keeps the text, and the search settles on it."""
    base, _log = serve()
    got = qa_visit(SEARCH + r"""
  await page.click("[data-refresh]");
  await holding.refresh.came;
  await page.click("#fin-q");
  await page.keyboard.type("תלמיד");
  await release("refresh", "/api/desk/refresh");
  await page.waitForFunction(() => !document.querySelector("[data-refresh][disabled]"), null, { timeout: 15000 });
  out.steps.refreshed = await search();
  await page.keyboard.type("/ה 2");
  await page.waitForTimeout(400);
  out.steps.typed = await search();
""", base, fake_sdk({}), setup=HOLD)
    assert got["errors"] == []
    # the answer drew the search on what was typed (it had not settled yet), and kept the box's focus and caret
    assert got["steps"]["refreshed"] == {"value": "תלמיד", "focused": True, "caret": 5, "line": True, "refreshing": False}
    assert got["steps"]["typed"] == {"value": "תלמיד/ה 2", "focused": True, "caret": 9, "line": True, "refreshing": False}

    base, _log = serve(opened={"refreshing": True, "age_seconds": 40}, fresher=newer_view())
    got = qa_visit(SEARCH + r"""
  await holding.fresh.came;
  await page.click("#fin-q");
  await page.clock.pauseAt(Date.now() + 2000);
  await page.keyboard.type("תלמיד/ה 2");
  await page.evaluate(() => document.activeElement.blur());
  await release("fresh", "fresh=1");
  await page.waitForTimeout(300);
  out.steps.landed = await search();
  await page.clock.runFor(300);
  out.steps.settled = await search();
""", base, fake_sdk({}), setup=HOLD + "  await page.clock.install();\n")
    assert got["errors"] == []
    landed, settled = got["steps"]["landed"], got["steps"]["settled"]
    assert landed["value"] == "תלמיד/ה 2" and landed["refreshing"] is False and not landed["focused"], landed
    assert settled["value"] == "תלמיד/ה 2" and settled["line"], settled


def many_receipts(n: int = 16) -> dict:
    """The hand-made view with n more payments waiting for their receipt: the list "שולם, קבלה לטיפול" longer than the
    view, as Chava's 21 were."""
    v = view()
    for i in range(n):
        v["b2c"]["rows"].append(sc_row(str(5000 + i), "תלמיד/ה לדוגמה %d" % i, "receipt_pending", invoice="300%02d" % i,
                                       receipt="טרם", receipt_idx="0", money_in="5000", paid_typed="5000", net="5000",
                                       actions=["receipt_sent"]))
    return with_counts(v)


def test_a_details_plan_floats_beside_its_button_and_no_row_moves_when_it_opens_or_closes(serve):
    """QA 24.11, left for D4 (its review, 4b): the plan of an action in a row's details ("סימון "תשלום הוחזר"" there, and
    every action that has a box to type in) opened inside the details, under its button, and pushed every row below it
    down. It floats now beside its button, as a batch does beside what was pressed: no row moves - nor the details' own
    facts, nor the page - while it reads, while it is shown and after it closes; it is in the view, never over the button,
    so the pointer is not on "שמירה"; the focus is on it. At monday's view widths, at the list's top and scrolled down."""
    from app.desk import view as fd

    v = long_view()
    v["b2c"]["actions"]["list"].append(dict(fd.DESK_ACTIONS["refund_partial"], key="refund_partial"))
    for r in v["b2c"]["rows"]:
        if r["state"] == "refund_to_execute":
            r["actions"] = ["refund_done", "refund_partial"]    # the partial one takes its amount from a box beside its button
    base, _log = serve(v)
    store = {"5093448677": {rid: {"__name": "תלמיד/ה לדוגמה", "color_mm1kxpvt": {"text": "שולם", "index": 1, "label": "שולם"}}
                            for rid in ("5001", "5008", "5015")}}
    labels = {"5093448677": {"color_mm1kxpvt": {"1": "שולם", "4": "תשלום הוחזר", "5": "הוחזר חלקי"}}}
    got = qa_visit(r"""
  await page.click('[data-bstate="refund_to_execute"]');
  await page.waitForSelector('tr[data-row="5015"]');
  const rows = () => page.evaluate(() => [...document.querySelectorAll("tr[data-row], .fin-detail dt")].map((r, i) =>
    [r.dataset.row || "dt" + i, Math.round(r.getBoundingClientRect().top)]));
  const center = (sel) => page.$eval(sel, (el) => { const b = el.getBoundingClientRect(); return [b.x + b.width / 2, b.y + b.height / 2]; });
  const box = (pressed, at) => page.evaluate(([pressed, at]) => {
    const pop = document.querySelector(".fin-act-plan"), p = pop.getBoundingClientRect();
    const r = document.querySelector(pressed).getBoundingClientRect(), go = pop.querySelector("[data-act-go]").getBoundingClientRect();
    const c = document.querySelector(pressed).closest(".fin-act").getBoundingClientRect();
    return { y: Math.round(window.scrollY), focus: document.activeElement === pop, inView: p.top >= 0 && p.bottom <= window.innerHeight,
      clear: p.bottom <= r.top || p.top >= r.bottom || p.right <= r.left || p.left >= r.right,
      /* and of the whole control, the box she typed into with it */
      ctlClear: p.bottom <= c.top || p.top >= c.bottom || p.right <= c.left || p.left >= c.right,
      /* beside the action's control: just above it or just below it */
      beside: Math.min(Math.abs(c.top - p.bottom), Math.abs(p.top - c.bottom)) <= 8,
      onGo: at[0] >= go.left && at[0] <= go.right && at[1] >= go.top && at[1] <= go.bottom, inDetails: !!pop.closest(".fin-acts"),
      text: pop.innerText };
  }, [pressed, at]);
  out.steps[at] = {};
  for (const [name, row, key] of [["top", "5001", "refund_done"], ["scrolled", "5015", "refund_done"],
                                  ["typed", "5008", "refund_partial"]]) {
    await page.click('tr[data-row="' + row + '"] td.k-to_refund');
    const act = '[data-act="sc:' + row + ':' + key + '"]';
    await page.waitForSelector(act);
    if (key === "refund_partial") { await page.fill('[data-act-input="sc:' + row + ':numeric_mm62za1f"]', "100"); }
    await page.$eval(act, (el) => el.scrollIntoView({ block: "center" }));
    await page.waitForTimeout(100);
    const before = await rows(), y = Math.round(await page.evaluate(() => window.scrollY)), spot = await center(act);
    await page.mouse.click(spot[0], spot[1]);
    await page.waitForSelector(".fin-act-plan [data-act-go]");
    const open = await rows(), shown = await box(act, spot);
    /* the page scrolled a little with the plan open: it follows its button */
    const held = await page.evaluate(() => window.scrollY);
    await page.evaluate(() => window.scrollBy(0, 40));
    await page.waitForTimeout(100);
    shown.followed = (await box(act, [-1, -1])).beside && (await page.evaluate(() => window.scrollY)) !== held;
    await page.evaluate((y) => window.scrollTo(0, y), held);
    await page.waitForTimeout(100);
    await page.click('[data-act-close="1"]');
    await page.waitForSelector(".fin-act-plan", { state: "detached" });
    out.steps[at][name] = { before: before, y: y, open: open, box: shown, closed: await rows(),
      after: Math.round(await page.evaluate(() => window.scrollY)) };
    await page.click('tr[data-row="' + row + '"] td.k-to_refund');            /* its details closed again */
    await page.waitForSelector('tr[data-row="' + row + '"][aria-expanded="false"]');
  }
""", base, fake_sdk(store, labels), sizes=((1280, 748), (1400, 748)))
    assert got["errors"] == [], got["errors"]
    for size in ("1280x748", "1400x748"):
        for name in ("top", "scrolled", "typed"):
            step, box = got["steps"][size][name], got["steps"][size][name]["box"]
            # every row and every fact of the details where it was, while the plan is open and after it closed; the page too
            assert step["open"] == step["before"] and step["closed"] == step["before"], (size, name, step["before"], step["open"])
            assert box["y"] == step["y"] == step["after"], (size, name, box["y"], step["y"], step["after"])
            # in the view, clear of the button, the pointer not on "שמירה", the focus on it - and still the details' own
            assert box["inView"] and box["clear"] and not box["onGo"] and box["focus"] and box["inDetails"], (size, name, box)
            assert box["beside"] and box["followed"] and box["ctlClear"], (size, name, box)
        assert 'סטטוס תשלום: עכשיו "שולם", ייכתב "תשלום הוחזר"' in got["steps"][size]["top"]["box"]["text"]
        typed = got["steps"][size]["typed"]["box"]["text"]
        assert "סכום החזר: עכשיו ריק, ייכתב 100.00 ₪" in typed and 'ייכתב "הוחזר חלקי"' in typed, typed


def test_a_box_near_the_top_stays_below_the_tables_sticky_header_and_whole_in_the_view(serve):
    """QA 26.11 (A): with the row near the top of the view, the confirmation's first line ("יעודכן במאנדיי בשורה...") was cut
    under the table's header, which sticks at the top as the page scrolls - seen in "הוחזר חלקי" and in "חויב": a row's
    action floats inside its details, which paint under that header, and a box placed above the row reached up to the view's
    edge. The whole box stays below a header stuck at the top, and in the view: a details' action, and a row's own button.
    The control is put just low enough for the box to fit above it up to the view's edge - so above it, under the header,
    was where it went."""
    from app.desk import view as fd

    v = long_view(copies=4)
    v["b2c"]["actions"]["list"].append(dict(fd.DESK_ACTIONS["refund_partial"], key="refund_partial"))
    for r in v["b2c"]["rows"]:
        if r["state"] == "refund_to_execute":
            r["actions"] = ["refund_done", "refund_partial"]
    base, _log = serve(v)
    store = {"5093448677": {rid: {"__name": "תלמיד/ה לדוגמה", "color_mm1kxpvt": {"text": "שולם", "index": 1, "label": "שולם"}}
                            for rid in ("5001", "5008", "5015", "5022")}}
    labels = {"5093448677": {"color_mm1kxpvt": {"1": "שולם", "4": "תשלום הוחזר", "5": "הוחזר חלקי"}}}
    got = qa_visit(r"""
  await page.click('[data-bstate=""]');
  await page.waitForSelector('tr[data-row="5022"]');
  const look = (pop) => page.evaluate((pop) => {
    const box = document.querySelector(pop), p = box.getBoundingClientRect(), first = box.querySelector("p").getBoundingClientRect();
    const heads = [...document.querySelectorAll("table.fin-t thead")].map((t) => t.getBoundingClientRect())
      .filter((r) => r.top <= 8 && r.bottom > 0);
    const hit = document.elementFromPoint(first.left + first.width / 2, first.top + first.height / 2);
    return { top: p.top, bottom: p.bottom, head: heads.length ? heads[0].bottom : null, inView: p.top >= 0 && p.bottom <= window.innerHeight,
      firstSeen: !!hit && box.contains(hit), text: box.innerText.split("\n")[0] };
  }, pop);
  /* put `sel` where a box of height h fits above it only up to the view's top, then press it */
  const pressAt = async (sel, h, ready) => {
    await page.$eval(sel, (el) => el.scrollIntoView({ block: "center" }));
    const top = await page.$eval(sel, (el) => el.closest(".fin-act, td").getBoundingClientRect().top);
    await page.evaluate((dy) => window.scrollBy(0, dy), top - (h + 20));
    await page.waitForTimeout(150);
    await page.click(sel);
    await page.waitForSelector(ready);
    await page.waitForTimeout(150);
  };
  out.steps[at] = {};
  /* a details' action ("סימון "הוחזר חלקי"" takes its amount from a box beside its button) */
  await page.click('tr[data-row="5022"] td.k-received');
  const act = '[data-act="sc:5022:refund_done"]';
  await page.waitForSelector(act);
  await pressAt(act, 0, ".fin-act-plan [data-act-go]");
  const h1 = await page.$eval(".fin-act-plan", (b) => b.getBoundingClientRect().height);
  await page.click('[data-act-close="1"]');
  await page.waitForSelector(".fin-act-plan", { state: "detached" });
  await pressAt(act, h1, ".fin-act-plan [data-act-go]");
  out.steps[at].details = await look(".fin-act-plan");
  await page.click('[data-act-close="1"]');
  await page.waitForSelector(".fin-act-plan", { state: "detached" });
  /* a row's own button ("סימון "תשלום הוחזר"" on the row) */
  const own = '[data-row-act="sc:5015:refund_done"]';
  await pressAt(own, 0, ".fin-batch-plan [data-batch-go]");
  const h2 = await page.$eval(".fin-batch-plan", (b) => b.getBoundingClientRect().height);
  await page.click('.fin-batch-plan [data-batch-close="1"]');
  await page.waitForSelector(".fin-batch-plan", { state: "detached" });
  await pressAt(own, h2, ".fin-batch-plan [data-batch-go]");
  out.steps[at].row = await look(".fin-batch-plan");
""", base, fake_sdk(store, labels), sizes=((1280, 748), (1400, 748)))
    assert got["errors"] == [], got["errors"]
    for size, steps in got["steps"].items():
        for name in ("details", "row"):
            box = steps[name]
            assert box["head"] is not None, (size, name, box)              # the table's header is stuck at the view's top
            assert box["top"] >= box["head"] and box["inView"] and box["firstSeen"], (size, name, box)
            assert box["text"].startswith("יעודכן במאנדיי בשורה"), (size, name, box)


def test_a_rows_confirmation_floats_beside_it_and_no_row_moves_when_it_opens_or_closes(serve):
    """QA2 3 (24.11): "the confirmation screen pushes the table down, and the mouse stays over the button of another row". It
    took the bar's place above the table, taller than it - every row 69 pixels lower - and with the list scrolled down the
    focus on it scrolled the page up to it, about 550 pixels. It floats now beside what was pressed: no row moves when it
    opens or closes, at the list's top or scrolled down, for a row's own button or the bar's; the page does not scroll;
    the box is in the view and never over what was pressed, so the pointer is not on "שמירה"; the focus is on the box."""
    base, _log = serve(many_receipts())
    store = {"5093448677": {str(5000 + i): {"__name": "תלמיד/ה לדוגמה %d" % i,
                                            "color_mm4cq1hw": {"text": "טרם", "index": 0, "label": "טרם"}} for i in range(16)}}
    labels = {"5093448677": {"color_mm4cq1hw": {"0": "טרם", "1": "יצאה", "2": "לא רלוונטי"}}}
    got = qa_visit(r"""
  await page.click('[data-bstate="receipt_pending"]');
  await page.waitForSelector('[data-row-act="sc:5000:receipt_sent"]');
  const rows = () => page.evaluate(() => [...document.querySelectorAll("tr[data-row]")].map((r) => [r.dataset.row,
    Math.round(r.getBoundingClientRect().top)]));
  const center = (sel) => page.$eval(sel, (el) => { const b = el.getBoundingClientRect(); return [b.x + b.width / 2, b.y + b.height / 2]; });
  /* the box against what was pressed and the pointer that pressed, the page's scroll, and what has the focus */
  const box = (pressed, at) => page.evaluate(([pressed, at]) => {
    const pop = document.querySelector(".fin-batch-plan"), p = pop.getBoundingClientRect();
    const what = document.querySelector(pressed), r = what && what.getBoundingClientRect();
    const go = pop.querySelector("[data-batch-go]").getBoundingClientRect();
    return { y: Math.round(window.scrollY), focus: document.activeElement === pop, inView: p.top >= 0 && p.bottom <= window.innerHeight,
      clear: !!r && (p.bottom <= r.top || p.top >= r.bottom),
      onGo: at[0] >= go.left && at[0] <= go.right && at[1] >= go.top && at[1] <= go.bottom, text: pop.innerText };
  }, [pressed, at]);
  const press = async (sel) => {
    const spot = await center(sel);
    await page.mouse.click(spot[0], spot[1]);
    await page.waitForSelector(".fin-batch-plan [data-batch-go]");
    return spot;
  };
  const close = async () => {
    await page.click('.fin-batch-plan [data-batch-close="1"]');
    await page.waitForSelector(".fin-batch-plan", { state: "detached" });
  };
  for (const [name, y, row] of [["top", 0, "5001"], ["scrolled", 700, "5010"]]) {
    await page.evaluate((y) => window.scrollTo(0, y), y);
    await page.waitForTimeout(100);
    const before = await rows(), spot = await press('[data-row-act="sc:' + row + ':receipt_sent"]');
    out.steps[name] = { before: before, open: await rows(), box: await box('tr[data-row="' + row + '"]', spot) };
    await close();
    out.steps[name].closed = await rows();
    out.steps[name].y = Math.round(await page.evaluate(() => window.scrollY));
  }
  /* the bar's own: two rows picked, its button pressed */
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.waitForTimeout(100);
  await page.click('[data-pick="sc:5000"]');
  await page.click('[data-pick="sc:5001"]');
  const before = await rows(), spot = await press('[data-batch="receipt_sent"]');
  out.steps.bar = { before: before, open: await rows(), box: await box(".fin-batchbar", spot) };
  await close();
  out.steps.bar.closed = await rows();
  out.steps.bar.y = Math.round(await page.evaluate(() => window.scrollY));
""", base, fake_sdk(store, labels), sizes=((1280, 748),))
    assert got["errors"] == []
    steps = got["steps"]
    for name, y in (("top", 0), ("scrolled", 700), ("bar", 0)):
        step, box = steps[name], steps[name]["box"]
        # every row where it was, while the confirmation is open and after it closed; the page where it was
        assert step["open"] == step["before"] and step["closed"] == step["before"], (name, step["before"][:3], step["open"][:3])
        assert box["y"] == y and step["y"] == y, (name, box["y"], step["y"])
        # the box in the view, clear of what was pressed, not under the pointer's "שמירה", and the focus on it
        assert box["inView"] and box["clear"] and not box["onGo"] and box["focus"], (name, box)
    assert 'יעודכן במאנדיי בשורה "תלמיד/ה לדוגמה 1", בשמך:' in steps["top"]["box"]["text"]
    assert 'יעודכן במאנדיי בשורה "תלמיד/ה לדוגמה 10", בשמך:' in steps["scrolled"]["box"]["text"]
    assert "יעודכן במאנדיי בשמך, ב-2 שורות:" in steps["bar"]["box"]["text"]
