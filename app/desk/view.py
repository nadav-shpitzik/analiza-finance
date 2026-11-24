"""The finance desk ("כספים") - the two charge boards, grouped the way a bookkeeper reads money (Analiza-Monday backlog
0301 D2; the desk move plan).

Ported on 24.11 from analiza-metrics `app/finance.py` at `da671b2`, lines 1-1659: the desk's build, unchanged in what it
computes and in the shape it answers (VIEW_SCHEMA), onto this service's reading of monday (app/desk/reading.py
`read_desk`, which has the shape of the old `FinanceReader.read()`). The old reader (lines 1660 on) is not ported: the new
one exists. The boards, their columns and what a payment links to are the reading's own, imported from it, so what is read
and what is built from it can never disagree about which lead was needed. The comments below keep naming analiza-metrics'
files as they were there (frontend/finance.js and its tests, app/pipeline.py): the desk's page is ported from that page.

WHAT IT IS. A read-only board view for finance (analiza-metrics backlog 0280 and `docs/design/finance-desk-spec.md`,
2026-11-09): what waits for the bookkeeper today ("לטיפול", the cards that each open as a flat work list), and the whole
picture under it - students by funding track > cohort > student > charge, companies by account > deal > charge, what was
invoiced and what was not. Like the pipeline map it is a reading of the current state, not a metric: no version, no
period, no `metric_values`.

WHAT IT IS NOT. A writer. Nothing here changes monday, and nothing in this service does (ADR-0188): the page writes a few
columns from the browser as the person (DESK_ACTIONS, below, says which), and since ADR-0188 a customer's billing details
too, which analiza-metrics' server wrote (app/account_writer.py there, not ported; `account_form` here is only what the
page draws them with).

THE TRUTH RULES IT KEEPS (the same lessons as analiza-metrics app/pipeline.py):
1. Status labels are keyed by their monday INDEX (the label id), never by their text. The text is
   read for display, and a known index whose text changed is counted as `label_drift`.
2. Every whole-board read is counted twice, rows against `items_count`, and items read by id are
   counted against the ids asked for. A short read says so on the page instead of numbers.
3. Every row lands in exactly one group and one money bucket, and the totals are tied.
4. Money is Decimal end to end (`json_safe` sends it as a string). Student money includes VAT,
   company money does not, and the two are never added together (knowledge ADR-0095, rejected 3).
5. "Empty" and "broken" differ: a paid row without an amount, a refund above its payment, a price
   that is not one clean number - each is counted and shown, never read as zero.

WHAT IT NEVER HOLDS. Three values analiza-metrics' server read, and this one never does: they are in NEVER_READ
(app/monday/client.py), so no reading holds one and no row here can carry one.
- A refund's bank account: `bank` is None on every payment. The page reads the four columns in the browser as the person,
  for "החזר לביצוע" only; analiza-metrics read them live by id (`attach_bank`, `note_bank_unread`, `refund_ids`: not
  ported, nor the note `bank_unread`).
- A lead's ID number: the Excel file no longer carries one (app/desk/export.py), and the payment's details show it read in
  the browser as the person (ADR-0173). `export_lead_ids` and `with_id_numbers` are not ported; a payment keeps its
  `lead_ids`, which the page reads the number by.
- An account's business number (ח.פ, `AC["vat_id"]`, a sole proprietor's ID number): `vat_id` is None in every
  customer's billing details (`account_billing`), never "" - which would say the cell is empty.
"""
from __future__ import annotations

import dataclasses
import re
from collections import Counter, OrderedDict, defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from typing import Any, Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .reading import TAB_BOARDS
from .reading import (AC, ACCOUNTS_BOARD, B2, B2B_CHARGE, BOARDS, CO, COHORTS_BOARD, DEALS_BOARD, DL, EN,  # noqa: F401
                      ENROLLMENTS_BOARD, LD, LEADS_BOARD, SC, STUDENT_CHARGE, _uniq, cohort_ids, enrollment_cohorts,
                      enrollment_ids, lead_ids, lead_names_of, lead_rep, lead_reps_of, leads_deciding)

# 2: the students tab is a flat list by state (Nadav 10.11). 3 (round 3, 11.11): a refund's request day and its last
# day, one company state before billing, every state with its sentence, monday's own names, the legend. The same here:
# the answer's shape did not change in the move.
VIEW_SCHEMA = "finance/3"
ZERO = Decimal("0")

# the account's four billing details, which the ledger shows: the business number (`vat_id`) among them by name only - its
# value is never read here (NEVER_READ), and `account_billing` says it as None
BILLING_DETAILS = ("vat_id", "hash_key", "invoice_name", "terms")
STATUS_COLUMNS = {SC["status"], SC["receipt"], B2["billing"], B2["payment"], DL["stage"], EN["voucher"]}

# ---------------------------------------------------------------- labels by index (read live 2026-11-09)
PAY = {"0": "לא שולם", "1": "שולם", "2": "יש בעיה", "3": "מלגת אנליזה", "4": "תשלום הוחזר",
       "5": "הוחזר חלקי", "9": "ביקש החזר"}
P_UNPAID, P_PAID, P_PROBLEM, P_SCHOLARSHIP, P_REFUNDED, P_PART, P_ASKED = "0", "1", "2", "3", "4", "5", "9"
MONEY_IN = {P_PAID, P_ASKED, P_REFUNDED, P_PART}          # money was received on the row
REFUNDED = {P_REFUNDED, P_PART}
RECEIPT = {"0": "טרם", "1": "יצאה", "2": "לא רלוונטי"}
R_PENDING, R_SENT, R_NOT_RELEVANT = "0", "1", "2"
BILLING = {"0": "לאישור הנהלת חשבונות", "1": "מאושר לחיוב", "2": "בוטל", "6": "חויב", "7": "בספירת תלמידים",
           "9": "ממתין לסכום"}
B_APPROVAL, B_APPROVED, B_CANCELLED, B_BILLED = "0", "1", "2", "6"
B2B_PAY = {"0": "שולם חלקית", "1": "שולם", "2": "לא שולם"}
BP_PARTIAL, BP_PAID, BP_UNPAID = "0", "1", "2"
STAGE_NO_CONTRACT = "160"   # "בהרצה ללא חוזה" (app/pipeline.py DEAL_STAGES)
# Enrollments' "סטטוס שובר משה"ע" (color_mm5fr6d6), read live 2026-11-10. 5 is the grey empty label.
VOUCHER = {"0": "הוגש – ממתין להחלטה", "1": "אושר - ממתין לתשלום", "2": "נדחה", "3": "נדחה - קיבל מלגה מאנליזה",
           "4": "אושר - יתרה שולמה", "5": "", "6": "ביטל השתתפות - דחייה", "7": "ביטל השתתפות - אחר",
           "10": "לא רלוונטי", "17": "טרם הוגש"}
LABELS = {SC["status"]: PAY, SC["receipt"]: RECEIPT, B2["billing"]: BILLING, B2["payment"]: B2B_PAY,
          EN["voucher"]: VOUCHER}
# the status cells a row shows, by the row's field name: one that holds a value with no label is said, not shown empty
B2C_UNNAMED = {"status": SC["status"], "receipt": SC["receipt"], "channel": SC["channel"], "type": SC["ctype"],
               "refund_method": SC["refund_method"]}
B2B_UNNAMED = {"billing": B2["billing"], "payment": B2["payment"], "component": B2["component"]}
B2C_COLUMNS = set(SC.values()) | set(EN.values())   # the students tab's columns, for its label notes

# "אסמכתת חשבונית זיכוי" exists from 25.10 (knowledge MON-87). The card takes every refund from that
# day, as the 08.11 control proposed (§6.ב.5): the view "זיכוי חסר" misses refunds marked by hand.
CREDIT_REF_FROM = date(2026, 10, 25)

# A refund's legal last day, called "מועד אחרון להחזר" everywhere in the desk, its legend and its Excel files (Nadav
# 11.11): the enrollment's "תאריך בקשת ביטול" plus 14 calendar days. It MUST equal the day analiza-transfer-student
# counts Inna's queue to (`DUE_AFTER_DAYS = 14` in src/refund-read.js and public/refund-panel.js, verified
# 2026-11-11), and the page words its lateness as that app does. One computation, one name.
REFUND_DUE_DAYS = 14
LAST_DAY_LABEL = "מועד אחרון להחזר"


def refund_last_day(asked: date | None) -> date | None:
    """The request day plus 14 calendar days; no request day, no last day (unknown, which sorts first)."""
    return asked + timedelta(days=REFUND_DUE_DAYS) if asked else None


# A row where a refund is in play: asked for, approved or done. Its request day and its last day belong there and
# nowhere else (the Excel file fills them on these rows only).
REFUND_STATUSES = {P_REFUNDED, P_PART, P_ASKED}


def is_refund_row(row: dict) -> bool:
    return row["status_idx"] in REFUND_STATUSES or row["credit_approved"] > 0


# Student Charge's `date_mm8cp8gj`: the transfer round set when the refund is approved (10, 15, 20 or month end,
# knowledge MON-83). The name is monday's own since 11.11, when the column was renamed to it - its old title was the
# legal day's name above. frontend/finance.js PLANNED_LABEL and LAST_DAY_LABEL are held to these two by a test.
PLANNED_LABEL = "מועד העברה מתוכנן"

TERMS_DAYS = {"שוטף": 0, "שוטף+30": 30, "שוטף+45": 45, "שוטף+60": 60}
VAT_RATE = Decimal("1.18")   # Israel since 01.2025; a partly paid charge's balance assumes it for every customer
NO_ACCOUNT = "none"   # the customer id of a charge linked to no account


def _q(label: str) -> str:
    """A monday label inside a sentence: in quotes, exactly as monday spells it."""
    return '"%s"' % label


def _quoted(labels: list[str], conj: str = "או") -> str:
    """'"א", "ב" או "ג"' (or 'ו"ג"'): labels that hold commas of their own stay readable as a list."""
    qs = [_q(x) for x in labels]
    return qs[0] if len(qs) == 1 else ", ".join(qs[:-1]) + (" ו" if conj == "ו" else " %s " % conj) + qs[-1]


def _or_empty(labels: list[str]) -> str:
    """'"א", "ב" או ריק': a status whose empty cell counts with its labels."""
    return ", ".join(_q(x) for x in labels) + " או ריק"


# One state per company charge, the first that fits wins (Nadav 10.11, "3 תבנה": the companies tab like the students
# tab; knowledge docs/research/2026-11-10-finance-desk-bookkeeper-practice.md §3). A charge to bill whose "מועד דרישת
# תשלום" is after this month is "planned", not work yet (SAP B1's Future Remit). No reference decides a state - the
# Hashavshevet integration will fill them (as for the students).
# Round 3 (Nadav 11.11): "לאישור הנהלת חשבונות" and "מאושר לחיוב" are one state - "את חווה לא מעניין ממש הבדל" - and
# monday's verb is "חיוב", so the desk says "לחיוב", never "להפקה". `about` is the sentence the page shows above a
# state's list: what is in it and why, in monday's own labels (WAYS-OF-WORKING, "שם אחד לכל דבר, גם באפליקציות").
# The labels come from the dictionaries above, so a label and the sentences that name it cannot drift apart.
TO_BILL_LABEL = "לחיוב החודש"
_BILLED, _UNPAID = _q(BILLING[B_BILLED]), _q(B2B_PAY[BP_UNPAID])
_BEFORE_BILLING = _quoted([BILLING[B_APPROVAL], BILLING[B_APPROVED]])
# How `due_by_terms` counts a due date, in one sentence for the open state and the companies' Excel file: most companies
# have no terms (17 of 22 billed on 09.11), and theirs is the demand day itself (hostile review 11.11)
DUE_RULE = ("סוף החודש של מועד דרישת התשלום ועוד תנאי התשלום של החברה (%s); בלי אחד מהם, מועד דרישת התשלום עצמו"
            % _quoted(list(TERMS_DAYS)))
B2B_STATES = OrderedDict([
    ("exception", {"label": "חריג, לבדיקה", "task": True,
                   "about": "חיובים שהמסך לא יכול לשבץ: תווית במצב חיוב או בסטטוס תשלום שהמסך לא מכיר, סכום לחיוב "
                            "(ללא מע\"מ) שלא הוקלד, סטטוס תשלום %s לפני מצב חיוב %s, %s ולא שולם בלי מועד דרישת "
                            "תשלום, או %s ומועד דרישת התשלום עוד לא הגיע. ליד כל שורה כתוב מה לבדוק."
                            % (_q(B2B_PAY[BP_PAID]), _BILLED, _BILLED, _BILLED)}),
    ("to_bill", {"label": TO_BILL_LABEL, "task": True,
                 "about": "מצב חיוב %s, ומועד דרישת התשלום עד סוף החודש או ריק: החיובים לחייב עכשיו."
                          % _BEFORE_BILLING}),
    ("overdue", {"label": "בפיגור", "task": True,
                 "about": "מצב חיוב %s, סטטוס תשלום %s או ריק, ומועד הפירעון עבר." % (_BILLED, _UNPAID)}),
    ("partial", {"label": B2B_PAY[BP_PARTIAL], "task": True,
                 "about": "מצב חיוב %s וסטטוס תשלום %s. יתרה לגבייה מחושבת רק כשסכום ששולם (כולל מע\"מ) הוקלד. חיוב "
                          "כזה בלי מועד דרישת תשלום נשאר כאן, וליד מועד דרישת תשלום כתוב שהוא חסר."
                          % (_BILLED, _q(B2B_PAY[BP_PARTIAL]))}),
    ("open", {"label": "%s, טרם הגיע מועד הפירעון" % BILLING[B_BILLED], "task": False,
              # "עוד לא עבר", not "עוד לא [הגיע]": a charge due today is here too (it is late from tomorrow)
              "about": "מצב חיוב %s, סטטוס תשלום %s או ריק, מועד דרישת התשלום כבר הגיע ומועד הפירעון עוד לא עבר. "
                       "מועד הפירעון: %s." % (_BILLED, _UNPAID, DUE_RULE)}),
    ("planned", {"label": "לחיוב בחודש הבא ואילך", "task": False,
                 "about": "מצב חיוב %s, ומועד דרישת התשלום אחרי סוף החודש. בתחילת החודש של מועד דרישת התשלום "
                          "החיוב עובר ל%s." % (_BEFORE_BILLING, _q(TO_BILL_LABEL))}),
    ("pending", {"label": "ממתין לנתונים", "task": False,
                 # not "no amount yet": a typed amount may sit here, and the tile sums it
                 "about": "מצב חיוב %s: החיוב עוד לא הגיע ל%s." % (_or_empty([BILLING["9"], BILLING["7"]]),
                                                                    _q(BILLING[B_APPROVAL]))}),
    ("paid", {"label": B2B_PAY[BP_PAID], "task": False,
              "about": "מצב חיוב %s וסטטוס תשלום %s. חיוב כזה בלי מועד דרישת תשלום נשאר כאן, וליד מועד דרישת תשלום "
                       "כתוב שהוא חסר." % (_BILLED, _q(B2B_PAY[BP_PAID]))}),
    ("cancelled", {"label": BILLING[B_CANCELLED], "task": False,
                   "about": "מצב חיוב %s. לא נספר באף סכום." % _q(BILLING[B_CANCELLED])}),
])
B2B_TASK_STATES = [k for k, v in B2B_STATES.items() if v["task"]]
# the aging bands of open money, by days past the due date (the A/R aging report; Nadav 10.11 "תעשה את 2"). The first is
# "לא בפיגור", not "טרם הגיע מועד" (QA 11.11, 2.4): it also holds the open money of charges in "חריג, לבדיקה" whose due
# date has not passed, so it showed another number than the tile "חויב, טרם הגיע מועד הפירעון" under a near-identical name
AGE_BANDS = OrderedDict([("current", "לא בפיגור"), ("d1_30", "1-30"), ("d31_60", "31-60"), ("d61_90", "61-90"),
                         ("d90", "90+"), ("no_due", "ללא מועד פירעון")])


