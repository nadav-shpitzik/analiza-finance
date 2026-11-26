"""A small hand-made view of the desk, for the page's tests (tests/test_desk_page.py, tests/test_desk_browser.py).

It has the shape of analiza-metrics' /api/finance `view` (app/finance.py build_view, then visible_part for a reader of both
tabs) - the shape D2's GET /api/desk answers under `view` - written by hand: five payments and three charges, every state's
entry, the companies' aging and the billing form, and one or two actions per row. Names and numbers are invented and
generic. It is not the server's output: whatever this view holds, the server's port answers its own, and the tests that
hold the page to the server (its filters, its allowlist) wait for that port. What the server never sends is not here
either: no ID number, no bank account, no ח.פ (the page reads those from monday as the person)."""
from __future__ import annotations

import copy

SC_BOARD, B2_BOARD = "5093448677", "5097721228"
TODAY = "2026-11-24"
TAKEN = "2026-11-24T08:00:00+00:00"

B2C_STATES = [
    ("exception", "חריג, לבדיקה", True, "תקבולים שהמסך לא יכול לשבץ. ליד כל שורה כתוב מה לבדוק."),
    ("refund_to_execute", "החזר לביצוע", True, "החזרים שאושרו והכסף עוד לא יצא: יש סכום לזיכוי, ואין סכום החזר או תאריך החזר."),
    ("receipt_pending", "שולם, קבלה לטיפול", True, "נכנס כסף, והקבלה עליו עוד לא יצאה."),
    ("refund_asked", "החזר ממתין לאישור", False, "סטטוס תשלום \"ביקש החזר\", בלי סכום לזיכוי."),
    ("done", "הושלם", False, "הכסף נכנס, הקבלה יצאה, ואין החזר פתוח."),
    ("refunded", "החזר בוצע", False, "סטטוס תשלום \"תשלום הוחזר\" או \"הוחזר חלקי\"."),
    ("no_money", "ללא תקבול", False, "שורות שלא נכנס בהן כסף."),
]
B2B_STATES = [
    ("exception", "חריג, לבדיקה", True, "חיובים שהמסך לא יכול לשבץ."),
    ("to_bill", "לחיוב החודש", True, "מצב חיוב \"מאושר לחיוב\", ומועד דרישת התשלום עד סוף החודש."),
    ("overdue", "בפיגור", True, "מצב חיוב \"חויב\", סטטוס תשלום \"לא שולם\", ומועד הפירעון עבר."),
    ("partial", "שולם חלקית", True, "מצב חיוב \"חויב\" וסטטוס תשלום \"שולם חלקית\"."),
    ("open", "חויב, ממתין לפירעון", False, "מצב חיוב \"חויב\", ומועד הפירעון עוד לא הגיע."),
    ("planned", "לחיוב בחודש הבא ואילך", False, "מועד דרישת התשלום אחרי סוף החודש."),
    ("pending", "ממתין לנתונים", False, "החיוב עוד לא הגיע לאישור."),
    ("paid", "שולם", False, "מצב חיוב \"חויב\" וסטטוס תשלום \"שולם\"."),
    ("cancelled", "בוטל", False, "מצב חיוב \"בוטל\". לא נספר באף סכום."),
]

SC_ACTIONS = [
    {"key": "refund_done", "board": "sc", "button": "סימון \"תשלום הוחזר\"",
     "steps": [[{"column": "color_mm1kxpvt", "title": "סטטוס תשלום", "kind": "status", "index": "4", "label": "תשלום הוחזר"}]],
     "after": "אחרי השמירה אפליקציית ההחזרים סוגרת את הבקשה."},
    {"key": "receipt_sent", "board": "sc", "button": "שליחת קבלה: \"יצאה\"",
     "steps": [[{"column": "color_mm4cq1hw", "title": "שליחת קבלה", "kind": "status", "index": "1", "label": "יצאה"}]]},
]
B2_ACTIONS = [
    {"key": "b2b_paid", "board": "b2", "button": "סימון \"שולם\"",
     "steps": [[{"column": "color_mm4bhvta", "title": "סטטוס תשלום", "kind": "status", "index": "1", "label": "שולם"}]]},
]


