"""The desk's page in a real browser (D2): /desk/ served by this service's own routes, its API a stub answering the
hand-made view (tests/desk_page_view.py), monday a fake SDK. Chromium through Playwright for node; skipped without them.

One visit, the way Chava works: the students tab opens on the view's board, a refund to execute is opened (its bank
account and the ID number are read from monday as the person, then and only then), the companies tab, one company's
ledger (its ח.פ read the same way, its billing details with their edit, ADR-0188), the Excel link, a refresh; and
/desk/?check=1 draws D1's six lines. Set DESK_SCREENSHOT=<path.png> to keep a screenshot of the refund's details."""
from __future__ import annotations

import json
import os
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


def stub_app(log: list, v: dict | None = None, refreshed: dict | None = None):
    """This service's page routes, behind a stub of D2's API that records every request it is asked: the hand-made view,
    or `v`; a refresh answers `refreshed` when given (the reading after a write)."""
    from fastapi import FastAPI, Request
    from fastapi.responses import Response

    from app.desk.routes import router as desk_router
    from app.main import CSP

    app = FastAPI()
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
    def desk():
        return answer(v) if v is not None else answer()

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
    sent = json.dumps(log, ensure_ascii=False)
    for secret in list(LEADS.values()) + list(BANK.values()) + list(VAT.values()):
        assert not secret or secret not in sent, secret


# --- the QA of 24.11: the page in monday as Chava sees it, each finding by its number in the QA report -------------------

@pytest.fixture
def serve():
    """Start the stub on a free port with a given view (and the reading a refresh answers); stop it after the test."""
    import uvicorn

    started = []

    def start(v=None, refreshed=None):
        log: list = []
        port = _free_port()
        server = uvicorn.Server(uvicorn.Config(stub_app(log, v, refreshed), host="127.0.0.1", port=port, log_level="warning"))
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


def qa_visit(script: str, base: str, sdk: str, sizes=((1434, 900),)) -> dict:
    run = subprocess.run([NODE, "-e", QA_PRELUDE + script + QA_CODA, base, sdk, json.dumps([list(s) for s in sizes])],
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
    """QA 1 (24.11): in "שולם, שליחת קבלה: טרם", ticking the first row's box inserted a bar above the table and pushed the
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


def test_every_column_of_a_general_list_fits_its_frame_at_chavas_width(serve):
    """QA 4 (24.11): at full width (a 1707-pixel window with monday's sidebar open) "לטיפול" and "הכל" ran about 40 pixels
    past their frame: "פתיחה במאנדיי" showed only "פר" and was reached only by scrolling sideways. At monday's view widths of
    1280 and 1400, with rows as long as real ones, every column of those lists - on both tabs - is inside its frame. And
    the date that was not typed reads "(לפי יום היצירה)" in its cell (QA 10: finance.css draws the words around the term)."""
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
        assert got["steps"][size + " created"] == ['"(לפי "', "יום היצירה", '")"', "block"]


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
""", base, fake_sdk({}), sizes=((1280, 748), (683, 748)))
    assert got["errors"] == []
    wide, narrow = got["steps"]["1280x748"], got["steps"]["683x748"]
    # wide: the table taller than the view scrolls with the page - no frame that scrolls - and its header stays on top
    assert wide["table"] > wide["view"] and wide["page"], wide
    assert not wide["inner"] and wide["overflowY"] == "visible" and not wide["sideways"], wide
    scrolled = got["steps"]["1280x748 scrolled"]
    assert scrolled["frame"] < 0 and scrolled["head"] == 0, scrolled
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
    assert 'ייכתב במאנדיי בשורה "תלמיד/ה 3", בשמך:' in steps["row"] and steps["refreshed"].startswith("נכון ל-")