def band_heading(key: str, label: str) -> str:
    """An aging band as the page and the file head it: the counts of days say "ימים", the two named bands do not."""
    return label if key in ("current", "no_due") else label + " ימים"


def age_band(due_iso: str, today: date) -> str:
    due = day(due_iso)
    if not due:
        return "no_due"
    late = (today - due).days
    if late <= 0:
        return "current"
    return "d1_30" if late <= 30 else "d31_60" if late <= 60 else "d61_90" if late <= 90 else "d90"

NO_FLOW = "ללא מסלול"
LOCAL_TZ = ZoneInfo("Asia/Jerusalem")
HE_MONTHS = ["ינואר", "פברואר", "מרץ", "אפריל", "מאי", "יוני", "יולי", "אוגוסט", "ספטמבר", "אוקטובר", "נובמבר", "דצמבר"]

# One state per student payment, from where the bookkeeper stands, checked in this order: the first that fits wins
# (Nadav 10.11, "לפי סטטוסים אצלה"; knowledge docs/research/2026-11-10-finance-desk-bookkeeper-practice.md §2).
# `task` is her work; `money` is the amount the state is about. No reference decides a state - neither the invoice's
# nor the credit note's: Chava has typed none since she started (activity log, 25.10-10.11), the Hashavshevet
# integration will fill them, and she does not backfill the months before her (Nadav 10.11: "תוריד כרגע את מצב של
# האסמכתא לא נרשמה", and for the credit note "1 תוריד"). The `credit_ref_missing` flag stays on the row, as a fact.
_PAID, _PART = _q(PAY[P_PAID]), _q(PAY[P_PART])
_RECEIPT_DONE = _quoted([RECEIPT[R_SENT], RECEIPT[R_NOT_RELEVANT]])
_REFUNDED_LABELS = _quoted([PAY[P_REFUNDED], PAY[P_PART]])
# what keeps a payment out of the plain states: an approved refund or a refund typed (QA 11.11, 1.1, 1.2 and 2.11 - the
# sentence of "הושלם" was true of refunds to execute too, which are not there)
_NO_REFUND = "בלי סכום לזיכוי, סכום החזר או תאריך החזר"
_EXCEPTION_LABEL = "חריג, לבדיקה"
B2C_STATES = OrderedDict([
    ("exception", {"label": _EXCEPTION_LABEL, "task": True, "money": "money_in",
                   # "לשבץ", not "לספור": a refund above its payment is here and its money IS counted (capped)
                   "about": "תקבולים שהמסך לא יכול לשבץ: תווית בסטטוס תשלום או בשליחת קבלה שהמסך לא מכיר; סכום "
                            "ששולם שלא הוקלד בתקבול שנכנס בו כסף; סכום החזר גדול מסכום ששולם; %s בלי סכום החזר, או "
                            "עם 0; יש סכום לזיכוי, וסטטוס התשלום אינו %s; או סכום החזר או תאריך החזר, וסטטוס התשלום "
                            "אינו %s. ליד כל שורה כתוב מה לבדוק."
                            % (_PART, _quoted([PAY[P_PAID], PAY[P_REFUNDED], PAY[P_PART]]), _REFUNDED_LABELS)}),
    ("refund_to_execute", {"label": "החזר לביצוע", "task": True, "money": "credit_approved",
                           "about": "החזרים שאושרו והכסף עוד לא יצא: יש סכום לזיכוי, סטטוס תשלום עדיין %s, ואין סכום "
                                    "החזר או תאריך החזר. אחרי ההעברה מסמנים %s. בראש הרשימה: החזרים בלי תאריך בקשת "
                                    "ביטול, ש%s שלהם לא ידוע; אחריהם החזרים ש%s שלהם עבר, מהוותיק; ואז לפי %s, ובאותו "
                                    "%s לפי %s."
                                    % (_PAID, _REFUNDED_LABELS, LAST_DAY_LABEL, LAST_DAY_LABEL, PLANNED_LABEL,
                                       PLANNED_LABEL, LAST_DAY_LABEL)}),
    ("receipt_pending", {"label": "%s, שליחת קבלה: %s" % (PAY[P_PAID], RECEIPT[R_PENDING]), "task": True,
                         "money": "money_in",
                         # why, not only what (QA 7): money came in and its receipt has not gone out
                         "about": "נכנס כסף, והקבלה עליו עוד לא יצאה: סטטוס תשלום %s, ושליחת קבלה %s או ריקה, %s."
                                  % (_quoted([PAY[P_PAID], PAY[P_ASKED]]), _q(RECEIPT[R_PENDING]), _NO_REFUND)}),
    ("refund_asked", {"label": "%s, טרם אושר" % PAY[P_ASKED], "task": False, "money": "money_in",
                      "about": "סטטוס תשלום %s, ושליחת קבלה %s, %s. הבקשה עצמה מטופלת בהרשמה, באפליקציית ההחזרים."
                               % (_q(PAY[P_ASKED]), _RECEIPT_DONE, _NO_REFUND)}),
    ("done", {"label": "הושלם", "task": False, "money": "money_in",
              "about": "הכסף נכנס, הקבלה יצאה, ואין החזר פתוח: סטטוס תשלום %s, ושליחת קבלה %s, %s. החזר שאושר "
                       "נמצא ב\"החזר לביצוע\"." % (_PAID, _RECEIPT_DONE, _NO_REFUND)}),
    # QA 24.11: the sentence said '"הוחזר חלקי" בלי סכום החזר או עם 0, וסכום החזר גדול מסכום ששולם, נמצאים ב"חריג"', and
    # its "ו" read as one row with both. Two cases now, and every one there is (b2c_state: B2C_EXCEPTIONS on these
    # statuses, and "הוחזר חלקי" without an amount)
    ("refunded", {"label": "החזר בוצע", "task": False, "money": "refunded",
                  "about": "סטטוס תשלום %s. %s בלי סכום החזר או עם 0 נמצא ב%s, וגם תקבולים בשני הסטטוסים שסכום החזר "
                           "בהם גדול מסכום ששולם, שסכום ששולם בהם לא הוקלד, או שבשליחת קבלה שלהם תווית שהמסך לא מכיר."
                           % (_REFUNDED_LABELS, _PART, _q(_EXCEPTION_LABEL))}),
    ("no_money", {"label": "ללא תקבול", "task": False, "money": "money_in",
                  "about": "שורות שלא נכנס בהן כסף: סטטוס תשלום %s, %s."
                           % (_or_empty([PAY[P_UNPAID], PAY[P_PROBLEM], PAY[P_SCHOLARSHIP]]), _NO_REFUND)}),
])
TASK_STATES = [k for k, v in B2C_STATES.items() if v["task"]]

# "לטיפול" on each tab: the tile that sums her work, and the sentence above its list (QA 11.11, 2.1: it had none)
TASKS_ABOUT = {
    "b2c": "כל מה שמחכה לפעולה, יחד: %s. כל תקבול מופיע פעם אחת, ובעמודה שיבוץ כתוב באיזו משבצת הוא. מהחדש לישן."
           % _quoted([B2C_STATES[k]["label"] for k in TASK_STATES], "ו"),
    "b2b": "כל מה שמחכה לפעולה, יחד: %s. כל חיוב מופיע פעם אחת, ובעמודה שיבוץ כתוב באיזו משבצת הוא. ממועד דרישת "
           "התשלום הישן לחדש." % _quoted([B2B_STATES[k]["label"] for k in B2B_TASK_STATES], "ו"),
}
_NO_MONEY_LABELS = _or_empty([PAY[P_UNPAID], PAY[P_PROBLEM], PAY[P_SCHOLARSHIP]])
# the students' two money figures, the desk's own words for what it counts (the legend says how): what came in, and how
# much of it went back - the Excel file heads its counted refund by the second, beside monday's "סכום החזר" (QA 24.11)
RECEIVED_WORD, REFUNDED_WORD = "התקבלו", "הוחזרו"
_RELATIVE_DAYS = ("היום · בעוד · לפני", "ליד תאריך בפרטים: היום, או כמה ימים נותרו עד אליו, או כמה ימים עברו מאז.")
# what the page writes in a status cell that holds a value with no label (`unnamed_labels`), and what the Excel files write
# there too (QA 24.11: the file's cell was empty); and beside a company whose "תנאי תשלום" is empty, on the page and in
# the files alike
UNNAMED_LABEL, NO_TERMS = "תווית בלי שם", "אין תנאי תשלום"
_UNNAMED_WORD = (UNNAMED_LABEL, "ערך בעמודת סטטוס שאין לו תווית במאנדיי: התווית נמחקה, או שאין לה שם, ובמאנדיי הוא "
                                 "נראה כמו תא ריק. פותחים את השורה במאנדיי ובוחרים תווית.")