def _states(entries: dict, keys: list, extra=()) -> dict:
    out = {}
    for k in keys:
        n, money = entries.get(k, (0, "0"))
        out[k] = {"count": n, "money": money}
        for f in extra:
            out[k][f] = money if f == "money_in" else "0"
    return out


def sc_row(id_, name, state, **kw) -> dict:
    row = {"id": id_, "board": "sc", "name": name, "created": "2026-11-02", "paid_date": "2026-11-03", "date": "2026-11-03",
           "date_from": "paid", "month": "2026-11", "status": "שולם", "status_idx": "1", "type": "קורס", "channel": "אשראי",
           "invoice": "", "receipt": "יצאה", "receipt_idx": "1", "money_in": "4000", "refunded": "0", "net": "4000",
           "credit_approved": "0", "planned": "", "asked_date": "", "refund_last_day": "", "enrollment_linked": True,
           "paid_typed": "4000", "counted": True, "refund_typed": None, "unnamed": [], "refund_method": "", "refund_date": "",
           "credit_ref": "", "voucher": "", "voucher_idx": "", "flags": [], "cohort_code": "A651",
           "cohort_title": "הגנת סייבר", "flow": "תשלום עצמי", "cohort_from": "enrollment", "person": "E:" + id_,
           "lead_ids": [], "rep": "נציג/ה", "state": state, "actions": []}
    row.update(kw)
    return row


def b2_row(id_, name, state, account_id, account, terms, **kw) -> dict:
    row = {"id": id_, "board": "b2", "name": name, "created": "2026-09-01", "billing": "חויב", "billing_idx": "6",
           "payment": "לא שולם", "payment_idx": "2", "amount": "15000", "bucket": "unpaid", "component": "פעימות",
           "month": "2026-11", "course": "A651", "demand": "2026-10-01", "due": "2026-10-31", "terms": terms, "days_late": None,
           "invoice": "", "flags": [], "unnamed": [], "paid_so_far": None, "balance": None, "amount_known": True,
           "open_money": "15000", "account_terms": terms, "account_no_terms": not terms, "account_id": account_id,
           "deal_id": "8801", "account": account, "deal": "עסקה 1", "deal_stage": "נחתם", "deal_value": "120000",
           "payer": account, "ctx": {"account": account, "deal": "עסקה 1"}, "age_band": "current", "ledger_key": "",
           "ledger_open": "15000", "state": state, "actions": []}
    row.update(kw)
    return row


