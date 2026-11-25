"""The finance desk's Excel files (Analiza-Monday backlog 0301 D2; analiza-metrics backlog 0280, Nadav 10.11: "וכמובן
יכולת ייצוא לאקסל!").

Ported on 24.11 from analiza-metrics `app/finance_export.py` at `da671b2`, unchanged but for the ID number: the students'
file carries none (below).

WHAT IT IS. The students list exactly as the page filters it (`view.filter_b2c`: a state, a period, the
search box), as an .xlsx a bookkeeper opens in Excel: a right-to-left sheet, the header frozen with a filter
on it, dates as dates, amounts as numbers, references as text, and under the rows a total that follows the
filter (SUBTOTAL 109). A second sheet says what the file is: when monday was read, which filter, how many rows, and
each total again as a number, the file's as made (QA 24.11: a preview, on a phone, may show the formula empty).
The companies list (`view.filter_b2b`) and the aging by customer (`view.b2b_customers`) the same way.

The conventions, and why not CSV, are in the knowledge repo:
docs/research/2026-11-10-finance-desk-bookkeeper-practice.md §4.

WHAT IT CARRIES AND LEAVES OUT. No ID number. analiza-metrics' file had a "ת.ז" column, read live from monday for the
rows it exported (Nadav 10.11: "כדאי שתהיה"); this service never reads one (NEVER_READ in app/monday/client.py; ADR-0173
§1, an ID number is never stored server side), so the column is gone and the file says so in its second sheet, at its
head (`ID_NUMBERS_NOT_IN_FILE`). The page shows a payment's ID number read in the browser as the person. Bank details
are out: they belong to the refund, in monday. Amounts are as the board holds them, including VAT; no VAT column is
computed here.

NAMES (round 3, 11.11, the one-name norm): a column that holds a monday column is headed by that column's monday
name, exactly; only what the desk computes keeps its own word ("שיבוץ", "מועד פירעון", "מועד אחרון להחזר", the aging
bands), the same word as on the page and in its legend. A refund row carries its two dates side by side:
"מועד אחרון להחזר" (the legal day) and "מועד העברה מתוכנן" (the transfer round).

WHAT IS WRONG WITH A ROW (QA 24.11): the column "מה לבדוק", on every row that has something and not only in "חריג",
says it in the page's chips (view.row_reasons: their words, their order, all of them where the page shows five).
"סכום החזר" is monday's own, as typed (a 6,000 refund on a 5,000 payment is 6,000: that is why its row is in "חריג");
the refund the desk counts, capped and with a full refund's amount where none was typed, is beside it under the
page's own word for it, "הוחזרו", and the totals count that one, as the page does.
"""
from __future__ import annotations

import io
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Callable
from urllib.parse import quote

from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .view import (B2B_STATES, B2C_STATES, BOOKS_AS_OF_WORD, BOOKS_CANDIDATE, BOOKS_DOC_HEADS, BOOKS_SUM_WORD,
                   BOOKS_UNCLEAR_WHY, DUE_RULE,
                   FLOW_LABEL, LAST_DAY_LABEL, LOCAL_TZ, MONEY_IN, NO_TERMS, P_PART, P_REFUNDED, PAY, PLANNED_LABEL,
                   REFUND_DUE_DAYS, REFUNDED_WORD, UNNAMED_LABEL, aging_columns, aging_file_header, aging_value, day,
                   is_refund_row, row_reasons)

MONEY_FMT = "#,##0.00"
DATE_FMT = "dd/mm/yyyy"
HEADER_FILL = PatternFill("solid", fgColor="E8ECF4")
ZERO = Decimal("0")
# The students' file's line about the ID number, at the head of its second sheet: the column analiza-metrics had is gone
ID_NUMBERS_NOT_IN_FILE = ("מספרי ת.ז אינם בקובץ: השרת של השולחן לא קורא אותם ולא שומר אותם. ת.ז של תקבול מוצגת "
                          "בפרטים שלו בשולחן, ונקראת ממאנדיי בשמך.")


