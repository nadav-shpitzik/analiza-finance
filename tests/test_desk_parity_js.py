"""The page's students filter (frontend/finance.js scFilter) against the server's (app/desk/view.py filter_b2c).

The Excel file is built on the server from the filter the page sends, so the page and the file must pick the same
payments in the same order. The hostile review of 10.11 found three ways they did not (float amounts, a vanished
month, non-ASCII digits); this runs both on the same rows and every filter. Needs node; skipped without it.

D2 (Analiza-Monday backlog 0301): analiza-metrics tests/test_finance_js.py at da671b2, held to this service's server
(app/desk/view.py, export.py and routes.py). Not ported, with what they tested: the server reads no bank account
(NEVER_READ), so no view carries one (`attach_bank`) and its note "bank_unread" is gone - the page reads a refund's
account in the browser, as the person, when its details open (tests/test_desk_page.py)."""
from __future__ import annotations

import itertools
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app.desk import routes as dr
from app.desk import view as fd
from app.desk.view import json_safe
from tests.desk_fixtures import TODAY, D, acct, b2, deal, enrollment, lead, sc, served, snap
from tests.test_desk import desk, session_token  # noqa: F401 - `desk` is the fixture

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")
PAGE = Path(__file__).resolve().parents[1] / "frontend" / "finance.js"
RUN = """
const vm = require("vm"), fs = require("fs");
global.window = {};
vm.runInThisContext(fs.readFileSync(process.argv[1], "utf8"));
const input = JSON.parse(fs.readFileSync(0, "utf8"));
const f = input.tab === "b2b" ? window.FinanceDesk._b2Filter : window.FinanceDesk._scFilter;
console.log(JSON.stringify(input.cases.map(c => f(input.rows, input.b2c, c[0], c[1], c[2]).map(r => r.id))));
"""


def rows():
    out = []
    for i, (paid, extra) in enumerate([
        ("8192.88", {}), ("3950.5", {"invoice": ""}), ("4000", {"invoice": "", "receipt": "0"}),
        ("4000", {"st": "4", "refund_date": "2026-11-02"}), ("6000", {"credit_approved": "5900", "planned": "2026-11-20"}),
        ("100.05", {"st": "5", "refund": "50", "refund_date": "2026-10-02", "credit_ref": "ZK-1"}),
        ("4000", {"st": "9"}), ("0", {"st": "0"}), ("4000", {"receipt": "0", "invoice": "00123"}),
        ("12000", {"paid_date": "2026-09-30"}), ("4000", {"st": "31"}), ("777.7", {"invoice": "INV-AI"}),
        # refunds to execute (round 3): their own order - an unknown last day, then a passed one, then by planned date
        ("3000", {"credit_approved": "2900", "planned": "2026-11-15", "enr": ["E1"]}),
        ("3000", {"credit_approved": "2900", "planned": "2026-11-10", "enr": ["E2"]}),
        ("3000", {"credit_approved": "2900", "planned": "", "enr": ["E3"]}),
        ("3000", {"credit_approved": "2900", "planned": "2026-11-30", "enr": ["E4"]}),
        ("3000", {"credit_approved": "2900", "planned": "2026-11-10", "enr": ["E9"]}),
        ("3000", {"credit_approved": "2900", "planned": "2026-11-10"}),
        ("3000", {"credit_approved": "2900", "planned": "2026-11-20", "enr": ["E5"]}),
        ("3000", {"credit_approved": "2900", "planned": "", "enr": ["E6"]}),
        # QA 2.6: on one planned day, the nearest last day first - here 18.11 and 16.11, both ahead, both on 20.11
        ("3000", {"credit_approved": "2900", "planned": "2026-11-20", "enr": ["E7"]}),
        ("3000", {"credit_approved": "2900", "planned": "2026-11-20", "enr": ["E8"]}),
    ]):
        created = "2026-%02d-%02dT09:00:00Z" % (9 + i % 3, 1 + i)
        out.append(sc(i + 1, paid=paid, created=created, **extra))
    return out


def enrollments():
    return [enrollment("E1", asked="2026-10-18"), enrollment("E2", asked="2026-10-16"), enrollment("E3", asked="2026-10-20"),
            enrollment("E4", asked="2026-11-05"), enrollment("E5", asked="2026-10-16"), enrollment("E6", asked="2026-11-01"),
            enrollment("E7", asked="2026-11-04"), enrollment("E8", asked="2026-11-02"), enrollment("E9")]


