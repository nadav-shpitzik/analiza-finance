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

from tests.desk_page_view import answer, view

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
  var store = %(store)s;
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
        return { id: id, board: { id: board }, column_values: (v.c || []).map(function (c) { return { id: c, text: (cells[id] || {})[c] || "" }; }) };
      });
      return Promise.resolve({ data: { items: items, boards: [{ id: board, columns: (v.c || []).map(function (c) { return { id: c }; }) }] } });
    },
    execute: function (what, args) { window.__executed.push({ what: what, args: args }); return Promise.resolve({}); },
    listen: function () {}
  };
};
"""

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
    sdk = FAKE_SDK % {"store": json.dumps(store, ensure_ascii=False)}
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