def _money_or_blank(v: Decimal) -> Decimal | None:
    return v if v else None


def _refund_day(field: str) -> Callable[[dict], Any]:
    """A refund's date, on a refund row only (view.is_refund_row): elsewhere it would read as a deadline."""
    return lambda r: day(r[field]) if is_refund_row(r) else None


def _counted_paid(r: dict) -> Decimal | None:
    """"סכום ששולם" where it counts: a status that brought money in, with the amount typed. Elsewhere blank, so no
    total counts it - as a cancelled company charge is (the page shows such an amount struck through)."""
    return r["money_in"] if r["status_idx"] in MONEY_IN and r["paid_typed"] is not None else None


def _label(field: str) -> Callable[[dict], str]:
    """A status cell as the page writes it: its label, or "תווית בלי שם" where monday holds a value whose label has no name
    (the row's `unnamed`). QA 24.11, 2829373007: the page said so in "ערוץ תשלום", and the file's cell was empty."""
    return lambda r: UNNAMED_LABEL if field in (r.get("unnamed") or ()) else r[field]


def _terms(r: dict) -> str:
    """The company's "תנאי תשלום", or "אין תנאי תשלום" where its cell is empty, as the page writes beside it (QA 24.11: the
    ledger file of a company without terms had the column empty on every row). A charge with no company has none."""
    return r["account_terms"] or (NO_TERMS if r["account_no_terms"] else "")


def _reasons(tab: str) -> Callable[[dict], str]:
    """The row's "מה לבדוק": every chip the page would show on it, in its order, one after the other."""
    return lambda r: " · ".join(row_reasons(tab, r))


def _what_to_check(word: str, states: dict) -> tuple[str, str]:
    """The second sheet's line on "מה לבדוק" (`word`: a payment, a charge; `states`: its tab's)."""
    return ("מה לבדוק", "מה המסך מצא ב%s, באותן מילים ובאותו סדר כמו בעמודה \"מה לבדוק\" במסך, ובכל שורה שיש בה משהו, "
                        "לא רק ב\"%s\". במסך מוצגים עד חמישה, וכאן כולם." % (word, states["exception"]["label"]))


# (header, value of a row, kind, width) - in the order of her ledger card in Hashavshevet: date, reference, who, amount.
# A kind: "money" is an amount with a total under the rows; "amount" an amount without one (monday's own value, where
# the total is of what the desk counts, in a column of its own); "date", "int", "text".
B2C_COLUMNS: list[tuple[str, Callable[[dict], Any], str, int]] = [
    # monday's "תאריך תקבול", as on the page: where it was not typed, the day the row was created, and the next column
    # says so in the page's word for it ("יום היצירה", QA 11.11, 2.5: "תאריך" held two things under one name)
    ("תאריך תקבול", lambda r: day(r["date"]), "date", 12),
    ("מקור התאריך", lambda r: "הוקלד" if r["date_from"] == "paid" else "יום היצירה", "text", 13),
    ("אסמכתת חשבונית", lambda r: r["invoice"], "text", 15),
    ("סטודנט/ית", lambda r: r["name"], "text", 26),
    # no "ת.ז" here: analiza-metrics read it live for the file, and this service never reads one (ID_NUMBERS_NOT_IN_FILE)
    # Student Charge's "נציג מכירות", next to the name as on her board (17.11): the lead's current Owner
    ("נציג מכירות", lambda r: r.get("rep", ""), "text", 16),
    ("סכום ששולם", _counted_paid, "money", 14),
    ("ערוץ תשלום", _label("channel"), "text", 13),
    ("סוג חיוב", _label("type"), "text", 13),
    ("מחזור", lambda r: r["cohort_code"], "text", 9),
    ("שם המחזור", lambda r: r["cohort_title"], "text", 30),
    # the cohort's "Registration Flow", by the name the same field carries on the Deals board (17.11, view.FLOW_LABEL)
    (FLOW_LABEL, lambda r: r["flow"], "text", 18),
    ("שיבוץ", lambda r: B2C_STATES[r["state"]]["label"], "text", 28),
    ("מה לבדוק", _reasons("b2c"), "text", 40),
    ("סטטוס תשלום", _label("status"), "text", 13),
    ("שליחת קבלה", _label("receipt"), "text", 11),
    # monday's "סכום החזר" as typed, 0 too (QA 24.11: the file wrote the counted refund under monday's name); what the
    # desk counts of it is the next column, under the page's word for it, and the total is that one's
    ("סכום החזר", lambda r: r["refund_typed"], "amount", 13),
    (REFUNDED_WORD, lambda r: _money_or_blank(r["refunded"]), "money", 13),
    ("תאריך החזר", lambda r: day(r["refund_date"]), "date", 12),
    ("אופן החזר", _label("refund_method"), "text", 12),
    ("אסמכתת זיכוי", lambda r: r["credit_ref"], "text", 14),
    ("סכום לזיכוי", lambda r: _money_or_blank(r["credit_approved"]), "money", 13),
    ("תאריך בקשת ביטול", _refund_day("asked_date"), "date", 12),
    (LAST_DAY_LABEL, _refund_day("refund_last_day"), "date", 12),
    (PLANNED_LABEL, _refund_day("planned"), "date", 12),
    ("סטטוס שובר משה\"ע", lambda r: r["voucher"], "text", 22),
    ("מזהה במאנדיי", lambda r: r["id"], "text", 13),
]