def test_the_page_and_the_file_pick_the_same_payments_in_the_same_order():
    view = fd.build_view(snap(rows(), enrollments=enrollments()), TODAY)
    b2c = view["b2c"]
    states = ["", "tasks"] + list(fd.B2C_STATES)
    periods = [p["key"] for p in b2c["periods"]] + ["2031-01", "2031-01+2031-02"]
    queries = ["", "8191.89", "8191.88", "8193.87", "3951.49", "3951.5", "3,950", "₪4000", "4000.0", " 4000 ",
               "٤٠٠٠", "４０００", "8192.885", "-4000", "תלמיד", "inv-ai", "ZK", "00123", "a", "0", "2900"]
    refunds = [x["id"] for x in fd.filter_b2c(b2c, "refund_to_execute", "", "")]
    assert len(refunds) == 11 and refunds.index("22") < refunds.index("21")      # 16.11 before 18.11 on one planned day
    cases = list(itertools.product(states, periods, queries))
    data = json.dumps(json_safe({"rows": b2c["rows"], "b2c": {"states": b2c["states"], "periods": b2c["periods"]},
                                 "cases": cases}), default=str)
    out = subprocess.run([NODE, "-e", RUN, str(PAGE)], input=data, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    page = json.loads(out.stdout)
    server = [[x["id"] for x in fd.filter_b2c(b2c, s, p, q)] for s, p, q in cases]
    diff = [(c, a, b) for c, a, b in zip(cases, page, server) if a != b]
    assert not diff, diff[:5]


def test_the_companies_page_and_file_pick_the_same_charges_in_the_same_order():
    rows = [b2(i + 1, billing=bl, payment=pm, amount=amt, account=acc, due=due, paid=paid)
            for i, (bl, pm, amt, acc, due, paid) in enumerate([
                ("6", "2", "1500.5", "A1", "2026-09-01", ""), ("6", "2", "800", "A2", "2026-08-15", ""),
                ("0", "2", "12000", "A1", "2026-10-29", ""), ("1", "2", "4000", "A2", "2026-12-20", ""),
                ("6", "0", "10000", "A1", "2026-10-01", "5000"), ("6", "0", "300", "A2", "", ""),
                ("6", "1", "999.99", "A1", "2026-07-01", ""), ("7", "2", "", "A2", "", ""), ("2", "2", "50", "A1", "", ""),
                ("6", "2", "700", "A2", "2026-11-30", ""), ("6", "2", "", "A1", "2026-09-05", ""),
                ("6", "2", "450", "", "2026-09-07", ""), ("6", "0", "2000", "A2", "2026-09-09", "2360"),
                ("6", "0", "", "A1", "", "100"),
                # round 3: one state before billing, and a charge billed before its demand day
                ("1", "2", "640", "A1", "2026-11-20", ""), ("6", "2", "910", "A2", "2026-11-25", ""),
                ("0", "2", "220", "A2", "", "")])]
    view = fd.build_view(snap([sc(1)], rows, accounts=[acct("A1"), acct("A2")]), TODAY)
    b = view["b2b"]
    states = ["", "tasks"] + list(fd.B2B_STATES)
    customers = ["", "A1", "A2", "A9", fd.NO_ACCOUNT]
    queries = ["", "1501.49", "1501.5", "6800", "799.01", "999.99", "חיוב", "a", "55", "٨٠٠", "1,500.50"]
    cases = list(itertools.product(states, customers, queries))
    data = json.dumps(json_safe({"tab": "b2b", "rows": b["rows"], "b2c": {"states": b["states"]}, "cases": cases}), default=str)
    out = subprocess.run([NODE, "-e", RUN, str(PAGE)], input=data, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    page = json.loads(out.stdout)
    server = [[x["id"] for x in fd.filter_b2b(b, s, c, q)] for s, c, q in cases]
    diff = [(c, a, x) for c, a, x in zip(cases, page, server) if a != x]
    assert not diff, diff[:5]


def test_the_page_and_the_server_agree_which_searches_can_be_exported():
    qs = ["", "תלמיד", "abc", "1,500.50 ₪", 'א"ב (2)', "a/b-c.d", "12\ufeff000", "x\x1fy", "\x85", "٣٩٥٠", "４０００",
          "tab\there", "x" * 100, "x" * 101, "emoji \U0001F600"]
    script = """
const vm = require("vm"), fs = require("fs");
global.window = {};
vm.runInThisContext(fs.readFileSync(process.argv[1], "utf8"));
const qs = JSON.parse(fs.readFileSync(0, "utf8"));
console.log(JSON.stringify(qs.map(q => window.FinanceDesk._exportable(q))));
"""
    out = subprocess.run([NODE, "-e", script, str(PAGE)], input=json.dumps(qs), capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == [fd.query_exportable(q) for q in qs]


# --- round 3 (Nadav 11.11): the page's words, its columns, its return rule and what it draws ----------------------------

def node(script: str, data) -> object:
    out = subprocess.run([NODE, "-e", script, str(PAGE)], input=json.dumps(data, default=str), capture_output=True,
                         text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


LOAD = """
const vm = require("vm"), fs = require("fs");
global.window = {};
vm.runInThisContext(fs.readFileSync(process.argv[1], "utf8"));
const input = JSON.parse(fs.readFileSync(0, "utf8"));
const F = window.FinanceDesk;
"""


def test_the_page_names_a_refunds_two_dates_as_the_server_does():
    got = node(LOAD + "console.log(JSON.stringify(F._labels));", None)
    assert got == {"planned": fd.PLANNED_LABEL, "lastDay": fd.LAST_DAY_LABEL}
    source = PAGE.read_text(encoding="utf-8")
    assert source.count('var PLANNED_LABEL = "%s";' % fd.PLANNED_LABEL) == 1
    assert source.count('var LAST_DAY_LABEL = "%s";' % fd.LAST_DAY_LABEL) == 1


def test_the_page_words_a_refunds_lateness_as_innas_app_does():
    got = node(LOAD + "console.log(JSON.stringify(input.map(F._lastDayText)));", [-12, -2, -1, 0, 1, 2, 14])
    assert got == ["באיחור של 12 ימים", "באיחור של 2 ימים", "באיחור של יום אחד", "היום הוא היום האחרון", "נותר יום אחד",
                   "נותרו 2 ימים", "נותרו 14 ימים"]


def test_the_page_heads_mondays_columns_by_their_monday_names_and_lays_out_the_refund_and_open_lists():
    cols = node(LOAD + "console.log(JSON.stringify(F._columns()));", None)
    heads = cols["heads"]
    assert {k: heads[k] for k in ("received", "receipt", "to_refund", "refunded", "credit_ref", "invoice", "method",
                                  "refund_date", "asked", "last_day", "planned", "demand", "amount", "paid_so_far",
                                  "account", "course", "status", "payment", "open")} == {
        "received": "סכום ששולם", "receipt": "שליחת קבלה", "to_refund": "סכום לזיכוי", "refunded": "סכום החזר",
        "credit_ref": "אסמכתת זיכוי", "invoice": "אסמכתת חשבונית", "method": "אופן החזר", "refund_date": "תאריך החזר",
        "asked": "תאריך בקשת ביטול", "last_day": fd.LAST_DAY_LABEL, "planned": fd.PLANNED_LABEL,
        "demand": "מועד דרישת תשלום", "amount": 'סכום לחיוב (ללא מע"מ)', "paid_so_far": 'סכום ששולם (כולל מע"מ)',
        "account": "חברה", "course": "קוד קורס", "status": "מצב חיוב", "payment": "סטטוס תשלום", "open": "פתיחה במאנדיי"}
    refund = cols["sc"]["refund_to_execute"]
    i = refund.index("last_day")
    assert refund[i - 1:i + 2] == ["asked", "last_day", "planned"]              # the two dates side by side
    done = cols["sc"]["refunded"]
    assert done[done.index("last_day") + 1] == "planned"
    assert cols["b2"]["open"][0] == "due" and "to_bill" in cols["b2"] and "approval" not in cols["b2"]
    assert set(cols["b2"]) - {""} <= set(fd.B2B_STATES) and set(cols["sc"]) - {""} <= set(fd.B2C_STATES)


def test_the_page_asks_again_on_return_only_after_5_seconds_and_never_inside_the_refresh_gap():
    """Round 3, slice 2: a refresh reads only what changed (about a second), so a reading over 5 seconds old is asked
    again on return - 20 before - and the server's gap between two presses is that same span."""
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


def test_the_pages_return_rule_is_never_shorter_than_the_servers_gap():
    """A reader who comes back after the page's span is never refused by the server's own gap for her last press (the
    press came before the reading it brought landed): only another reader's press inside the gap answers 429."""
    returns_after = node(LOAD + "console.log(JSON.stringify(F._returnAfterMs));", None)
    assert returns_after >= dr.REFRESH_GAP_S * 1000          # the desk routes' gap between two presses
    # analiza-metrics also held its reading's TTL under 10 s; here a full reading is held HELD_TTL_S (60 s) until the
    # refresh that reads only what changed is ported (the D2 list, 1.3), so only the gap is held here
    assert dr.REFRESH_GAP_S <= 5


def render(cases, rows_sc=(), rows_b2=(), **kw):
    # no bank account is attached: the server never reads one (NEVER_READ). The page reads it as the person when a
    # refund's details open, and drawn here without monday, the details say where it shows
    view = fd.build_view(snap(list(rows_sc), list(rows_b2), **kw), TODAY)
    body = json_safe({"view": view, "taken_at": "2026-11-09T20:00:00+00:00", "age_seconds": 0, "refreshing": False,
                      "stale": False, "last_attempt": None})
    return node(LOAD + "console.log(JSON.stringify(input.cases.map(c => F._render(input.body, c))));",
                {"body": body, "cases": cases})


def test_the_page_draws_each_states_sentence_above_its_list_and_the_legend_on_demand():
    refund = sc(1, enr=["E1"], credit_approved="1000", planned="2026-11-15")
    sc_html, b2_html, tasks, legend = render([
        {"tab": "b2c", "legend": False, "sc": {"state": "refund_to_execute", "open": {}}},
        {"tab": "b2b", "legend": False, "b2": {"state": "to_bill", "view": "list", "customer": "", "open": {}}},
        {"tab": "b2c", "legend": False, "sc": {"state": "tasks", "open": {}}},
        {"tab": "b2b", "legend": True, "b2": {"state": "", "view": "list", "customer": "", "open": {}}}],
        [refund, sc(2)], [b2(1, billing="0", payment="2")], enrollments=[enrollment("E1", asked="2026-10-20")])
    about = fd.B2C_STATES["refund_to_execute"]["about"].replace('"', "&quot;")
    assert '<p class="fin-about">%s</p>' % about in sc_html
    assert sc_html.index("fin-about") < sc_html.index("<table")                    # above the list
    assert '<p class="fin-about">%s</p>' % fd.B2B_STATES["to_bill"]["about"].replace('"', "&quot;") in b2_html
    # "לטיפול" has its sentence too since QA 2.1; the legend opens on demand only
    assert '<p class="fin-about">%s</p>' % fd.TASKS_ABOUT["b2c"].replace('"', "&quot;") in tasks
    assert 'class="fin-legend"' not in tasks and 'data-legend="1"' in tasks
    assert 'class="fin-legend"' in legend
    for term, _text in fd.LEGEND["b2b"]:
        assert "<dt>%s</dt>" % term.replace('"', "&quot;") in legend


def test_a_rows_name_opens_its_monday_card_and_the_open_column_has_a_header():
    sc_html, b2_html = render([{"tab": "b2c", "sc": {"state": "", "open": {}}},
                               {"tab": "b2b", "b2": {"state": "", "view": "list", "customer": "", "open": {}}}],
                              [sc(7)], [b2(9)])
    assert '<a class="fin-name" data-open="7" href="https://analiza-college-group.monday.com/boards/5093448677/pulses/7"' in sc_html
    assert '<a class="fin-name" data-open="9" href="https://analiza-college-group.monday.com/boards/5097721228/pulses/9"' in b2_html
    for html in (sc_html, b2_html):
        assert '<th class="k-open">פתיחה במאנדיי</th>' in html and "fin-sr" not in html


def test_a_refunds_two_dates_are_side_by_side_in_its_details_and_the_legal_one_has_the_old_label():
    rows = [sc(1, enr=["E1"], credit_approved="1000", planned="2026-11-15"), sc(2, st="0", paid="2500"),
            sc(3, st="4", refund_date="2026-11-02")]
    refund, plain = render([{"tab": "b2c", "sc": {"state": "refund_to_execute", "open": {"1": True}}},
                            {"tab": "b2c", "sc": {"state": "", "open": {"3": True}}}],
                           rows, enrollments=[enrollment("E1", asked="2026-10-20")])
    grid = refund[refund.index('<dl class="fin-dx-grid">'):]
    dts = [x.split("<")[0] for x in grid.split("<dt>")[1:4]]
    # the refund's part comes first, its two dates side by side; the request day is the enrollment's, under "הרשמה" (QA 2.9)
    assert dts == [fd.LAST_DAY_LABEL, fd.PLANNED_LABEL, "סכום לזיכוי"]
    assert '<h3 class="fin-dx-h">הרשמה</h3><dl class="fin-dx-grid"><dt>סטטוס שובר משה&quot;ע</dt><dd><span class="fin-subtle">-</span></dd><dt>תאריך בקשת ביטול</dt><dd>20.10.26</dd>' in refund
    assert "03.11.26 <span class=\"fin-badtext\">באיחור של 6 ימים</span>" in refund
    assert "<td class=\"k-planned\">15.11.26</td>" in refund
    # a "לא שולם" row's typed amount is shown struck through, never as money; an old refund has no dates to show
    assert '<s class="fin-subtle" title="סטטוס תשלום &quot;לא שולם&quot;: לא נספר"><bdi>2,500.00 ₪</bdi></s>' in plain
    details = plain[plain.index('<tr class="fin-detail">'):]
    # an old refund shows the same refund fields as every refund (QA 2.9), its unknown dates as "-"
    assert "<dt>%s</dt><dd><span class=\"fin-subtle\">-</span></dd>" % fd.LAST_DAY_LABEL in details
    assert "* סכום החזר" not in plain


def test_the_aging_table_groups_the_bands_and_ends_with_their_total():
    html, = render([{"tab": "b2b", "b2": {"state": "", "view": "customers", "customer": "", "open": {}}}],
                   [sc(1)], [b2(1, payment="2", due="2026-09-01"), b2(2, billing="1", payment="2"),
                             b2(3, payment="0", amount="2000"), b2(4, payment="0", amount="1000", paid="590")])
    head = html[html.index("<thead>"):html.index("</thead>")]
    assert '<th colspan="5" class="fin-group fin-band fin-band-first fin-band-last" scope="colgroup">לפי מועד פירעון</th>' in head
    assert head.index("לפי מועד פירעון") < head.index("סה&quot;כ יתרה פתוחה") and "לחיוב החודש" in head
    first_row = head.split("</tr>")[0]
    assert first_row.rstrip().endswith('<th rowspan="2" class="n">סה&quot;כ יתרה פתוחה (ללא מע&quot;מ)</th>')
    # partly paid without a typed paid amount: what they bill, in a column of its own under "שולם חלקית" (QA 2.12)
    assert '<th class="n k-partial_unknown_amount fin-band fin-band-last">סכום לחיוב (ללא מע&quot;מ), סכום ששולם (כולל מע&quot;מ) לא הוקלד</th>' in head
    assert '<td class="n k-partial_unknown_amount fin-band fin-band-last"><bdi>2,000.00 ₪</bdi></td>' in html
    assert "להפקה" not in html and "פתוח לגבייה" not in html


def test_a_charge_billed_without_a_demand_day_shows_its_invoice_and_an_open_list_opens_with_the_due_date():
    exc, open_ = render([{"tab": "b2b", "b2": {"state": "exception", "view": "list", "customer": "", "open": {"1": True}}},
                         {"tab": "b2b", "b2": {"state": "open", "view": "list", "customer": "", "open": {}}}],
                        [sc(1)], [b2(1, payment="2", due="", invoice="INV-7"), b2(2, payment="2", due="2026-11-05")],
                        accounts=[acct("A1", "שוטף+30")])
    assert "<li>חויב בלי מועד דרישת תשלום, אסמכתת חשבונית INV-7</li>" in exc
    assert '<span class="fin-chip">חויב בלי מועד דרישת תשלום</span>' in exc
    head = open_[open_.index("<thead>"):open_.index("</thead>")]
    assert head.index("מועד פירעון") < head.index("חברה")
    assert '<td class="k-due">30.12.26 <bdi class="fin-subtle">שוטף+30</bdi></td>' in open_      # 30.11 + 30


def test_the_page_has_no_retired_term_and_keeps_the_house_style():
    import re
    source = re.sub(r"/\*.*?\*/", "", PAGE.read_text(encoding="utf-8"), flags=re.S)
    retired = ["חשבונית זיכוי", "מספר חשבונית", "להפקה", "הפקת", "לפני מע\\\"מ", "להחזיר עד", "היום האחרון להחזר",
               "פתוח לגבייה", "שולם עד כה", "נותר לגבות", "סטטוס במאנדיי", "מצב חיוב במאנדיי", "סטטוס תשלום במאנדיי",
               "מועד דרישה\"", "מועד דרישה ", "מועד הדרישה",
               "מסלול מימון", "\"לחשבון\"", "\"קבלה\"", "\"תקבול\"", "היקף העסקה", "המשלם בעסקה", "הסכום ששולם",
               # a company is monday's "חברה" (the hostile review of 11.11): only the tab and the view keep "לקוח"
               "כל הלקוחות", "\"לקוח אחד\"", "לקוחות עם משהו פתוח"]
    found = [t for t in retired if t in source]
    assert not found, found
    css = (PAGE.parent / "finance.css").read_text(encoding="utf-8")
    for text in (PAGE.read_text(encoding="utf-8"), css):
        assert "—" not in text and "→" not in text



# --- the hostile review of round 3 (11.11): each finding below failed before its fix ---------------------------------

# mount() and a return to the page in a document just big enough for them: a clock the test moves, and the server's
# answers in order, per request ("GET <path>" / "POST <path>"); a request with no answer left never settles
VISIT = """
const vm = require("vm"), fs = require("fs");
const input = JSON.parse(fs.readFileSync(0, "utf8"));
let now = 1000000;
const RealDate = Date;
global.Date = class extends RealDate { static now() { return now; } };
const listeners = {};
global.document = { visibilityState: "visible", activeElement: null, body: { appendChild() {} },
  addEventListener(t, f) { (listeners[t] = listeners[t] || []).push(f); }, getElementById() { return null; },
  createElement() { return { setAttribute() {}, remove() {} }; } };
global.window = { scrollY: 0, scrollTo() {}, addEventListener(t, f) { (listeners["window:" + t] = listeners["window:" + t] || []).push(f); } };
const root = { innerHTML: "", addEventListener() {}, contains() { return false; }, querySelector() { return null; } };
vm.runInThisContext(fs.readFileSync(process.argv[1], "utf8"));
const calls = [];
function answer(key) {
  calls.push(key);
  const a = (input.answers[key] || []).shift();
  return !a ? new Promise(() => {}) : a.reject ? Promise.reject(a.reject) : Promise.resolve(a.body);
}
/* a usage batch (16.11) is answered and left out of the requests a case compares */
const deps = { api: (p) => answer("GET " + p), post: (p) => p === "/api/usage" ? Promise.resolve({ stored: 0, refused: 0 }) : answer("POST " + p),
  monday: null, board: "" };
const settle = async () => { for (let i = 0; i < 20; i++) { await new Promise((r) => setTimeout(r, 0)); } };
/* nothing keeps node waiting for a request that never settles: a page that asks a path the case does not answer (the
   port moved /api/finance to /api/desk) never mounts, and node ended with nothing printed - said here */
let done = false;
process.on("beforeExit", () => { if (!done) { console.error("the case never finished; the page asked " + JSON.stringify(calls)); process.exit(1); } });
(async () => {
  await window.FinanceDesk.mount(root, deps);
  await settle();
  const seen = [];
  for (const r of input.returns) {
    /* a return: [ms away, how] - "visible" (another tab, the default), "focus" (monday's item card closed) or "both" */
    const [away, how] = Array.isArray(r) ? r : [r, "visible"];
    now += away;
    if (how !== "focus") { listeners.visibilitychange.forEach((f) => f()); }
    if (how !== "visible") { (listeners["window:focus"] || []).forEach((f) => f()); }
    await settle();
    seen.push(root.innerHTML.indexOf(input.marker) !== -1);
  }
  console.log(JSON.stringify({ calls: calls.filter((c) => c.indexOf("/totals") === -1), seen: seen }));
  done = true;
})();
"""


def visit(answers: dict, returns: list[int]) -> dict:
    out = subprocess.run([NODE, "-e", VISIT, str(PAGE)], capture_output=True, text=True, timeout=60,
                         input=json.dumps({"answers": answers, "returns": returns, "marker": "שורה-חדשה"}))
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def answer_body(rows_sc, taken_at: str, age: int) -> dict:
    view = fd.build_view(snap(rows_sc, [b2(1)]), TODAY)
    return {"body": json_safe({"view": view, "taken_at": taken_at, "age_seconds": age, "refreshing": False,
                               "stale": False, "last_attempt": None})}


def test_a_return_that_meets_the_refresh_gap_still_shows_the_reading_the_server_already_holds():
    older = answer_body([sc(1)], "2026-11-09T20:00:00+00:00", 0)
    # the newer reading holds one more payment, waiting for its receipt: on the list the page opens on ("לטיפול")
    newer = answer_body([sc(1), dict(sc(2, receipt="0"), name="שורה-חדשה")], "2026-11-09T20:00:40.250000+00:00", 4)
    not_yet = {"reject": {"error": "rate_limited", "retryAfter": 20}}
    # another reader (or this one, on the other charge board) refreshed a moment ago: the refresh answers 429, and the
    # page takes the newer reading this instance holds through the plain open - which is no refresh and no read
    got = visit({"GET /api/desk": [older, newer], "POST /api/desk/refresh": [not_yet]}, [25_000])
    assert got == {"calls": ["GET /api/desk", "POST /api/desk/refresh", "GET /api/desk"], "seen": [True]}
    # nothing newer held: the page keeps its reading, keeps the Retry-After (no ask 5s later), and asks after it
    got = visit({"GET /api/desk": [older, older], "POST /api/desk/refresh": [not_yet, newer]},
                [25_000, 5_000, 20_000])
    assert got == {"calls": ["GET /api/desk", "POST /api/desk/refresh", "GET /api/desk",
                             "POST /api/desk/refresh"], "seen": [False, False, True]}
    # a refresh that worked is a fresh reading: no second ask 3 seconds later; past the span (slice 2: 5 seconds) it is
    # asked again
    newest = answer_body([sc(1), dict(sc(2, receipt="0"), name="שורה-חדשה")], "2026-11-09T20:00:40.250000+00:00", 0)
    got = visit({"GET /api/desk": [older], "POST /api/desk/refresh": [newest, newest]}, [25_000, 3_000, 3_000])
    assert got == {"calls": ["GET /api/desk", "POST /api/desk/refresh", "POST /api/desk/refresh"],
                   "seen": [True, True, True]}


def test_the_page_asks_again_when_its_window_takes_the_focus_back_after_mondays_item_card():
    """QA 11.11 (backlog 0280 round 3, item 5): Chava edits a row in monday's item card over the view, closes it, and the
    page still shows the row as it was - closing the card fires no visibilitychange. The view's window taking the focus
    back asks as a return does: the same span, nothing twice, and the server's Retry-After kept."""
    older = answer_body([sc(1)], "2026-11-09T20:00:00+00:00", 0)
    newer = answer_body([sc(1), dict(sc(2, receipt="0"), name="שורה-חדשה")], "2026-11-09T20:00:40.250000+00:00", 0)
    # the card closed 6 seconds after the reading landed: the focus asks, and the edited list shows
    got = visit({"GET /api/desk": [older], "POST /api/desk/refresh": [newer]}, [[6_000, "focus"]])
    assert got == {"calls": ["GET /api/desk", "POST /api/desk/refresh"], "seen": [True]}
    # within the span the focus asks nothing; a return that fires both events asks once
    got = visit({"GET /api/desk": [older], "POST /api/desk/refresh": [newer, newer]},
                [[3_000, "focus"], [3_000, "both"]])
    assert got == {"calls": ["GET /api/desk", "POST /api/desk/refresh"], "seen": [False, True]}
    # the server's "not yet" holds for the focus as for a return: nothing asked before its Retry-After, then asked
    not_yet = {"reject": {"error": "rate_limited", "retryAfter": 5}}
    got = visit({"GET /api/desk": [older, older], "POST /api/desk/refresh": [not_yet, newer]},
                [[6_000, "focus"], [2_000, "focus"], [4_000, "focus"]])
    assert got == {"calls": ["GET /api/desk", "POST /api/desk/refresh", "GET /api/desk",
                             "POST /api/desk/refresh"], "seen": [False, False, True]}


def test_the_companies_tab_names_its_two_amount_columns_in_full_never_as_the_students_column():
    """'סכום ששולם' is Student Charge's column; B2B Charge's is 'סכום ששולם (כולל מע"מ)' and its amount
    'סכום לחיוב (ללא מע"מ)'. On the companies tab, its legend and its files, a shortened name is a second name for the
    one and the exact name of the other."""
    import re
    from app.desk import export as fx
    rows_b2 = [b2(1, payment="0", amount="1000", paid="590"), b2(2, payment="0", amount="2000"),
               b2(3, payment="2", due="2026-09-01")]
    html = render([{"tab": "b2b", "b2": {"state": "partial", "view": "list", "customer": "", "open": {"1": True, "2": True}}},
                   {"tab": "b2b", "b2": {"state": "", "view": "customers", "customer": "", "open": {}}}],
                  [sc(1)], rows_b2)
    texts = [re.sub(r"<[^>]+>", " ", h).replace("&quot;", '"') for h in html]
    texts += [s["about"] for s in fd.B2B_STATES.values()] + [t for _term, t in fd.LEGEND["b2b"]]
    texts += [c[0] for c in fx.B2B_COLUMNS] + [fd.aging_file_header({"group": g, "header": h})
                                               for _k, h, g, _kind, _w in fd.AGING_COLUMNS]
    short = [t[m.start():m.start() + 30] for t in texts for name, full in
             (("סכום ששולם", 'סכום ששולם (כולל מע"מ)'), ("סכום לחיוב", 'סכום לחיוב (ללא מע"מ)'))
             for m in re.finditer(name, t) if not t.startswith(full, m.start())]
    assert not short, short


def test_a_refund_without_an_enrollment_says_so_and_not_that_its_enrollment_lacks_the_day():
    rows = [sc(1, credit_approved="500", planned="2026-11-20", lead=["L1"]),     # a charge born before its enrollment
            sc(2, credit_approved="500", planned="2026-11-20", enr=["E2"])]      # an enrollment without the day
    no_link, no_day = render([{"tab": "b2c", "sc": {"state": "refund_to_execute", "open": {"1": True}}},
                              {"tab": "b2c", "sc": {"state": "refund_to_execute", "open": {"2": True}}}],
                             rows, leads=[lead("L1")], enrollments=[enrollment("E2")])
    details = no_link[no_link.index('<tr class="fin-detail">'):]
    assert "התקבול לא מקושר להרשמה" in details
    assert "בהרשמה אין תאריך בקשת ביטול" not in details and "אין בהרשמה" not in details
    details = no_day[no_day.index('<tr class="fin-detail">'):]
    assert "בהרשמה אין תאריך בקשת ביטול" in details and "אין בהרשמה" in details


# --- QA of slice 1 (11.11): money and work on the page. Each test below failed before its fix -------------------------

def test_an_approved_refund_on_a_status_without_money_shows_in_the_exceptions_with_why_and_its_clock():
    """QA 1.1 and 1.2 on the page: the chip names the column and the status, the 4,000 to credit shows in the list, the
    refund's last day keeps its lateness, and the details say the contradiction in one sentence."""
    rows = [sc(1, st="2", paid="4000", credit_approved="4000", planned="2026-11-01", enr=["E1"]),
            sc(2, credit_approved="1000", refund="1000", refund_date="2026-11-11")]
    exc, = render([{"tab": "b2c", "sc": {"state": "exception", "open": {"1": True, "2": True}}}],
                  rows, enrollments=[enrollment("E1", asked="2026-10-22")])
    assert '<span class="fin-chip bad">סכום לזיכוי בסטטוס &quot;יש בעיה&quot;</span>' in exc
    assert '<span class="fin-chip bad">סכום החזר בסטטוס &quot;שולם&quot;</span>' in exc
    assert '<span class="fin-chip bad">תאריך החזר בסטטוס &quot;שולם&quot;</span>' in exc
    assert '<td class="k-to_refund n"><bdi>4,000.00 ₪</bdi></td>' in exc
    assert "05.11.26 <span class=\"fin-badtext\">באיחור של 4 ימים</span>" in exc                  # 22.10 + 14 days
    assert "סכום לזיכוי 4,000.00 ₪ בסטטוס תשלום &quot;יש בעיה&quot;: החזר שאושר, ועוד לא סומן" in exc
    assert "החזר לביצוע</span>" not in exc.split("<tbody>")[1].split("fin-detail")[0]    # no chip repeats a state's name


def test_a_company_without_terms_is_marked_beside_its_due_dates_name_and_arrears():
    """QA 1.3 on the page: a chip beside each due date counted from the demand day for lack of terms, a marker beside the
    company in "לפי לקוח" and in its ledger, and how much of "בפיגור" comes from such companies."""
    rows = [b2(1, payment="2", due="2026-10-31", account="A1", amount="200000"),
            b2(2, payment="2", due="2026-09-10", account="A2", amount="27757")]
    accounts = [dict(acct("A1", ""), name="Fastlane"), acct("A2", "שוטף+30")]
    overdue, aging, ledger = render([
        {"tab": "b2b", "b2": {"state": "overdue", "view": "list", "customer": "", "open": {}}},
        {"tab": "b2b", "b2": {"state": "", "view": "customers", "customer": "", "open": {}}},
        {"tab": "b2b", "b2": {"state": "", "view": "list", "customer": "A1", "open": {}}}], [sc(1)], rows, accounts=accounts)
    # round 3, slice 3: beside a due date and in the aging the chip is a way to the company's ledger, where its billing
    # details show (read only since D2, so its title no longer promises an edit); in the ledger itself it is a mark, and
    # the ledger's "פרטי חיוב" says it too
    link = ('<button type="button" class="fin-chip fin-chiplink" data-terms="A1" title="פתיחת הכרטסת של החברה">'
            'אין תנאי תשלום</button>')
    assert '<td class="k-due">31.10.26 %s</td>' % link in overdue
    assert '<td class="k-due">30.10.26 <bdi class="fin-subtle">שוטף+30</bdi></td>' in overdue
    assert "(מזה <bdi>200,000 ₪</bdi> בחברות בלי תנאי תשלום)" in overdue
    assert '<td>Fastlane</td><td class="k-terms">%s</td>' % link in aging
    assert 'Fastlane</span> <span class="fin-chip">אין תנאי תשלום</span>' in ledger
    assert '<dt>תנאי תשלום</dt><dd><span class="fin-chip">אין תנאי תשלום</span></dd>' in ledger


def test_a_billed_charge_without_a_demand_day_says_so_in_every_list_with_its_invoice():
    """QA 1.4 on the page: 2960243096, partly paid and billed with no demand day, was marked nowhere."""
    rows = [b2(1, payment="0", amount="258539", paid="100000", due="", invoice="INV-1"), b2(2, payment="1", due="")]
    partial, paid = render([{"tab": "b2b", "b2": {"state": "partial", "view": "list", "customer": "", "open": {"1": True}}},
                            {"tab": "b2b", "b2": {"state": "paid", "view": "list", "customer": "", "open": {}}}],
                           [sc(1)], rows)
    first = partial.split("<tbody>")[1].split("</tr>")[0]
    assert '<td class="k-demand"><span class="fin-chip bad">חסר</span></td>' in first and "INV-1" in first
    assert "<li>חויב בלי מועד דרישת תשלום, אסמכתת חשבונית INV-1</li>" in partial
    assert '<td class="k-demand"><span class="fin-chip bad">חסר</span></td>' in paid


def test_a_zero_refund_and_a_label_without_a_name_are_said_as_they_are():
    """QA 2.10: "סכום החזר לא הוקלד" where monday holds 0, and a "ערוץ תשלום" value with no label shown empty."""
    zero = sc(1, st="5", paid="0", refund="0")
    zero[fd.SC["channel"]], zero[fd.SC["channel"] + "#"] = "", "17"
    exc, = render([{"tab": "b2c", "sc": {"state": "exception", "open": {}}}], [zero, sc(2, st="5", paid="10")])
    assert '<span class="fin-chip">סכום החזר 0</span>' in exc and '<span class="fin-chip">סכום החזר לא הוקלד</span>' in exc
    assert '<td class="k-channel hide-xs"><span class="fin-badtext fin-nw">תווית בלי שם</span></td>' in exc


# --- QA of slice 1 (11.11): one name for one thing, the legend, the details. Each failed before its fix -----------------

def test_lists_and_details_call_mondays_columns_by_their_titles_and_the_desks_state_by_its_own_word():
    """QA 2.5: "תאריך" held two things, "להחזיר" was "סכום לזיכוי", "ליד מקור" was missing, "סטטוס העסקה" is no monday
    column, "מצב" stood beside "מצב חיוב", and "לא ה-Account משלם של העסקה" did not read."""
    rows_sc = [sc(1, paid_date="2026-10-04", lead=["L1"]), sc(2, created="2026-10-06T08:00:00Z"),
               sc(3, credit_approved="1000", planned="2026-11-15", enr=["E3"])]
    rows_b2 = [b2(1, billing="0", payment="2", account="A2"), b2(2, payment="2", due="2026-09-01")]
    all_sc, refund, all_b2, to_bill = render([
        {"tab": "b2c", "sc": {"state": "", "open": {"1": True}}},
        {"tab": "b2c", "sc": {"state": "refund_to_execute", "open": {"3": True}}},
        {"tab": "b2b", "b2": {"state": "", "view": "list", "customer": "", "open": {"2": True}}},
        {"tab": "b2b", "b2": {"state": "to_bill", "view": "list", "customer": "", "open": {}}}],
        rows_sc, rows_b2, leads=[dict(lead("L1"), name="שם מהליד")], enrollments=[enrollment("E3", asked="2026-11-01")],
        accounts=[acct("A1"), acct("A2")], deals=[dict(deal("D1", value="96000"), **{fd.DL["stage"]: "בהרצה ללא חוזה"})])
    assert '<th class="k-date">תאריך תקבול</th>' in all_sc and '<th class="k-state">שיבוץ</th>' in all_sc
    assert '<td class="k-date">04.10.26</td>' in all_sc
    assert '<td class="k-date">06.10.26 <span class="fin-subtle">יום היצירה</span></td>' in all_sc
    # "ליד מקור" was added by QA 2.5 and left on 17.11 (Nadav: "זה לא אומר לה כלום ולא משנה")
    assert "<dt>ליד מקור</dt>" not in all_sc and "שם מהליד" not in all_sc
    assert "<b>סכום לזיכוי <bdi>1,000.00 ₪</bdi></b>" in refund and "להחזיר" not in refund
    assert '<th class="k-b2state">שיבוץ</th>' in all_b2 and ">מצב<" not in all_b2 + all_sc
    assert '<h3 class="fin-dx-h">עסקה <span class="fin-dx-name">עסקה D1</span></h3>' in all_b2
    assert "<dt>סטטוס</dt><dd>בהרצה ללא חוזה</dd><dt>שווי עסקה</dt><dd><bdi>96,000.00 ₪</bdi></dd>" in all_b2
    assert "<dt>Account משלם</dt><dd>לקוח A1</dd>" in all_b2 and "סטטוס העסקה" not in all_b2
    assert '<span class="fin-chip q">החברה אינה Account משלם בעסקה</span>' in to_bill and "לא ה-Account" not in to_bill


def test_the_tasks_list_has_its_sentence_on_both_tabs():
    """QA 2.1: "לטיפול" had no sentence, on either tab."""
    sc_html, b2_html = render([{"tab": "b2c", "sc": {"state": "tasks", "open": {}}},
                               {"tab": "b2b", "b2": {"state": "tasks", "view": "list", "customer": "", "open": {}}}],
                              [sc(1, receipt="0")], [b2(1, billing="0", payment="2")])
    for html, tab in ((sc_html, "b2c"), (b2_html, "b2b")):
        about = fd.TASKS_ABOUT[tab]
        assert '<p class="fin-about">%s</p>' % about.replace('"', "&quot;") in html
        assert all(fd.B2C_STATES[k]["label"] in about for k in fd.TASK_STATES) if tab == "b2c" else \
            all(fd.B2B_STATES[k]["label"] in about for k in fd.B2B_TASK_STATES)


def test_the_details_show_the_same_fields_in_the_same_places_on_every_row():
    """QA 2.9: the details' fields moved from row to row, "סוג חיוב" there on some rows only."""
    import re
    full = sc(1, ctype="השלמת תשלום", channel="אשראי", lead=["L1"], enr=["E1"])
    bare = sc(2, invoice="")
    rows_b2 = [dict(b2(1, payment="2", due="2026-09-01"), **{fd.B2["month"]: "2026-11", fd.B2["course"]: "A101",
                                                           fd.B2["component"]: "פעימות"}),
               b2(2, payment="1", due="", invoice="", deal="", account="")]
    html = render([{"tab": "b2c", "sc": {"state": "", "open": {"1": True, "2": True}}},
                   {"tab": "b2b", "b2": {"state": "", "view": "list", "customer": "", "open": {"1": True, "2": True}}}],
                  [full, bare], rows_b2, leads=[lead("L1")], enrollments=[enrollment("E1")])
    for tab_html in html:
        details = re.findall(r'<tr class="fin-detail">(.*?)</td></tr>', tab_html)
        assert len(details) == 2
        # the row's actions (round 3, slice 3) come first, before any fact; the rows of one list all offer the same ones,
        # so the facts sit in the same place on every row of a list
        assert all(x.find('class="fin-dx-block fin-acts"') == -1 or x.find('class="fin-dx-block fin-acts"') < x.find("<dl")
                   for x in details)
        facts_only = [re.sub(r'<section class="fin-dx-block fin-acts".*?</section>', "", x, flags=re.S) for x in details]
        fields = [[f.strip() for f in re.findall(r"<(?:dt|h3[^>]*)>([^<]+)", x)] for x in facts_only]
        assert fields[0] == fields[1] and len(fields[0]) >= 10, fields


def test_the_aging_band_and_the_tile_carry_different_names_and_the_legend_says_what_each_holds():
    """QA 2.4: the band "טרם הגיע מועד" (272,731) and the tile "חויב, טרם הגיע מועד הפירעון" (260,731): near-identical names,
    different numbers - the band also holds 3275685583, a charge in "חריג" that is still open money."""
    rows = [b2(1, payment="2", due="2026-11-05", amount="1000"),           # open: due 05.11 with no terms, today 09.11? no:
            b2(2, payment="2", due="2026-11-30", amount="12000")]          # billed before its demand day: "חריג", still owed
    v = fd.build_view(snap([sc(1)], rows, accounts=[acct("A1", "שוטף+30")]), TODAY)
    c = v["b2b"]["customers"][0]
    assert v["b2b"]["numbers"]["open"]["money"] == D(1000) and c["aging"]["current"] == D(13000)
    band, tile = fd.AGE_BANDS["current"], fd.B2B_STATES["open"]["label"]
    assert not set(band.replace(",", "").split()) & set(tile.replace(",", "").split()), (band, tile)
    aging, = render([{"tab": "b2b", "b2": {"state": "", "view": "customers", "customer": "", "open": {}}}],
                    [sc(1)], rows, accounts=[acct("A1", "שוטף+30")])
    assert '<th class="n k-aging_current fin-band fin-band-first">%s</th>' % band in aging and "טרם הגיע מועד</th>" not in aging
    said = dict((t, x) for p in v["b2b"]["legend"] for t, x in ((i["term"], i["text"]) for i in p["items"]))
    assert tile in said[band] and fd.B2C_STATES["exception"]["label"] in said[band]


# every word a tab draws as a label - a column, a group, a figure, a tile, a detail, a block, a chip - is monday's title, a
# word of the legend, or the page's own controls (QA 2.2: the legend missed words the screen used)
MONDAY_TITLES = {
    "b2c": {"סכום ששולם", "ערוץ תשלום", "אסמכתת חשבונית", "שליחת קבלה", "סכום לזיכוי", "אופן החזר", "תאריך החזר",
            "סכום החזר", "אסמכתת זיכוי", "תאריך בקשת ביטול", fd.PLANNED_LABEL, "סטטוס תשלום", "סוג חיוב", "תאריך תקבול",
            "ליד מקור", "הרשמה", 'סטטוס שובר משה"ע', fd.FLOW_LABEL},   # Deals' name for Cohorts' Registration Flow
    "b2b": {"מועד דרישת תשלום", "חברה", "עסקה", "קוד קורס", 'סכום לחיוב (ללא מע"מ)', "מצב חיוב", "סטטוס תשלום",
            "תנאי תשלום", "אסמכתת חשבונית", 'סכום ששולם (כולל מע"מ)', "סוג רכיב", "B2B2C Month", "סטטוס", "שווי עסקה",
            "Account משלם"},
}
CONTROLS = {"פתיחה במאנדיי", "הכל", "כל התקבולים", "כל החיובים"}


def _labels(html: str) -> list[str]:
    import html as h
    import re
    strip = lambda x: h.unescape(re.sub(r"<[^>]+>", "", x)).strip()       # noqa: E731
    html = re.sub(r'<section class="fin-legend".*?</section>', "", html, flags=re.S)   # the legend defines, it is no label
    found = [strip(x) for x in re.findall(r"<th[^>]*>(.*?)</th>", html, flags=re.S)]
    found += [strip(re.sub(r"<span[^>]*>.*?</span>", "", x)) for x in re.findall(r"<h2>(.*?)</h2>", html)]
    found += [strip(x) for x in re.findall(r'<span class="l">(.*?)</span>', html)]
    found += [strip(x) for x in re.findall(r"<dt>(.*?)</dt>", html)]
    found += [strip(re.sub(r'<span class="fin-dx-name">.*?</span>', "", x)) for x in re.findall(r'<h3 class="fin-dx-h">(.*?)</h3>', html)]
    found += [strip(x) for x in re.findall(r'<span class="fin-chip[^"]*">(.*?)</span>', html)]
    found += [strip(x) for x in re.findall(r'<span class="fin-statechip"[^>]*>(.*?)</span>', html)]
    return [x for x in found if x]


def _words_of(label: str) -> list[str]:
    """A label as the words it is made of: a group header and its column ("שולם חלקית: יתרה לגבייה"), a VAT note."""
    import re
    label = re.sub(r'\s*\((כולל|ללא) מע"מ\)$', "", label)
    return [w.strip() for w in label.split(": ")] if ": " in label and "שליחת קבלה: " not in label else [label]


def test_every_label_the_page_draws_is_mondays_title_or_in_the_legend():
    import re
    rows_sc = [sc(1, st="2", paid="4000", credit_approved="4000", planned="2026-11-01", enr=["E1"]),
               sc(2, credit_approved="1000", refund="1000", refund_date="2026-11-11"),
               sc(3, st="5", paid="10", refund="0"), sc(4, st="77"), sc(5, st="4", paid="4000", refund="9000"),
               sc(6, receipt="0", invoice=""), sc(7, st="9"), sc(8), sc(9, st="4", refund_date="2026-11-02"),
               sc(10, st="0", paid="300"), sc(11, paid="", lead=["L1"]), sc(12, credit_approved="500", planned="2026-11-20",
                                                                            enr=["E12"]),
               sc(13, st="5", paid="4000")]
    rows_b2 = [b2(1, billing="0", payment="2", amount="12000", due="2026-10-29", account="A2"),
               b2(2, payment="2", due="2026-09-01"), b2(3, payment="0", amount="1000", paid="590"),
               b2(4, payment="0", amount="2000"), b2(5, payment="2", due=""), b2(6, payment="2", due="2026-11-30"),
               b2(7, billing="1", payment="2", due="2026-12-20"), b2(8, billing="7", amount=""), b2(9, billing="2"),
               b2(10, billing="0", payment="1"), b2(11, billing="0", payment="2", amount=""), b2(12, payment="31"),
               b2(13, payment="2", due="2026-11-05", account="A3")]
    sc_states = ["tasks", ""] + list(fd.B2C_STATES)
    b2_states = ["tasks", ""] + list(fd.B2B_STATES)
    cases = [{"tab": "b2c", "legend": False, "sc": {"state": k, "open": {str(i): True for i in range(1, 14)}}} for k in sc_states]
    cases += [{"tab": "b2b", "legend": False, "b2": {"state": k, "view": "list", "customer": "", "open": {str(i): True for i in range(1, 14)}}}
              for k in b2_states]
    cases += [{"tab": "b2b", "legend": False, "b2": {"state": "", "view": "customers", "customer": "", "open": {}}},
              {"tab": "b2b", "legend": False, "b2": {"state": "", "view": "list", "customer": "A1", "open": {"2": True}}}]
    # no bank account from the server (NEVER_READ): the list "החזר לביצוע" has no column for one, and the page reads it
    # as the person when a refund's details open
    pages = render(cases, rows_sc, rows_b2, enrollments=[enrollment("E1", asked="2026-10-22"), enrollment("E12", asked="2026-11-01")],
                   leads=[lead("L1")], accounts=[acct("A1"), acct("A2", "שוטף+30"), acct("A3", "אחר")],
                   deals=[deal("D1", stage=fd.STAGE_NO_CONTRACT, value="1000")])
    chips = {tab: [re.compile("^" + re.escape(t).replace(re.escape("{status}"), '("[^"]*"|ריק)') + "$")
                   for _k, t, _x in fd.LEGEND_FLAGS[tab]] for tab in fd.LEGEND_FLAGS}
    missing = set()
    for case, html in zip(cases, pages):
        tab = case["tab"]
        known = fd.legend_terms(tab) | MONDAY_TITLES[tab] | CONTROLS
        for label in _labels(html):
            if label in known or re.fullmatch(r"ועוד \d+", label) or any(p.match(label) for p in chips[tab]):
                continue
            if not all(w in known for w in _words_of(label)):
                missing.add((tab, label))
    assert not missing, sorted(missing)
    # and the words the page writes beside a value are in the legend too
    for word in ("יום היצירה", "עבר", "חסר", "תווית בלי שם", "סכום מחוק", "היום", "בעוד", "לפני"):
        assert word in fd.legend_terms("b2c") | fd.legend_terms("b2b"), word


def test_every_chip_the_page_can_show_is_explained_in_its_tabs_legend():
    flags = node(LOAD + "console.log(JSON.stringify(F._flags()));", None)
    texts = {"b2c": {k: t for k, t, _x in fd.LEGEND_FLAGS["b2c"]}, "b2b": {k: t for k, t, _x in fd.LEGEND_FLAGS["b2b"]}}
    for tab in ("b2c", "b2b"):
        board = "sc" if tab == "b2c" else "b2"
        for key, text in texts[tab].items():
            assert (flags["on"].get(board, {}).get(key) or flags["all"].get(key)) == text, (tab, key)
    # no chip on the page without its explanation, on one tab or the other
    assert set(flags["all"]) <= set(texts["b2c"]) | set(texts["b2b"]), set(flags["all"]) - set(texts["b2c"]) - set(texts["b2b"])


# --- QA of slice 1 (11.11): the layout. Each failed before its fix -----------------------------------------------------

def test_the_bands_group_frames_all_its_bands_from_first_to_last():
    """QA 2.3: "טרם הגיע מועד" and "ללא מועד פירעון" seemed to sit outside the header "לפי מועד פירעון": the centred header
    had no visible extent. The group's line runs under all its columns, and its first and last bands carry its edges
    down the whole table, the totals row included."""
    import re
    html, = render([{"tab": "b2b", "b2": {"state": "", "view": "customers", "customer": "", "open": {}}}],
                   [sc(1)], [b2(1, payment="2", due="2026-09-01"), b2(2, payment="2", due=""), b2(3, payment="2", due="2026-12-01")])
    head = html[html.index("<thead>"):html.index("</thead>")]
    assert re.search(r'<th colspan="6" class="fin-group fin-band fin-band-first fin-band-last" scope="colgroup">לפי מועד פירעון</th>', head)
    headings = [fd.band_heading(k, v) for k, v in fd.AGE_BANDS.items()]
    bands = [b for b in re.findall(r'<th class="n k-[a-z0-9_]+ fin-band([^"]*)">([^<]+)</th>', head) if b[1] in headings]
    assert [b[1] for b in bands] == headings
    assert "fin-band-first" in bands[0][0] and "fin-band-last" in bands[-1][0]
    groups = head.count('class="fin-group')                      # each group frames its own columns the same way
    body = html[html.index("<tbody>"):html.index("</tbody>")]
    assert body.count("fin-band-first") == body.count("<tr") * groups == body.count("fin-band-last")
    css = (PAGE.parent / "finance.css").read_text(encoding="utf-8")
    assert ".fin-band-first { border-inline-start:" in css and ".fin-band-last { border-inline-end:" in css
    assert re.search(r"th\.fin-group \{[^}]*box-shadow: inset 0 -2px 0", css)


def test_the_charges_planned_for_later_say_what_to_look_at_too():
    """QA 2.7: "מעל שווי עסקה" was marked in "לחיוב החודש" and not in "לחיוב בחודש הבא ואילך", for charges of one deal."""
    rows = [b2(1, billing="1", payment="2", amount="12000", due="2026-11-20"),
            b2(2, billing="1", payment="2", amount="96000", due="2026-12-20")]
    planned, = render([{"tab": "b2b", "b2": {"state": "planned", "view": "list", "customer": "", "open": {}}}],
                      [sc(1)], rows, deals=[deal("D1", value="96000")])
    assert '<th class="k-notes">מה לבדוק</th>' in planned
    assert '<span class="fin-chip bad">מעל שווי עסקה</span>' in planned


def _aging_headers(html: str) -> list[str]:
    """The aging table's headers as the page stacks them, read left to right in its order: a grouped one as "group: header"."""
    import html as h
    import re
    head = html[html.index('<table class="fin-t fin-aging-t">'):]
    head = head[head.index("<thead>"):head.index("</thead>")]
    first, second = head.split('<tr class="fin-sub">')
    subs = [h.unescape(x) for x in re.findall(r"<th[^>]*>([^<]*)</th>", second)]
    out = []
    for attrs, text in re.findall(r"<th([^>]*)>([^<]*)</th>", first):
        n = re.search(r'colspan="(\d+)"', attrs)
        if n:
            out += ["%s: %s" % (h.unescape(text), subs.pop(0)) for _ in range(int(n.group(1)))]
        else:
            out.append(h.unescape(text))
    return out


def test_the_aging_files_headers_are_the_pages_for_the_same_companies(desk, monkeypatch):  # noqa: F811
    """QA 2.12: the "לפי לקוח" file called its columns otherwise than the page ('(ללא מע"מ)' on two, three columns the page
    did not have, the bands' group in the file only). Now one list of columns heads both, an optional column in both or
    in neither - also when the search leaves out the only company that has one."""
    import io
    import openpyxl
    rows = [b2(1, payment="2", due="2026-09-01", account="A1"), b2(2, payment="2", due="", account="A2"),
            b2(3, payment="0", amount="2000", account="A2"), b2(4, payment="0", amount="1000", paid="590", account="A3"),
            b2(5, payment="2", due="2026-11-05", account="A4")]          # open, not due before 30.12: no task at all
    accounts = [dict(acct("A1"), name="אלפא"), dict(acct("A2", "שוטף+30"), name="בטא"), dict(acct("A3"), name="גמא"),
                dict(acct("A4", "שוטף+30"), name="דלתא")]
    # the desk's reading answers this snapshot (analiza-metrics' FakeFinance stood for monday), on the rows' own day: on
    # the clock, A4's 30.12 would pass one day and give it a task
    served(monkeypatch, snap([sc(1)], rows, accounts=accounts))
    monkeypatch.setattr(dr, "today_in", lambda _tz: TODAY)
    auth = {"Authorization": "Bearer " + session_token(1)}                # a member of both charge boards
    body = desk.client.get("/api/desk", headers=auth).json()
    for q in ("", "אלפא", "גמא"):
        page, = node(LOAD + "console.log(JSON.stringify(input.cases.map(c => F._render(input.body, c))));",
                     {"body": body, "cases": [{"tab": "b2b", "query": q, "b2": {"state": "", "view": "customers", "customer": "", "open": {}}}]})
        url = desk.client.post("/api/desk/export-link", headers=auth, json={"tab": "b2b_customers", "q": q}).json()["url"]
        ws = openpyxl.load_workbook(io.BytesIO(desk.client.get(url).content))["לפי לקוח"]
        assert _aging_headers(page) == [x.value for x in ws[1]], q
        # and a count the page shows as 0 is 0 in the file, not a blank (the hostile review of the QA round)
        tasks = [x.value for x in ws[1]].index("לטיפול")
        shown = [cust for cust in body["view"]["b2b"]["customers"] if not q or q in cust["name"]]
        assert [ws.cell(row=i + 2, column=tasks + 1).value for i in range(len(shown))] == [cust["tasks"] for cust in shown]
    assert 0 in [cust["tasks"] for cust in body["view"]["b2b"]["customers"]]
    # with every company shown, the two optional columns are there; with only "אלפא", neither is
    every = _aging_headers(node(LOAD + "console.log(JSON.stringify(input.cases.map(c => F._render(input.body, c))));",
                                {"body": body, "cases": [{"tab": "b2b", "query": "", "b2": {"state": "", "view": "customers", "customer": "", "open": {}}}]})[0])
    assert "לפי מועד פירעון: ללא מועד פירעון" in every
    assert 'שולם חלקית: סכום לחיוב (ללא מע"מ), סכום ששולם (כולל מע"מ) לא הוקלד' in every


# --- D2, 1.2 (the desk move plan §1b, 24.11): a search looks at every row of its tab ------------------------------------
# It replaces 16.11's "חיפוש ללא סינון" (Chava, through Nadav: "לחפש בכל השיבוצים" read as the desk's jargon), which a
# search now does by itself: that test held the button, and its words, which are gone (2.1).

def test_a_search_looks_at_every_row_of_its_tab_and_a_row_outside_the_open_list_shows_its_tile():
    """Chava's four searches of 17-23.11 found nothing in the list that was open, and three would have found rows without
    its filters. A search looks at every row of the tab, whatever tile, period or company is open; a row outside the open
    list carries its own tile under its name; the head says the search went over every row and names the open list. The
    rows are the server's filter with no tile, period or company, in its order, so the totals and the Excel file of the
    search (asked so, tests/test_desk_page.py) hold what the screen shows. "השיבוצים" stays out of every word."""
    import re
    rows_sc = [sc(1, created="2026-10-02T09:00:00Z"), sc(2, receipt="0", created="2026-09-03T09:00:00Z"),
               dict(sc(3, created="2026-09-04T09:00:00Z"), name="דנה כהן")]
    rows_b2 = [b2(1, account="A1"), b2(2, billing="0", payment="2", account="A2")]
    view = fd.build_view(snap(rows_sc, rows_b2, accounts=[acct("A1"), acct("A2")]), TODAY)
    body = json_safe({"view": view, "taken_at": "2026-11-09T20:00:00+00:00", "age_seconds": 0, "refreshing": False,
                      "stale": False, "last_attempt": None})
    cases = [
        # the tasks of October: the payments of September and October both, each outside the open list
        {"tab": "b2c", "query": "תלמיד", "sc": {"state": "tasks", "period": "2026-10", "open": {}}},
        # every tile, the whole span: the open list is every payment, so nothing is outside it
        {"tab": "b2c", "query": "תלמיד/ה 1", "sc": {"state": "", "period": "", "open": {}}},
        # the open tile holds one of the two it finds
        {"tab": "b2c", "query": "תלמיד", "sc": {"state": "receipt_pending", "period": "", "open": {}}},
        {"tab": "b2c", "query": "zzz", "sc": {"state": "tasks", "period": "2026-10", "open": {}}},
        # one company's ledger and a search that finds a charge of another company, in another tile
        {"tab": "b2b", "query": "לקוח A2", "b2": {"state": "", "view": "list", "customer": "A1", "open": {}}},
        # no search, one company in one state: its ledger's way, unchanged
        {"tab": "b2b", "query": "", "b2": {"state": "tasks", "view": "list", "customer": "A1", "open": {}}},
    ]
    got = node(LOAD + "console.log(JSON.stringify(input.cases.map(c => F._render(input.body, c))));",
               {"body": body, "cases": cases})
    for html in got:
        assert "השיבוצים" not in html and "data-unfilter" not in html and "חיפוש ללא סינון" not in html
    tasks_oct, everything, one_tile, nothing, ledger, no_search = got
    shown = lambda html: re.findall(r'data-row="(\d+)"', html)                       # noqa: E731
    # the page's rows are the server's filter with no tile, period or company, in its order (the file's and the totals')
    for html, q in ((tasks_oct, "תלמיד"), (everything, "תלמיד/ה 1"), (one_tile, "תלמיד")):
        assert shown(html) == [r["id"] for r in fd.filter_b2c(view["b2c"], "", "", q)]
    assert shown(ledger) == [r["id"] for r in fd.filter_b2b(view["b2b"], "", "", "לקוח A2")] == ["2"]
    assert shown(tasks_oct) == ["1", "2"]
    assert "<h2>חיפוש בכל התקבולים</h2>" in tasks_oct
    assert "2 תקבולים · חיפוש: תלמיד · 2 מהם מחוץ לרשימה הפתוחה" in tasks_oct
    assert ("החיפוש עובר על כל התקבולים, לא רק על הרשימה הפתוחה (לטיפול · אוקטובר 2026). ליד שורה מחוץ לה כתוב השיבוץ "
            "שלה.") in tasks_oct
    where = re.findall(r'<span class="fin-where" title="מחוץ לרשימה הפתוחה"><span class="fin-statechip" [^>]*>([^<]+)</span>',
                       tasks_oct)
    assert where == [fd.B2C_STATES["done"]["label"], fd.B2C_STATES["receipt_pending"]["label"]]
    assert "fin-where" not in everything and "מהם מחוץ לרשימה הפתוחה" not in everything
    assert "תקבול אחד · חיפוש: תלמיד/ה 1</div>" in everything
    # the tile open holds the receipt to send; the payment done is outside it, and only it is marked
    assert "2 תקבולים · חיפוש: תלמיד · אחד מהם מחוץ לרשימה הפתוחה" in one_tile and one_tile.count('class="fin-where"') == 1
    assert "אין תקבולים שמתאימים לחיפוש." in nothing and "<button" not in nothing.split("fin-empty")[1].split("</div>")[0]
    # the ledger of A1, searched: every company's charges as the list of every charge, the other company's marked
    assert "<h2>חיפוש בכל החיובים</h2>" in ledger and "(כל החיובים · לקוח A1)" in ledger
    assert "חיוב אחד · חיפוש: לקוח A2 · אחד מהם מחוץ לרשימה הפתוחה" in ledger
    assert '<th class="k-account">חברה</th>' in ledger and '<th class="k-ledger n">' not in ledger
    assert '<button class="fin-linkbtn" type="button" data-b2state="">כל החיובים של החברה</button>' in no_search
    source = re.sub(r"/\*.*?\*/", "", PAGE.read_text(encoding="utf-8"), flags=re.S)      # a comment may tell the history
    assert "בכל השיבוצים" not in source and "בשאר השיבוצים" not in source
