"""D2's list in a real browser (the desk move plan §1b, 24.11): /desk/ served by this service's own routes, its API the stub
of tests/test_desk_browser.py answering a hand-made view, monday a fake SDK that keeps its boards, answers the page's
reads and writes as monday would, and records every request. Chromium through Playwright for node; skipped without them.

1. 1.1: two payments waiting for their receipt, picked and written with one confirmation.
2. 1.2: a search that finds payments outside the open tile, each with its own tile under its name.
3. 4 (ADR-0188): a company's billing details edited from the browser as the person, with and without updates:write.

Set DESK_SHOTS=<directory> to keep a screenshot of each step that shows something new."""
from __future__ import annotations

import copy
import json
import os
import subprocess
import threading
import time

import pytest

from tests.desk_page_view import answer, sc_row, view
from tests.test_desk_browser import NODE, PW_ROOT, _free_port, stub_app

pytestmark = pytest.mark.skipif(PW_ROOT is None, reason="node with playwright is not installed")

SC_BOARD, B2_BOARD, ACCOUNTS = "5093448677", "5097721228", "5089697343"
RECEIPT, STATUS = "color_mm4cq1hw", "color_mm1kxpvt"
VAT, HASH, NAME, TERMS = "text_mm3y3gm7", "text_mm3yprqa", "text_mm3yfrew", "dropdown_mm3yd36d"
WRITE = ["boards:read", "boards:write"]

# monday as the person reads it (invented, never a real number): its boards' items, and its columns' labels
SDK = r"""
window.__monday = [];
window.__updates = [];
window.mondaySdk = function () {
  var store = %(store)s, labels = %(labels)s, context = %(context)s;
  function cell(item, c) {
    var x = item.cells[c] || { text: "" }, out = { id: c, text: x.text || "", value: null };
    if (x.index !== undefined) { out.index = x.index; out.label = x.label; }
    return out;
  }
  return {
    get: function (what) {
      if (what === "context") { return Promise.resolve({ data: context }); }
      if (what === "sessionToken") { return Promise.resolve({ data: "stub-session-token" }); }
      return Promise.resolve({ data: null });
    },
    api: function (query, opts) {
      var v = (opts || {}).variables || {};
      window.__monday.push({ query: query, variables: v, apiVersion: (opts || {}).apiVersion });
      if (query.indexOf("create_update(") !== -1) {
        window.__updates.push(v);
        return Promise.resolve({ data: { create_update: { id: "9001" } } });
      }
      if (query.trim().indexOf("mutation") === 0) {
        var item = store[v.b][v.i], vals = JSON.parse(v.v);
        Object.keys(vals).forEach(function (c) {
          var val = vals[c];
          if (val && typeof val === "object" && "index" in val) {
            var l = labels[v.b][c][String(val.index)]; item.cells[c] = { text: l, index: val.index, label: l };
          } else if (val && typeof val === "object" && "ids" in val) {
            var d = labels[v.b][c].filter(function (x) { return x.id === val.ids[0]; })[0]; item.cells[c] = { text: d ? d.name : "" };
          } else { item.cells[c] = { text: String(val) }; }
        });
        return Promise.resolve({ data: { change_multiple_column_values: { id: v.i, column_values: v.c.map(function (c) { return cell(item, c); }) } } });
      }
      var board = String((v.b || [])[0]), items = store[board] || {};
      return Promise.resolve({ data: {
        items: (v.i || []).filter(function (id) { return Object.prototype.hasOwnProperty.call(items, id); }).map(function (id) {
          return { id: id, name: items[id].name, board: { id: board }, column_values: (v.c || []).map(function (c) { return cell(items[id], c); }) };
        }),
        boards: [{ id: board, columns: (v.c || []).map(function (c) {
          return { id: c, type: "x", settings_str: JSON.stringify({ labels: (labels[board] || {})[c] || {} }) };
        }) }] } });
    },
    execute: function () { return Promise.resolve({}); },
    listen: function () {}
  };
};
"""


def sdk(store: dict, labels: dict, board: str, scopes: list) -> str:
    context = {"boardId": int(board), "instanceType": "board_view", "theme": "light",
               "user": {"id": "1", "isViewOnly": False, "isGuest": False},
               "permissions": {"approvedScopes": scopes, "requiredScopes": WRITE}}
    return SDK % {"store": json.dumps(store, ensure_ascii=False), "labels": json.dumps(labels, ensure_ascii=False),
                  "context": json.dumps(context)}