# the companies list, in the order of a ledger card: date, customer, what, amount; then the states and the collecting
B2B_COLUMNS: list[tuple[str, Callable[[dict], Any], str, int]] = [
    ("מועד דרישת תשלום", lambda r: day(r["demand"]), "date", 12),
    ("חברה", lambda r: r["account"], "text", 26),
    ("עסקה", lambda r: r["deal"], "text", 30),
    ("חיוב", lambda r: r["name"], "text", 30),
    ("קוד קורס", lambda r: r["course"], "text", 10),
    # a cancelled charge's amount, or one never typed, is not money: blank, so no total counts it (review 10.11)
    ("סכום לחיוב (ללא מע\"מ)", lambda r: r["amount"] if r["amount_known"] and r["state"] != "cancelled" else None, "money", 15),
    ("שיבוץ", lambda r: B2B_STATES[r["state"]]["label"], "text", 22),
    ("מה לבדוק", _reasons("b2b"), "text", 40),
    ("מצב חיוב", _label("billing"), "text", 18),
    ("סטטוס תשלום", _label("payment"), "text", 13),
    ("מועד פירעון", lambda r: day(r["due"]), "date", 12),
    # the account's own "תנאי תשלום", as monday holds it ("אחר" too); the due date counts only the four it knows
    ("תנאי תשלום", _terms, "text", 11),
    ("ימים בפיגור", lambda r: r["days_late"], "int", 10),
    ("אסמכתת חשבונית", lambda r: r["invoice"], "text", 15),
    ("סכום ששולם (כולל מע\"מ)", lambda r: r["paid_so_far"], "money", 16),
    ("יתרה לגבייה (כולל מע\"מ)", lambda r: r["balance"], "money", 16),
    ("מזהה במאנדיי", lambda r: r["id"], "text", 13),
]


def customer_columns(customers: list[dict]) -> list[tuple[str, Callable[[dict], Any], str, int]]:
    """The aging by customer - the "לפי לקוח" view as a file, its columns the page's own (view.AGING_COLUMNS, QA 11.11,
    2.12): the same columns in the same order, each headed as the page heads it - a grouped one as "group: header", since
    the file has one header row (its filter sits on it). An optional column is in the file when it is on the page."""
    width = {"name": 28, "terms": 14, "tasks": 9}

    def value(key: str, kind: str) -> Callable[[dict], Any]:
        if key == "terms":   # as the page marks it: a company whose "תנאי תשלום" is empty
            return lambda c: c["terms"] or (NO_TERMS if c.get("terms_missing") else "")
        if kind == "money":  # no money is a blank cell, as the page's "-", so no total counts a zero
            return lambda c: aging_value(c, key) or None
        return lambda c: aging_value(c, key)   # a name, and a count the page shows as 0 too
    return [(aging_file_header(col), value(col["key"], col["kind"]), col["kind"], width.get(col["key"], 16))
            for col in aging_columns(customers)]


