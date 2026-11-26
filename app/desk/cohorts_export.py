"""The students by cohort as an Excel file (D4, Analiza-Monday backlog 0313): the view "לפי מחזור" exactly as the page
filters it (app/desk/cohorts.py `filter_cohorts`, the search box), in the desk's file conventions - app/desk/export.py's
own workbook, reused, never forked: right to left, the header frozen with a filter on it, amounts as numbers, every text
as text, a total under the rows that follows the filter, and "פרטים" saying what the file is.

THE SHEETS:
1. "לפי מחזור": a line per cohort, the page's columns in the page's order (`columns_for`: an optional part only when a
   cohort in the file has money in it), then what the page says beside a line: the waived and the out, what was paid over
   the price, the payments with no enrollment.
2. "סטודנטים": every student of those cohorts under the same money columns, each balance in its part's column, so each
   column of a cohort's students sums to that cohort's line (backlog 0313: "סכומי הסטודנטים שווים לשורת המחזור"). What a
   waived or an out student paid is in "שולם בפועל" and their expected, as on the line (the review of D4).
3. "בלי מחיר": the cohorts the view lists without money, and why.
No ID number: the reading never holds one (NEVER_READ).
"""
from __future__ import annotations

from datetime import date

from .cohorts import (BALANCE_WORD, EXPECTED_WORD, OUT_WORD, OVERPAID_WORD, PAID_WORD, PARTS, STUDENTS_WORD,
                      UNLINKED_WORD, UNPRICED_TITLE, VIEW_WORD, WAIVED_WORD, columns_for)
from .export import _workbook, file_part
from .view import FLOW_LABEL

# a column of the page's (app/desk/cohorts.py COLUMNS) as the file writes it: its value on a cohort's line and on a
# student, its kind, its width
_PRICE_H = "מחיר"


def _part(key: str) -> tuple:
    return (lambda x: x["part_" + key] or None), (lambda s: s["balance"] if s["part"] == key and s["balance"] else None)


_MONEY = {
    "expected": ((lambda x: x["expected"] or None), (lambda s: s["expected"] or None)),
    "paid": ((lambda x: x["paid"] or None), (lambda s: s["paid"] or None)),
    "balance": ((lambda x: x["balance"] or None), (lambda s: s["balance"] or None)),
    **{"part_" + k: _part(k) for k in PARTS},
}
WIDTH = {"cohort": 9, "price": 11, "students": 9}
IN_EXPECTED = "בצפי"   # a student counted, beside the waived and the out, whose line says why


def cohort_columns(lines: list) -> list:
    """The first sheet's columns: the page's own, the cohort as its code and its name, and after them what the page says
    beside a line."""
    out: list = []
    for col in columns_for(lines):
        key = col["key"]
        if key == "cohort":
            out += [("מחזור", lambda x: x["code"], "text", 9), ("שם המחזור", lambda x: x["title"], "text", 30),
                    (FLOW_LABEL, lambda x: x["flow"], "text", 18)]
        elif key == "price":
            out.append((col["header"], lambda x: x["price"], "amount", WIDTH["price"]))
        elif key == "students":
            out.append((col["header"], lambda x: x["students"], "int", WIDTH["students"]))
        else:
            out.append((col["header"], _MONEY[key][0], "money", 15))
    return out + [(WAIVED_WORD, lambda x: x["waived"], "int", 8), (OUT_WORD, lambda x: x["out"], "int", 10),
                  (OVERPAID_WORD, lambda x: x["overpaid"] or None, "money", 14),
                  (UNLINKED_WORD, lambda x: x["unlinked"]["money"] or None, "money", 15)]


def student_columns(lines: list) -> list:
    """The students' sheet: who, which cohort, their two statuses, then the very money columns of the first sheet."""
    out: list = [("מחזור", lambda s: s["code"], "text", 9), ("סטודנט/ית", lambda s: s["name"], "text", 26),
                 ("סטטוס שובר משה\"ע", lambda s: s["voucher"], "text", 22), ("Status", lambda s: s["status"], "text", 12),
                 ("בחשבון", lambda s: s["said"] or IN_EXPECTED, "text", 30)]
    for col in columns_for(lines):
        if col["key"] in _MONEY:
            out.append((col["header"], _MONEY[col["key"]][1], "money", 15))
    return out + [("מזהה במאנדיי", lambda s: s["id"], "text", 13)]


UNPRICED_COLUMNS: list = [("מחזור", lambda x: x["code"], "text", 9), ("שם המחזור", lambda x: x["title"], "text", 30),
                          (FLOW_LABEL, lambda x: x["flow"], "text", 18), ("Status", lambda x: x["status"], "text", 12),
                          ("למה", lambda x: x["why_text"], "text", 22), (_PRICE_H, lambda x: x["price_text"], "text", 16)]


def students_of(lines: list) -> list:
    """Every student of these cohorts, in the page's order, with their cohort's code."""
    return [dict(s, code=x["code"]) for x in lines for s in x["students_list"]]


def cohorts_workbook(lines: list, unpriced: list, about: dict) -> bytes:
    """lines: the cohorts with a price the page shows (cohorts.filter_cohorts); unpriced: those without one it lists. about:
    the reading and the filter, for "פרטים"."""
    students = students_of(lines)
    said = ["סכומי הסטודנטים של כל מחזור, בכל עמודת כסף, שווים לשורת המחזור בגיליון \"%s\"." % VIEW_WORD,
            "%s כולל מה ששילמו סטודנטים עם %s או %s, ואצלם %s הוא אותו סכום." % (PAID_WORD, WAIVED_WORD, OUT_WORD,
                                                                              EXPECTED_WORD)]
    sheets = [("סטודנטים", student_columns(lines), students, said),
              ("בלי מחיר", UNPRICED_COLUMNS, list(unpriced), ["%s: מוצגים בלי %s ובלי %s." % (UNPRICED_TITLE, EXPECTED_WORD,
                                                                                            BALANCE_WORD)])]
    return _workbook(VIEW_WORD, cohort_columns(lines), lines, about, "התלמידים לפי מחזור מתוך שולחן הכספים",
                     [("הסכומים", "כולל מע\"מ, כמו בלוח Student Charge. %s: המחיר כפול %s, ועוד מה ששילמו סטודנטים "
                                   "עם %s או %s. %s: כל מה שנכנס. %s: %s פחות %s לכל סטודנט/ית, ולא פחות מ-0, מחולקת "
                                   "לפי סטטוס השובר" % (EXPECTED_WORD, STUDENTS_WORD, WAIVED_WORD, OUT_WORD, PAID_WORD,
                                                       BALANCE_WORD, _PRICE_H, PAID_WORD)),
                      ("החלקים", " · ".join(p["label"] for p in PARTS.values())),
                      ("סטודנטים", "%d בגיליון \"סטודנטים\"" % len(students)),
                      (UNPRICED_TITLE, "%d בגיליון \"בלי מחיר\"" % len(unpriced))], sheets=sheets)


def cohorts_file_names(q: str, today: date) -> tuple:
    """The file's name names its view, and its search when there is one (the page's other files name their list)."""
    return ("לפי-מחזור_%s%s.xlsx" % (("חיפוש-" + file_part(q) + "_") if q.strip() else "", today.strftime("%d.%m.%Y")),
            "finance-by-cohort-%s.xlsx" % today.isoformat())