def status(text: str, index: int) -> dict:
    return {"text": text, "index": index, "label": text}


@pytest.fixture
def serve():
    """Start the stub on a free port with a given view (and reading after a write); stop it after the test."""
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


def visit(script: str, base: str, fake: str) -> dict:
    shots = os.environ.get("DESK_SHOTS", "")
    if shots:
        os.makedirs(shots, exist_ok=True)
    run = subprocess.run([NODE, "-e", PRELUDE + script + CODA, base, fake, shots], capture_output=True, text=True,
                         timeout=120, env=dict(os.environ, NODE_PATH=PW_ROOT))
    assert run.returncode == 0, (run.stdout[-3000:], run.stderr[-3000:])
    got = json.loads(run.stdout.strip().splitlines()[-1])
    assert got["errors"] == [], got["errors"]
    return got


PRELUDE = r"""
const { chromium } = require("playwright");
const [base, sdk, shots] = process.argv.slice(1);
(async () => {
  const browser = await chromium.launch();
  const out = { errors: [], steps: {} };
  const page = await browser.newPage({ viewport: { width: 1434, height: 900 }, deviceScaleFactor: 1 });
  page.on("pageerror", (e) => out.errors.push("pageerror: " + e));
  page.on("console", (m) => { if (m.type() === "error") { out.errors.push("console: " + m.text()); } });
  await page.route("**/desk/vendor/monday-sdk.js*", (r) => r.fulfill({ contentType: "application/javascript", body: sdk }));
  const text = (sel) => page.$eval(sel, (el) => el.innerText);
  const shot = async (name) => { if (shots) { await page.screenshot({ path: shots + "/" + name + ".png", fullPage: true }); } };
  await page.goto(base + "/desk/");
  await page.waitForSelector("#screen-finance:not([hidden]) .fin-tabs", { timeout: 15000 });
"""
CODA = r"""
  out.monday = await page.evaluate(() => window.__monday);
  out.updates = await page.evaluate(() => window.__updates);
  await browser.close();
  console.log(JSON.stringify(out));
})().catch((e) => { console.log(JSON.stringify({ crashed: String(e && e.stack || e) })); process.exit(1); });
"""


def mutations(got: dict) -> list:
    return [(m["variables"].get("b"), m["variables"]["i"], json.loads(m["variables"]["v"])) for m in got["monday"]
            if m["query"].startswith("mutation") and "change_multiple_column_values" in m["query"]]


# --- 1.1: a batch of two -----------------------------------------------------------------------------------------------

def receipts_view() -> dict:
    v = view()
    v["b2c"]["rows"].append(sc_row("3106", "תלמיד/ה 6", "receipt_pending", invoice="30006", receipt="טרם", receipt_idx="0",
                                   money_in="5000", paid_typed="5000", net="5000", actions=["receipt_sent"]))
    return v


def recount(v: dict) -> dict:
    """The hand-made view's tile counts, from its rows: a reading after a write places them anew."""
    for p in v["b2c"]["periods"]:
        for key, st in p["states"].items():
            st["count"] = len([r for r in v["b2c"]["rows"] if r["state"] == key])
    return v