def _sheet_base(ws) -> None:
    ws.sheet_view.rightToLeft = True


def _text(cell, v: Any) -> None:
    """A text from monday is written as text, always. openpyxl reads a string that opens with "=" as a formula
    and "#N/A" as an error, and refuses control characters outright; a student name comes from a public form,
    so neither may reach the file (hostile review 10.11: a name became a live HYPERLINK formula)."""
    text = ILLEGAL_CHARACTERS_RE.sub("", "" if v is None else str(v))
    if not text:
        return
    cell.value = text
    cell.data_type = "s"
    cell.number_format = "@"   # and a reference like 00123 stays a reference


# D3 (app/desk/books.py): "לטיפול" of a tab carries its list of the books' documents with no charge on monday, as a sheet of
# its own after "פרטים" - the students', the companies' (with the line of the vouchers' card), and the unclear documents for
# a member of both charge boards only. The card's name is the books', cleaned of ID numbers before it was ever stored (spec
# §3.4); there is no ID column, and a candidate by name is said as one, never as a match.
_NUMBER_H, _TYPE_H, _DATE_H, _CARD_H = BOOKS_DOC_HEADS
_SUM_INCL, _SUM_EXCL = BOOKS_SUM_WORD + " (כולל מע\"מ)", BOOKS_SUM_WORD + " (ללא מע\"מ)"


def _candidates(e: dict) -> str:
    """A document's candidates by name: each row's name, its state and its "סטטוס תשלום", and how many more."""
    out = ["%s (%s, %s)" % (c.get("name") or "", B2C_STATES.get(c.get("state"), {}).get("label", c.get("state") or ""),
                            c.get("status") or "ריק") for c in e.get("candidates") or []]
    if e.get("candidates_more"):
        out.append("ועוד %d" % e["candidates_more"])
    return " · ".join(out)


_DOC_COLUMNS = [(_NUMBER_H, lambda e: e["number"], "text", 12), (_TYPE_H, lambda e: e["type_name"], "text", 16),
                (_DATE_H, lambda e: day(e["date"]), "date", 12)]
BOOKS_SHEETS = {
    "b2c": ("מסמכים בלי חיוב", _DOC_COLUMNS + [(_SUM_INCL, lambda e: e["total_incl"], "money", 16),
                                             (_CARD_H, lambda e: e["card"], "text", 26),
                                             (BOOKS_CANDIDATE, _candidates, "text", 50)]),
    "b2b": ("מסמכים בלי חיוב", _DOC_COLUMNS + [(_SUM_EXCL, lambda e: e["total_excl"], "money", 16),
                                             (_SUM_INCL, lambda e: e["total_incl"], "money", 16),
                                             (_CARD_H, lambda e: e["card"], "text", 26),
                                             ("חברה", lambda e: " · ".join(e.get("accounts") or []), "text", 26)]),
    "unclear": ("לא ברור של מי", _DOC_COLUMNS + [(_SUM_EXCL, lambda e: e["total_excl"], "money", 16),
                                               (_SUM_INCL, lambda e: e["total_incl"], "money", 16),
                                               (_CARD_H, lambda e: e["card"], "text", 26),
                                               (BOOKS_UNCLEAR_WHY, lambda e: e.get("why_text") or "", "text", 50)]),
}


def books_sheet(kind: str, part: dict) -> tuple:
    """(title, columns, entries, lines under them) of one list of the books (the view's `books` of a tab, or its
    `books_unclear`): its title and sentence under the rows, and the companies' line of the vouchers' card."""
    title, columns = BOOKS_SHEETS[kind]
    lines = [part.get("title") or "", part.get("about") or "", part.get("vouchers_said") or ""]
    return title, columns, list(part.get("documents") or []), [x for x in lines if x]