def view() -> dict:
    sc_rows = [
        sc_row("3101", "תלמיד/ה 1", "done", invoice="30001", lead_ids=["6101"]),
        sc_row("3102", "תלמיד/ה 2", "receipt_pending", invoice="30002", receipt="טרם", receipt_idx="0", money_in="6500",
               paid_typed="6500", net="6500", lead_ids=["6102"], actions=["receipt_sent"]),
        sc_row("3103", "תלמיד/ה 3", "refund_to_execute", invoice="30003", money_in="3950", paid_typed="3950", net="3950",
               credit_approved="3950", planned="2026-11-27", asked_date="2026-11-12", refund_last_day="2026-11-26",
               refund_method="העברה בנקאית", flags=["refund_to_execute"], lead_ids=["6103"], actions=["refund_done"]),
        sc_row("3104", "תלמיד/ה 4", "exception", status="יש בעיה", status_idx="2", money_in="0", paid_typed="8200",
               counted=False, net="0", credit_approved="4000", flags=["credit_on_status"], lead_ids=[]),
        sc_row("3105", "תלמיד/ה 5", "refunded", status="תשלום הוחזר", status_idx="4", refunded="4000", net="0",
               refund_typed="4000", refund_date="2026-11-10", refund_method="העברה בנקאית", credit_approved="4000",
               lead_ids=["6105"]),
    ]
    sc_counts = {"exception": (1, "0"), "refund_to_execute": (1, "3950"), "receipt_pending": (1, "6500"),
                 "done": (1, "4000"), "refunded": (1, "4000")}
    keys = [s[0] for s in B2C_STATES]
    period_states = _states(sc_counts, keys, extra=("money_in", "refunded"))
    periods = [dict(key="", label="כל התקופה", kind="all", months=[], count=5, students=5, money_in="18450", refunded="4000",
                    states=copy.deepcopy(period_states)),
               dict(key="2026-11", label="נובמבר 2026", kind="month", months=["2026-11"], count=5, students=5,
                    money_in="18450", refunded="4000", states=copy.deepcopy(period_states))]

    accounts = [("7701", "חברה א", "שוטף+30"), ("7702", "חברה ב", "שוטף+60"), ("7703", "חברה ג", "")]
    b2_rows = [
        b2_row("9901", "חיוב 1 · 2026-10", "overdue", "7701", "חברה א", "שוטף+30", due="2026-10-31", days_late=24,
               invoice="501", age_band="d1_30", flags=["overdue"], actions=["b2b_paid"]),
        b2_row("9902", "חיוב 2 · 2026-11", "to_bill", "7703", "חברה ג", "", billing="מאושר לחיוב", billing_idx="1",
               amount="20000", open_money="0", demand="2026-11-20", due="", flags=["terms_missing"], age_band="no_due"),
        b2_row("9903", "חיוב 3 · 2026-11", "partial", "7702", "חברה ב", "שוטף+60", payment="שולם חלקית", payment_idx="0",
               amount="17500", paid_so_far="9000", balance="11650", open_money="7627.12", due="2026-12-31",
               age_band="current", invoice="503", actions=["b2b_paid"]),
    ]
    b2_counts = {"overdue": (1, "15000"), "to_bill": (1, "20000"), "partial": (1, "17500")}
    numbers = _states(b2_counts, [s[0] for s in B2B_STATES])
    numbers["partial"].update(balance="11650", balance_unknown=0)
    aging0 = {"current": "0", "d1_30": "0", "d31_60": "0", "d61_90": "0", "d90": "0", "no_due": "0"}

    def customer(aid, name, terms, aging, open_, overdue, to_bill, tasks, partial=("0", 0)):
        return {"id": aid, "name": name, "terms": terms, "terms_missing": not terms, "to_bill": to_bill,
                "aging": dict(aging0, **aging), "open": open_, "overdue": overdue, "oldest_days": 24,
                "partial_balance": partial[0], "partial_unknown": 0, "partial_unknown_amount": "0",
                "partial_count": partial[1], "exceptions": 0, "tasks": tasks}

    customers = [customer("7701", "חברה א", "שוטף+30", {"d1_30": "15000"}, "15000", "15000", "0", 1),
                 customer("7702", "חברה ב", "שוטף+60", {"current": "7627.12"}, "7627.12", "0", "0", 1, ("11650", 1)),
                 customer("7703", "חברה ג", "", {}, "0", "0", "20000", 1)]
    by_customer = {}
    for aid, name, terms in accounts:
        c = [x for x in customers if x["id"] == aid][0]
        mine = [r for r in b2_rows if r["account_id"] == aid]
        # the billing details as D2's server can send them: never the ח.פ, which it never reads
        by_customer[aid] = {"name": name, "terms": terms, "terms_missing": not terms,
                            "billing": {"hash_key": "H" + aid, "invoice_name": name + " בע\"מ", "terms": terms},
                            "count": len(mine), "numbers": _states({r["state"]: (1, r["amount"]) for r in mine},
                                                                   [s[0] for s in B2B_STATES]),
                            "open": c["open"], "overdue": c["overdue"], "to_bill": c["to_bill"], "aging": c["aging"]}
        by_customer[aid]["numbers"]["partial"].update(balance=c["partial_balance"], balance_unknown=0)

    band_heads = [("current", "לא בפיגור", "לא בפיגור"), ("d1_30", "1-30", "1-30 ימים"), ("d31_60", "31-60", "31-60 ימים"),
                  ("d61_90", "61-90", "61-90 ימים"), ("d90", "91+", "91+ ימים"),
                  ("no_due", "ללא מועד פירעון", "ללא מועד פירעון")]
    aging_columns = [
        {"key": "name", "header": "חברה", "group": "", "kind": "text", "shown_when": ""},
        {"key": "terms", "header": "תנאי תשלום", "group": "", "kind": "text", "shown_when": ""},
        {"key": "to_bill", "header": "לחיוב החודש (ללא מע\"מ)", "group": "", "kind": "money", "shown_when": ""},
        {"key": "partial_balance", "header": "יתרה לגבייה (כולל מע\"מ)", "group": "שולם חלקית", "kind": "money", "shown_when": ""},
        {"key": "tasks", "header": "לטיפול", "group": "", "kind": "int", "shown_when": ""}]
    aging_columns += [{"key": "aging_" + k, "header": h, "group": "לפי מועד פירעון", "kind": "money",
                       "shown_when": "aging_no_due" if k == "no_due" else ""} for k, _label, h in band_heads]
    aging_columns.append({"key": "open", "header": "סה\"כ יתרה פתוחה (ללא מע\"מ)", "group": "", "kind": "money", "shown_when": ""})

    def legend(tab_words):
        return [{"title": "מונחים וחישובים", "items": [{"term": t, "text": x} for t, x in tab_words]}]

    return {
        "schema": "finance/3", "read_at": TAKEN, "today": TODAY, "tabs": {"b2c": True, "b2b": True},
        "boards": {"student_charge": SC_BOARD, "b2b_charge": B2_BOARD},
        "checks": {"ok": True, "b2c_ok": True, "b2b_ok": True, "b2c_items_count": 5, "b2b_items_count": 3},
        "data_notes": {}, "label_drift": {},
        "b2c": {"states": [{"key": k, "label": label, "task": task, "about": about} for k, label, task, about in B2C_STATES],
                "tasks_about": "כל מה שמחכה לפעולה, יחד.", "periods": periods, "rows": sc_rows,
                "count": 5, "students": 5, "money_in": "18450", "refunded": "4000", "net": "14450", "since": "2026-10-01",
                "dated_by_payment": 5, "actions": {"board": SC_BOARD, "list": SC_ACTIONS},
                "legend": legend([("תקבול", "שורה בלוח Student Charge."), ("שיבוץ", "הרשימה שהמסך שם בה את התקבול.")])},
        "b2b": {"states": [{"key": k, "label": label, "task": task, "about": about} for k, label, task, about in B2B_STATES],
                "tasks_about": "כל מה שמחכה לפעולה, יחד.", "numbers": numbers, "count": 3, "open": "22627.12",
                "overdue": "15000", "overdue_no_terms": "0", "customers": customers, "by_customer": by_customer,
                "bands": [{"key": k, "label": label, "heading": h} for k, label, h in band_heads],
                "aging_columns": aging_columns, "rows": b2_rows, "deals": 1, "accounts_count": 3, "vat_rate": "1.18",
                "account_form": {
                    "board": "5089697343",
                    "fields": [{"key": "vat_id", "title": "ח.פ", "column": "text_mm3y3gm7", "kind": "text", "hint": ""},
                               {"key": "hash_key", "title": "מפתח חשבשבת", "column": "text_mm3yprqa", "kind": "text", "hint": ""},
                               {"key": "invoice_name", "title": "חשבונית על שם", "column": "text_mm3yfrew", "kind": "text",
                                "hint": ""},
                               {"key": "terms", "title": "תנאי תשלום", "column": "dropdown_mm3yd36d", "kind": "dropdown",
                                "hint": ""}],
                    "terms": [{"id": "1", "label": "שוטף"}, {"id": "2", "label": "שוטף+30"}, {"id": "3", "label": "שוטף+60"}],
                    "mon43": "", "vat_id_digits": 9, "hash_key_max": 15, "invoice_name_max": 120},
                "actions": {"board": B2_BOARD, "list": B2_ACTIONS},
                "legend": legend([("חיוב", "שורה בלוח B2B Charge."), ("מועד פירעון", "מועד דרישת התשלום ועוד תנאי התשלום.")])},
    }


def answer(v: dict | None = "default", **over) -> dict:
    """GET /api/desk's answer around a view (D2's contract): `view` (None: nothing read yet), when it was read, its age,
    whether a newer one is on its way, and D1's parts (`user`, `tabs`, `reading`)."""
    v = view() if v == "default" else v
    out = {"view": v, "taken_at": TAKEN if v is not None else None, "age_seconds": 0, "refreshing": False, "stale": False,
           "last_attempt": {"taken_at": TAKEN, "ok": True, "error": None} if v is not None else None,
           "user": {"id": "1", "view_only": False, "guest": False, "claims": []},
           "tabs": {"b2c": True, "b2b": True}, "reading": None}
    out.update(over)
    return out