def test_two_receipts_picked_on_their_rows_are_written_with_one_confirmation(serve):
    v = recount(receipts_view())
    after = copy.deepcopy(v)
    for r in after["b2c"]["rows"]:
        if r["id"] in ("3102", "3106"):
            r.update(state="done", receipt="יצאה", receipt_idx="1", actions=[])
    recount(after)
    base, log = serve(v, after)
    store = {SC_BOARD: {"3102": {"name": "תלמיד/ה 2", "cells": {RECEIPT: status("טרם", 0)}},
                        "3106": {"name": "תלמיד/ה 6", "cells": {RECEIPT: status("טרם", 0)}},
                        "3103": {"name": "תלמיד/ה 3", "cells": {STATUS: status("שולם", 1)}}}}
    labels = {SC_BOARD: {RECEIPT: {"0": "טרם", "1": "יצאה", "2": "לא רלוונטי"},
                         STATUS: {"1": "שולם", "4": "תשלום הוחזר", "5": "הוחזר חלקי"}}}
    got = visit(r"""
  out.steps.rows = await page.$$eval("[data-row-act]", (b) => b.map((x) => [x.dataset.rowAct, x.innerText]));
  await page.click('[data-pick="sc:3102"]');
  await page.click('[data-pick="sc:3106"]');
  await page.waitForSelector(".fin-batchbar");
  out.steps.bar = await text(".fin-batchbar");
  await shot("1.1-a-two-rows-picked");
  await page.click('[data-batch="receipt_sent"]');
  await page.waitForSelector(".fin-batch-plan");
  out.steps.plan = await text(".fin-batch-plan");
  await shot("1.1-b-one-confirmation");
  await page.click("[data-batch-go]");
  await page.waitForSelector(".fin-batch-done", { timeout: 15000 });
  await page.waitForFunction(() => !document.querySelector('tr[data-row="3102"]'), null, { timeout: 15000 });
  out.steps.done = await text(".fin-batch-done");
  await shot("1.1-c-written-and-placed");
""", base, sdk(store, labels, SC_BOARD, WRITE))
    steps = got["steps"]
    # the buttons on the rows that offer them, with the server's words
    assert sorted(steps["rows"]) == [["sc:3102:receipt_sent", 'שליחת קבלה: "יצאה"'], ["sc:3103:refund_done", 'סימון "תשלום הוחזר"'],
                                     ["sc:3106:receipt_sent", 'שליחת קבלה: "יצאה"']]
    bar = " ".join(steps["bar"].split())
    assert "נבחרו 2 שורות" in bar and 'שליחת קבלה: "יצאה" (2)' in bar
    assert "ייכתב במאנדיי בשמך, ב-2 שורות:" in steps["plan"]
    assert 'תלמיד/ה 2 · שליחת קבלה: עכשיו "טרם", ייכתב "יצאה"' in steps["plan"]
    assert 'תלמיד/ה 6 · שליחת קבלה: עכשיו "טרם", ייכתב "יצאה"' in steps["plan"]
    assert "נכתב במאנדיי בשמך ב-2 שורות." in steps["done"]
    # one read of the two for the confirmation, then each read right before its write, by index
    reads = [m["variables"]["i"] for m in got["monday"] if m["query"].startswith("query") and m["variables"].get("c") == [RECEIPT]]
    assert reads == [["3102", "3106"], ["3102"], ["3106"]]
    assert mutations(got) == [(SC_BOARD, "3102", {RECEIPT: {"index": 1}}), (SC_BOARD, "3106", {RECEIPT: {"index": 1}})]
    assert all(m["apiVersion"] == "2026-07" for m in got["monday"])
    # one refresh after the batch, as after one action
    assert len([x for x in log if x["path"] == "/api/desk/refresh"]) == 1


# --- 1.2: a search that finds rows in another tile --------------------------------------------------------------------

def test_a_search_finds_payments_outside_the_open_tile_and_says_where_each_is(serve):
    base, log = serve()
    got = visit(r"""
  out.steps.open = await text(".fin-listhead h2");
  await page.fill("#fin-q", "תלמיד");
  await page.waitForFunction(() => /חיפוש בכל התקבולים/.test((document.querySelector(".fin-listhead h2") || {}).innerText || ""),
    null, { timeout: 15000 });
  out.steps.head = await text(".fin-listhead");
  out.steps.rows = await page.$$eval("tr[data-row]", (t) => t.map((x) => x.dataset.row));
  out.steps.where = await page.$$eval("tr[data-row]", (t) => t.map((x) => {
    const w = x.querySelector(".fin-where"); return w ? [x.dataset.row, w.innerText] : null; }).filter(Boolean));
  await shot("1.2-search-outside-the-open-tile");
  await page.click('[data-export="b2c"]');
  await page.waitForTimeout(300);
""", base, sdk({}, {}, SC_BOARD, WRITE))
    steps = got["steps"]
    assert steps["open"].startswith("לטיפול")
    assert "חיפוש בכל התקבולים" in steps["head"] and "5 תקבולים · חיפוש: תלמיד · 2 מהם מחוץ לרשימה הפתוחה" in steps["head"]
    assert "החיפוש עובר על כל התקבולים, לא רק על הרשימה הפתוחה (לטיפול · כל התקופה)." in steps["head"]
    assert sorted(steps["rows"]) == ["3101", "3102", "3103", "3104", "3105"]
    assert sorted(steps["where"]) == [["3101", "הושלם"], ["3105", "החזר בוצע"]]
    link = [json.loads(x["body"]) for x in log if x["path"] == "/api/desk/export-link"]
    assert [(x["state"], x["period"], x["q"]) for x in link] == [("", "", "תלמיד")]