def books_lines(books: Any, local: Callable[[Any], str]) -> list:
    """The line of "פרטים" that says which copy of the books the file's marks and lists are of: "נכון ל-" its time, and
    why it is not newer; or why there is none. `local` writes a time as the file writes the reading's."""
    if not isinstance(books, dict) or not books.get("state"):
        return []
    try:
        at = local(datetime.fromisoformat(books["as_of"])) if books.get("as_of") else ""
    except (TypeError, ValueError):
        at = ""
    text = ("נכון ל-%s" % at if at else "") + ((". " if at else "") + books["said"] if books.get("said") else "")
    return [(BOOKS_AS_OF_WORD, text)] if text else []


def b2c_workbook(rows: list[dict], about: dict[str, Any], sheets: list | None = None) -> bytes:
    """rows: the filtered student payments (view.filter_b2c). about: the filter and the reading, for sheet 2, whose head
    says that the file carries no ID number. sheets: the books' lists of "לטיפול" (D3, `books_sheet`)."""
    return _workbook("תקבולים", B2C_COLUMNS, rows, about, "תקבולי סטודנטים מתוך שולחן הכספים",
                     [("הסכומים", "כפי שהם בלוח Student Charge, כולל מע\"מ"),
                      ("תאריך תקבול", "כפי שהוקלד בלוח; כשלא הוקלד, היום שבו נוצרה השורה במאנדיי, ואז במקור "
                                      "התאריך כתוב \"יום היצירה\""),
                      ("שיבוץ", "הרשימה שהמסך שם בה את התקבול, כמו במסך ובמקרא שלו"),
                      ("נציג מכירות", "בעל הליד מקור כפי שהוא היום, כמו בלוח: מי שמטפל בליד עכשיו, לא בהכרח מי "
                                      "שמכר"),
                      ("סכום ששולם", "בתקבולים שנכנס בהם כסף בלבד; בסטטוס תשלום \"לא שולם\", \"יש בעיה\" או "
                                     "\"מלגת אנליזה\" התא ריק ולא נספר"),
                      ("סכום החזר", "כפי שהוא במאנדיי: גם 0, וגם כשהוא גדול מסכום ששולם או בסטטוס תשלום שלא אומר "
                                    "שהוחזר. אין לו סה\"כ: את ההחזר שהמסך סופר סוכמת העמודה \"%s\"" % REFUNDED_WORD),
                      (REFUNDED_WORD, "ההחזר כפי שהמסך סופר אותו ב\"%s\": רק בסטטוס תשלום \"%s\" או \"%s\"; ב\"%s\" "
                                      "בלי סכום החזר או עם 0, כל סכום ששולם; ולעולם לא יותר מסכום ששולם. ממנה סה\"כ "
                                      "ההחזרים, כמו במסך"
                       % (REFUNDED_WORD, PAY[P_REFUNDED], PAY[P_PART], PAY[P_REFUNDED])),
                      _what_to_check("תקבול", B2C_STATES),
                      (LAST_DAY_LABEL, "תאריך בקשת ביטול בהרשמה ועוד %d יום, בשורות של החזר בלבד, ליד %s"
                       % (REFUND_DUE_DAYS, PLANNED_LABEL))], head_lines=[("ת.ז", ID_NUMBERS_NOT_IN_FILE)], sheets=sheets)


