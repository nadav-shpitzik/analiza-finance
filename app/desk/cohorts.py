"""The desk's students by cohort, "לפי מחזור" (Analiza-Monday backlog 0313; D4 of backlog 0301): Chava's request of 26.11,
built before the switch on Nadav's go. In each cohort with a price: what its students should pay, what came in, and what
is still to come, each student's balance in one of Chava's parts by the voucher status of their enrollment.

WHERE EACH NUMBER COMES FROM (the backlog, from the runbook voucher-completion-payment and the boards, read 26.11):
1. THE PRICE: the cohort's "מחיר" (`text_mm5fhydb`), a TEXT column typed "10,000" or "12000": one clean number (`price_of`),
   or the cohort is listed without money and says why (UNPRICED_WHY): no price, a price that is not one number, a price of
   0, or a cohort whose Status is "בוטל". The student pays the whole price; the voucher is the ministry's refund to the
   student (Annex ו'), so no voucher is money the desk counts.
2. THE STUDENTS: from Enrollments, not from the payments - every enrollment whose own cohort link names the cohort (the
   reading reads them through the cohort's link to its enrollments, app/desk/reading.py `roster_ids`). A student who paid
   nothing owes the whole price.
   - In the count: an enrollment whose Status (`color_mm0sxcvn`) is one of ENROLLMENT_IN, by index: נרשם (1), פעיל (2),
     At Risk (3) and סיים (4) - the "IN" set the helper's enrollee count keeps (knowledge b2c-flow, Cohorts
     `numeric_mm4dhhjx`, "IN statuses נרשם/פעיל/At Risk/סיים"; its rollup: "label INDEX in {1,2,3,4}"). Any other -
     Registered (0, unused), עבר לקורס אחר (5), ביטל השתתפות (6), Frozen (7), the retired ביקשה החזר (8), an empty cell, a
     label added later - is out, as the helper counts it, and listed with why, never dropped.
   - One student per person: a person's enrollments in the cohort (their Directory link, `board_relation_mm0spa0g`) are one
     student, as the helper counts; an enrollment without the link is a student of its own.
   - An enrollment linked to two cohorts is in neither count: it is listed in both, out, with why.
   - An archived enrollment or cohort is read as any other (the reader keeps it, its `state` saying so): a refresh cannot
     see an item archived - its time does not move - and the desk lays a refresh over a full reading the same either way.
     An archived enrollment is counted until its Status says otherwise, as the helper's count keeps it until the cohort
     is touched (knowledge backlog 0157).
3. WHAT A STUDENT PAID: the Student Charge rows linked to their enrollments in the cohort, each row's `net` - what the desk
   counts as "התקבלו" less "הוחזרו" (app/desk/view.py `_b2c_row`, its money reused, never forked: only a status that brought
   money in, a refund netted, never above what came in). The deposit, and the balance row the helper opens when sales mark
   "אושר - יתרה שולמה", "<student> | השלמת תשלום". A row linked to the enrollments of two students, or of two cohorts, is
   counted for no one and said on each cohort it names (`shared`): it would be counted twice otherwise. A payment the desk
   files under the cohort by its lead, with no enrollment, is said on the cohort (`unlinked`) and counted for no student.
4. WHERE THE BALANCE IS: the price less what the student paid, never below 0 (more is `overpaid`, said, and never netted
   against another student's balance). It sits in one part by the voucher status (`color_mm5fr6d6`, by INDEX, never by its
   text; the runbook's live set): PARTS, in Chava's order (her answer of 26.11) - "אושר - ממתין לתשלום" (1) first,
   "הוגש – ממתין להחלטה" (0), "בטיפול מטה" or "ממתין לתקציב" (8, 9), "טרם הוגש" (17) - and two of the desk's own:
   "אושר - יתרה שולמה" (4) with a balance left, where the payments do not show what sales marked, and "בלי סטטוס שובר": an
   empty status, the grey empty label (5), a label this module does not know, or a person whose enrollments say two
   statuses. Never hidden. "נדחה" and "נדחה - קיבל מלגה מאנליזה" (2, 3) are waived: no balance, the waiver recorded on the
   enrollment only. "ביטל השתתפות" (6, 7) and "לא רלוונטי" (10) are out of the count.

THE SUMS TIE: a cohort's every figure is the sum of its students' - the expected (the price for each student counted), what
they paid, each part, the balance and what was paid over the price - and expected = paid + balance - overpaid, to the
agora (Decimal end to end). `checks["ties"]` says so, and the tests hold it. Waived and out students are listed with what
they paid, in no sum of the cohort's line but their own (`waived_paid`, `out_paid`).

WHAT IT IS NOT: a writer, or a second money rule. It reads the reading and the desk's own payment rows; nothing here asks
monday, and the page adds no money up (frontend/finance.js draws these sums, and the totals of a filtered list come from
GET /api/desk/totals).
"""
from __future__ import annotations