# The legend ("מקרא"), per tab, in four parts (QA 11.11, 2.2: words on the screen were missing from it). The page shows it
# under the "מקרא" button. A term that holds several words separates them with " · ".
# 1. The words the desk computes itself, and only those: anything monday holds is called by its monday name (round 3,
#    the one-name norm), so it needs no entry here - except where it comes from (part 4).
LEGEND = {
    "b2c": [
        ("תקבול", "שורה אחת בלוח Student Charge: תשלום אחד של סטודנט/ית."),
        ("סטודנט/ית", "השם של השורה בלוח Student Charge. לחיצה על השם פותחת אותה במאנדיי."),
        ("סטודנטים", "תקבולים שחולקים ליד מקור או הרשמה נספרים כסטודנט/ית אחד/ת."),
        ("שיבוץ", "הרשימה שהמסך שם בה כל תקבול, לפי סטטוס תשלום, שליחת קבלה, סכום לזיכוי, סכום החזר ותאריך החזר. כל "
                  "תקבול בשיבוץ אחד בלבד, הראשון שמתאים בסדר המשבצות, ולכן שום תקבול לא נספר פעמיים. השיבוצים "
                  "מוסברים כאן למטה, וכל אחד גם מעל הרשימה שלו."),
        ("לטיפול", TASKS_ABOUT["b2c"]),
        ("הכל", "כל התקבולים של התקופה שנבחרה, בכל השיבוצים, גם %s: סכום המשבצות. הרשימה שלו מהחדש לישן."
                % _q(B2C_STATES["no_money"]["label"])),
        ("יום היצירה", "ליד תאריך תקבול: תאריך תקבול לא הוקלד, ולכן מוצג היום (בשעון ישראל) שבו נוצרה השורה במאנדיי. "
                       "לפיו נקבעים גם החודש והתקופה של התקבול."),
        (LAST_DAY_LABEL, "תאריך בקשת ביטול בהרשמה ועוד %d יום, כמו באפליקציית ההחזרים. ליד התאריך: באיחור של כמה "
                         "ימים, היום הוא היום האחרון, או כמה ימים נותרו. בלי תאריך בקשת ביטול הוא לא ידוע, וההחזר "
                         "מופיע ראשון. הוא לא %s: זה נקבע באישור ההחזר (10, 15, 20 או סוף החודש), ויכול לבוא "
                         "אחריו." % (REFUND_DUE_DAYS, PLANNED_LABEL)),
        ("עבר", "ליד %s: היום הזה עבר, וההחזר עוד לא סומן %s." % (PLANNED_LABEL, _REFUNDED_LABELS)),
        ("החזר", "בפרטים של תקבול שיש בו החזר (ביקש החזר, אושר, בוצע, או שהוקלד בו סכום החזר או תאריך החזר): %s, %s "
                 "והעמודות של ההחזר בשורה. ב%s גם פרטי חשבון." % (LAST_DAY_LABEL, PLANNED_LABEL, _q("החזר לביצוע"))),
        _RELATIVE_DAYS,
        ("%s · %s" % (RECEIVED_WORD, REFUNDED_WORD),
         "סכום ששולם בתקבולים שנכנס בהם כסף (סטטוס תשלום %s), וסכום החזר מתוכם. %s בלי סכום "
         "החזר, או עם 0, נספר כהחזר של כל סכום ששולם, והחזר לא נספר מעבר לסכום ששולם."
         % (_quoted([PAY[k] for k in (P_PAID, P_ASKED, P_REFUNDED, P_PART)]), _q(PAY[P_REFUNDED]))),
        ("מחזור", "המחזור שההרשמה מקושרת אליו; כשבהרשמה אין מחזור, המחזור של הליד מקור. יותר ממחזור אחד, או אף "
                  "אחד: בלי מחזור, ופרטי התקבול אומרים למה."),
        # QA 24.11: "...שאינה מקלידה באישור ההחזר" (Inna types them as she approves) read as "which is not typing"
        ("פרטי חשבון", "ארבע עמודות הבנק בלוח Student Charge: שם בעל החשבון, בנק, סניף ומספר חשבון. הן ממולאות כשההחזר "
                       "מאושר, ומוצגות רק בפרטים של תקבול ב\"החזר לביצוע\": המסך קורא אותן ממאנדיי בשמך ובהרשאות שלך "
                       "כשהפרטים נפתחים, והן לא נשמרות במסך."),
        ("סכום מחוק", "סכום ששולם בתקבול שהסטטוס שלו לא מכניס כסף (%s): מוצג כמו שהוקלד, ולא נספר באף סכום."
                      % _NO_MONEY_LABELS),
        _UNNAMED_WORD,
        # D2, 1.1 (the desk move plan §1b): two of them on the row itself too, for one row or for several picked at once.
        # Their names are the buttons' (DESK_ACTIONS, below: "שליחת קבלה" and "סימון" with monday's labels)
        ("פעולות", "בפרטים של תקבול: מה שאפשר לכתוב ממנו במאנדיי בלי לצאת מהמסך. %s ו%s נמצאות גם בשורה עצמה, "
                   "בכפתור, ותיבה בתחילת השורה בוחרת כמה שורות לכתיבה אחת (עד 25). הכתיבה היא שלך, "
                   "בהרשאות שלך במאנדיי, ויומן הלוח רושם אותך. לפני הכתיבה המסך קורא את השורות במאנדיי ומראה בדיוק מה "
                   "ייכתב בכל אחת; שורה שהשתנתה במאנדיי מאז הקריאה לא נכתבת, והמסך אומר למה. אחרי הכתיבה הוא בודק "
                   "שהערך נשמר בכל שורה, אומר מה נכתב ומה לא, ומבקש קריאה חדשה של הלוחות."
                   % ("%s: %s" % ("שליחת קבלה", _q(RECEIPT[R_SENT])), "סימון %s" % _q(PAY[P_REFUNDED]))),
        ("נכתב, מתעדכן", "ליד השם של שורה: היא נכתבה מכאן לפני רגע, והמסך מחכה לקריאה חדשה של הלוחות כדי לשבץ אותה "
                         "מחדש. עד אז היא מוצגת עם הערכים שנכתבו, עד שלוש דקות."),
    ],
    "b2b": [
        ("חיוב", "שורה אחת בלוח B2B Charge. לחיצה על השם שלה פותחת אותה במאנדיי."),
        ("שיבוץ", "הרשימה שהמסך שם בה כל חיוב, לפי מצב חיוב, סטטוס תשלום, מועד דרישת תשלום ותנאי תשלום. כל חיוב "
                  "בשיבוץ אחד בלבד, הראשון שמתאים בסדר המשבצות, ולכן שום חיוב לא נספר פעמיים. השיבוצים מוסברים כאן "
                  "למטה, וכל אחד גם מעל הרשימה שלו."),
        ("לטיפול", TASKS_ABOUT["b2b"]),
        ("הכל", "כל החיובים, בכל השיבוצים, גם %s: סכום המשבצות. הרשימה שלו ממועד דרישת התשלום החדש לישן. כשנבחרה "
                "חברה: כל החיובים שלה, מהישן לחדש, כמו בכרטסת שלה." % _q(B2B_STATES["cancelled"]["label"])),
        ("מועד פירעון", "לפי תנאי התשלום של החברה: שוטף הוא סוף החודש של מועד דרישת התשלום, ושוטף+30, שוטף+45 "
                        "ושוטף+60 הם סוף החודש ועוד 30, 45 או 60 יום. בלי תנאי תשלום, או \"אחר\": מועד דרישת "
                        "התשלום עצמו. ליד מועד הפירעון כתובים תנאי התשלום, או %s." % _q(NO_TERMS)),
        # two entries, not one: "מתוכה בפיגור" is the money of "בפיגור" only, while days late are counted on a partly paid
        # charge too (the hostile review of the QA round: one sentence said both of both)
        ("מתוכה בפיגור", "מתוך היתרה הפתוחה: החיובים ב%s, שמועד הפירעון שלהם עבר. ליד הסכום: כמה ממנו בחברות בלי "
                         "תנאי תשלום, שבהן מועד הפירעון הוא מועד דרישת התשלום עצמו." % _q(B2B_STATES["overdue"]["label"])),
        ("ימים בפיגור", "כמה ימים עברו ממועד הפירעון, גם בחיוב ששולם חלקית ונשארה בו יתרה לגבייה."),
        ("יתרה פתוחה · סה\"כ יתרה פתוחה · יתרה פתוחה מצטברת",
         "סכום לחיוב (ללא מע\"מ) של כל חיוב במצב חיוב %s וסטטוס תשלום %s או ריק, עם מועד פירעון או בלי. חיוב ששולם "
         "חלקית לא נכלל בה. בכרטסת של חברה, יתרה פתוחה מצטברת היא היתרה עד החיוב הזה, לפי סדר מועד דרישת התשלום."
         % (_BILLED, _UNPAID)),
        ("לפי מועד פירעון", "היתרה הפתוחה של כל חברה, לפי הימים שעברו ממועד הפירעון. סכום העמודות שתחת הכותרת הוא "
                            "סה\"כ יתרה פתוחה."),
        ("לא בפיגור", "יתרה פתוחה שמועד הפירעון שלה היום או אחריו. היא כוללת את %s, וגם חיובים ב%s שחויבו ולא שולמו "
                      "ומועד הפירעון שלהם לא עבר, ולכן היא יכולה להיות גדולה מהמשבצת %s."
                      % (_q(B2B_STATES["open"]["label"]), _q(_EXCEPTION_LABEL), _q(B2B_STATES["open"]["label"]))),
        ("1-30 ימים · 31-60 ימים · 61-90 ימים · 90+ ימים", "יתרה פתוחה לפי כמה ימים עברו ממועד הפירעון."),
        ("ללא מועד פירעון", "יתרה פתוחה של חיובים שחויבו בלי מועד דרישת תשלום, ולכן אין להם מועד פירעון. הם ב%s."
                            % _q(_EXCEPTION_LABEL)),
        ("יתרה לגבייה", "בחיוב ששולם חלקית: סכום לחיוב (ללא מע\"מ) כפול %s, פחות סכום ששולם (כולל מע\"מ). כשסכום "
                        "ששולם (כולל מע\"מ) לא הוקלד אין יתרה, והמסך אומר כמה חיובים כאלה יש ומה סך סכום לחיוב (ללא "
                        "מע\"מ) שלהם." % VAT_RATE),
        ("סכום לחיוב (ללא מע\"מ), סכום ששולם (כולל מע\"מ) לא הוקלד",
         "ב\"לפי לקוח\", תחת %s: סך סכום לחיוב (ללא מע\"מ) של החיובים ששולמו חלקית ואין בהם סכום ששולם (כולל מע\"מ), "
         "ולכן אין להם יתרה לגבייה. העמודה מופיעה רק כשיש חיובים כאלה." % _q(B2B_PAY[BP_PARTIAL])),
        ("לפי לקוח · חברות עם משהו פתוח", "כל חברה עם משהו לחיוב החודש, ביתרה פתוחה, ששולם חלקית או ב%s. לחיצה על "
                                          "חברה פותחת את הכרטסת שלה: כל החיובים שלה, מהישן לחדש."
                                          % _q(_EXCEPTION_LABEL)),
        ("חסר", "במקום מועד דרישת תשלום: החיוב במצב חיוב %s, ואין לו מועד דרישת תשלום." % _BILLED),
        _RELATIVE_DAYS,
        ("סכום מחוק", "סכום לחיוב (ללא מע\"מ) של חיוב במצב חיוב %s: מוצג, ולא נספר באף סכום."
                      % _q(BILLING[B_CANCELLED])),
        _UNNAMED_WORD,
        ("פעולות", "בפרטים של חיוב: מה שאפשר לכתוב ממנו במאנדיי בלי לצאת מהמסך. הכתיבה היא שלך, בהרשאות שלך במאנדיי, "
                   "ויומן הלוח רושם אותך. לפני הכתיבה המסך קורא את השורה במאנדיי ומראה בדיוק מה ייכתב; אחריה הוא "
                   "בודק שהערך נשמר, ומבקש קריאה חדשה של הלוחות."),
        ("נכתב, מתעדכן", "ליד השם של שורה: היא נכתבה מכאן לפני רגע, והמסך מחכה לקריאה חדשה של הלוחות כדי לשבץ אותה "
                         "מחדש. עד אז היא מוצגת עם הערכים שנכתבו, עד שלוש דקות."),
        # ADR-0188: the edit is the person's own, from the browser, like every action; in analiza-metrics the server wrote
        # it ("נכתבת בידי השרת של המסך ולא בשמך, כי ללוח החברות אין גישה בלי מושב CRM"), which this service never does.
        # What was there before is kept by monday's activity log of the board (MON-41); the old Update on the company is
        # written only when the app holds the permission to write updates (updates:write), which it does not ask for
        ("פרטי חיוב", "בכרטסת של חברה: ח.פ, מפתח חשבשבת, חשבונית על שם ותנאי תשלום, ארבע עמודות של החברה בלוח החברות. "
                      "עריכה שלהן נכתבת מהדפדפן בשמך, בהרשאות שלך במאנדיי, כמו שאר הפעולות, ויומן הפעילות של לוח "
                      "החברות שומר מה היה ומה נכתב. עדכון על החברה, עם מה שהיה ומה שנכתב, נכתב בשמך רק כשלאפליקציה "
                      "יש הרשאה לכתוב עדכונים במאנדיי."),
    ],
}

# 2. The states: each tile's name and its sentence, as above its list (built from B2C_STATES / B2B_STATES).
# 3. "מה לבדוק": what each chip on a row says. The text is the chip's on the page (frontend/finance.js FLAG, held to this by
#    tests/test_finance_js.py); "{status}" is the row's own "סטטוס תשלום", in quotes.
_REFUND_NOT_MARKED = "וההחזר עוד לא סומן %s" % _REFUNDED_LABELS
LEGEND_FLAGS = {
    "b2c": [
        # "נבנה", not "נכתב", which is the word of the desk's own writes ("נכתב, מתעדכן"; QA 24.11)
        ("status_unknown", "תווית שהמסך לא מכיר", "בסטטוס תשלום או בשליחת קבלה יש תווית שנוספה במאנדיי אחרי שהמסך "
                                                    "נבנה. התקבול לא נספר בכסף עד שהמסך יכיר אותה."),
        ("credit_on_status", "סכום לזיכוי בסטטוס {status}",
         "יש סכום לזיכוי, כלומר החזר שאושר, וסטטוס התשלום אינו %s: החזר מאושר יוצא משורה בסטטוס %s, %s. %s ו%s "
         "שלו ממשיכים לרוץ." % (_PAID, _PAID, _REFUND_NOT_MARKED, LAST_DAY_LABEL, PLANNED_LABEL)),
        ("refund_amount_on_status", "סכום החזר בסטטוס {status}",
         "הוקלד סכום החזר, וסטטוס התשלום אינו %s. אחד מהם לא נכון." % _REFUNDED_LABELS),
        ("refund_date_on_status", "תאריך החזר בסטטוס {status}",
         "הוקלד תאריך החזר, וסטטוס התשלום אינו %s. אחד מהם לא נכון." % _REFUNDED_LABELS),
        ("refund_last_day_passed", LAST_DAY_LABEL + " עבר", "היום האחרון להחזר לפי החוק עבר, %s." % _REFUND_NOT_MARKED),
        ("refund_overdue", PLANNED_LABEL + " עבר", "מועד ההעברה שנקבע באישור עבר, %s." % _REFUND_NOT_MARKED),
        ("refund_exceeds_paid", "סכום החזר גדול מסכום ששולם", "ההחזר נספר עד סכום ששולם."),
        ("refund_last_day_unknown", LAST_DAY_LABEL + " לא ידוע", "אין תאריך בקשת ביטול: התקבול לא מקושר להרשמה, או "
                                                                 "שבהרשמה אין תאריך. הפרטים אומרים איזה מהשניים."),
        ("invoice_missing", "חסרה אסמכתת חשבונית", "סטטוס תשלום %s, ואסמכתת חשבונית ריקה. לא משנה את השיבוץ: את "
                                                   "האסמכתאות ימלא החיבור לחשבשבת." % _PAID),
        ("receipt_pending", "שליחת קבלה: טרם", "סטטוס תשלום %s, ושליחת קבלה %s או ריקה." % (_PAID, _q(RECEIPT[R_PENDING]))),
        ("credit_ref_missing", "חסרה אסמכתת זיכוי", "תשלום שהוחזר מ-%s ואילך, ואסמכתת זיכוי ריקה. לא משנה את השיבוץ."
                                                     % CREDIT_REF_FROM.strftime("%d.%m.%Y")),
        ("refund_amount_missing", "סכום החזר לא הוקלד", "סטטוס התשלום אומר שהוחזר, וסכום החזר ריק. ב%s נספר כל סכום "
                                                        "ששולם; %s עובר ל%s." % (_q(PAY[P_REFUNDED]), _PART,
                                                                                 _q(_EXCEPTION_LABEL))),
        ("refund_amount_zero", "סכום החזר 0", "סטטוס התשלום אומר שהוחזר, וסכום החזר הוא 0. ב%s נספר כל סכום ששולם; %s "
                                              "עובר ל%s." % (_q(PAY[P_REFUNDED]), _PART, _q(_EXCEPTION_LABEL))),
        ("amount_missing", "סכום ששולם לא הוקלד", "בתקבול שנכנס בו כסף, סכום ששולם ריק."),
        ("unlinked", "בלי הרשמה ובלי ליד מקור", "התקבול לא מקושר להרשמה וגם לא לליד מקור, ולכן אין לו מחזור."),
    ],
    "b2b": [
        ("status_unknown", "תווית שהמסך לא מכיר", "במצב חיוב או בסטטוס תשלום יש תווית שנוספה במאנדיי אחרי שהמסך נבנה. "
                                                    "החיוב לא נספר ביתרה הפתוחה עד שהמסך יכיר אותה."),
        ("invoice_late", "מועד דרישת התשלום עבר", "מועד דרישת התשלום עבר, והחיוב עוד לא חויב."),
        ("no_contract", "בהרצה ללא חוזה", "העסקה בסטטוס \"בהרצה ללא חוזה\"."),
        ("over_deal_value", "מעל שווי עסקה", "סך סכום לחיוב (ללא מע\"מ) של חיובי העסקה עובר את שווי עסקה."),
        ("billed_no_due", "חויב בלי מועד דרישת תשלום", "מצב חיוב %s ואין מועד דרישת תשלום, ולכן גם אין מועד פירעון. "
                                                       "בפרטים: אסמכתת חשבונית, כדי למצוא את החשבונית." % _BILLED),
        ("billed_before_demand", "חויב, ומועד דרישת התשלום עוד לא הגיע", "מצב חיוב %s, ומועד דרישת התשלום אחרי היום: "
                                                                          "התאריך או המצב לא נכונים." % _BILLED),
        ("partial_amount_unknown", "סכום ששולם (כולל מע\"מ) לא הוקלד", "סטטוס תשלום %s, ואין סכום ששולם (כולל "
                                                                       "מע\"מ), ולכן אין יתרה לגבייה."
                                                                       % _q(B2B_PAY[BP_PARTIAL])),
        ("billed_no_invoice", "חסרה אסמכתת חשבונית", "מצב חיוב %s, ואסמכתת חשבונית ריקה. לא משנה את השיבוץ." % _BILLED),
        ("paid_not_billed", "\"שולם\" לפני \"חויב\"", "סטטוס תשלום %s, ומצב חיוב אינו %s." % (_q(B2B_PAY[BP_PAID]), _BILLED)),
        ("payer_mismatch", "החברה אינה Account משלם בעסקה", "החברה בחיוב אחרת מ-Account משלם בעסקה."),
        ("terms_missing", NO_TERMS, "תנאי תשלום של החברה ריק בלוח החברות, ולכן מועד הפירעון הוא מועד דרישת "
                                            "התשלום עצמו, וממנו נספר גם הפיגור. מופיע גם ליד מועד הפירעון, ליד החברה "
                                            "ב\"לפי לקוח\" ובכרטסת שלה."),
        ("amount_missing", "סכום לחיוב (ללא מע\"מ) לא הוקלד", "חיוב לחייב, או שכבר חויב, בלי סכום."),
    ],
}

