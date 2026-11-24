"""Round 3, slice 3 (11.11): the page's writes, as the person, through monday.api() (frontend/finance.js).

The page is mounted in node with a fake monday SDK that keeps two boards, answers the page's read and its writes as monday
would, and records every request; the server's answers come in order, as in tests/test_desk_parity_js.py. Every action is
driven step by step: what is read, the plan shown before anything is sent, what is sent, what the row then says, and how a
failure is said. The allowlist is held to the server's (app/desk/view.py desk_writes) and every write outside it is
refused before monday.api() is called. Needs node; skipped without it.

D2 (Analiza-Monday backlog 0301): analiza-metrics tests/test_finance_actions_js.py at da671b2, on this service's paths
(/api/desk, /api/desk/refresh). Not ported, with what they tested: a customer's billing details edited in the ledger and
saved by the server (POST /api/finance/account, the page's form and its hooks `_bill`, `_billSave`, `_billGo`,
`_billState`) - ADR-0188: this server writes nothing to monday; since D2 the page edits them itself, from the browser as
the person (tests/test_desk_page.py)."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app.desk import view as fd
from app.monday.client import MONDAY_API_VERSION
from app.desk.view import json_safe
from tests.desk_fixtures import TODAY, acct, b2, enrollment, sc, snap

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")
PAGE = Path(__file__).resolve().parents[1] / "frontend" / "finance.js"
SC_BOARD, B2_BOARD = fd.STUDENT_CHARGE, fd.B2B_CHARGE
WRITE_SCOPES = {"permissions": {"approvedScopes": ["boards:read", "boards:write"], "requiredScopes": ["boards:read", "boards:write"]}}

HARNESS = r"""
const vm = require("vm"), fs = require("fs");
const input = JSON.parse(fs.readFileSync(0, "utf8"));
const toasts = [];
global.document = { visibilityState: "visible", activeElement: null, body: { appendChild() {} },
  addEventListener() {}, getElementById() { return null; },
  createElement() { const el = { setAttribute() {}, remove() {} };
    Object.defineProperty(el, "textContent", { set(v) { toasts.push(v); } }); return el; } };
global.window = { scrollY: 0, scrollTo() {}, addEventListener() {} };
const root = { innerHTML: "", addEventListener() {}, contains() { return false; }, querySelector() { return null; },
  querySelectorAll() { return []; } };
vm.runInThisContext(fs.readFileSync(process.argv[1], "utf8"));
const F = window.FinanceDesk;
const M = input.monday || {}, store = M.items || {}, labels = M.labels || {}, fail = M.fail || {};
const sent = [], posts = [], calls = [];
let writes = 0;
function cv(item, c) {
  const x = item.cells[c] || { text: "" };
  const out = { id: c, text: x.text || "", value: null };
  if (x.index !== undefined) { out.index = x.index; out.label = x.label; }
  return out;
}
const monday = M.absent ? null : {
  get: () => input.context === "never" ? new Promise(() => {}) : input.context === null ? Promise.reject(new Error("no context"))
    : Promise.resolve({ data: input.context || {} }),
  execute: () => Promise.resolve(),
  api: (query, opts) => {
    const v = (opts || {}).variables;
    sent.push({ kind: query.trim().split(" ")[0], query: query, variables: v, apiVersion: (opts || {}).apiVersion });
    if ((M.hang || []).indexOf(query.trim().split(" ")[0]) !== -1) { return new Promise(() => {}); }   // monday never answers
    if (query.trim().startsWith("query")) {
      if (fail.read) { return fail.read.reject ? Promise.reject(fail.read.reject) : Promise.resolve(fail.read.answer); }
      const item = store[v.i[0]];
      const items = item ? [{ id: v.i[0], name: item.name, board: { id: item.board }, column_values: v.c.map((c) => cv(item, c)) }] : [];
      const cols = v.c.filter((c) => (M.missing || []).indexOf(c) === -1).map((c) => ({ id: c, type: "x",
        settings_str: JSON.stringify({ labels: (labels[v.b[0]] || {})[c] || {} }) }));
      return Promise.resolve({ data: { items: items, boards: [{ columns: cols }] }, account_id: 1 });
    }
    writes += 1;
    if (fail.write && fail.write.at === writes) {
      return fail.write.reject ? Promise.reject(fail.write.reject) : Promise.resolve(fail.write.answer);
    }
    const item = store[v.i], vals = JSON.parse(v.v);
    Object.keys(vals).forEach((c) => {
      if ((M.keep || []).indexOf(c) !== -1) { return; }
      const val = vals[c];
      if (val && typeof val === "object" && "index" in val) {
        const l = labels[v.b][c][String(val.index)];
        item.cells[c] = { text: l, index: val.index, label: l };
      } else if (val && typeof val === "object" && "date" in val) { item.cells[c] = { text: val.date }; }
      else { item.cells[c] = { text: String(val) }; }
    });
    return Promise.resolve({ data: { change_multiple_column_values: { id: v.i, column_values: v.c.map((c) => cv(item, c)) } } });
  }
};
function answer(key) {
  calls.push(key);
  const a = (input.answers[key] || []).shift();
  return !a ? new Promise(() => {}) : a.reject ? Promise.reject(a.reject) : Promise.resolve(a.body);
}
/* a usage batch (16.11) is answered and left out of the requests a case compares */
const deps = { api: (p) => answer("GET " + p), post: (p, body) => { if (p === "/api/usage") { return Promise.resolve({ stored: 0, refused: 0 }); }
    posts.push([p, body === undefined ? null : body]); return answer("POST " + p); },
  monday: monday, board: input.board || "5093448677", later: (fn) => fn(), today: () => "2026-11-11",
  mondayWaitMs: input.mondayWaitMs };