import re
import unicodedata
from collections import OrderedDict, defaultdict
from decimal import Decimal
from typing import Any, Optional

from .reading import CO, COHORT_CANCELLED, EN, SC, _uniq
from .view import UNNAMED_LABEL, VOUCHER, ZERO, _cohort_meta, _dsum, _q, label_of

# The enrollment's Status (color_mm0sxcvn) that is in the count, by index: the helper's "IN" set
ENROLLMENT_IN = OrderedDict([("1", "נרשם"), ("2", "פעיל"), ("3", "At Risk"), ("4", "סיים")])
# The voucher status (color_mm5fr6d6) by index: its part, waived, or out. Any other index - "" (empty), "5" (the grey empty
# label), one added in monday later - is "no_status"
PART_OF = {"1": "approved", "0": "submitted", "8": "hq", "9": "hq", "17": "not_submitted", "4": "settled"}
WAIVED = ("2", "3")       # נדחה, נדחה - קיבל מלגה מאנליזה: the waiver is recorded on the enrollment only
OUT = ("6", "7", "10")    # ביטל השתתפות - דחייה, ביטל השתתפות - אחר, לא רלוונטי: outside the count

# ---------------------------------------------------------------- the words (the page, its legend and the Excel file)
VIEW_WORD = "לפי מחזור"
PRICE_WORD = "מחיר"                    # monday's own title, on the Cohorts board
STUDENTS_WORD = "סטודנטים בצפי"         # not the tab's "סטודנטים" (people by their payments)
EXPECTED_WORD, PAID_WORD, BALANCE_WORD = "צפי תשלום", "שולם בפועל", "יתרה לגבייה"   # the request's words (backlog 0313)
OVERPAID_WORD = "שולם מעבר למחיר"
WAIVED_WORD, OUT_WORD = "ויתור", "מחוץ לחשבון"
NO_STATUS_LABEL = "בלי סטטוס שובר"
UNPRICED_TITLE = "מחזורים בלי מחיר"
UNLINKED_WORD, SHARED_WORD = "תקבולים בלי הרשמה", "תקבול של כמה סטודנטים"
_IN_LABELS = ", ".join(_q(x) for x in list(ENROLLMENT_IN.values())[:-1]) + " או " + _q(list(ENROLLMENT_IN.values())[-1])