# 4. monday's own titles that need a word: where each comes from, and the English ones as they are (renaming them in
#    monday is a gated write, proposed to Nadav). Shown by their monday names everywhere on the page - "Registration Flow"
#    by its name on the Deals board since 17.11 (FLOW_LABEL).
FLOW_LABEL = "נוהל הרשמה"
LEGEND_MONDAY = {
    "b2c": [
        # no longer a field of the details (17.11); kept here because the notes and the cohort's line still name it
        ("ליד מקור", "עמודה בלוח Student Charge: הליד בלוח הלידים שממנו בא התקבול."),
        # read in the browser as the person, never by this server's answer (16.11; frontend/finance.js idCell). The
        # Excel file no longer carries it (app/desk/export.py), so its last sentence lost ", כמו בקובץ האקסל של התקבולים"
        ("ת.ז", "עמודה בלוח הלידים, של הליד מקור (בלוח Student Charge יש לה מראה באותו שם). בפרטים היא נקראת ממאנדיי "
                "בשמך ובהרשאות שלך כשהפרטים נפתחים, ולכן מוצגת רק למי שיש לו גישה ללוח הלידים, ולא נשמרת במסך. "
                "כשלתקבול כמה לידים: הראשון שיש בו ת.ז."),
        ("נציג מכירות", "עמודה בלוח Student Charge, מראה של Owner בלוח הלידים, של הליד מקור. המסך קורא אותה מהליד "
                        "עצמו, ולכן זה מי שהליד אצלו היום, לא בהכרח מי שמכר. ליד שעבר לנציג אחר מתעדכן כאן "
                        "לכל היותר יום וחצי אחרי."),
        ("הרשמה", "עמודה בלוח Student Charge: ההרשמה בלוח ההרשמות (Enrollments) שהתקבול שייך לה. בפרטים, תחת "
                  "\"הרשמה\": מה שבא ממנה."),
        ("סטטוס שובר משה\"ע", "עמודה בלוח ההרשמות, של ההרשמה שהתקבול מקושר אליה. בלוח Student Charge יש לה מראה בשם "
                              "\"סטטוס שובר (מההרשמה)\": אותו ערך, בשני שמות במאנדיי."),
        ("תאריך בקשת ביטול", "עמודה בלוח ההרשמות: היום שבו הסטודנט/ית ביקש/ה לבטל. ממנו נספר %s." % LAST_DAY_LABEL),
        # 17.11 (Nadav: "Registration Flow - שים משהו בעברית"): the desk says it by the name the same field carries on the
        # Deals board, "נוהל הרשמה" (color_mm4jykdh, carried to the cohort's column by its values). The Cohorts board is
        # not renamed: another team's board keeps its names (WOW, the one-name norm's exception, 11.11)
        (FLOW_LABEL, "עמודה בלוח המחזורים, של המחזור שהתקבול שויך אליו: המסלול שבו נרשמים למחזור, למשל \"שוברים משרד "
                     "העבודה\". בלוח העסקאות היא נקראת \"נוהל הרשמה\", ובלוח המחזורים Registration Flow: אותו שדה, באותם "
                     "ערכים. בפרטים, תחת \"מחזור\"."),
    ],
    "b2b": [
        ("חברה", "עמודה בלוח B2B Charge: החברה בלוח החברות (Accounts) שהחיוב רשום עליה."),
        ("תנאי תשלום", "עמודה בלוח החברות, של החברה בחיוב. ממנה נספר מועד פירעון."),
        ("ח.פ", "עמודה בלוח החברות, של החברה בחיוב: המספר שעל החשבונית. בכרטסת, תחת פרטי חיוב."),
        ("מפתח חשבשבת", "עמודה בלוח החברות, של החברה בחיוב: מספר הכרטיס שלה בחשבשבת. חברה עם כמה כרטיסים באותו ח.פ "
                        "מקבלת ח.פ בלי מפתח. בכרטסת, תחת פרטי חיוב."),   # MON-43
        ("חשבונית על שם", "עמודה בלוח החברות, של החברה בחיוב: השם שהחשבונית יוצאת עליו. בכרטסת, תחת פרטי חיוב."),
        ("עסקה", "עמודה בלוח B2B Charge: העסקה בלוח העסקאות (Deals) שהחיוב שייך לה. בפרטים, תחת \"עסקה\": סטטוס, "
                 "שווי עסקה ו-Account משלם שלה."),
        ("סטטוס", "עמודה בלוח העסקאות: הסטטוס של העסקה, למשל \"בהרצה ללא חוזה\". בפרטים, תחת \"עסקה\"."),
        ("שווי עסקה", "עמודה בלוח העסקאות. כשסך סכום לחיוב (ללא מע\"מ) של חיובי העסקה עובר אותו, מסומן \"מעל שווי "
                      "עסקה\"."),
        ("Account משלם", "עמודה בלוח העסקאות: החברה שמשלמת על העסקה. כשהחברה בחיוב אחרת, מסומן \"החברה אינה Account "
                         "משלם בעסקה\"."),
        ("B2B2C Month", "עמודה בלוח B2B Charge: החודש שהחיוב שייך לו, בכתיב שנה-חודש."),
    ],
}
LEGEND_TITLES = ("מילים שהמסך מחשב", "השיבוצים", "מה לבדוק", "עמודות של מאנדיי, ומאיפה הן")
WHAT_TO_CHECK = {
    "b2c": "עמודה ב%s: מה המסך מצא בתקבול. נקודה אדומה: סתירה בין עמודות, או מועד שעבר." % _q(_EXCEPTION_LABEL),
    "b2b": "עמודה ב%s, %s ו%s: מה המסך מצא בחיוב. נקודה אדומה: סתירה בין עמודות, או מועד שעבר."
           % (_q(_EXCEPTION_LABEL), _q(TO_BILL_LABEL), _q(B2B_STATES["planned"]["label"])),
}


def flag_legend_term(text: str) -> str:
    """A chip as the legend names it: the row's own status is shown as "...", since it changes from row to row."""
    return text.replace("{status}", "\"...\"")


# The order the page lists a row's chips in its "מה לבדוק" column (frontend/finance.js FLAG_ORDER, held to this by
# tests/test_desk_parity_js.py): contradictions and deadlines first, then what is missing, then the rest. Every chip of
# LEGEND_FLAGS is in it once. The Excel files list a row's reasons in this order and in the chips' words (`row_reasons`).
FLAG_ORDER = ["status_unknown", "credit_on_status", "refund_amount_on_status", "refund_date_on_status",
              "refund_last_day_passed", "refund_overdue", "refund_exceeds_paid", "invoice_late", "no_contract",
              "over_deal_value", "refund_last_day_unknown", "invoice_missing", "billed_no_invoice", "receipt_pending",
              "credit_ref_missing", "refund_amount_missing", "refund_amount_zero", "billed_no_due", "billed_before_demand",
              "partial_amount_unknown", "amount_missing", "terms_missing", "unlinked", "paid_not_billed", "payer_mismatch"]


def status_word(row: dict) -> str:
    """A payment's "סטטוס תשלום" inside a chip: in quotes, as monday spells it, or "ריק" (finance.js statusWord)."""
    return _q(row["status"]) if row.get("status") else "ריק"


def row_reasons(tab: str, row: dict) -> list[str]:
    """What the page's column "מה לבדוק" says of a row (finance.js rowChips): each of its chips, in FLAG_ORDER, in the
    chip's own words - "{status}" the row's own status (`status_word`). The page shows five and "ועוד N"; this is all of
    them, for the Excel files, which carry the column on every row (QA 24.11: a row in "חריג" said nothing there)."""
    texts = {k: t for k, t, _x in LEGEND_FLAGS[tab]}
    return [texts[k].replace("{status}", status_word(row)) for k in FLAG_ORDER if k in row["flags"] and k in texts]


# ---------------------------------------------------------------- actions from the desk (round 3, slice 3, 11.11)
# Written from the browser AS THE PERSON (frontend/finance.js `mondayWrite`, through monday.api(); knowledge ADR-0020 §2,
# spec finance-desk-live-and-actions §2.5), never through this server: monday's own permissions apply, the board's activity
# log shows who wrote, and the automations and webhooks fire exactly as on a board edit. Each action is one item, in steps,
# each step one mutation. A status is written by its INDEX, taken from the dictionaries above and sent to the page with its
# label - the page never writes a label's text. Every column below is on the page's allowlist of (board, column) pairs, each status
# with the indexes set here (finance.js WRITABLE), which tests/test_finance_actions_js.py holds to exactly these. Which rows offer
# which action is decided here (`b2c_actions`, `b2b_actions`), like their state. This server writes nothing to monday
# (ADR-0188): a customer's billing details too are the person's write, in the browser (`account_form`, below), where
# analiza-metrics' server wrote them itself (its app/account_writer.py, not ported).
def _set(col: str, title: str, labels: dict, idx: str) -> dict:
    return {"column": col, "title": title, "kind": "status", "index": idx, "label": labels[idx]}


def _typed(col: str, title: str, kind: str, required: bool = True, **more: Any) -> dict:
    """A value the person types: a number (an amount) or a text (a reference). Not required: empty is allowed (a
    reference typed in error can be cleared) - or, with `skip_empty`, an empty one is not written at all."""
    return dict({"column": col, "title": title, "kind": kind, "input": True, "required": required}, **more)


_PAY_TITLE, _RECEIPT_TITLE, _BILLING_TITLE = "סטטוס תשלום", "שליחת קבלה", "מצב חיוב"
DESK_ACTIONS = OrderedDict([
    # "החזר לביצוע": the money went back (MON-84: finance marks it when the money is out). The transfer app's webhook then
    # copies "סכום לזיכוי" into "סכום החזר" and closes the request when it was its last row (MON-86)
    ("refund_done", {"board": "sc", "button": "סימון %s" % _q(PAY[P_REFUNDED]),
                     "steps": [[_set(SC["status"], _PAY_TITLE, PAY, P_REFUNDED)]],
                     "after": "אחרי הכתיבה אפליקציית ההחזרים מעתיקה את סכום לזיכוי לסכום החזר, ואם זו השורה האחרונה של "
                              "הבקשה, סוגרת אותה."}),
    # part of it went back: "סכום החזר" first, then the status - when the status lands the amount is there, and the
    # request closes once (MON-86: it closes in either order, a webhook on each column)
    ("refund_partial", {"board": "sc", "button": "סימון %s" % _q(PAY[P_PART]),
                        "steps": [[_typed(SC["refund"], "סכום החזר", "number")],
                                  [_set(SC["status"], _PAY_TITLE, PAY, P_PART)]],
                        "after": "קודם נכתב סכום החזר, ואחריו סטטוס תשלום. אחרי שניהם אפליקציית ההחזרים סוגרת את "
                                 "הבקשה, אם זו השורה האחרונה שלה."}),
    ("receipt_sent", {"board": "sc", "button": "%s: %s" % (_RECEIPT_TITLE, _q(RECEIPT[R_SENT])),
                      "steps": [[_set(SC["receipt"], _RECEIPT_TITLE, RECEIPT, R_SENT)]]}),
    # the references, typed in place (MON-87 for the credit note)
    ("credit_ref", {"board": "sc", "button": "שמירה", "field": True,
                    "steps": [[_typed(SC["credit_ref"], "אסמכתת זיכוי", "text", required=False)]]}),
    ("sc_invoice", {"board": "sc", "button": "שמירה", "field": True,
                    "steps": [[_typed(SC["invoice"], "אסמכתת חשבונית", "text", required=False)]]}),
    # a company charge billed: with its invoice's reference when typed (an empty box never clears one), and "מועד דרישת
    # תשלום" today when it is empty (MON-92: a charge that moves to "חויב" without one gets the day it moved)
    ("billed", {"board": "b2", "button": "סימון %s" % _q(BILLING[B_BILLED]),
                "steps": [[_set(B2["billing"], _BILLING_TITLE, BILLING, B_BILLED),
                           _typed(B2["invoice"], "אסמכתת חשבונית", "text", required=False, skip_empty=True),
                           {"column": B2["due"], "title": "מועד דרישת תשלום", "kind": "date", "today": True,
                            "if_empty": True}]],
                "after": "מועד דרישת תשלום נכתב רק כשהוא ריק במאנדיי, והוא היום."}),
    ("b2b_paid", {"board": "b2", "button": "סימון %s" % _q(B2B_PAY[BP_PAID]),
                  "steps": [[_set(B2["payment"], _PAY_TITLE, B2B_PAY, BP_PAID)]]}),
    ("b2b_partial", {"board": "b2", "button": "סימון %s" % _q(B2B_PAY[BP_PARTIAL]),
                     "steps": [[_typed(B2["paid_so_far"], 'סכום ששולם (כולל מע"מ)', "number"),
                                _set(B2["payment"], _PAY_TITLE, B2B_PAY, BP_PARTIAL)]]}),
])
ACTION_BOARDS = {"sc": STUDENT_CHARGE, "b2": B2B_CHARGE}


def desk_writes() -> dict[str, dict[str, Any]]:
    """Every (board, column) an action writes, with the kind of value - and for a status, instead of its kind, the indexes
    the actions set, in order (review 11.11: the page allowed a status column with every one of its labels): what the
    page's allowlist must hold, and no more.

    Since ADR-0188 (D2, the desk move plan §1b item 4), also a customer's four billing details on Accounts, which the page
    writes as the person: each text by its own rule, named by its field (`vat_id` nine digits or empty, `hash_key` up to
    15 letters, digits or "-" or empty, `invoice_name` up to INVOICE_NAME_MAX characters on one line - analiza-metrics
    app/account_writer.py `clean` at da671b2), and "תנאי תשלום" as a dropdown by the ids the form offers, never cleared."""
    out: dict[str, dict[str, Any]] = {}
    for a in DESK_ACTIONS.values():
        for step in a["steps"]:
            for w in step:
                cols = out.setdefault(ACTION_BOARDS[a["board"]], {})
                if w["kind"] == "status":
                    cols[w["column"]] = sorted(set(cols.get(w["column"]) or []) | {int(w["index"])})
                else:
                    cols[w["column"]] = w["kind"]
    out[ACCOUNTS_BOARD] = {f["column"]: {"dropdown": sorted(int(i) for i in ACCOUNT_TERMS)} if f["kind"] == "dropdown" else key
                           for key, f in ACCOUNT_FIELDS.items()}
    return out


def actions_out(board: str) -> dict:
    """A tab's actions for the page: its board, and each action with its steps, in order."""
    return {"board": ACTION_BOARDS[board],
            "list": [dict(a, key=k) for k, a in DESK_ACTIONS.items() if a["board"] == board]}