/* a request with no answer never settles, and nothing keeps node waiting for it: a page that asks a path the case does
   not answer (the port moved /api/finance to /api/desk) never mounts, and node ended with nothing printed - said here */
process.on("beforeExit", () => { console.error("the case never finished; the page asked " + JSON.stringify(calls)); process.exit(1); });
const settle = async () => { for (let i = 0; i < 40; i++) { await new Promise((r) => setTimeout(r, 0)); } };
(async () => {
  await F.mount(root, deps);
  await settle();
  const out = { steps: [] };
  for (const step of input.steps) {
    let refused = null;
    if (step.mutate) { Object.keys(step.mutate).forEach((id) => Object.assign(store[id].cells, step.mutate[id])); }
    if (step.relabel) { Object.keys(step.relabel).forEach((b) => Object.assign(labels[b], step.relabel[b])); }
    if (step.act) { F._act(step.act[0], step.act[1], step.typed); }
    if (step.confirm) { F._confirm(); }
    if (step.write) { refused = await F._write(step.write[0], step.write[1], step.write[2]).then(() => "sent", (e) => e.code); }
    await settle();
    const act = F._actState();
    out.steps.push({ phase: act && act.phase, error: act && act.error, refused: refused,
      plan: act && act.plan ? act.plan.lines.map((l) => [l.title, !!l.same, !!l.kept, l.to === undefined ? null : l.to]) : null,
      html: step.show ? F._show(step.show) : "", can: F._can() });
  }
  out.sent = sent; out.posts = posts; out.toasts = toasts; out.calls = calls;
  console.log(JSON.stringify(out));
  process.exit(0);
})();
"""


def body_of(rows_sc=(), rows_b2=(), taken="2026-11-11T08:00:00+00:00", **kw) -> dict:
    view = fd.build_view(snap(list(rows_sc) or [sc(99)], list(rows_b2), **kw), TODAY)
    return {"body": json_safe({"view": view, "taken_at": taken, "age_seconds": 0, "refreshing": False, "stale": False,
                               "last_attempt": None})}


def status_cell(labels: dict, idx: str) -> dict:
    return {"text": labels[idx], "index": int(idx), "label": labels[idx]}


LABELS = {SC_BOARD: {fd.SC["status"]: fd.PAY, fd.SC["receipt"]: fd.RECEIPT},
          B2_BOARD: {fd.B2["billing"]: fd.BILLING, fd.B2["payment"]: fd.B2B_PAY}}


def run(steps, first, monday_items=None, answers=None, context=None, wait_ms=None, **monday):
    data = {"steps": steps, "answers": dict({"GET /api/desk": [first]}, **(answers or {})),
            "context": WRITE_SCOPES if context is None else context, "mondayWaitMs": wait_ms,
            "monday": dict({"items": monday_items or {}, "labels": json.loads(json.dumps(LABELS))}, **monday)}
    out = subprocess.run([NODE, "-e", HARNESS, str(PAGE)], input=json.dumps(data, default=str), capture_output=True,
                         text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def mutations(out):
    return [(m["variables"]["b"], m["variables"]["i"], json.loads(m["variables"]["v"])) for m in out["sent"]
            if m["kind"] == "mutation"]


def refund_row(id_="1", **kw):
    return sc(id_, credit_approved="1000", planned="2026-11-20", enr=["E1"], **kw)


def sc_item(status="1", **cells):
    base = {fd.SC["status"]: status_cell(fd.PAY, status)}
    base.update(cells)
    return {"board": SC_BOARD, "name": "תלמיד/ה 1", "cells": base}


# --- the allowlist ---------------------------------------------------------------------------------------------------

def test_the_pages_allowlist_is_exactly_what_the_servers_actions_write():
    out = subprocess.run([NODE, "-e", "const vm=require('vm'),fs=require('fs');global.window={};"
                          "vm.runInThisContext(fs.readFileSync(process.argv[1],'utf8'));"
                          "console.log(JSON.stringify({w:window.FinanceDesk._writable(),v:window.FinanceDesk._apiVersion}))",
                          str(PAGE)], capture_output=True, text=True, timeout=60)
    got = json.loads(out.stdout)
    assert got["w"] == fd.desk_writes()
    assert got["v"] == MONDAY_API_VERSION                  # the page asks monday for the version the server reads at


@pytest.mark.parametrize("board, item, values", [
    (fd.ACCOUNTS_BOARD, "1", {"text_mm3y3gm7": "512345678"}),                      # another board
    (fd.DEALS_BOARD, "1", {"deal_stage": {"index": 1}}),
    (SC_BOARD, "1", {fd.SC["bank_account"]: "123"}),                               # a column the desk never writes
    (SC_BOARD, "1", {fd.SC["credit_approved"]: "100"}),
    (B2_BOARD, "1", {fd.SC["status"]: {"index": 4}}),                              # a column of the other board
    (SC_BOARD, "1", {fd.SC["status"]: {"label": "תשלום הוחזר"}}),                  # never by a label's text
    (SC_BOARD, "1", {fd.SC["status"]: {"index": "4"}}),
    (SC_BOARD, "1", {fd.SC["status"]: {"index": 4, "label": "תשלום הוחזר"}}),
    (SC_BOARD, "1", {fd.SC["refund"]: "12a"}), (SC_BOARD, "1", {fd.SC["refund"]: 500}),
    (B2_BOARD, "1", {fd.B2["due"]: {"date": "11.11.2026"}}), (B2_BOARD, "1", {fd.B2["due"]: "2026-11-11"}),
    (SC_BOARD, "1", {fd.SC["credit_ref"]: "ZK\n1"}), (SC_BOARD, "1", {fd.SC["credit_ref"]: "x" * 61}),
    (SC_BOARD, "1", {}), (SC_BOARD, "abc", {fd.SC["credit_ref"]: "ZK-1"}),
    (SC_BOARD, "1", {fd.SC["credit_ref"]: "ZK-1", fd.SC["planned"]: {"date": "2026-11-11"}}),   # one bad column of two
])
def test_a_write_outside_the_allowlist_is_refused_before_monday_is_asked(board, item, values):
    out = run([{"write": [board, item, values]}], body_of([sc(1)]))
    assert out["steps"][0]["refused"] == "write_refused"
    assert out["sent"] == []


def test_the_page_composes_a_mutation_in_one_place_and_calls_monday_api_in_one():
    """D2 (ADR-0188) replaced the one mutation of slice 3 on purpose: a billing edit's Update (MON-41) is the page's second,
    composed once and sent by mondayUpdate only (tests/test_desk_page.py holds its gate)."""
    import re
    source = re.sub(r"/\*.*?\*/", "", PAGE.read_text(encoding="utf-8"), flags=re.S)
    assert source.count('"mutation ') == 2 and source.count("monday.api(") == 1   # deps.api is this service, not monday
    assert source.count("change_multiple_column_values(") == 1 and source.count("create_update(") == 1
    assert "change_simple_column_value" not in source and "create_labels_if_missing: false" in source


# --- each action, end to end -----------------------------------------------------------------------------------------

def test_refund_done_reads_shows_the_plan_writes_the_index_and_asks_the_server_again():
    first = body_of([refund_row()], enrollments=[enrollment("E1", asked="2026-11-01")])
    done = refund_row(st="4", refund="1000")
    after = body_of([done], taken="2026-11-11T08:00:09+00:00", enrollments=[enrollment("E1", asked="2026-11-01")])
    out = run([{"act": ["sc:1", "refund_done"]}, {"confirm": True}], first, {"1": sc_item("1")},
              answers={"POST /api/desk/refresh": [after]})
    plan, wrote = out["steps"]
    # before "כתיבה": the plan, from the row read live, and nothing sent but that read
    assert plan["phase"] == "confirm" and plan["plan"] == [["סטטוס תשלום", False, False, "תשלום הוחזר"]]
    # the row read as the plan is made, and again right before the write: only then the mutation
    assert [m["kind"] for m in out["sent"]] == ["query", "query", "mutation"]
    read = out["sent"][0]
    assert read["variables"] == {"i": ["1"], "c": [fd.SC["status"]], "b": [SC_BOARD]} and read["apiVersion"] == "2026-07"
    assert mutations(out) == [(SC_BOARD, "1", {fd.SC["status"]: {"index": 4}})]      # by index, never by text
    assert wrote["phase"] == "done" and out["posts"][-1] == ["/api/desk/refresh", None]
    assert any("נכתב במאנדיי בשמך: סטטוס תשלום \"תשלום הוחזר\"" in t for t in out["toasts"])


def test_a_partial_refund_types_its_amount_first_then_its_status_in_two_writes():
    out = run([{"act": ["sc:1", "refund_partial"], "typed": {fd.SC["refund"]: "1,250.5"}}, {"confirm": True}],
              body_of([refund_row(paid="4000")]), {"1": sc_item("1")})
    plan, wrote = out["steps"]
    assert plan["plan"] == [["סכום החזר", False, False, "1250.5"], ["סטטוס תשלום", False, False, "הוחזר חלקי"]]
    assert mutations(out) == [(SC_BOARD, "1", {fd.SC["refund"]: "1250.5"}), (SC_BOARD, "1", {fd.SC["status"]: {"index": 5}})]
    assert wrote["phase"] == "done"


def test_a_second_step_that_fails_says_what_landed_and_the_row_keeps_it():
    denied = {"reject": {"message": "User unauthorized to perform action", "data": {}}}
    out = run([{"act": ["sc:1", "refund_partial"], "typed": {fd.SC["refund"]: "500"}}, {"confirm": True},
               {"show": {"tab": "b2c", "sc": {"state": "", "open": {"1": True}}}}],
              body_of([refund_row()]), {"1": sc_item("1")}, fail={"write": {"at": 2, **denied}})
    failed = out["steps"][1]
    assert failed["phase"] == "failed"
    assert failed["error"] == "נכתב רק סכום החזר, ושאר הפעולה לא: מאנדיי לא מרשה לך לכתוב בשורה הזאת."
    assert len(mutations(out)) == 2
    html = out["steps"][2]["html"]
    assert "נכתב, מתעדכן" in html and "User unauthorized to perform action" in html     # monday's own words, under it
    assert ["/api/desk/refresh", None] in out["posts"]                                 # what landed is read again


def test_billed_writes_its_status_its_invoice_and_today_when_the_demand_day_is_empty():
    row = b2(1, billing="0", payment="2", due="")
    item = {"board": B2_BOARD, "name": "חיוב", "cells": {fd.B2["billing"]: status_cell(fd.BILLING, "0")}}
    out = run([{"act": ["b2:1", "billed"], "typed": {fd.B2["invoice"]: "INV-9"}}, {"confirm": True}],
              body_of([sc(9)], [row]), {"1": item}, board=B2_BOARD)
    plan = out["steps"][0]["plan"]
    assert plan == [["מצב חיוב", False, False, "חויב"], ["אסמכתת חשבונית", False, False, "INV-9"],
                    ["מועד דרישת תשלום", False, False, "2026-11-11"]]
    assert mutations(out) == [(B2_BOARD, "1", {fd.B2["billing"]: {"index": 6}, fd.B2["invoice"]: "INV-9",
                                               fd.B2["due"]: {"date": "2026-11-11"}})]


def test_billed_keeps_a_demand_day_monday_holds_and_an_empty_invoice_box_writes_no_invoice():
    row = b2(1, billing="1", payment="2", due="2026-11-05")
    item = {"board": B2_BOARD, "name": "חיוב", "cells": {fd.B2["billing"]: status_cell(fd.BILLING, "1"),
                                                          fd.B2["due"]: {"text": "2026-11-05"}, fd.B2["invoice"]: {"text": "77"}}}
    out = run([{"act": ["b2:1", "billed"], "typed": {fd.B2["invoice"]: ""}}, {"confirm": True}],
              body_of([sc(9)], [row]), {"1": item}, board=B2_BOARD)
    assert out["steps"][0]["plan"] == [["מצב חיוב", False, False, "חויב"], ["מועד דרישת תשלום", False, True, None]]
    assert mutations(out) == [(B2_BOARD, "1", {fd.B2["billing"]: {"index": 6}})]
    assert item["cells"][fd.B2["invoice"]] == {"text": "77"}                       # never cleared by an empty box


def test_paid_partly_writes_the_amount_with_the_status_and_paid_writes_the_status():
    row = b2(1, billing="6", payment="2", due="2026-09-01")
    item = lambda: {"board": B2_BOARD, "name": "חיוב", "cells": {fd.B2["payment"]: status_cell(fd.B2B_PAY, "2")}}  # noqa: E731
    out = run([{"act": ["b2:1", "b2b_partial"], "typed": {fd.B2["paid_so_far"]: "590"}}, {"confirm": True}],
              body_of([sc(9)], [row]), {"1": item()}, board=B2_BOARD)
    assert mutations(out) == [(B2_BOARD, "1", {fd.B2["paid_so_far"]: "590", fd.B2["payment"]: {"index": 0}})]
    out = run([{"act": ["b2:1", "b2b_paid"]}, {"confirm": True}], body_of([sc(9)], [row]), {"1": item()}, board=B2_BOARD)
    assert mutations(out) == [(B2_BOARD, "1", {fd.B2["payment"]: {"index": 1}})]


def test_a_reference_is_typed_in_place_cleared_on_purpose_and_an_unchanged_one_writes_nothing():
    refunded = sc(1, st="4", refund="1000", refund_date="2026-11-02", credit_ref="ZK-1")
    item = sc_item("4", **{fd.SC["credit_ref"]: {"text": "ZK-1"}})
    out = run([{"act": ["sc:1", "credit_ref"], "typed": {fd.SC["credit_ref"]: "ZK-7"}}, {"confirm": True}],
              body_of([refunded]), {"1": item})
    assert mutations(out) == [(SC_BOARD, "1", {fd.SC["credit_ref"]: "ZK-7"})]
    item = sc_item("4", **{fd.SC["credit_ref"]: {"text": "ZK-1"}})
    out = run([{"act": ["sc:1", "credit_ref"], "typed": {fd.SC["credit_ref"]: "  "}}, {"confirm": True}],
              body_of([refunded]), {"1": item})
    assert out["steps"][0]["plan"] == [["אסמכתת זיכוי", False, False, ""]]
    assert mutations(out) == [(SC_BOARD, "1", {fd.SC["credit_ref"]: ""})]
    item = sc_item("4", **{fd.SC["credit_ref"]: {"text": "ZK-1"}})
    out = run([{"act": ["sc:1", "credit_ref"]}], body_of([refunded]), {"1": item})      # the box holds the row's value
    assert out["steps"][0]["phase"] == "nothing" and mutations(out) == []


def test_receipt_sent_writes_its_index():
    out = run([{"act": ["sc:1", "receipt_sent"]}, {"confirm": True}], body_of([sc(1, receipt="0")]),
              {"1": sc_item("1", **{fd.SC["receipt"]: status_cell(fd.RECEIPT, "0")})})
    assert mutations(out) == [(SC_BOARD, "1", {fd.SC["receipt"]: {"index": 1}})]


def test_a_status_monday_already_holds_is_not_written_again():
    out = run([{"act": ["sc:1", "refund_done"]}], body_of([refund_row()]), {"1": sc_item("4")})
    assert out["steps"][0]["phase"] == "nothing" and out["steps"][0]["plan"] == [["סטטוס תשלום", True, False, None]]
    assert mutations(out) == []


def test_an_action_a_row_does_not_offer_does_nothing():
    out = run([{"act": ["sc:1", "refund_done"]}, {"act": ["sc:1", "billed"]}], body_of([sc(1)]), {"1": sc_item("1")})
    assert out["sent"] == [] and out["steps"][0]["phase"] is None


# --- the row after a write, and the server asked again ----------------------------------------------------------------

def test_the_written_row_is_shown_written_until_a_reading_shows_it_and_the_server_is_asked_again_twice_at_most():
    first = body_of([refund_row()])
    still = body_of([refund_row()], taken="2026-11-11T08:00:05+00:00")
    landed = body_of([refund_row(st="4", refund="1000")], taken="2026-11-11T08:00:12+00:00")
    steps = [{"act": ["sc:1", "refund_done"]}, {"confirm": True}, {"show": {"tab": "b2c", "sc": {"state": "", "open": {}}}}]
    # monday's filter behind the write: the first reading after it still has the old row - asked again, and it lands
    out = run(steps, first, {"1": sc_item("1")}, answers={"POST /api/desk/refresh": [still, landed]})
    assert [p[0] for p in out["posts"]] == ["/api/desk/refresh", "/api/desk/refresh"]
    assert "נכתב, מתעדכן" not in out["steps"][2]["html"]
    assert fd.B2C_STATES["refunded"]["label"] in out["steps"][2]["html"]                 # placed by the server's reading
    # it never lands: asked three times in all, and the row still says it was written, with its new status
    out = run(steps, first, {"1": sc_item("1")}, answers={"POST /api/desk/refresh": [still, still, still, still]})
    assert len(out["posts"]) == 3
    html = out["steps"][2]["html"]
    assert "נכתב, מתעדכן" in html and "<td class=\"k-state\">" in html


def test_a_refresh_refused_for_the_gap_is_asked_again_after_it():
    landed = body_of([refund_row(st="4", refund="1000")], taken="2026-11-11T08:00:12+00:00")
    out = run([{"act": ["sc:1", "refund_done"]}, {"confirm": True}], body_of([refund_row()]), {"1": sc_item("1")},
              answers={"POST /api/desk/refresh": [{"reject": {"error": "rate_limited", "retryAfter": 3}}, landed]})
    assert len(out["posts"]) == 2 and out["steps"][1]["phase"] == "done"


# --- every failure, in words ------------------------------------------------------------------------------------------

def test_without_the_write_permission_in_mondays_context_the_buttons_say_so_before_any_click():
    no_write = {"permissions": {"approvedScopes": ["boards:read"], "requiredScopes": ["boards:read"]}}
    show = {"show": {"tab": "b2c", "sc": {"state": "", "open": {"1": True}}}}
    out = run([{"act": ["sc:1", "refund_done"]}, show], body_of([refund_row()]), {"1": sc_item("1")}, context=no_write)
    assert out["sent"] == [] and out["steps"][0]["can"] == {"state": "no", "why": "no_scope"}
    html = out["steps"][1]["html"]
    assert "לאפליקציה עוד אין הרשאת כתיבה ללוחות במאנדיי, ולכן אין כאן פעולות." in html
    assert 'data-act="sc:1:refund_done" disabled' in html


@pytest.mark.parametrize("context, monday, why", [
    ({"permissions": {"approvedScopes": ["boards:read"], "requiredScopes": ["boards:read", "boards:write"]}}, {}, "approve"),
    (dict(WRITE_SCOPES, user={"isViewOnly": True}), {}, "view_only"),
    ({}, {"absent": True}, "outside"),
])
def test_each_reason_the_page_cannot_write_is_its_own_sentence(context, monday, why):
    out = run([{"show": {"tab": "b2c", "sc": {"state": "", "open": {"1": True}}}}], body_of([refund_row()]),
              {"1": sc_item("1")}, context=context, **monday)
    assert out["steps"][0]["can"] == {"state": "no", "why": why}
    sentence = {"approve": "מאנדיי מבקש לאשר אותה בבאנר בראש התצוגה", "view_only": "יש לך הרשאת צפייה בלבד",
                "outside": "רק כשהתצוגה פתוחה מתוך מאנדיי"}[why]
    assert sentence in out["steps"][0]["html"]


def test_a_missing_scope_monday_answers_with_is_said_and_turns_every_button_off():
    """monday did not say in its context (a field not verified yet): the page tries, and learns from the answer."""
    scope = {"answer": {"errors": [{"message": "Your app is missing the required scopes: boards:write"}]}}
    out = run([{"act": ["sc:1", "refund_done"]}, {"show": {"tab": "b2c", "sc": {"state": "", "open": {"1": True}}}}],
              body_of([refund_row()]), {"1": sc_item("1")}, context={}, fail={"read": scope})
    first = out["steps"][0]
    assert first["phase"] == "failed" and first["error"] == "לאפליקציה אין הרשאה לכתוב בלוח הזה. לא נכתב כלום."
    assert first["can"] == {"state": "no", "why": "no_scope"} and mutations(out) == []
    assert 'data-act="sc:1:refund_done" disabled' in out["steps"][1]["html"]


@pytest.mark.parametrize("monday, words", [
    ({"fail": {"read": {"reject": {"message": "rate limit exceeded"}}}}, "מאנדיי ביקשה להאט; לנסות שוב בעוד דקה. לא נכתב כלום."),
    ({"missing": [fd.SC["status"]]}, "עמודה שהפעולה כותבת לא נמצאה בלוח; נדב צריך לבדוק. לא נכתב כלום."),
    ({"labels": {SC_BOARD: {fd.SC["status"]: dict(fd.PAY, **{"4": "הוחזר"})}}},
     "תווית שהפעולה כותבת השתנתה במאנדיי; נדב צריך לבדוק. לא נכתב כלום."),
    ({"keep": [fd.SC["status"]]}, "מאנדיי ענתה, אבל הערך בשורה אינו מה שנשלח; לבדוק את השורה במאנדיי."),
    ({"fail": {"write": {"at": 1, "answer": {"errors": [{"message": "Something went wrong"}]}}}},
     "מאנדיי לא קיבלה את הכתיבה. לא נכתב כלום."),
])
def test_a_failure_is_said_in_words_never_silent(monday, words):
    out = run([{"act": ["sc:1", "refund_done"]}, {"confirm": True}], body_of([refund_row()]), {"1": sc_item("1")}, **monday)
    last = [s for s in out["steps"] if s["phase"]][-1]
    assert last["phase"] == "failed" and last["error"] == words
    assert any(words in t for t in out["toasts"])


def test_a_row_monday_holds_on_another_board_is_not_written():
    item = dict(sc_item("1"), board=B2_BOARD)
    out = run([{"act": ["sc:1", "refund_done"]}], body_of([refund_row()]), {"1": item})
    assert out["steps"][0]["error"] == "השורה לא נמצאה בלוח (אולי נמחקה או הועברה). לא נכתב כלום." and mutations(out) == []


@pytest.mark.parametrize("typed, words", [
    ("abc", "סכום החזר: סכום גדול מאפס, בספרות, עד שתי ספרות אחרי הנקודה."),
    ("0", "סכום החזר: סכום גדול מאפס, בספרות, עד שתי ספרות אחרי הנקודה."),
    ("12.345", "סכום החזר: סכום גדול מאפס, בספרות, עד שתי ספרות אחרי הנקודה."),
    ("١٢٣", "סכום החזר: סכום גדול מאפס, בספרות, עד שתי ספרות אחרי הנקודה."),
    ("", "סכום החזר: סכום גדול מאפס, בספרות, עד שתי ספרות אחרי הנקודה."),
])
def test_an_amount_that_is_not_one_is_said_before_monday_is_asked(typed, words):
    out = run([{"act": ["sc:1", "refund_partial"], "typed": {fd.SC["refund"]: typed}}], body_of([refund_row()]),
              {"1": sc_item("1")})
    assert out["steps"][0]["phase"] == "invalid" and out["steps"][0]["error"] == words and out["sent"] == []


def test_the_plan_box_shows_what_will_be_written_in_mondays_words_and_the_house_style():
    out = run([{"act": ["sc:1", "refund_done"]}, {"show": {"tab": "b2c", "sc": {"state": "refund_to_execute", "open": {"1": True}}}}],
              body_of([refund_row()]), {"1": sc_item("1")})
    import html as h
    html = h.unescape(out["steps"][1]["html"])
    assert 'ייכתב במאנדיי בשורה "תלמיד/ה 1", בשמך:' in html
    assert '<li><b>סטטוס תשלום</b>: עכשיו "שולם", ייכתב "תשלום הוחזר"</li>' in html
    assert fd.DESK_ACTIONS["refund_done"]["after"] in html and 'data-act-go="1">כתיבה</button>' in html
    assert "—" not in html and "→" not in html


# --- the ledger: a customer's billing details, as the server sends them ------------------------------------------------
# D2: their save through the server (POST /api/finance/account) is not ported, nor are its tests (ADR-0188: this server
# writes nothing to monday). Their edit is the person's own, from the browser: tests/test_desk_page.py holds it.

def ledger_body():
    # no ח.פ: the reading never holds one (NEVER_READ), and the page reads it in the browser, as the person
    a1 = dict(acct("A1", ""), **{fd.AC["hash_key"]: "10269", fd.AC["invoice_name"]: "חברה"})
    return body_of([sc(9)], [b2(1, payment="2", due="2026-10-01", account="A1")], accounts=[a1])


def test_the_ledger_shows_the_four_details():
    show = {"show": {"tab": "b2b", "b2": {"state": "", "view": "list", "customer": "A1", "open": {}}}}
    out = run([show], ledger_body(), board=B2_BOARD)
    html = out["steps"][0]["html"]
    assert "<h2>פרטי חיוב</h2>" in html and '<dt>מפתח חשבשבת</dt><dd><bdi dir="ltr">10269</bdi></dd>' in html
    assert all("<dt>%s</dt>" % f["title"] in html for f in fd.account_form()["fields"])     # by the server's titles


def test_no_terms_beside_a_company_opens_its_ledger():
    show = {"show": {"tab": "b2b", "b2": {"state": "overdue", "view": "list", "customer": "", "open": {"1": True}}}}
    out = run([show], ledger_body(), board=B2_BOARD)
    html = out["steps"][0]["html"]
    assert html.count('data-terms="A1"') >= 2                         # beside the due date, and in the row's details


# --- the hostile reread of slice 3 (11.11): each failed before its fix ------------------------------------------------

def test_the_desk_shows_even_when_monday_never_answers_whether_it_may_write():
    """mount() waited for monday's context; app.js shows the desk only when mount settles, so a monday slow to answer, or
    not answering, kept the whole desk on its loading screen."""
    out = run([{"show": {"tab": "b2c", "sc": {"state": "", "open": {}}}}], body_of([refund_row()]), {"1": sc_item("1")},
              context="never")
    assert "<h1>כספים</h1>" in out["steps"][0]["html"] and out["steps"][0]["can"]["state"] == "unknown"


def test_a_plan_shown_before_the_row_changed_in_monday_is_planned_again_and_never_written_over_unseen():
    """A plan read a while ago was written as it was: the row is read again on "כתיבה", and what changed is planned again."""
    moved = {fd.SC["status"]: status_cell(fd.PAY, "2")}                    # someone set "יש בעיה" meanwhile
    out = run([{"act": ["sc:1", "refund_done"]}, {"mutate": {"1": moved}}, {"confirm": True}],
              body_of([refund_row()]), {"1": sc_item("1")})
    again = out["steps"][2]
    assert again["phase"] == "confirm" and again["plan"] == [["סטטוס תשלום", False, False, "תשלום הוחזר"]]
    assert mutations(out) == []
    assert [m["kind"] for m in out["sent"]] == ["query", "query"]
    # and the plan it shows says the row changed, and what it holds now
    out = run([{"act": ["sc:1", "refund_done"]}, {"mutate": {"1": moved}}, {"confirm": True},
               {"show": {"tab": "b2c", "sc": {"state": "", "open": {"1": True}}}}], body_of([refund_row()]), {"1": sc_item("1")})
    import html as h
    page = h.unescape(out["steps"][3]["html"])
    assert "השורה השתנתה במאנדיי מאז שהתוכנית הוצגה" in page and 'עכשיו "יש בעיה", ייכתב "תשלום הוחזר"' in page
    # monday marked it meanwhile: nothing left to write, and nothing is
    out = run([{"act": ["sc:1", "refund_done"]}, {"mutate": {"1": {fd.SC["status"]: status_cell(fd.PAY, "4")}}},
               {"confirm": True}], body_of([refund_row()]), {"1": sc_item("1")})
    assert out["steps"][2]["phase"] == "nothing" and mutations(out) == []


def test_a_partial_refund_not_smaller_than_its_credit_is_warned_in_the_plan():
    show = {"show": {"tab": "b2c", "sc": {"state": "", "open": {"1": True}}}}
    out = run([{"act": ["sc:1", "refund_partial"], "typed": {fd.SC["refund"]: "1000"}}, show], body_of([refund_row()]),
              {"1": sc_item("1")})
    import html as h
    assert 'הסכום אינו קטן מסכום לזיכוי (1,000.00 ₪). אם כל סכום לזיכוי הוחזר, מסמנים "תשלום הוחזר".' \
        in h.unescape(out["steps"][1]["html"])
    out = run([{"act": ["sc:1", "refund_partial"], "typed": {fd.SC["refund"]: "400"}}, show], body_of([refund_row()]),
              {"1": sc_item("1")})
    assert "הסכום אינו קטן מסכום לזיכוי" not in h.unescape(out["steps"][1]["html"])


def test_a_reference_written_leaves_its_box_to_the_rows_value():
    refunded = sc(1, st="4", refund="1000", refund_date="2026-11-02", credit_ref="")
    later = body_of([sc(1, st="4", refund="1000", refund_date="2026-11-02", credit_ref="ZK-9")], taken="2026-11-11T08:00:09+00:00")
    out = run([{"act": ["sc:1", "credit_ref"], "typed": {fd.SC["credit_ref"]: "ZK-7"}}, {"confirm": True},
               {"show": {"tab": "b2c", "sc": {"state": "", "open": {"1": True}}}}], body_of([refunded]),
              {"1": sc_item("4", **{fd.SC["credit_ref"]: {"text": ""}})},
              answers={"POST /api/desk/refresh": [later]})
    # a newer reading says ZK-9 (someone corrected it in monday): the box shows the row's value, not the old draft
    assert 'data-act-input="sc:1:%s" dir="auto" maxlength="60" value="ZK-9"' % fd.SC["credit_ref"] in out["steps"][2]["html"]


def test_a_written_row_covers_only_a_reading_that_has_not_caught_up_never_a_later_change():
    """The row keeps its written values while a reading still shows the values from before the write; a reading that shows
    a third value saw a later change in monday, and the page shows that."""
    refunded = sc(1, st="4", refund="1000", refund_date="2026-11-02", credit_ref="")
    behind = body_of([refunded], taken="2026-11-11T08:00:05+00:00")
    show = {"show": {"tab": "b2c", "sc": {"state": "", "open": {"1": True}}}}
    out = run([{"act": ["sc:1", "credit_ref"], "typed": {fd.SC["credit_ref"]: "ZK-7"}}, {"confirm": True}, show],
              body_of([refunded]), {"1": sc_item("4", **{fd.SC["credit_ref"]: {"text": ""}})},
              answers={"POST /api/desk/refresh": [behind, behind, behind]})
    assert "נכתב, מתעדכן" in out["steps"][2]["html"] and 'value="ZK-7"' in out["steps"][2]["html"]


def test_the_done_line_says_the_row_moves_only_when_a_status_was_written():
    show = {"show": {"tab": "b2c", "sc": {"state": "", "open": {"1": True}}}}
    out = run([{"act": ["sc:1", "refund_done"]}, {"confirm": True}, show], body_of([refund_row()]), {"1": sc_item("1")})
    assert "נכתב במאנדיי בשמך. המסך מתעדכן, והשורה תעבור לשיבוץ שלה." in out["steps"][2]["html"]
    refunded = sc(1, st="4", refund="1000", refund_date="2026-11-02")
    out = run([{"act": ["sc:1", "credit_ref"], "typed": {fd.SC["credit_ref"]: "ZK-7"}}, {"confirm": True}, show],
              body_of([refunded]), {"1": sc_item("4", **{fd.SC["credit_ref"]: {"text": ""}})})
    assert "נכתב במאנדיי בשמך. המסך מתעדכן. " in out["steps"][2]["html"] and "תעבור לשיבוץ" not in out["steps"][2]["html"]


# --- the second hostile review of slice 3 (11.11): each failed before its fix ---------------------------------------------

@pytest.mark.parametrize("board, values", [
    (SC_BOARD, {fd.SC["status"]: {"index": 0}}),            # "לא שולם": a label of the column, set by no action
    (SC_BOARD, {fd.SC["status"]: {"index": 2}}),
    (SC_BOARD, {fd.SC["receipt"]: {"index": 0}}),
    (B2_BOARD, {fd.B2["billing"]: {"index": 2}}),           # "בוטל"
    (B2_BOARD, {fd.B2["payment"]: {"index": 2}}),
])
def test_a_status_index_no_action_sets_is_refused_before_monday_is_asked(board, values):
    """The allowlist held each status column and not its indexes: any label of an allowed column went out."""
    out = run([{"write": [board, "1", values]}], body_of([sc(1)]))
    assert out["steps"][0]["refused"] == "write_refused" and out["sent"] == []


def test_a_plan_the_server_sends_with_another_index_is_never_written():
    """A plan names a label monday really holds at another index: the label check passes it, the allowlist must not."""
    first = body_of([refund_row()])
    for a in first["body"]["view"]["b2c"]["actions"]["list"]:
        if a["key"] == "refund_done":
            a["steps"][0][0].update(index=fd.P_UNPAID, label=fd.PAY[fd.P_UNPAID])
    out = run([{"act": ["sc:1", "refund_done"]}, {"confirm": True}], first, {"1": sc_item("1")})
    assert mutations(out) == []
    assert out["steps"][1]["phase"] == "failed" and out["steps"][1]["error"] == \
        "הכתיבה הזאת מחוץ לעמודות שהמסך רשאי לכתוב, ולכן לא נשלחה. לא נכתב כלום."


def test_a_label_renamed_in_monday_after_the_plan_was_shown_stops_the_write():
    """The read on "כתיבה" compared the cells and not the labels: index 4 went out under another name."""
    renamed = {SC_BOARD: {fd.SC["status"]: dict(fd.PAY, **{fd.P_REFUNDED: "בוטל"})}}
    out = run([{"act": ["sc:1", "refund_done"]}, {"relabel": renamed}, {"confirm": True}], body_of([refund_row()]),
              {"1": sc_item("1")})
    assert out["steps"][0]["phase"] == "confirm"
    assert mutations(out) == []
    assert out["steps"][2]["phase"] == "failed" and out["steps"][2]["error"] == \
        "תווית שהפעולה כותבת השתנתה במאנדיי; נדב צריך לבדוק. לא נכתב כלום."


def test_billed_never_writes_an_invoice_box_she_did_not_touch_over_what_monday_holds_now():
    """The box is filled from the reading; monday holds a reference corrected since. "חויב" wrote the old one back."""
    row = b2(1, billing="1", payment="2", due="", invoice="INV-1")
    item = {"board": B2_BOARD, "name": "חיוב", "cells": {fd.B2["billing"]: status_cell(fd.BILLING, "1"),
                                                          fd.B2["invoice"]: {"text": "INV-2"}}}
    out = run([{"act": ["b2:1", "billed"]}, {"confirm": True}], body_of([sc(9)], [row]), {"1": item}, board=B2_BOARD)
    assert out["steps"][0]["plan"] == [["מצב חיוב", False, False, "חויב"], ["אסמכתת חשבונית", False, True, None],
                                       ["מועד דרישת תשלום", False, False, "2026-11-11"]]
    assert mutations(out) == [(B2_BOARD, "1", {fd.B2["billing"]: {"index": 6}, fd.B2["due"]: {"date": "2026-11-11"}})]
    # typed, it is hers: written over what monday holds, and the plan says so
    out = run([{"act": ["b2:1", "billed"], "typed": {fd.B2["invoice"]: "INV-1"}}, {"confirm": True}],
              body_of([sc(9)], [row]), {"1": item}, board=B2_BOARD)
    assert mutations(out)[0][2][fd.B2["invoice"]] == "INV-1"


def test_a_reference_box_she_did_not_touch_never_writes_the_readings_value_over_a_newer_one():
    refunded = sc(1, st="4", refund="1000", refund_date="2026-11-02", credit_ref="ZK-1")
    out = run([{"act": ["sc:1", "credit_ref"]}, {"confirm": True}], body_of([refunded]),
              {"1": sc_item("4", **{fd.SC["credit_ref"]: {"text": "ZK-9"}})})
    assert out["steps"][0]["phase"] == "nothing" and out["steps"][0]["plan"] == [["אסמכתת זיכוי", False, True, None]]
    assert mutations(out) == []


def test_a_written_row_shows_a_later_change_of_one_column_while_another_is_still_behind():
    """The mark covered every written column when one was behind - and so hid "סכום החזר" changed in monday after it."""
    later = body_of([refund_row(refund="700")], taken="2026-11-11T08:00:05+00:00")
    show = {"show": {"tab": "b2c", "sc": {"state": "", "open": {"1": True}}}}
    out = run([{"act": ["sc:1", "refund_partial"], "typed": {fd.SC["refund"]: "500"}}, {"confirm": True}, show],
              body_of([refund_row()]), {"1": sc_item("1")}, answers={"POST /api/desk/refresh": [later, later, later]})
    import html as h
    page = h.unescape(out["steps"][2]["html"])
    assert "<dt>סכום החזר</dt><dd><bdi>700.00 ₪</bdi></dd>" in page and "<bdi>500.00 ₪</bdi>" not in page
    assert "נכתב, מתעדכן" in page and fd.PAY[fd.P_PART] in page          # the status, still behind, is covered


def test_a_write_monday_never_answers_frees_the_desk_and_says_it_may_have_landed():
    show = {"show": {"tab": "b2c", "sc": {"state": "", "open": {"1": True}}}}
    out = run([{"act": ["sc:1", "refund_done"]}, {"confirm": True}, show], body_of([refund_row()]), {"1": sc_item("1")},
              wait_ms=5, hang=["mutation"])
    wrote = out["steps"][1]
    assert wrote["phase"] == "failed" and wrote["error"] == \
        "מאנדיי לא ענתה על הכתיבה בזמן, ולכן לא ידוע אם נכתבה; לבדוק את השורה במאנדיי."
    assert ["/api/desk/refresh", None] in out["posts"]
    assert 'data-act="sc:1:refund_done" disabled' not in out["steps"][2]["html"]


def test_a_read_monday_never_answers_frees_the_desk_and_says_nothing_was_written():
    out = run([{"act": ["sc:1", "refund_done"]}], body_of([refund_row()]), {"1": sc_item("1")}, wait_ms=5, hang=["query"])
    assert out["steps"][0]["phase"] == "failed" and out["steps"][0]["error"] == "מאנדיי לא ענתה בזמן. לא נכתב כלום."


def test_an_arabic_letter_mark_is_refused_like_the_other_bidi_marks():
    refunded = sc(1, st="4", refund="1000", refund_date="2026-11-02", credit_ref="")
    out = run([{"act": ["sc:1", "credit_ref"], "typed": {fd.SC["credit_ref"]: "ZK؜-7"}}], body_of([refunded]),
              {"1": sc_item("4", **{fd.SC["credit_ref"]: {"text": ""}})})
    assert out["steps"][0]["phase"] == "invalid" and out["sent"] == []
    out = run([{"write": [SC_BOARD, "1", {fd.SC["credit_ref"]: "ZK؜-7"}]}], body_of([sc(1)]))
    assert out["steps"][0]["refused"] == "write_refused"
    # D2: the ledger's form refused the mark too; the form is not ported (ADR-0188), so neither is that case