# Chava's parts, in her order, each in her words (backlog 0313, her answer of 26.11); then the desk's two. `about` is the
# legend's sentence, the part's label is its column's header
PARTS = OrderedDict([
    ("approved", {"label": VOUCHER["1"], "about": "השובר אושר, והיתרה עוד לא שולמה: כסף שכבר היה צריך להיכנס, הכי חשוב. "
                                                  "ראשון במסך, ומודגש. אחרי הגבייה מכירות מסמנים %s." % _q(VOUCHER["4"])}),
    ("submitted", {"label": VOUCHER["0"], "about": "הבקשה לשובר הוגשה, וממתינה להחלטה: צפי תוך שבועיים."}),
    ("hq", {"label": "%s / %s" % (VOUCHER["8"], VOUCHER["9"]),
            "about": "%s או %s: צפי תוך חודשיים." % (_q(VOUCHER["8"]), _q(VOUCHER["9"]))}),
    ("not_submitted", {"label": VOUCHER["17"], "about": "הבקשה לשובר עוד לא הוגשה: צפי לא ודאי. מוצג בנפרד ולא נמחק, כדי "
                                                        "לראות כמה כסף תלוי שם."}),
    ("settled", {"label": VOUCHER["4"] + ", וחסר בתקבולים",
                 "about": "סטטוס השובר %s, ובתקבולים של הסטודנט/ית נכנס פחות מהמחיר: שורת %s חסרה, או שהסכום בה אחר. "
                          "לבדוק את התקבולים. העמודה מופיעה רק כשיש בה סכום." % (_q(VOUCHER["4"]), _q("השלמת תשלום"))}),
    ("no_status", {"label": NO_STATUS_LABEL,
                   "about": "סטטוס השובר ריק, תווית שהמסך לא מכיר, או שתי הרשמות של אותו/ה סטודנט/ית עם שני סטטוסים: "
                            "היתרה מוצגת כאן, ולא נעלמת."}),
])
PART_INDEX = {k: i for i, k in enumerate(PARTS)}
# a part's column is on the page and in the file only when a cohort shown has something in it ("settled": normally none)
PART_OPTIONAL = ("settled",)

UNPRICED_WHY = OrderedDict([("empty", "אין מחיר"), ("unreadable", "המחיר אינו מספר אחד"), ("zero", "המחיר 0"),
                            ("cancelled", "המחזור בוטל")])

# what the page says above the table, and the legend's part on the students' tab
ABOUT = ("לכל מחזור עם מחיר: %s (המחיר כפול הסטודנטים), %s, ו%s - מחולקת לפי סטטוס השובר של כל סטודנט/ית, ו%s ראשון: "
         "כסף שכבר היה צריך להיכנס. לחיצה על מחזור פותחת את הסטודנטים שלו, וכל יתרה של סטודנט/ית בעמודה של החלק שלה. "
         "הקוד של מחזור והשם של סטודנט/ית פותחים אותם במאנדיי."
         % (EXPECTED_WORD, PAID_WORD, BALANCE_WORD, _q(VOUCHER["1"])))