def b2c_actions(row: dict) -> list[str]:
    """The actions a payment offers, by its state and its status index."""
    out = []
    if row["state"] == "refund_to_execute":
        out += ["refund_done", "refund_partial"]
    if row["state"] == "receipt_pending":
        out.append("receipt_sent")
    if row["status_idx"] in REFUNDED:
        out.append("credit_ref")
    if row["status_idx"] in MONEY_IN:
        out.append("sc_invoice")
    return out


def b2b_actions(row: dict) -> list[str]:
    """The actions a company charge offers: "חויב" from "לחיוב החודש" (not from a charge planned for a later month, which
    would be billed before its demand day), and "שולם" or "שולם חלקית" on a billed charge not paid in full."""
    if row["billing_idx"] in (B_APPROVAL, B_APPROVED) and row["state"] == "to_bill":
        return ["billed"]
    if row["billing_idx"] == B_BILLED and row["payment_idx"] != BP_PAID:
        return ["b2b_paid", "b2b_partial"]
    return []


def account_billing(accounts: list[dict]) -> dict[str, dict[str, str | None]]:
    """Each account's four billing details as monday shows them ("" when empty): the ledger shows them and edits them.
    The business number (`vat_id`) is None, always, whatever a row holds: it is never read here (NEVER_READ; a sole
    proprietor's is their ID number, ADR-0173 §1), and the page reads it in the browser as the person. None and not "",
    which would say monday's cell is empty."""
    return {str(a["id"]): {k: None if k == "vat_id" else (a.get(AC[k]) or "").strip() for k in BILLING_DETAILS}
            for a in accounts}


# ---------------------------------------------------------------- the billing details' form (ADR-0188)
# What the ledger draws a customer's four billing details with, and the form that edits them: analiza-metrics
# app/account_writer.py `form_contract()` at da671b2, its values copied here, since that module is not ported - in this
# service the server writes nothing to monday (ADR-0188). The edit is the person's own, in the browser through
# monday.api(), like every action of the desk (its four columns are on desk_writes' allowlist, each by its rule), and so
# is its Update (MON-41), written only when the app holds updates:write: nothing here checks or writes a value. The
# business number's field is named by its column so the page can read it as the person; its value is never read here.
ACCOUNT_FIELDS = OrderedDict([
    ("vat_id", {"column": AC["vat_id"], "title": "ח.פ", "kind": "text"}),
    ("hash_key", {"column": AC["hash_key"], "title": "מפתח חשבשבת", "kind": "text"}),
    ("invoice_name", {"column": AC["invoice_name"], "title": "חשבונית על שם", "kind": "text"}),
    ("terms", {"column": AC["terms"], "title": "תנאי תשלום", "kind": "dropdown"}),
])
# "תנאי תשלום" by dropdown id (knowledge b2b-flow: 1 שוטף · 2 שוטף+30 · 3 שוטף+60 · 4 אחר · 5 שוטף+45), in the order the
# form lists them: by days, then "אחר"
ACCOUNT_TERMS = OrderedDict([("1", "שוטף"), ("2", "שוטף+30"), ("5", "שוטף+45"), ("3", "שוטף+60"), ("4", "אחר")])
INVOICE_NAME_MAX = 120
# MON-43 (knowledge ADR-0086, boundaries §2), shown beside מפתח חשבשבת in the form - without its number, which is ours,
# not the person's (QA 24.11). Every task number above (MON-86, MON-92) is kept in a comment for the same reason
MON43 = ("חברה עם כמה כרטיסים בחשבשבת באותו ח.פ (למשל משרד הביטחון) מקבלת ח.פ בלי מפתח חשבשבת: עמודה אחת לא "
         "יכולה לשאת כמה כרטיסים, ומקומו של מפתח כזה בעסקה או בחיוב.")
ACCOUNT_HINTS = {"vat_id": "9 ספרות, בלי מקפים", "hash_key": "עד 15 אותיות באנגלית או ספרות",
                 "invoice_name": "עד %d תווים" % INVOICE_NAME_MAX}


def account_form() -> dict:
    """What the page needs to draw the billing details and their form: the fields with their monday titles, the terms by
    id (the shape of analiza-metrics' `form_contract`, unchanged)."""
    return {"board": ACCOUNTS_BOARD,
            "fields": [{"key": k, "title": f["title"], "column": f["column"], "kind": f["kind"],
                        "hint": ACCOUNT_HINTS.get(k, "")} for k, f in ACCOUNT_FIELDS.items()],
            "terms": [{"id": i, "label": label} for i, label in ACCOUNT_TERMS.items()],
            "mon43": MON43, "vat_id_digits": 9, "hash_key_max": 15, "invoice_name_max": INVOICE_NAME_MAX}


# ---------------------------------------------------------------- small parsers
def amount(text: Any) -> Decimal | None:
    """'1,234' / '1234.5' -> Decimal; '' or unparsable -> None."""
    if text is None:
        return None
    s = re.sub(r"[^\d.\-]", "", str(text))
    if s in ("", "-", ".", "-."):
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def day(text: Any) -> date | None:
    if not text:
        return None
    try:
        return date.fromisoformat(str(text)[:10])
    except ValueError:
        return None


def cohort_title(name: str, code: str) -> str:
    """'A665 | מפתחות AI | 08.11.26 | נשים חרדיות' -> 'מפתחות AI · נשים חרדיות' (code and bare dates dropped)."""
    parts = [p.strip() for p in (name or "").split("|")]
    keep = [p for p in parts if p and p != code and not re.fullmatch(r"[\d./\- ]+", p)]
    return " · ".join(keep)


def end_of_month(d: date) -> date:
    nxt = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
    return nxt - timedelta(days=1)


def due_by_terms(demand: date | None, terms: str) -> tuple[date | None, bool]:
    """שוטף+N: N days after the end of the demand month. Unknown or 'אחר' terms: the demand date itself."""
    if not demand:
        return None, False
    if terms in TERMS_DAYS:
        return end_of_month(demand) + timedelta(days=TERMS_DAYS[terms]), True
    return demand, False


def local_day(stamp: Any) -> date | None:
    """monday's created_at is UTC; a payment at 01:00 Israel time on the 1st belongs to the 1st, not the month before."""
    if not stamp:
        return None
    try:
        return datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).astimezone(LOCAL_TZ).date()
    except ValueError:
        return day(stamp)


def label_of(row: dict, col: str) -> tuple[str, str]:
    """(index, label) of a status cell. An index this module knows with another text is drift."""
    return str(row.get(col + "#") or ""), (row.get(col) or "").strip()


def unnamed_labels(row: dict, fields: dict[str, str]) -> list[str]:
    """The row's status cells that hold a value monday shows with no label (an index whose label has no name, or was
    deleted): {field name on the row: column id}. The page says "תווית בלי שם" there instead of an empty cell (QA 2.10)."""
    return [f for f, col in fields.items() if label_of(row, col)[0] and not label_of(row, col)[1]]


class _UF:
    def __init__(self):
        self.p: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[max(ra, rb)] = min(ra, rb)


def _dsum(values: Iterable[Decimal]) -> Decimal:
    return sum(values, ZERO)


# ---------------------------------------------------------------- students
def _check_label(col: str, idx: str, label: str, known: dict, labels: Counter) -> bool:
    """False for an index this module does not know (counted as ("unknown", col)); a known index whose text
    changed is counted as ("drift", col) and still read by its index."""
    if idx and idx not in known:
        labels[("unknown", col)] += 1
        return False
    if idx in known and label and label != known[idx]:
        labels[("drift", col)] += 1
    return True


def _b2c_row(r: dict, today: date, labels: Counter, asked: date | None = None) -> dict:
    """One payment. `asked` is its enrollment's "תאריך בקשת ביטול" (build_b2c reads it by id): the day the legal
    clock of a refund starts, which its "מועד אחרון להחזר" counts from."""
    s_idx, s_label = label_of(r, SC["status"])
    s_known = _check_label(SC["status"], s_idx, s_label, PAY, labels)
    r_idx, r_label = label_of(r, SC["receipt"])
    r_known = _check_label(SC["receipt"], r_idx, r_label, RECEIPT, labels)
    paid_typed = amount(r.get(SC["paid"]))
    money_in = (paid_typed or ZERO) if s_idx in MONEY_IN else ZERO
    refund_typed = amount(r.get(SC["refund"]))
    if s_idx == P_REFUNDED:
        # the label says all of it - with no amount typed, or a 0 typed (the status rules, knowledge ADR-0023)
        refunded_raw = refund_typed if refund_typed else money_in
    elif s_idx == P_PART:
        refunded_raw = refund_typed or ZERO
    else:
        refunded_raw = ZERO
    refunded = min(refunded_raw, money_in)  # totals never return more than the row received
    credit_approved = amount(r.get(SC["credit_approved"])) or ZERO
    planned = day(r.get(SC["planned"]))
    last_day = refund_last_day(asked)
    refund_date = day(r.get(SC["refund_date"]))
    invoice = (r.get(SC["invoice"]) or "").strip()
    flags: list[str] = []
    if s_idx == P_PAID and not invoice:
        flags.append("invoice_missing")
    if s_idx == P_PAID and r_idx in ("", R_PENDING):
        flags.append("receipt_pending")
    # An approved refund ("סכום לזיכוי") stays somebody's work until its status says the money went back (QA 11.11,
    # 1.1: a refund whose status turned "יש בעיה" fell into "ללא תקבול" and out of every work list). On "שולם" it is a
    # refund to execute; on any other status ("יש בעיה", "לא שולם", "מלגת אנליזה", "ביקש החזר", empty, or a label the
    # desk does not know) no approval writes it, so it is an exception - and its clock runs either way.
    if credit_approved > 0 and s_idx not in REFUNDED:
        flags.append("refund_to_execute" if s_idx == P_PAID else "credit_on_status")
        if planned and planned < today:
            flags.append("refund_overdue")             # "מועד העברה מתוכנן" passed and the money is still here
        if last_day is None:
            flags.append("refund_last_day_unknown")    # no request day: the legal clock cannot be read
        elif last_day < today:
            flags.append("refund_last_day_passed")     # "מועד אחרון להחזר" passed and the money is still here
    # A refund amount or day on a row whose status says no refund went out (QA 1.2: "שולם" with both filled read as a
    # refund still to execute). A 0 is no refund amount.
    if s_idx not in REFUNDED:
        if refund_typed:
            flags.append("refund_amount_on_status")
        if refund_date:
            flags.append("refund_date_on_status")
    if s_idx in REFUNDED and not (r.get(SC["credit_ref"]) or "").strip() and refund_date \
            and refund_date >= CREDIT_REF_FROM:
        flags.append("credit_ref_missing")
    if s_idx in REFUNDED and refund_typed is None:
        flags.append("refund_amount_missing")
    elif s_idx in REFUNDED and refund_typed == 0:
        flags.append("refund_amount_zero")             # typed, as 0: said as such, never as "not typed" (QA 2.10)
    if refunded_raw > money_in + Decimal("0.5"):
        flags.append("refund_exceeds_paid")
    if s_idx in MONEY_IN and paid_typed is None:
        flags.append("amount_missing")
    if not s_known or not r_known:
        flags.append("status_unknown")  # a label added in monday after this module was written: no money, no task
    if s_idx == P_SCHOLARSHIP:
        flags.append("scholarship")
    elif s_idx not in MONEY_IN and s_known:
        flags.append("exception")  # לא שולם, יש בעיה, or empty
    if not r.get(SC["enrollment"]) and not r.get(SC["lead"]):
        flags.append("unlinked")
    created = local_day(r.get("created_at"))
    paid_on = day(r.get(SC["paid_date"]))
    on = paid_on or created
    return {
        "id": str(r["id"]), "board": "sc", "name": r.get("name", ""), "created": created.isoformat() if created else "",
        # the date the desk files a payment under: "תאריך תקבול" when typed, else the day the row was created
        "paid_date": paid_on.isoformat() if paid_on else "", "date": on.isoformat() if on else "",
        "date_from": "paid" if paid_on else "created", "month": on.isoformat()[:7] if on else "",
        "status": s_label, "status_idx": s_idx, "type": r.get(SC["ctype"], "") or "", "channel": r.get(SC["channel"], "") or "",
        "invoice": invoice, "receipt": r_label, "receipt_idx": r_idx,
        "money_in": money_in, "refunded": refunded, "net": money_in - refunded,
        "credit_approved": credit_approved, "planned": planned.isoformat() if planned else "",
        "asked_date": asked.isoformat() if asked else "",
        "refund_last_day": last_day.isoformat() if last_day else "",
        # no request day has two causes with two fixes: the payment links no enrollment (a charge is born before its
        # enrollment), or its enrollment holds no "תאריך בקשת ביטול" - the page says which (hostile review 11.11)
        "enrollment_linked": bool(r.get(SC["enrollment"])),
        # what "סכום ששולם" holds as typed, also on a row whose status brought no money in: `counted` says whether the
        # status brought money in (MONEY_IN) - the page strikes the amount through where it did not
        "paid_typed": paid_typed, "counted": s_idx in MONEY_IN,
        # "סכום החזר" as typed (None: not typed), also where the status says no refund went out and `refunded` is 0
        "refund_typed": refund_typed,
        "unnamed": unnamed_labels(r, B2C_UNNAMED),
        "refund_method": r.get(SC["refund_method"], "") or "",
        "refund_date": refund_date.isoformat() if refund_date else "",
        "credit_ref": (r.get(SC["credit_ref"]) or "").strip(),
        "voucher": "", "voucher_idx": "",   # the enrollment's, set by build_b2c
        # the refund's bank account is never on a row: no reading holds it (NEVER_READ), and the server never reads it,
        # the page does, in the browser as the person, for the rows of "החזר לביצוע" only. analiza-metrics put it here from a
        # live read by id (`attach_bank`, round 3, slice 2), not ported
        "bank": None,
        "flags": flags,
    }


B2C_EXCEPTIONS = {"status_unknown", "amount_missing", "refund_exceeds_paid", "credit_on_status", "refund_amount_on_status",
                  "refund_date_on_status"}
REFUND_AMOUNT_UNSET = {"refund_amount_missing", "refund_amount_zero"}


def b2c_state(row: dict) -> str:
    """The payment's one state (B2C_STATES), checked in order. A refund executed and credited, or executed before
    the credit-note column existed (25.10), is never a task."""
    flags, s = set(row["flags"]), row["status_idx"]
    if flags & B2C_EXCEPTIONS or (s == P_PART and flags & REFUND_AMOUNT_UNSET):
        return "exception"
    if "refund_to_execute" in flags:
        return "refund_to_execute"
    if s in REFUNDED:
        return "refunded"
    if s in (P_PAID, P_ASKED):
        if row["receipt_idx"] not in (R_SENT, R_NOT_RELEVANT):
            return "receipt_pending"
        return "refund_asked" if s == P_ASKED else "done"
    return "no_money"