# --- 4: a company's billing details, with and without updates:write -----------------------------------------------------

def billing_after(v: dict) -> dict:
    after = copy.deepcopy(v)
    mine = after["b2b"]["by_customer"]["7703"]
    mine["billing"]["terms"], mine["terms"], mine["terms_missing"] = "שוטף+30", "שוטף+30", False
    return after


@pytest.mark.parametrize("scopes, name", [(WRITE, "without-update"), (WRITE + ["updates:write"], "with-update")])
def test_a_companys_billing_details_are_edited_from_the_browser_as_the_person(serve, scopes, name):
    v = view()
    base, log = serve(v, billing_after(v))
    store = {ACCOUNTS: {"7701": {"name": "חברה א", "cells": {VAT: {"text": "500000001"}}},
                        "7703": {"name": "חברה ג", "cells": {VAT: {"text": ""}, HASH: {"text": "H7703"},
                                                             NAME: {"text": 'חברה ג בע"מ'}, TERMS: {"text": ""}}}}}
    labels = {ACCOUNTS: {TERMS: [{"id": 1, "name": "שוטף"}, {"id": 2, "name": "שוטף+30"}, {"id": 3, "name": "שוטף+60"},
                                 {"id": 4, "name": "אחר"}, {"id": 5, "name": "שוטף+45"}]}}
    got = visit(r"""
  await page.click('[data-b2view="customers"]');
  await page.click('tr[data-customer="7703"]');
  await page.waitForSelector('[data-bill-edit="7703"]');
  await page.click('[data-bill-edit="7703"]');
  await page.waitForSelector("#fin-bill-vat_id");
  await page.fill("#fin-bill-vat_id", "512345678");
  await page.selectOption("#fin-bill-terms", "2");
  await shot("4-a-form-read-live-NAME");
  await page.click("[data-bill-go]");
  await page.waitForSelector(".fin-billing .fin-act-plan");
  out.steps.plan = await text(".fin-billing .fin-act-plan");
  await shot("4-b-confirmation-NAME");
  await page.click("[data-bill-save]");
  await page.waitForFunction(() => /נכתב בלוח החברות/.test((document.querySelector(".fin-billing") || {}).innerText || ""),
    null, { timeout: 15000 });
  await page.waitForFunction(() => !document.querySelector(".fin-refreshing"), null, { timeout: 15000 });
  out.steps.done = await text(".fin-billing");
  await shot("4-c-written-NAME");
""".replace("NAME", name), base, sdk(store, labels, B2_BOARD, scopes))
    steps, with_update = got["steps"], "updates:write" in scopes
    assert 'ייכתב בלוח החברות, בחברה "חברה ג", בשמך:' in steps["plan"]
    assert 'ח.פ: עכשיו ריק, ייכתב "512345678"' in steps["plan"] and 'תנאי תשלום: עכשיו ריק, ייכתב "שוטף+30"' in steps["plan"]
    assert mutations(got) == [(ACCOUNTS, "7703", {VAT: "512345678", TERMS: {"ids": [2]}})]
    if with_update:
        assert "על החברה ייכתב גם עדכון בשמך: מה היה ומה נכתב." in steps["plan"]
        assert len(got["updates"]) == 1 and got["updates"][0]["i"] == "7703"
        assert "ח.פ: היה ריק, נכתב &quot;512345678&quot;." in got["updates"][0]["body"]
        assert "נכתב בלוח החברות בשמך, ועל החברה נכתב עדכון: מה היה ומה נכתב." in steps["done"]
    else:
        assert "עדכון על החברה לא ייכתב: לאפליקציה אין הרשאה לכתוב עדכונים." in steps["plan"]
        assert got["updates"] == [] and not [m for m in got["monday"] if "create_update" in m["query"]]
        assert "נכתב בלוח החברות בשמך. יומן הפעילות של הלוח שומר מה היה ומה נכתב." in steps["done"]
    assert "512345678" in steps["done"] and "שוטף+30" in steps["done"]
    # the server: one refresh after the write, and never the ח.פ
    assert len([x for x in log if x["path"] == "/api/desk/refresh"]) == 1
    assert "512345678" not in json.dumps(log, ensure_ascii=False)