LEGEND_TITLE = VIEW_WORD
LEGEND = [
    (VIEW_WORD, ABOUT),
    (STUDENTS_WORD,
     "ההרשמות של המחזור בלוח ההרשמות שה-Status שלהן %s, הרשמה אחת לכל אדם (לפי Person); בלי מי שהשובר שלו נדחה, ביטל "
     "השתתפות או לא רלוונטי. גם מי שלא שילם/ה כלום." % _IN_LABELS),
    (EXPECTED_WORD, "המחיר כפול הסטודנטים: מה שהיה צריך להיכנס מכולם."),
    (PAID_WORD, "מה שכבר נכנס: סכום ששולם בתקבולים של ההרשמה בלוח Student Charge, %s פחות %s, כמו בשאר המסך - המקדמה, "
                "ושורת %s שנפתחת כשמכירות מסמנים %s." % (_q("התקבלו"), _q("הוחזרו"), _q("השלמת תשלום"), _q(VOUCHER["4"]))),
    (BALANCE_WORD, "לכל סטודנט/ית: המחיר פחות %s, ולא פחות מ-0. היתרה של המחזור היא סכום היתרות, מחולקת לחלקים לפי סטטוס "
                   "השובר:" % PAID_WORD),
] + [(p["label"], p["about"]) for p in PARTS.values()] + [
    (WAIVED_WORD, "שובר %s או %s: הוויתור על היתרה מתועד בהרשמה בלבד, ולכן אין יתרה. מוצג ברשימה, ולא נספר בצפי."
                  % (_q(VOUCHER["2"]), _q(VOUCHER["3"]))),
    (OUT_WORD, "%s, %s או %s, הרשמה שה-Status שלה אינו %s, או הרשמה שמקושרת ליותר ממחזור אחד: מוצגת ברשימה עם הסיבה, "
               "ולא נספרת בצפי וביתרה." % (_q("ביטל השתתפות - דחייה"), _q("ביטל השתתפות - אחר"), _q(VOUCHER["10"]),
                                           _IN_LABELS)),
    (OVERPAID_WORD, "סטודנט/ית ששילם/ה יותר מהמחיר: היתרה 0, והעודף מוצג ליד השם ובשורת המחזור, ולא מקטין יתרה של אחר/ת."),
    ("סכום מחוק", "ב%s: מה ששילם/ה סטודנט/ית עם ויתור או מחוץ לחשבון. מוצג, ולא נספר בשורת המחזור." % PAID_WORD),
    (UNLINKED_WORD, "תקבולים שהמסך משייך למחזור לפי הליד מקור, כי אין להם הרשמה: לא נספרים לאף סטודנט/ית."),
    (SHARED_WORD, "תקבול שמקושר להרשמות של כמה סטודנטים, או של כמה מחזורים: לא נספר לאף אחד, כדי שלא ייספר פעמיים."),
    (UNPRICED_TITLE, "מחזורים בקריאה בלי מחיר, שהמחיר בהם אינו מספר אחד או 0, או שבוטלו: מוצגים עם הסיבה, בלי צפי ובלי "
                     "יתרה."),
    (PRICE_WORD, "עמודה בלוח המחזורים: המחיר המלא שהסטודנט/ית משלם/ת. השובר הוא ההחזר של משרד העבודה לסטודנט/ית "
                 "(נספח ו'), ולכן אינו יורד מהמחיר."),
    ("Status", "עמודה בלוח ההרשמות: מצב ההרשמה, למשל %s או %s." % (_q("נרשם"), _q("ביטל השתתפות"))),
]

# A student's chips: the server's words, the page draws them as sent; each is in the legend (NOTES_SAID)
NOTES = {"voucher_conflict": "בהרשמות של הסטודנט/ית שני סטטוסי שובר",
         "voucher_unknown": "תווית שובר שהמסך לא מכיר",
         "payment_uncounted": "תקבול שהמסך לא סופר בכסף",
         "overpaid": OVERPAID_WORD,
         "enrollments": "כמה הרשמות במחזור"}
NOTES_SAID = {"voucher_conflict": "לאותו אדם כמה הרשמות במחזור, ובכל אחת סטטוס שובר אחר: היתרה ב%s, עד שיתוקן בהרשמה."
                                  % _q(NO_STATUS_LABEL),
              "voucher_unknown": "בסטטוס השובר תווית שנוספה במאנדיי אחרי שהמסך נבנה: היתרה ב%s." % _q(NO_STATUS_LABEL),
              "payment_uncounted": "לסטודנט/ית תקבול שסכום ששולם בו לא הוקלד, או שבסטטוס התשלום שלו תווית שהמסך לא מכיר: "
                                   "הוא לא נספר ב%s, ולכן היתרה גדולה ממה שבאמת חסר." % _q(PAID_WORD),
              "enrollments": "לאותו אדם כמה הרשמות במחזור: הן סטודנט/ית אחד/ת, והתקבולים של כולן נספרים יחד."}
# said above the table when a line does not tie to its students: a fault of the server's, and the file is not exported
NOT_TIED = "הסכומים של מחזור לא מתאזנים עם הסטודנטים שלו. זו תקלה בשרת, ונדב צריך לבדוק אותה."
OUT_WHY = {"status": "Status", "voucher": "סטטוס השובר", "cohorts": "ההרשמה מקושרת ליותר ממחזור אחד"}