def _cohort_meta(cohort_rows: list[dict]) -> dict[str, dict]:
    meta = {}
    for c in cohort_rows:
        code = (c.get(CO["code"]) or "").strip()
        dates = (c.get(CO["dates"]) or "").split(" - ")
        meta[str(c["id"])] = {
            "id": str(c["id"]), "code": code, "name": c.get("name", ""), "title": cohort_title(c.get("name", ""), code),
            "external": (c.get(CO["external"]) or "").strip(), "status": c.get(CO["status"], "") or "",
            "flow": (c.get(CO["flow"]) or "").strip() or NO_FLOW,
            "start": dates[0].strip() if dates and dates[0] else "", "end": dates[1].strip() if len(dates) > 1 else "",
        }
    return meta


def build_b2c(sc_rows: list[dict], cohort_rows: list[dict], enrollments: list[dict], leads: list[dict], today: date,
              labels: Counter, names: dict[str, str | None] | None = None,
              reps: dict[str, str | None] | None = None) -> tuple[list[dict], Counter]:
    """Every student payment as one flat row, newest first: its state, the date it is filed under, its student
    and its cohort. A cohort is the one the payment's enrollment names; when that names none, the one its lead
    names. Two cohorts, none, or a cohort monday no longer returns: no cohort, and the note says why. `names` is the
    reading's `lead_names` (every linked lead's name; None: not returned); without it, the leads given name themselves.
    `reps` is its `lead_reps` (every linked lead's Owner), the same way."""
    meta = _cohort_meta(cohort_rows)
    by_enr = {str(e["id"]): _uniq(e.get(EN["cohort"]) or []) for e in enrollments}
    by_lead = {str(x["id"]): _uniq(x.get(LD["cohort"]) or []) for x in leads}
    lead_name = names if names is not None else {str(x["id"]): x.get("name") or "" for x in leads}
    rep_of = reps if reps is not None else {str(x["id"]): lead_rep(x) for x in leads}
    voucher_of: dict[str, tuple[str, str]] = {}
    asked_of: dict[str, date] = {}
    for e in enrollments:
        v_idx, v_label = label_of(e, EN["voucher"])
        _check_label(EN["voucher"], v_idx, v_label, VOUCHER, labels)   # once per enrollment, not per payment
        voucher_of[str(e["id"])] = (v_idx, v_label)
        asked_on = day(e.get(EN["asked"]))
        if asked_on:
            asked_of[str(e["id"])] = asked_on
    uf = _UF()
    keyed = []
    for r in sc_rows:
        keys = ["L:%s" % x for x in r.get(SC["lead"]) or []] + ["E:%s" % x for x in r.get(SC["enrollment"]) or []]
        if not keys:
            keys = ["C:%s" % r["id"]]
        for k in keys[1:]:
            uf.union(keys[0], k)
        keyed.append((r, keys[0]))

    rows: list[dict] = []
    notes: Counter = Counter()
    for r, key in keyed:
        # a payment linked to two enrollments that both hold a request day takes the earlier: the stricter clock
        asked = min((asked_of[str(e)] for e in r.get(SC["enrollment"]) or [] if str(e) in asked_of), default=None)
        row = _b2c_row(r, today, labels, asked)
        enr = enrollment_cohorts(r, by_enr)
        # the lead decides only when the enrollment names no cohort - and only those leads were read
        lead = [] if enr else _uniq(c for x in r.get(SC["lead"]) or [] for c in by_lead.get(str(x), []))
        if len(enr) == 1:
            cid, how = enr[0], "enrollment"
        elif not enr and len(lead) == 1:
            cid, how = lead[0], "lead"
        else:
            cid = None
            how = "ambiguous" if (enr or lead) else ("no_link" if "unlinked" in row["flags"] else "no_cohort")
        if cid is not None and cid not in meta:
            cid, how = None, "missing"   # linked, but monday no longer returns that cohort
        if cid is None:
            notes["cohort_" + how] += 1
        m = meta.get(cid) if cid else None
        row.update({"cohort_code": m["code"] if m else "", "cohort_title": (m["title"] or m["name"]) if m else "",
                    "flow": m["flow"] if m else "", "cohort_from": how})
        row["voucher_idx"], row["voucher"] = next(
            (voucher_of[str(e)] for e in r.get(SC["enrollment"]) or [] if voucher_of.get(str(e), ("", ""))[0]), ("", ""))
        row["person"] = uf.find(key)
        # the payment's leads, in monday's order: the page reads their ID number by them, in the browser as the person,
        # since no reading holds one and the Excel file carries none (analiza-metrics read it live for the file,
        # `with_id_numbers`, not ported)
        row["lead_ids"] = [str(x) for x in r.get(SC["lead"]) or []]
        # "ליד מקור" as monday shows the link: the lead's name (QA 2.5: the details did not show it). Every linked lead's,
        # not only a deciding one's: a reading keeps every name (`lead_names`, 12.11)
        row["lead_names"] = [lead_name[str(x)] for x in r.get(SC["lead"]) or [] if lead_name.get(str(x)) is not None]
        # "נציג מכירות" as Student Charge's mirror shows it: the Owner of the payment's leads, each name once (17.11)
        row["rep"] = ", ".join(_uniq(n for x in r.get(SC["lead"]) or [] for n in [rep_of.get(str(x))] if n))
        row["state"] = b2c_state(row)
        row["actions"] = b2c_actions(row)
        rows.append(row)
    rows.sort(key=lambda x: (x["date"], x["id"]), reverse=True)
    return rows, notes


def _state_numbers(rows: list[dict]) -> dict[str, dict]:
    out = {}
    for key, spec in B2C_STATES.items():
        rs = [x for x in rows if x["state"] == key]
        out[key] = {"count": len(rs), "money": _dsum(x[spec["money"]] for x in rs),
                    "money_in": _dsum(x["money_in"] for x in rs), "refunded": _dsum(x["refunded"] for x in rs)}
    return out