def b2b_workbook(rows: list[dict], about: dict[str, Any], sheets: list | None = None) -> bytes:
    """rows: the filtered company charges (view.filter_b2b). Partly paid charges whose paid amount is not typed have no
    balance, and the page gives "סכום ששולם (כולל מע\"מ)" and "יתרה לגבייה" no total while one is in its list ("חסר ב-N",
    view.list_totals): the file's totals of the two are without them, and its second sheet says so beside them. sheets:
    the books' lists of "לטיפול" (D3, `books_sheet`)."""
    unknown = sum(1 for r in rows if r["state"] == "partial" and r["balance"] is None)
    which = ("חיוב אחד", "בלעדיו") if unknown == 1 else ("%d חיובים" % unknown, "בלעדיהם")
    return _workbook("חיובים", B2B_COLUMNS, rows, about, "חיובי לקוחות עסקיים מתוך שולחן הכספים",
                     ([("שימו לב", "%s ב\"%s\" בלי סכום ששולם (כולל מע\"מ), ולכן בלי יתרה לגבייה: בקובץ, הסה\"כ של סכום "
                                   "ששולם (כולל מע\"מ) ושל יתרה לגבייה (כולל מע\"מ) הוא %s, ובמסך אין לשתי העמודות סה\"כ"
                        % (which[0], B2B_STATES["partial"]["label"], which[1]))] if unknown else []) +
                     [("הסכומים", "סכום לחיוב (ללא מע\"מ), כמו בלוח B2B Charge; סכום ששולם (כולל מע\"מ) ויתרה "
                                  "לגבייה כולל מע\"מ (היתרה מניחה מע\"מ 18%)"),
                      ("מועד פירעון", DUE_RULE), _what_to_check("חיוב", B2B_STATES)], sheets=sheets)


# The second sheet's totals (QA 24.11): the row under the list is a SUBTOTAL formula, which follows Excel's filter and which
# Excel computes on opening the file; a preview (a phone, Quick Look) may show it empty. So each total is also written as a
# number, summed from the very cells the formula sums, under a line that says they are the file's as made.
FILE_TOTALS = ("סכומי הקובץ כפי שהופק", "סה\"כ כל עמודת סכום בכל שורות הקובץ, בלי סינון. שורת הסיכום שמתחת לשורות "
                                        "בגיליון \"%s\" היא נוסחה שמתעדכנת לפי הסינון באקסל, ותצוגה מקדימה (בטלפון, "
                                        "למשל) עשויה להציג אותה ריקה")


def total_label(head: str) -> str:
    """A column's total in the second sheet: 'סה"כ' and its header, which may say it already."""
    return head if head.startswith("סה\"כ") else "סה\"כ " + head