# The table's columns, once, for the page and its Excel file: (key, header, kind, shown_when). A column with `shown_when`
# (a part's key) is drawn only when a cohort shown has money in it - on the page and in the file alike. The page says the
# price and the students counted under the cohort's name (FOLDED), so its table fits monday's widths of 1280 and 1400 with
# sums as long as real ones (QA 4 of 24.11); the file keeps them as columns
COLUMNS = [("cohort", "מחזור", "text", ""), ("price", PRICE_WORD, "amount", ""), ("students", STUDENTS_WORD, "int", ""),
           ("expected", EXPECTED_WORD, "money", ""), ("paid", PAID_WORD, "money", "")] + \
          [("part_" + k, p["label"], "money", k if k in PART_OPTIONAL else "") for k, p in PARTS.items()] + \
          [("balance", BALANCE_WORD, "money", "")]
FOLDED = ("price", "students")
# what a cohort's line and the totals sum (a part as part_<key>)
SUM_FIELDS = ("expected", "paid", "balance", "overpaid", "waived_paid", "out_paid")
COUNT_FIELDS = ("students", "waived", "out")


# ---------------------------------------------------------------- the price
_PRICE = re.compile(r"(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]{1,2})?")
_SHEKEL = ("₪", "ש\"ח", "ש״ח")


def price_of(text: Any) -> tuple:
    """(the price, "") from a cohort's "מחיר" as typed: one number, its thousands with commas or without, at most agorot,
    a "₪" or "ש"ח" beside it - or (None, why): "empty"; "unreadable", anything else (two numbers, a range, words, a "10 000",
    a number the reading masked as an ID); "zero". Never a guess: a price that is not one clean number is said, not read."""
    t = unicodedata.normalize("NFKC", str(text or "")).strip()
    for s in _SHEKEL:
        t = t.replace(s, "")
    t = t.strip()
    if not t:
        return None, "empty"
    if not _PRICE.fullmatch(t):
        return None, "unreadable"
    value = Decimal(t.replace(",", ""))
    return (value, "") if value > 0 else (None, "zero")


# ---------------------------------------------------------------- the students
def _id_key(row: dict) -> tuple:
    """An item in id order (numeric, as its digits): what a list is read in, so nothing depends on the order read."""
    s = str(row["id"])
    return len(s), s


def _person(e: dict) -> str:
    """Who an enrollment is: its Directory person, or the enrollment itself without one (the helper's rule)."""
    ids = _uniq(e.get(EN["person"]) or [])
    return "P:" + ids[0] if ids else "E:" + str(e["id"])


def _why_out(e: dict, cohorts_of: dict) -> str:
    """Why an enrollment is not in its cohort's count ("" when it is): linked to more than one cohort, or a Status not IN."""
    if len(cohorts_of[str(e["id"])]) > 1:
        return "cohorts"
    return "" if label_of(e, EN["status"])[0] in ENROLLMENT_IN else "status"


def _label_said(idx: str, label: str) -> str:
    """A status as the page writes it: its label, "תווית בלי שם" for a value with no label, "ריק" for an empty cell."""
    return label or (UNNAMED_LABEL if idx else "ריק")