def b2c_periods(rows: list[dict]) -> list[dict]:
    """The periods a bookkeeper reads by: everything, each month, and the two-month VAT periods (Jan-Feb ...).
    Each carries its state counts and money, computed here once - the page only shows them."""
    months = sorted({x["month"] for x in rows if x["month"]}, reverse=True)

    def period(key: str, label: str, kind: str, ms: list[str]) -> dict:
        rs = [x for x in rows if not ms or x["month"] in ms]
        return {"key": key, "label": label, "kind": kind, "months": ms, "count": len(rs), "students": len({x["person"] for x in rs}),
                "money_in": _dsum(x["money_in"] for x in rs), "refunded": _dsum(x["refunded"] for x in rs),
                "states": _state_numbers(rs)}

    out = [period("", "כל התקופה", "all", [])]
    for m in months:
        y, mo = int(m[:4]), int(m[5:7])
        out.append(period(m, "%s %d" % (HE_MONTHS[mo - 1], y), "month", [m]))
    pairs = sorted({(int(m[:4]), (int(m[5:7]) + 1) // 2) for m in months}, reverse=True)
    for y, half in pairs:
        a, b = 2 * half - 1, 2 * half
        ms = ["%04d-%02d" % (y, a), "%04d-%02d" % (y, b)]
        out.append(period("+".join(ms), "%s-%s %d" % (HE_MONTHS[a - 1], HE_MONTHS[b - 1], y), "vat", ms))
    return out


_AMOUNT_Q = re.compile(r"[0-9]+(?:\.[0-9]{1,2})?")   # ASCII digits, at most agorot: the same in finance.js
# The search box's alphabet for an Excel file: Hebrew, ASCII letters and digits, a space and , . - / ₪ ' " ( ).
# Inside it the page and the server read a query the same way (trim, lower case, amounts); a query outside it still
# filters the page but is not exported (review 10.11: U+FEFF and \x85 are trimmed differently in the two languages).
_EXPORTABLE_Q = re.compile(r"[\u0590-\u05FFA-Za-z0-9 ,.\-/₪'\"()]{0,100}")


def query_exportable(q: str) -> bool:
    return bool(_EXPORTABLE_Q.fullmatch(q or ""))
_PERIOD_KEY = re.compile(r"[0-9]{4}-[0-9]{2}(?:\+[0-9]{4}-[0-9]{2})?")
# the list's order by the state chosen, as finance.js SC_SORT: (field, newest first); "החזר לביצוע" has its own
B2C_SORT = {"refunded": ("refund_date", True)}


def refund_order(row: dict) -> tuple:
    """The refunds to execute, as finance.js `refundKey`: first those whose "מועד אחרון להחזר" is unknown (no request
    day), then those whose last day passed, the oldest first, then the rest by "מועד העברה מתוכנן"; an empty planned
    date last; and on the same planned day, the nearest last day first (QA 2.6: ties came by descending id). The flags
    carry the day it was read on, so the page and the file need no clock of their own."""
    flags = row["flags"]
    group = 0 if "refund_last_day_unknown" in flags else 1 if "refund_last_day_passed" in flags else 2
    return (group, row["refund_last_day"] if group == 1 else "", 1 if not row["planned"] else 0, row["planned"],
            row["refund_last_day"])


def _agorot(v: Any) -> int:
    """An amount in whole agorot, the extra digits cut off - exactly as finance.js `agorot` reads a Decimal string."""
    return int((Decimal(str(v)) * 100).to_integral_value(rounding=ROUND_DOWN))


def b2c_matches(row: dict, q: str) -> bool:
    """The search box, the same rule as frontend/finance.js `scMatch` (tests/test_finance_js.py runs both on the
    same rows): an amount within a shekel, compared in whole agorot, or the text inside the student's name, the
    cohort, the invoice or the credit-note reference."""
    q = (q or "").strip().lower()
    if not q:
        return True
    num_text = re.sub(r"[,₪\s]", "", q)
    if _AMOUNT_Q.fullmatch(num_text):
        n = _agorot(num_text)
        if any(v and abs(_agorot(v) - n) < 100 for v in (row["money_in"], row["refunded"], row["credit_approved"])):
            return True
    hay = " ".join([row["name"], row["cohort_code"], row["cohort_title"], row["invoice"], row["credit_ref"]]).lower()
    return q in hay


def period_months(period: str) -> set[str]:
    """'' is every month; '2026-10' one; '2026-09+2026-10' two. Read from the key itself, so a period that a newer
    reading no longer lists is still a period (an empty one), never an error."""
    if not period:
        return set()
    if not _PERIOD_KEY.fullmatch(period):
        raise ValueError("unknown period")
    return set(period.split("+"))


def sort_b2c(rows: list[dict], state: str) -> list[dict]:
    """The screen's order (finance.js sortRows): by the state's field, empty values last, ties as they came."""
    if state == "refund_to_execute":
        return sorted(rows, key=refund_order)
    key, newest_first = B2C_SORT.get(state, ("date", True))
    filled = sorted((x for x in rows if x[key]), key=lambda x: x[key], reverse=newest_first)
    return filled + [x for x in rows if not x[key]]


def filter_b2c(b2c: dict, state: str, period: str, q: str) -> list[dict]:
    """The list as the page shows it, in its order: a state ('' all, 'tasks' every task state, or one key), a
    period key, and the search box. The Excel file is built from this."""
    months = period_months(period)
    if state and state != "tasks" and state not in B2C_STATES:
        raise ValueError("unknown state")
    keep = set(TASK_STATES) if state == "tasks" else ({state} if state else set(B2C_STATES))
    rows = [x for x in b2c["rows"] if x["state"] in keep and (not months or x["month"] in months) and b2c_matches(x, q)]
    return sort_b2c(rows, state if state in B2C_STATES else "")


# ---------------------------------------------------------------- companies
def _b2b_bucket(billing: str, payment: str) -> str | None:
    if billing == B_CANCELLED:
        return None
    if billing == B_BILLED:
        if payment == BP_PAID:
            return "paid"
        if payment == BP_PARTIAL:
            return "partial"
        return "unpaid"
    if billing == B_APPROVAL:
        return "approval"
    if billing == B_APPROVED:
        return "approved"
    return "pending"


def _b2b_row(r: dict, terms: str, today: date, labels: Counter) -> dict:
    b_idx, b_label = label_of(r, B2["billing"])
    p_idx, p_label = label_of(r, B2["payment"])
    known = _check_label(B2["billing"], b_idx, b_label, BILLING, labels)
    known = _check_label(B2["payment"], p_idx, p_label, B2B_PAY, labels) and known
    amt_typed = amount(r.get(B2["amount"]))
    amt = amt_typed or ZERO
    demand = day(r.get(B2["due"]))
    bucket = _b2b_bucket(b_idx, p_idx)
    due, by_terms = due_by_terms(demand, terms)
    flags: list[str] = []
    days_late = None
    if bucket in ("approval", "approved") and demand and demand < today:
        flags.append("invoice_late")  # the demand date passed and no invoice yet
    if not known:
        # a label added in monday after this module was written: the row stays in its bucket so the totals tie,
        # but it is no task - an unknown payment may well be paid, and must not be chased as arrears
        flags.append("status_unknown")
    if bucket == "unpaid" and known and due and due < today:
        flags.append("overdue")
        days_late = (today - due).days
    if b_idx == B_BILLED and not demand:
        # "חויב בלי מועד דרישת תשלום": no due date, so never "overdue" - whatever was paid (QA 1.4: a partly paid charge
        # of 305,077 without one was marked nowhere). Where it sits is b2b_state's: unpaid, an exception; partly paid,
        # "שולם חלקית" (her work already, its balance counted there once); paid, "שולם"
        flags.append("billed_no_due")
    if b_idx == B_BILLED and demand and demand > today:
        # "חויב, ומועד דרישת התשלום עוד לא הגיע" (round 3): billed before the day the payment is demanded - the
        # date or the label is wrong, and either way the charge is no "open, not due yet" money to wait for
        flags.append("billed_before_demand")
    if amt_typed is None and bucket in ("approval", "approved", "paid", "partial", "unpaid"):
        flags.append("amount_missing")  # an invoice to issue, or issued, with no amount: a silent 0 otherwise
    paid_so_far = amount(r.get(B2["paid_so_far"]))
    balance = None
    if bucket == "partial":
        if paid_so_far is None:
            flags.append("partial_amount_unknown")  # the paid share is not typed: no balance, never "overdue money"
        elif amt_typed is not None:   # no amount: no balance, and the row is "חריג" (amount_missing)
            balance = max(amt * VAT_RATE - paid_so_far, ZERO).quantize(Decimal("0.01"))
            if balance > 0 and due and due < today:   # nothing left: no arrears, whatever the date
                days_late = (today - due).days
    if b_idx == B_BILLED and not (r.get(B2["invoice"]) or "").strip():
        flags.append("billed_no_invoice")
    if p_idx == BP_PAID and b_idx not in (B_BILLED, B_CANCELLED):
        flags.append("paid_not_billed")
    return {
        "id": str(r["id"]), "board": "b2", "name": r.get("name", ""), "created": (r.get("created_at") or "")[:10],
        "billing": b_label, "payment": p_label, "amount": amt, "bucket": bucket or "cancelled",
        # the two statuses by index (round 3, slice 3): the actions a row offers are decided by them, never by the text
        "billing_idx": b_idx, "payment_idx": p_idx,
        "component": r.get(B2["component"], "") or "", "month": r.get(B2["month"], "") or "",
        "course": r.get(B2["course"], "") or "", "demand": demand.isoformat() if demand else "",
        "due": due.isoformat() if due else "", "terms": terms if by_terms else "", "days_late": days_late,
        "invoice": (r.get(B2["invoice"]) or "").strip(), "flags": flags, "unnamed": unnamed_labels(r, B2B_UNNAMED),
        "paid_so_far": paid_so_far, "balance": balance, "amount_known": amt_typed is not None,
        # money still to collect on an invoiced charge (before VAT): every unpaid invoiced charge - with a due date
        # or without - whose payment label the desk knows; a partly paid one is never "open" money
        "open_money": amt if bucket == "unpaid" and known else ZERO,
    }


B2B_EXCEPTIONS = {"status_unknown", "amount_missing", "paid_not_billed", "billed_before_demand"}


def b2b_state(row: dict, today: date) -> str:
    flags, bucket = set(row["flags"]), row["bucket"]
    if bucket == "cancelled":
        return "cancelled"
    if flags & B2B_EXCEPTIONS or ("billed_no_due" in flags and bucket == "unpaid"):
        return "exception"
    if bucket in ("approval", "approved"):   # "לאישור הנהלת חשבונות" or "מאושר לחיוב": one state (Nadav 11.11)
        demand = day(row["demand"])
        if demand and demand > end_of_month(today):
            return "planned"
        return "to_bill"
    if bucket == "unpaid":
        return "overdue" if "overdue" in flags else "open"
    return {"partial": "partial", "paid": "paid"}.get(bucket, "pending")


def build_b2b(b2_rows: list[dict], deals: list[dict], accounts: list[dict], today: date,
              labels: Counter) -> tuple[list[dict], dict]:
    """Every company charge as one flat row with its state, its customer and its deal, oldest demand first."""
    acct = {str(a["id"]): a for a in accounts}
    deal_by_id = {str(d["id"]): d for d in deals}
    deal_total: dict[str, Decimal] = defaultdict(lambda: ZERO)
    rows: list[dict] = []
    deal_accounts: dict[str, set] = defaultdict(set)
    for r in b2_rows:
        aid = str((r.get(B2["account"]) or [""])[0] or "")
        did = str((r.get(B2["deal"]) or [""])[0] or "")
        terms = ((acct.get(aid) or {}).get(AC["terms"]) or "").strip()
        row = _b2b_row(r, terms, today, labels)
        row["account_terms"] = terms
        # a company whose "תנאי תשלום" is empty: its due dates are the demand days themselves (DUE_RULE, unchanged), and
        # most of "בפיגור" came from such companies (QA 1.3) - marked on what is still to bill or to collect
        row["account_no_terms"] = aid in acct and not terms
        if row["account_no_terms"] and row["bucket"] in ("approval", "approved", "unpaid", "partial"):
            row["flags"].append("terms_missing")
        d = deal_by_id.get(did)
        payer = str(d[DL["payer"]][0]) if d and d.get(DL["payer"]) else ""
        if d and payer and payer != aid:
            row["flags"].append("payer_mismatch")
        if d and str(d.get(DL["stage"] + "#") or "") == STAGE_NO_CONTRACT and row["bucket"] in ("approval", "approved"):
            row["flags"].append("no_contract")
        if row["bucket"] != "cancelled":
            deal_total[did] += row["amount"]
        if did:
            deal_accounts[did].add(aid or NO_ACCOUNT)
        # no customer is a customer of its own ("none"), never "" - which the filter reads as every customer
        # a charge whose "חברה" is empty, or names an account monday no longer returns: "ללא חברה"
        row.update({"account_id": aid or NO_ACCOUNT, "deal_id": did, "account": (acct.get(aid) or {}).get("name", "ללא חברה"),
                    "deal": d.get("name", "") if d else "ללא עסקה", "deal_stage": (d.get(DL["stage"]) or "") if d else "",
                    # the deal's own columns, for the details' "עסקה" block (QA 2.5): "שווי עסקה" and "Account משלם"
                    "deal_value": amount(d.get(DL["value"])) if d else None,
                    "payer": (acct.get(payer) or {}).get("name", "") if payer else ""})
        row["ctx"] = {"account": row["account"], "deal": row["deal"]}
        rows.append(row)
    # the customer ledger (Nadav 10.11 "תעשה את 1"): each customer's charges oldest first, and the open money owed up
    # to each one - computed here, once, so the page adds nothing
    for row in rows:
        row["age_band"] = age_band(row["due"], today) if row["open_money"] else ""
        row["ledger_key"] = "%s|%s" % (row["demand"] or row["month"] or row["created"], row["id"])
    run: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for row in sorted(rows, key=lambda x: x["ledger_key"]):
        run[row["account_id"]] += row["open_money"]
        row["ledger_open"] = run[row["account_id"]]
    for row in rows:
        d = deal_by_id.get(row["deal_id"])
        declared = amount(d.get(DL["value"])) if d else None
        if declared and deal_total[row["deal_id"]] > declared * Decimal("1.005") + 1 and row["bucket"] in ("approval", "approved"):
            row["flags"].append("over_deal_value")
        row["state"] = b2b_state(row, today)
        row["actions"] = b2b_actions(row)
    rows.sort(key=lambda x: (x["demand"] or x["month"] or x["created"], x["id"]))
    terms_unknown = sum(1 for a in accounts if (a.get(AC["terms"]) or "").strip() not in ("", "אחר", *TERMS_DAYS))
    return rows, {"deals_distinct": len(deal_accounts),
                  "deals_split": sum(1 for accts in deal_accounts.values() if len(accts) > 1),
                  "terms_unknown": terms_unknown}


def b2b_states(rows: list[dict]) -> dict[str, dict]:
    """Every state's count and money (before VAT), and for "partial" the balance left including VAT."""
    out = {}
    for key in B2B_STATES:
        rs = [x for x in rows if x["state"] == key]
        out[key] = {"count": len(rs), "money": _dsum(x["amount"] for x in rs)}
    part = [x for x in rows if x["state"] == "partial"]
    out["partial"].update(balance=_dsum(x["balance"] for x in part if x["balance"] is not None),
                          balance_unknown=sum(1 for x in part if x["balance"] is None))
    return out


def b2b_customers(rows: list[dict]) -> list[dict]:
    """The aging by customer, one level (NetSuite, SAP B1, SUMIT): only customers with something to do or to
    collect - to bill this month, open, of which overdue, partly paid - and the oldest arrears in days. A partly paid
    charge whose paid amount is not typed has no balance; what it bills is summed instead (`partial_unknown_amount`,
    round 3), so the page says how much money such charges hold rather than only how many there are."""
    by: dict[str, list[dict]] = defaultdict(list)
    for x in rows:
        by[x["account_id"]].append(x)
    out = []
    for aid, rs in by.items():
        to_bill = _dsum(x["amount"] for x in rs if x["state"] == "to_bill")
        overdue = _dsum(x["amount"] for x in rs if x["state"] == "overdue")
        open_ = _dsum(x["open_money"] for x in rs)   # overdue, not due yet, and invoiced without a due date
        partial = [x for x in rs if x["state"] == "partial"]
        exceptions = sum(1 for x in rs if x["state"] == "exception")
        if not (to_bill or open_ or partial or exceptions):
            continue
        late = [x["days_late"] for x in rs if x["state"] in ("overdue", "partial") and x["days_late"] is not None]
        out.append({"id": aid, "name": rs[0]["account"], "terms": rs[0]["account_terms"],
                    "terms_missing": rs[0]["account_no_terms"], "to_bill": to_bill,
                    "aging": {k: _dsum(x["open_money"] for x in rs if x["age_band"] == k) for k in AGE_BANDS},
                    "open": open_, "overdue": overdue, "oldest_days": max(late) if late else None,
                    "partial_balance": _dsum(x["balance"] for x in partial if x["balance"] is not None),
                    "partial_unknown": sum(1 for x in partial if x["balance"] is None),
                    "partial_unknown_amount": _dsum(x["amount"] for x in partial if x["balance"] is None),
                    "partial_count": len(partial), "exceptions": exceptions,
                    "tasks": sum(1 for x in rs if x["state"] in B2B_TASK_STATES)})
    out.sort(key=lambda c: (-(c["oldest_days"] or -1), -c["overdue"], -c["to_bill"], c["name"]))
    return out


# The aging by customer ("לפי לקוח"): its columns, once, for the page and for its Excel file (QA 11.11, 2.12: the file's
# headers were not the screen's). (key, header, group, kind, shown_when): a grouped column stands under its group's header
# on the page, and in the file's one header row it is "group: header". A column with `shown_when` (a key of the company's
# figures) is drawn only when one of the companies shown has something there - on the page and in the file alike.
AGING_PARTIAL_GROUP = B2B_PAY[BP_PARTIAL]
AGING_BANDS_GROUP = "לפי מועד פירעון"
AGING_COLUMNS = [
    ("name", "חברה", "", "text", ""),
    ("terms", "תנאי תשלום", "", "text", ""),
    ("to_bill", "%s (ללא מע\"מ)" % TO_BILL_LABEL, "", "money", ""),
    ("partial_balance", "יתרה לגבייה (כולל מע\"מ)", AGING_PARTIAL_GROUP, "money", ""),
    ("partial_unknown_amount", "סכום לחיוב (ללא מע\"מ), סכום ששולם (כולל מע\"מ) לא הוקלד", AGING_PARTIAL_GROUP, "money",
     "partial_unknown_amount"),
    ("tasks", "לטיפול", "", "int", ""),
] + [("aging_" + k, band_heading(k, v), AGING_BANDS_GROUP, "money", "aging_no_due" if k == "no_due" else "")
     for k, v in AGE_BANDS.items()] + [
    ("open", "סה\"כ יתרה פתוחה (ללא מע\"מ)", "", "money", ""),
]


def aging_value(c: dict, key: str) -> Any:
    """A company's figure under an aging column's key (finance.js `agingValue`)."""
    if key.startswith("aging_"):
        return c["aging"][key[len("aging_"):]]
    if key == "partial_balance":   # only where some partly paid charge has its paid amount typed
        return c["partial_balance"] if c["partial_count"] - c["partial_unknown"] else None
    return c[key]


def aging_columns(customers: list[dict]) -> list[dict]:
    """The aging columns for these companies: an optional column only when one of them has something in it."""
    return [{"key": k, "header": h, "group": g, "kind": kind}
            for k, h, g, kind, when in AGING_COLUMNS if not when or any(aging_value(c, when) for c in customers)]


def aging_file_header(col: dict) -> str:
    """A column's header in the file's one header row: its group, then its own header, as the page stacks them."""
    return "%s: %s" % (col["group"], col["header"]) if col["group"] else col["header"]


def b2b_by_customer(rows: list[dict], billing: dict[str, dict] | None = None) -> dict[str, dict]:
    """Every customer's own figures, for the ledger: its tiles (b2b_states), open money, overdue, to bill, aging - and
    since round 3, slice 3 its four billing details (`account_billing`), None for no account or one monday did not return:
    the ledger shows them and edits them."""
    by: dict[str, list[dict]] = defaultdict(list)
    for x in rows:
        by[x["account_id"]].append(x)
    return {aid: {"name": rs[0]["account"], "terms": rs[0]["account_terms"], "terms_missing": rs[0]["account_no_terms"],
                  "billing": (billing or {}).get(aid),
                  "count": len(rs), "numbers": b2b_states(rs),
                  "open": _dsum(x["open_money"] for x in rs),
                  "overdue": _dsum(x["amount"] for x in rs if x["state"] == "overdue"),
                  "to_bill": _dsum(x["amount"] for x in rs if x["state"] == "to_bill"),
                  "aging": {k: _dsum(x["open_money"] for x in rs if x["age_band"] == k) for k in AGE_BANDS}}
            for aid, rs in by.items()}


# the list's totals, for whatever the page filtered (Nadav 10.11 "תעשה את 6"): summed here, never in the page
B2C_TOTALS = ("money_in", "refunded", "credit_approved")
B2B_TOTALS = ("amount", "open_money", "paid_so_far", "balance")


def list_totals(tab: str, rows: list[dict]) -> dict[str, Decimal]:
    if tab == "b2c":
        return {f: _dsum(x[f] for x in rows) for f in B2C_TOTALS}
    if tab == "b2b":
        live = [x for x in rows if x["state"] != "cancelled"]
        part = [x for x in live if x["state"] == "partial"]
        # what was paid and what is left exist only for partly paid charges, and only when the paid amount is typed:
        # a sum over some of them is no total, so it is None and `partial_unknown` says how many are missing
        unknown = sum(1 for x in part if x["balance"] is None)
        return {"amount": _dsum(x["amount"] for x in live if x["amount_known"]),
                "open_money": _dsum(x["open_money"] for x in live),
                "paid_so_far": None if unknown else _dsum(x["paid_so_far"] for x in part),
                "balance": None if unknown else _dsum(x["balance"] for x in part),
                "partial_unknown": unknown}
    sums = {f: _dsum(c[f] for c in rows) for f in ("to_bill", "open", "overdue", "partial_balance", "partial_unknown_amount")}
    sums["partial_count"] = sum(c["partial_count"] for c in rows)
    sums["partial_unknown"] = sum(c["partial_unknown"] for c in rows)
    sums.update({"aging_" + k: _dsum(c["aging"][k] for c in rows) for k in AGE_BANDS})
    return sums


B2B_SORT = {"overdue": ("days_late", True), "open": ("due", False), "": ("demand", True), "paid": ("demand", True),
            "cancelled": ("demand", True), "pending": ("demand", True)}   # every other: oldest demand first


def b2b_matches(row: dict, q: str) -> bool:
    """The companies' search box, as frontend/finance.js `b2Match`: an amount within a shekel (in agorot), or the text
    inside the customer, the deal, the course, the reference or the charge's name."""
    q = (q or "").strip().lower()
    if not q:
        return True
    num_text = re.sub(r"[,₪\s]", "", q)
    if _AMOUNT_Q.fullmatch(num_text):
        n = _agorot(num_text)
        if any(v and abs(_agorot(v) - n) < 100 for v in (row["amount"], row["paid_so_far"], row["balance"]) if v is not None):
            return True
    hay = " ".join([row["account"], row["deal"], row["course"], row["invoice"], row["name"]]).lower()
    return q in hay


def filter_customers(customers: list[dict], q: str) -> list[dict]:
    """The aging by customer as the page narrows it (finance.js customersShown): the name holds the search text."""
    q = (q or "").strip().lower()
    return [c for c in customers if not q or q in str(c["name"]).lower()]


def filter_b2b(b2b: dict, state: str, customer: str, q: str) -> list[dict]:
    """The companies list as the page shows it, in its order: a state, a customer (account id, '' for all), the
    search box. The Excel file is built from this."""
    if state and state != "tasks" and state not in B2B_STATES:
        raise ValueError("unknown state")
    keep = set(B2B_TASK_STATES) if state == "tasks" else ({state} if state else set(B2B_STATES))
    rows = [x for x in b2b["rows"] if x["state"] in keep and (not customer or x["account_id"] == customer)
            and b2b_matches(x, q)]
    if customer and not state:   # one customer, every state: the ledger, oldest first
        return sorted(rows, key=lambda x: x["ledger_key"])
    key, newest_first = B2B_SORT.get(state if state != "tasks" else "_", ("demand", False))
    filled = sorted((x for x in rows if x[key] not in (None, "")), key=lambda x: x[key], reverse=newest_first)
    return filled + [x for x in rows if x[key] in (None, "")]


# ---------------------------------------------------------------- whole view
def _states_out(states: OrderedDict) -> list[dict]:
    """A tab's states for the page, in order: each with its sentence, shown above its list when its tile is chosen."""
    return [{"key": k, "label": v["label"], "task": v["task"], "about": v["about"]} for k, v in states.items()]


def _legend_out(tab: str) -> list[dict]:
    """The tab's legend for the page, in its four parts (LEGEND_TITLES)."""
    states = B2C_STATES if tab == "b2c" else B2B_STATES

    def items(pairs) -> list[dict]:
        return [{"term": term, "text": text} for term, text in pairs]
    return [{"title": LEGEND_TITLES[0], "items": items(LEGEND[tab])},
            {"title": LEGEND_TITLES[1], "items": items((v["label"], v["about"]) for v in states.values())},
            {"title": LEGEND_TITLES[2], "items": items([("מה לבדוק", WHAT_TO_CHECK[tab])]
                                                       + [(flag_legend_term(t), x) for _k, t, x in LEGEND_FLAGS[tab]])},
            {"title": LEGEND_TITLES[3], "items": items(LEGEND_MONDAY[tab])}]


def legend_terms(tab: str) -> set[str]:
    """Every word the tab's legend defines (a term of several words is split on " · ")."""
    return {w for part in _legend_out(tab) for x in part["items"] for w in x["term"].split(" · ")}


def build_view(snapshot: dict, today: date) -> dict:
    """snapshot: {'read_at', 'student_charge': {'items_count','rows'}, 'b2b_charge': {...}, and the items read by id:
    'enrollments', 'leads' (the deciding ones, since 12.11), 'cohorts', 'deals', 'accounts' (lists), 'lead_names'
    ({id: name or None} for every linked lead), 'lead_reps' ({id: Owner or None}, the same leads),
    'asked': {<the same five>: [ids asked]}}.
    A status cell arrives as two keys: `col` (its label text) and `col#` (its index)."""
    labels: Counter = Counter()
    sc, b2 = snapshot["student_charge"], snapshot["b2b_charge"]
    enrollments, leads, cohorts = (snapshot.get(k) or [] for k in ("enrollments", "leads", "cohorts"))
    names = snapshot.get("lead_names")
    srows, notes = build_b2c(sc["rows"], cohorts, enrollments, leads, today, labels, names, snapshot.get("lead_reps"))
    periods = b2c_periods(srows)
    brows, b2b_counts = build_b2b(b2["rows"], snapshot.get("deals", []), snapshot.get("accounts", []), today, labels)
    bstates = b2b_states(brows)

    whole = periods[0]["states"]   # every payment is in exactly one state
    raw_b2c = [_b2c_row(r, today, Counter()) for r in sc["rows"]]
    asked = snapshot.get("asked") or {}
    # what was asked for, by the same definitions the reader uses (a snapshot without `asked` derives them)
    enr_ids = {str(x) for x in asked.get("enrollments", [])} or set(enrollment_ids(sc["rows"]))
    if names is not None:
        # every lead a payment links to is asked for its name, so the check counts them all by name, as before 12.11 -
        # `asked["leads"]` is the deciding ones since, the rows a reading keeps
        lead_ids_ = set(names)
        leads_found = {i for i, n in names.items() if n is not None}
    else:
        lead_ids_ = {str(x) for x in asked.get("leads", [])} or set(lead_ids(sc["rows"]))
        leads_found = {str(x["id"]) for x in leads} & lead_ids_
    deciding = set(leads_deciding(sc["rows"], enrollments))
    coh_ids = {str(x) for x in asked.get("cohorts", [])} or set(cohort_ids(enrollments, [x for x in leads if str(x["id"]) in deciding]))
    deal_ids = {str(d) for d in asked.get("deals", [])} or {str(d) for r in b2["rows"] for d in r.get(B2["deal"]) or []}
    acct_ids = {str(a) for a in asked.get("accounts", [])} or (
        {str(a) for r in b2["rows"] for a in r.get(B2["account"]) or []}
        | {str(a) for d in snapshot.get("deals", []) for a in d.get(DL["payer"]) or []})
    checks = {
        "b2c_rows_read": len(sc["rows"]), "b2c_items_count": sc.get("items_count"),
        "b2c_rows_placed": len(srows), "b2c_rows_unique": len({x["id"] for x in srows}),
        "b2c_states_partition": sum(v["count"] for v in whole.values()) == len(sc["rows"]),
        "b2c_money_ties": _dsum(v["money_in"] for v in whole.values()) == _dsum(x["money_in"] for x in raw_b2c),
        "b2c_refunds_tie": _dsum(v["refunded"] for v in whole.values()) == _dsum(x["refunded"] for x in raw_b2c),
        "enrollments_found": len({str(x["id"]) for x in enrollments} & enr_ids), "enrollments_linked": len(enr_ids),
        "leads_found": len(leads_found), "leads_linked": len(lead_ids_),
        "cohorts_found": len({str(x["id"]) for x in cohorts} & coh_ids), "cohorts_linked": len(coh_ids),
        "b2b_rows_read": len(b2["rows"]), "b2b_items_count": b2.get("items_count"),
        "b2b_rows_placed": len(brows), "b2b_rows_unique": len({x["id"] for x in brows}),
        "b2b_states_partition": sum(v["count"] for v in bstates.values()) == len(b2["rows"]),
        "b2b_money_ties": _dsum(v["money"] for k, v in bstates.items() if k != "cancelled") == _dsum(
            (amount(r.get(B2["amount"])) or ZERO) for r in b2["rows"] if str(r.get(B2["billing"] + "#") or "") != B_CANCELLED),
        "deals_found": len({str(d["id"]) for d in snapshot.get("deals", [])} & deal_ids), "deals_linked": len(deal_ids),
        "accounts_found": len({str(a["id"]) for a in snapshot.get("accounts", [])} & acct_ids),
        "accounts_linked": len(acct_ids),
    }
    b2c_ok = (checks["b2c_rows_read"] == checks["b2c_items_count"] == checks["b2c_rows_placed"] == checks["b2c_rows_unique"]
              and checks["b2c_states_partition"] and checks["b2c_money_ties"] and checks["b2c_refunds_tie"])
    b2b_ok = (checks["b2b_rows_read"] == checks["b2b_items_count"] == checks["b2b_rows_placed"] == checks["b2b_rows_unique"]
              and checks["b2b_states_partition"] and checks["b2b_money_ties"])
    # The reading's own counts (app/desk/reading.py `checks`): a board read short, a row answered from another board, or a
    # row of the right board without a column it was asked for, makes its tab not whole here too. analiza-metrics could
    # not hold such a reading; this one can, and the Excel file already said so while the page drew it whole (D2's review).
    read_checks = snapshot.get("checks") or {}
    if read_checks:
        checks["b2c_read_whole"] = all((read_checks.get(k) or {}).get("ok") is True for k in TAB_BOARDS["b2c"])
        checks["b2b_read_whole"] = all((read_checks.get(k) or {}).get("ok") is True for k in TAB_BOARDS["b2b"])
        b2c_ok = b2c_ok and checks["b2c_read_whole"]
        b2b_ok = b2b_ok and checks["b2b_read_whole"]
    checks.update({"b2c_ok": bool(b2c_ok), "b2b_ok": bool(b2b_ok), "ok": bool(b2c_ok and b2b_ok)})

    notes.update(Counter(f for x in srows for f in x["flags"] if f in ("refund_exceeds_paid", "amount_missing")))
    notes["amount_missing_b2b"] = sum(1 for x in brows if "amount_missing" in x["flags"])
    notes["payer_mismatch"] = sum(1 for x in brows if "payer_mismatch" in x["flags"])
    notes["deals_split"] = b2b_counts["deals_split"]
    b2_cols, sc_cols = set(B2.values()), set(SC.values())
    for kind in ("drift", "unknown"):
        notes["label_%s_b2c" % kind] = sum(n for (k, col), n in labels.items() if k == kind and col in sc_cols)
        # the voucher status is an enrollment's: a label the desk does not know leaves no payment out of the money
        notes["voucher_label_%s" % kind] = labels[(kind, EN["voucher"])]
        notes["label_%s_b2b" % kind] = sum(n for (k, col), n in labels.items() if k == kind and col in b2_cols)
    notes["terms_unknown"] = b2b_counts["terms_unknown"]
    # a deal or customer monday no longer returns (archived, deleted): its charges still count, only the name is lost
    notes["enrollments_missing"] = checks["enrollments_linked"] - checks["enrollments_found"]
    notes["leads_missing"] = checks["leads_linked"] - checks["leads_found"]
    notes["deals_missing"] = checks["deals_linked"] - checks["deals_found"]
    notes["accounts_missing"] = checks["accounts_linked"] - checks["accounts_found"]
    return {
        "schema": VIEW_SCHEMA, "read_at": snapshot.get("read_at"), "today": today.isoformat(), "boards": BOARDS,
        "tabs": {"b2c": True, "b2b": True},
        "b2c": {"rows": srows, "periods": periods, "states": _states_out(B2C_STATES), "legend": _legend_out("b2c"),
                "tasks_about": TASKS_ABOUT["b2c"],
                # round 3, slice 3: what the page may write from this tab, as the person (DESK_ACTIONS)
                "actions": actions_out("sc"),
                "money_in": periods[0]["money_in"], "refunded": periods[0]["refunded"],
                "net": periods[0]["money_in"] - periods[0]["refunded"], "students": len({x["person"] for x in srows}),
                "count": len(srows), "since": min((x["date"] for x in srows if x["date"]), default=""),
                "dated_by_payment": sum(1 for x in srows if x["date_from"] == "paid")},
        "b2b": {"rows": brows, "states": _states_out(B2B_STATES), "legend": _legend_out("b2b"), "tasks_about": TASKS_ABOUT["b2b"],
                "actions": actions_out("b2"),
                # the ledger's form for a customer's billing details, written by the person in the browser (ADR-0188)
                "account_form": account_form(),
                "numbers": bstates, "customers": b2b_customers(brows),
                "by_customer": b2b_by_customer(brows, account_billing(snapshot.get("accounts", []))),
                "bands": [{"key": k, "label": v, "heading": band_heading(k, v)} for k, v in AGE_BANDS.items()],
                # the aging table's columns (the page drops an optional one exactly as `aging_columns` does)
                "aging_columns": [{"key": k, "header": h, "group": g, "kind": kind, "shown_when": when}
                                  for k, h, g, kind, when in AGING_COLUMNS],
                "count": len(brows),
                "open": _dsum(x["open_money"] for x in brows), "overdue": bstates["overdue"]["money"],
                # how much of "בפיגור" is counted from a demand day for lack of terms (QA 1.3): said beside it
                "overdue_no_terms": _dsum(x["amount"] for x in brows if x["state"] == "overdue" and "terms_missing" in x["flags"]),
                "deals": b2b_counts["deals_distinct"], "accounts_count": len({x["account_id"] for x in brows if x["account_id"]}),
                "vat_rate": VAT_RATE},
        "data_notes": {k: v for k, v in notes.items() if v},
        "label_drift": {col: n for (k, col), n in labels.items() if k == "drift"}, "checks": checks,
    }


B2C_NOTES = ("cohort_no_link", "cohort_no_cohort", "cohort_missing", "cohort_ambiguous", "enrollments_missing",
             "leads_missing", "refund_exceeds_paid", "amount_missing", "label_drift_b2c", "label_unknown_b2c",
             "voucher_label_unknown", "voucher_label_drift")   # "bank_unread": the server reads no bank account (ADR-0188)

B2B_NOTES = ("payer_mismatch", "deals_split", "deals_missing", "accounts_missing", "terms_unknown",
             "label_drift_b2b", "label_unknown_b2b", "amount_missing_b2b")


def visible_part(view: dict, b2c: bool, b2b: bool) -> dict:
    """The answer for one reader: a tab whose board the reader is not on is removed, not hidden - its rows, its
    states, its checks and its notes never leave the server (spec §2.2); and no ID number leaves it at all."""
    out = dict(view)
    out["tabs"] = {"b2c": bool(b2c), "b2b": bool(b2b)}
    if not b2c:
        out["b2c"] = None
    else:
        # no row carries an ID number (NEVER_READ: the server never reads one); the key is dropped all the same, so one
        # could never leave in the page's answer (analiza-metrics held it for the Excel file only)
        out["b2c"] = dict(view["b2c"], rows=[{k: v for k, v in x.items() if k != "id_number"} for x in view["b2c"]["rows"]])
    if not b2b:
        out["b2b"] = None
    keep = set((B2C_NOTES if b2c else ()) + (B2B_NOTES if b2b else ()))
    out["data_notes"] = {k: v for k, v in view["data_notes"].items() if k in keep}
    visible_cols = (B2C_COLUMNS if b2c else set()) | (set(B2.values()) if b2b else set())
    out["label_drift"] = {col: n for col, n in (view.get("label_drift") or {}).items() if col in visible_cols}
    checks = view["checks"]
    out["checks"] = {k: v for k, v in checks.items()
                     if (b2c and k.startswith(("b2c", "cohorts", "enrollments", "leads")))
                     or (b2b and (k.startswith("b2b") or k.startswith("deals") or k.startswith("accounts")))}
    out["checks"]["ok"] = bool((not b2c or checks["b2c_ok"]) and (not b2b or checks["b2b_ok"]))
    return out


# ---------------------------------------------------------------- the answer (analiza-metrics app/api.py at da671b2)
# The desk's day turns at Israel's midnight: a state, a due date and a period are read in its days.
DESK_TZ = "Asia/Jerusalem"


def json_safe(obj: Any) -> Any:
    """A Decimal as a string (never a float: an amount must round-trip exactly), a datetime or a date in ISO 8601, a
    dataclass as a dict, a set or a tuple as a list. FastAPI's own encoder would turn a Decimal into a float, which is
    exactly the loss the desk exists to avoid (analiza-metrics `json_safe`, the same rule)."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return json_safe(dataclasses.asdict(obj))
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {str(k): json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [json_safe(v) for v in obj]
    return obj


def today_in(tz_name: str) -> date:
    """The day in the given timezone; one the system does not know is UTC's day (analiza-metrics `today_in`, the same)."""
    try:
        return datetime.now(ZoneInfo(tz_name)).date()
    except (ZoneInfoNotFoundError, ValueError):
        return datetime.now(timezone.utc).date()