def _workbook(title: str, columns: list, rows: list[dict], about: dict[str, Any], what: str, totals: list,
              head_lines: list | None = None, sheets: list | None = None) -> bytes:
    """`head_lines`: lines said right under what the file is, before when it was read (the students' file: no ID
    number). `totals`: the lines after the file's own totals (FILE_TOTALS), which this sums from its cells. `sheets`: the
    books' lists (D3), each a sheet of its own after "פרטים"."""
    wb = Workbook()
    ws = wb.active
    ws.title = title
    _sheet_base(ws)
    bold = Font(bold=True)
    for c, (head, _fn, _kind, width) in enumerate(columns, start=1):
        cell = ws.cell(row=1, column=c, value=head)
        cell.font, cell.fill = bold, HEADER_FILL
        cell.alignment = Alignment(horizontal="right", vertical="center", wrap_text=True)
        ws.column_dimensions[get_column_letter(c)].width = width
    sums = {c: ZERO for c, (_head, _fn, kind, _width) in enumerate(columns, start=1) if kind == "money"}
    for i, r in enumerate(rows, start=2):
        for c, (_head, fn, kind, _width) in enumerate(columns, start=1):
            v = fn(r)
            cell = ws.cell(row=i, column=c)
            if kind == "text":
                _text(cell, v)
                continue
            cell.value = v
            cell.number_format = {"money": MONEY_FMT, "amount": MONEY_FMT, "date": DATE_FMT, "int": "0"}[kind]
            if c in sums and v is not None:
                sums[c] += v
    last = len(rows) + 1
    last_col = get_column_letter(len(columns))
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = "A1:%s%d" % (last_col, max(last, 1))
    # the total, one row below the data and outside the filter, follows whatever the filter hides
    total_row = last + 2
    ws.cell(row=total_row, column=2, value="סה\"כ לפי הסינון").font = bold
    for c, (_head, _fn, kind, _width) in enumerate(columns, start=1):
        if kind == "money" and rows:
            col = get_column_letter(c)
            cell = ws.cell(row=total_row, column=c, value="=SUBTOTAL(109,%s2:%s%d)" % (col, col, last))
            cell.number_format, cell.font = MONEY_FMT, bold

    info = wb.create_sheet("פרטים")
    _sheet_base(info)
    lines = []
    if not about.get("complete", True):
        lines.append(("שימו לב", "הקריאה ממאנדיי לא שלמה: אין להסתמך על הסכומים בקובץ. רענון בשולחן יקרא שוב."))
    if about.get("screen_taken_at"):   # set only when the page showed another reading
        lines.append(("שימו לב", "הקובץ נבנה מקריאה של %s, והמסך הציג קריאה של %s: ייתכן שהשורות שונות מהמסך."
                      % (about.get("taken_at"), about.get("screen_taken_at"))))
    lines += [
        ("הקובץ", what),
        *(head_lines or []),
        ("נקרא ממאנדיי", about.get("taken_at") or ""),
        *(about.get("books_lines") or []),
        ("הופק", datetime.now(LOCAL_TZ).strftime("%d/%m/%Y %H:%M")),
        ("רשימה", about.get("state_label") or "הכל"),
        ("סינון", about.get("period_label") or "כל התקופה"),
        ("חיפוש", ILLEGAL_CHARACTERS_RE.sub("", about.get("q") or "") or "-"),
        ("שורות", len(rows)),
        (FILE_TOTALS[0], FILE_TOTALS[1] % title),
        *[(total_label(columns[c - 1][0]), v) for c, v in sums.items()],
    ] + totals
    for i, (k, v) in enumerate(lines, start=1):
        info.cell(row=i, column=1, value=k).font = bold
        cell = info.cell(row=i, column=2)
        if isinstance(v, (Decimal, int)):
            cell.value = v
            if isinstance(v, Decimal):
                cell.number_format = MONEY_FMT
        else:
            _text(cell, v)
    info.column_dimensions["A"].width = 34   # a total's name, as long as its column's header
    info.column_dimensions["B"].width = 60
    for sheet in sheets or []:
        _list_sheet(wb, *sheet)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def _list_sheet(wb, title: str, columns: list, entries: list, lines: list) -> None:
    """One list of the books as a sheet: right to left, its header frozen with a filter, dates as dates, amounts as
    numbers, every text as text (`_text`), and under the rows its title and sentence."""
    ws = wb.create_sheet(title)
    _sheet_base(ws)
    bold = Font(bold=True)
    for c, (head, _fn, _kind, width) in enumerate(columns, start=1):
        cell = ws.cell(row=1, column=c, value=head)
        cell.font, cell.fill = bold, HEADER_FILL
        cell.alignment = Alignment(horizontal="right", vertical="center", wrap_text=True)
        ws.column_dimensions[get_column_letter(c)].width = width
    for i, e in enumerate(entries, start=2):
        for c, (_head, fn, kind, _width) in enumerate(columns, start=1):
            cell = ws.cell(row=i, column=c)
            v = fn(e)
            if kind == "text":
                _text(cell, v)
                continue
            cell.value = v
            cell.number_format = {"money": MONEY_FMT, "date": DATE_FMT}[kind]
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = "A1:%s%d" % (get_column_letter(len(columns)), max(len(entries) + 1, 1))
    for k, text in enumerate(lines, start=len(entries) + 3):
        _text(ws.cell(row=k, column=1), text)


def download_headers(name: str, ascii_name: str) -> dict[str, str]:
    """RFC 6266: a plain ASCII name for old clients, and the Hebrew one in filename* for everyone else - UTF-8, no
    language, every byte outside RFC 8187's attr-char percent-encoded (`safe=""`: quote() leaves "/" as it is by default).
    QA 24.11 saw a name end "24.11.2026.xlsx" without its Hebrew letters: that date is the Hebrew name's alone (the ASCII
    one says 2026-11-24), so the downloader had read filename* and dropped the letters itself."""
    return {"Content-Disposition": "attachment; filename=\"%s\"; filename*=UTF-8''%s" % (ascii_name, quote(name, safe="")),
            "Cache-Control": "no-store"}