def _student(enrollments: list, cohorts_of: dict, price: Decimal, payments: list) -> dict:
    """One student of a cohort: their enrollments there (id order), what they paid, and where their balance is."""
    ins = [e for e in enrollments if not _why_out(e, cohorts_of)]
    first = (ins or enrollments)[0]
    s_idx, s_label = label_of(first, EN["status"])
    notes: list = []
    standing, part, said = "counted", "", ""
    if not ins:
        standing, why = "out", _why_out(first, cohorts_of)
        v_idx, v_label = label_of(first, EN["voucher"])
        said = "%s: %s" % (OUT_WORD, OUT_WHY["cohorts"] if why == "cohorts"
                           else "%s %s" % (OUT_WHY["status"], _q(s_label) if s_label else _label_said(s_idx, s_label)))
    else:
        vouchers = OrderedDict()
        for e in ins:
            idx, label = label_of(e, EN["voucher"])
            if idx:
                vouchers.setdefault(idx, label)
        if len(vouchers) > 1:
            v_idx, v_label, part = "", " / ".join(_label_said(i, x) for i, x in vouchers.items()), "no_status"
            notes.append("voucher_conflict")
        else:
            v_idx, v_label = next(iter(vouchers.items()), ("", ""))
            if v_idx in WAIVED:
                standing, said = "waived", "%s: %s" % (WAIVED_WORD, _q(v_label or VOUCHER[v_idx]))
            elif v_idx in OUT:
                standing, said = "out", "%s: %s" % (OUT_WORD, _q(v_label or VOUCHER[v_idx]))
            else:
                part = PART_OF.get(v_idx, "no_status")
                if v_idx and v_idx not in VOUCHER:
                    notes.append("voucher_unknown")
    paid = _dsum(p["net"] for p in payments)
    counted = standing == "counted"
    balance = max(price - paid, ZERO) if counted else ZERO
    overpaid = max(paid - price, ZERO) if counted else ZERO
    if any({"amount_missing", "status_unknown"} & set(p.get("flags") or ()) for p in payments):
        notes.append("payment_uncounted")
    if overpaid:
        notes.append("overpaid")
    if len(enrollments) > 1:
        notes.append("enrollments")
    return {"id": str(first["id"]), "name": first.get("name") or "", "enrollments": [str(e["id"]) for e in enrollments],
            "status": _label_said(s_idx, s_label), "status_idx": s_idx,
            "voucher": _label_said(v_idx, v_label), "voucher_idx": v_idx,
            "standing": standing, "part": part, "said": said,
            "price": price if counted else None, "paid": paid, "balance": balance, "overpaid": overpaid,
            "payments": [str(p["id"]) for p in payments],
            "notes": [NOTES[k] for k in notes], "flags": notes}


def _student_order(s: dict) -> tuple:
    """Counted first, by Chava's parts and the largest balance; then waived; then out; by name within."""
    return ({"counted": 0, "waived": 1, "out": 2}[s["standing"]], PART_INDEX.get(s["part"], len(PARTS)), -s["balance"],
            s["name"], s["id"])


# ---------------------------------------------------------------- a cohort
def _sums(students: list) -> dict:
    """A cohort's figures, as the sums of its students': counted students only, but what waived and out ones paid."""
    counted = [s for s in students if s["standing"] == "counted"]
    out = {"students": len(counted), "waived": sum(1 for s in students if s["standing"] == "waived"),
           "out": sum(1 for s in students if s["standing"] == "out"),
           "expected": _dsum(s["price"] for s in counted), "paid": _dsum(s["paid"] for s in counted),
           "overpaid": _dsum(s["overpaid"] for s in counted),
           "waived_paid": _dsum(s["paid"] for s in students if s["standing"] == "waived"),
           "out_paid": _dsum(s["paid"] for s in students if s["standing"] == "out")}
    for k in PARTS:
        out["part_" + k] = _dsum(s["balance"] for s in counted if s["part"] == k)
    out["balance"] = _dsum(out["part_" + k] for k in PARTS)
    # how many counted students still owe in each part: the count beside a part's money
    out["owing"] = {k: sum(1 for s in counted if s["part"] == k and s["balance"] > 0) for k in PARTS}
    return out


def _ties(line: dict) -> bool:
    """The cohort's line is its students' sums, and its expected is what was paid and what is still to come."""
    again = _sums(line["students_list"])
    same = all(again[f] == line[f] for f in SUM_FIELDS + COUNT_FIELDS + tuple("part_" + k for k in PARTS))
    return same and line["expected"] == line["paid"] + line["balance"] - line["overpaid"] \
        and line["expected"] == line["price"] * line["students"]


def build_cohorts(snapshot: dict, rows: list) -> dict:
    """The students by cohort of a reading (`snapshot`, app/desk/reading.py's shape) and the desk's payment rows built from
    it (`rows`, app/desk/view.py build_b2c): every cohort the reading holds, those with a price as lines and the rest listed
    with why; and what the page and the file need to draw them."""
    cohorts = snapshot.get("cohorts") or []
    enrollments = sorted((e for e in snapshot.get("enrollments") or [] if isinstance(e, dict)), key=_id_key)
    raw = (snapshot.get("student_charge") or {}).get("rows") or []
    meta = _cohort_meta(cohorts)
    built = {str(r["id"]): r for r in rows}
    cohorts_of = {str(e["id"]): _uniq(e.get(EN["cohort"]) or []) for e in enrollments}
    person_of = {str(e["id"]): _person(e) for e in enrollments}

    # each payment to the one (cohort, person) its enrollments name; one that names more is no one's (`shared`)
    paid_by: dict = defaultdict(list)
    shared: dict = defaultdict(list)
    for r in raw:
        b = built.get(str(r["id"]))
        if b is None:
            continue
        pairs = list(dict.fromkeys((c, person_of[e]) for e in (str(x) for x in r.get(SC["enrollment"]) or [])
                                   if e in cohorts_of for c in cohorts_of[e]))
        if len(pairs) == 1:
            paid_by[pairs[0]].append(b)
        else:
            for c in dict.fromkeys(c for c, _p in pairs):
                shared[c].append(b)
    unlinked: dict = defaultdict(list)   # the payments the desk files under a cohort by their lead: no enrollment
    for b in rows:
        if b.get("cohort_from") == "lead" and b.get("cohort_id"):
            unlinked[b["cohort_id"]].append(b)
    held = set(cohorts_of)
    members: dict = defaultdict(list)    # each cohort's enrollments, by their own link, in id order
    for e in enrollments:
        for c in cohorts_of[str(e["id"])]:
            members[c].append(e)

    lines, unpriced = [], []
    for c in sorted(cohorts, key=_id_key):
        cid = str(c["id"])
        m = meta[cid]
        price, why = price_of(c.get(CO["price"]))
        if str(c.get(CO["status"] + "#") or "") == COHORT_CANCELLED:
            price, why = None, "cancelled"
        head = {"id": cid, "code": m["code"], "title": m["title"] or m["name"], "name": m["name"],
                "status": m["status"], "flow": m["flow"], "start": m["start"], "end": m["end"],
                "price_text": str(c.get(CO["price"]) or "").strip()}
        if price is None:
            unpriced.append(dict(head, why=why, why_text=UNPRICED_WHY[why]))
            continue
        groups: dict = OrderedDict()
        for e in members[cid]:
            groups.setdefault(person_of[str(e["id"])], []).append(e)
        students = sorted((_student(es, cohorts_of, price, paid_by.get((cid, p), [])) for p, es in groups.items()),
                          key=_student_order)
        link = _uniq(c.get(CO["roster"]) or [])
        line = dict(head, price=price, students_list=students, **_sums(students),
                    unlinked={"count": len(unlinked[cid]), "money": _dsum(b["net"] for b in unlinked[cid])},
                    shared={"count": len(shared[cid]), "money": _dsum(b["net"] for b in shared[cid])},
                    # the cohort's link names an enrollment the reading did not get, or one whose own link is elsewhere
                    roster_missing=sum(1 for e in link if e not in held),
                    roster_elsewhere=sum(1 for e in link if e in held and cid not in cohorts_of[e]))
        lines.append(line)
    # the money that should already have come in first, then the most still to come, the newest cohort, its code
    lines.sort(key=lambda x: (x["code"], x["id"]))
    lines.sort(key=lambda x: x["start"], reverse=True)
    lines.sort(key=lambda x: (-x["part_approved"], -x["balance"]))
    unpriced.sort(key=lambda x: (x["code"], x["id"]))
    unpriced.sort(key=lambda x: x["start"], reverse=True)
    return {"cohorts": lines, "unpriced": unpriced, "totals": cohort_totals(lines),
            "parts": [{"key": k, "label": p["label"], "about": p["about"]} for k, p in PARTS.items()],
            "columns": [{"key": k, "header": h, "kind": kind, "shown_when": when, "folded": k in FOLDED}
                        for k, h, kind, when in COLUMNS],
            "about": ABOUT, "legend": {"title": LEGEND_TITLE, "items": [{"term": t, "text": x} for t, x in LEGEND]
                                       + [{"term": NOTES[k], "text": NOTES_SAID[k]} for k in NOTES_SAID]},
            # the view's own words, which the page draws beside the server's numbers
            "words": {"view": VIEW_WORD, "expected": EXPECTED_WORD, "paid": PAID_WORD, "balance": BALANCE_WORD,
                      "students": STUDENTS_WORD, "waived": WAIVED_WORD, "out": OUT_WORD, "overpaid": OVERPAID_WORD,
                      "unlinked": UNLINKED_WORD, "shared": SHARED_WORD, "unpriced": UNPRICED_TITLE, "not_tied": NOT_TIED},
            "unpriced_why": [{"key": k, "label": v} for k, v in UNPRICED_WHY.items()],
            "checks": {"ties": all(_ties(x) for x in lines)
                       and cohort_totals(lines) == _totals_by_student(lines)}}