def customers_workbook(rows: list[dict], about: dict[str, Any]) -> bytes:
    """rows: the customers with something to do (view.b2b_customers), oldest arrears first."""
    return _workbook("לפי לקוח", customer_columns(rows), rows, about, "לפי לקוח: כל חברה עם משהו פתוח, מתוך שולחן הכספים",
                     [("הסכומים", "לחיוב החודש, סה\"כ יתרה פתוחה והעמודות שתחת לפי מועד פירעון: סכום לחיוב (ללא "
                                  "מע\"מ), כמו בלוח B2B Charge. "
                                  "יתרה לגבייה בחיובים ששולמו חלקית: כולל מע\"מ (מניחה מע\"מ 18%), רק מחיובים שסכום "
                                  "ששולם (כולל מע\"מ) בהם הוקלד"),
                      ("לפי מועד פירעון", "היתרה הפתוחה לפי הימים שעברו ממועד הפירעון; סכום העמודות הוא סה\"כ "
                                          "יתרה פתוחה"),
                      ("מי ברשימה", "רק חברות עם משהו לחיוב החודש, ביתרה פתוחה, ששולם חלקית או ב\"חריג, לבדיקה\""),
                      ("סדר", "מהפיגור הוותיק ביותר, כמו במסך"),
                      ("העמודות", "כמו במסך, באותו סדר ובאותם שמות; עמודה שתחת כותרת במסך כתובה כאן אחרי שם "
                                  "הכותרת ונקודתיים")])


# The files' names (QA 11.11, 2.12: every students' file was "תקבולים-סטודנטים_הכל_<date>", whatever its list, so a few
# exports came back as (1), (2), and only the second sheet said which list each one held): each names its list as the page
# does - "לטיפול", a state's label, "הכל" - and its period, or its company when one is chosen. The aging file is named by
# the view's own name, "לפי לקוח" (the page has no "גיול"). The ASCII name, for old clients, says the same in keys.
def file_part(text: str) -> str:
    """A part of a file name, safe on every system: "שולם, שליחת קבלה: טרם" is "שולם-שליחת-קבלה-טרם", a company
    "מרכז רפואי העמק/כללית" is "מרכז-רפואי-העמק-כללית", and a quote mark is dropped ('צה"ל' is "צהל")."""
    text = re.sub(r"[\"'\u05f3\u05f4\x00-\x1f\x7f]", "", text or "")
    text = re.sub(r"[\\/:*?<>|,.\s]+", "-", text)
    return re.sub(r"-{2,}", "-", text).strip("-")[:60] or "-"


def b2c_file_names(list_label: str, state: str, period_label: str, period: str, today: date) -> tuple[str, str]:
    return ("תקבולים-סטודנטים_%s_%s_%s.xlsx" % (file_part(list_label), file_part(period_label), today.strftime("%d.%m.%Y")),
            "finance-students-%s-%s-%s.xlsx" % (state or "all", period.replace("+", "_") or "all", today.isoformat()))


def b2b_file_names(list_label: str, state: str, customer_name: str, customer: str, today: date) -> tuple[str, str]:
    # a company the reading no longer holds is named by its id rather than by nothing
    return ("חיובים-לקוחות-עסקיים_%s%s_%s.xlsx" % (file_part(list_label), ("_" + file_part(customer_name or customer)) if customer else "",
                                                 today.strftime("%d.%m.%Y")),
            "finance-companies-%s%s-%s.xlsx" % (state or "all", ("-" + customer) if customer else "", today.isoformat()))


def customers_file_names(today: date) -> tuple[str, str]:
    return ("לפי-לקוח_%s.xlsx" % today.strftime("%d.%m.%Y"), "finance-by-customer-%s.xlsx" % today.isoformat())