# ---------------------------------------------------------------- the lists, as the page shows them
def cohort_totals(lines: list) -> dict:
    """The totals of these cohorts' lines (the page's footer, GET /api/desk/totals, the Excel file's): each figure summed,
    a part as part_<key>, and how many cohorts."""
    out: dict = {f: _dsum(x[f] for x in lines) for f in SUM_FIELDS + tuple("part_" + k for k in PARTS)}
    out.update({f: sum(x[f] for x in lines) for f in COUNT_FIELDS})
    out["count"] = len(lines)
    return out


def _totals_by_student(lines: list) -> dict:
    """The same totals summed from every student of every line, not from the lines: what `checks["ties"]` compares."""
    return cohort_totals([dict(x, **_sums(x["students_list"])) for x in lines])


def columns_for(lines: list) -> list:
    """The table's columns for these cohorts: an optional part only when one of them has money in it (the page drops it
    exactly so, frontend/finance.js cohortCols, and says the FOLDED two under the cohort's name instead of as columns)."""
    return [{"key": k, "header": h, "kind": kind} for k, h, kind, when in COLUMNS
            if not when or any(x["part_" + when] for x in lines)]


def cohort_matches(line: dict, q: str) -> bool:
    """The search box on the view, the same rule as frontend/finance.js `cohortMatch`: the text inside the cohort's code,
    its title, its name, or one of its students' names."""
    q = (q or "").strip().lower()
    if not q:
        return True
    hay = " ".join([line.get("code") or "", line.get("title") or "", line.get("name") or ""]
                   + [s["name"] for s in line.get("students_list") or []]).lower()
    return q in hay


def filter_cohorts(part: Optional[dict], q: str) -> list:
    """The cohorts with a price as the page shows them for this search, in their order. The Excel file is built from this."""
    return [x for x in ((part or {}).get("cohorts") or []) if cohort_matches(x, q)]


def filter_unpriced(part: Optional[dict], q: str) -> list:
    """The cohorts without a price as the page lists them for this search."""
    return [x for x in ((part or {}).get("unpriced") or []) if cohort_matches(x, q)]
