"""What the books say that monday does not, in the desk, read only (Analiza-Monday backlog 0301 D3; the desk move plan
§1a.2; the spec §6.1-§6.4; the rules' §5: FR-C1, FR-C4, FR-D1, FR-6).

THREE THINGS, all computed here, once per reading and per books copy, and only drawn by the page:
1. **A mark on a Student Charge row** (spec §6.1.1), from A1's own judgement of every row (app/recon/receipt_dates.py
   `analyse`, reused as it is - its buckets are the rules FR-C1, FR-3 and FR-4; nothing here judges a row again):
   - `books_ref_invalid`: the bucket `ref_not_in_books`, `ref_ambiguous` or `ref_not_an_invoice` - a number in
     "אסמכתת חשבונית" that is no valid document; the details say which number and why ("טיוטה" for a number the copy
     holds as a draft). **But a number just past the end of a series is not yet in the copy** (`not_yet_read`: at most
     NOT_YET_SPAN past the last number of the series nearest below it, and held by no document of the copy): the books are
     read every hour, and a document issued and cited since is no gap - no mark for it, and the row's details say "עוד לא
     בהעתק של הספרים" (`books_note`). A number further on, between two series or below every series is mistyped: marked;
   - `books_amount_differs`: `amount_mismatch` only (the rounding band is no mark) - "בשורה X, בחשבשבת Y", the group's two
     sums, including VAT;
   - `books_receipt_unpaid`: read off the row's group itself (A1's `group`), not off A1's order of buckets - A1 matched
     the group (RECEIPT_BUCKETS, named), its two sums agree within the tolerance, its documents are invoice-receipts (a tax
     invoice waits for its receipt), and the row's status does not say the money came in. The books say the money came in
     (FR-L12) and the row says it did not: the case of 23.11, also when the group's invoice-receipts carry different days
     (A1's `dates_differ`, which A1 judges before the row's status).
   Every other bucket gives no mark. **A mark moves no row out of its state** (משבצת), so no state's count or sum changes;
   a marked row whose date is from ERA_FROM on (the row's date as the desk dates it: "תאריך תקבול", else the day it was
   made) also joins "לטיפול" (`books_task`); an older one keeps its mark on the row only (spec §6.4.2, ADR-0168 §5).
2. **"מסמכים בחשבשבת בלי חיוב במאנדיי"** (spec §6.1.2; FR-C4, FR-D1): status-1 tax invoices and invoice-receipts of the era
   that no charge row cites. On a student's card, dated within Student Charge's window (FR-7), each with the Student
   Charge rows whose name equals the card's name as a set of words - **a candidate, never a match, never a link, never a
   fill** (the law of the rules' §5). On a company's card, with the Accounts that name it. Card VOUCHER_CARD is counted
   apart (FR-D1).
3. **The side of a card** (FR-6, the plan §1a.2.3, until B3's sort codes): an Account's "מפתח חשבשבת" (read from the whole
   Accounts board, `AccountKeys`) is a company's card; otherwise a card that holds an invoice-receipt and no proforma
   (type 78: students are sent none) is a student's; anything else is `unclear`, and its documents are shown only to a
   member of both charge boards (spec §6.4.1). **A key that is empty or masked names no card** (`card_of`): the copy cleans
   a key like any text, so a key of six digits or more - a student's card keyed by an ID number, a company's by its ח.פ - is
   stored "#", and every such card would pool into one. Such a document is judged alone, by its own type - an
   invoice-receipt a student's, as FR-6's interim rule; a tax invoice unclear (`key_masked`, `key_empty`) - and never an
   Account's.

EVERY DOCUMENT OF THE ERA LANDS IN ONE PLACE (spec §6.3.1), the first that fits in PLACES: outside (another status or
type, a cancellation, before the era, or a student's outside Student Charge's window), cited by a charge row, answered
"not found" (missing), counted apart (the vouchers' card), or listed - the students', the companies' or the unclear.

ONLY A WHOLE COPY CHANGES WHAT IS SHOWN (spec §6.1.5). A result is computed only on a desk reading whose two charge boards
were read whole and on a copy that is whole by A1's own rule (app/books/store.py `not_ready`, judged on one repeatable-
read snapshot, `desk_snapshot`). Otherwise the last result this instance computed is shown, `held`, with the time of its
copy and why it is not newer; with none, `none` and why. Never on Demo (`demo`: the books of the Demo company must never
meet the real boards, the rules' §0.6), nor without a database (`not_configured`): `off`.

WHAT LEAVES. The result lives in this instance's memory and in the desk's gated answer only: the marks and the students'
list in the students' tab (Student Charge's members), the companies' list in the companies' tab (B2B Charge's), the
unclear documents only to a member of both (app/desk/view.py `visible_part`). Nothing of it is logged, stored or put in
/health, which says the state, the copy's time and the reasons - never a count. A card's name from the books was cleaned
of ID numbers before it was stored (spec §3.4), and no card key, no ID number and no business number is in an answer.
"""

from __future__ import annotations

import logging
import re
import threading
import time
import unicodedata
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Optional

from ..books.doctypes import DRAFT_STATUS, INFERRED, VALID_STATUS, role
from ..books.store import BooksStoreError, not_ready, summarize_series
from ..monday.client import MondayError, MondayReader
from ..recon import receipt_dates as a1
from ..recon.reading import Board, check_board
from .reading import AC, ACCOUNTS_BOARD, B2, masked
from .view import BOOKS_LIST_TITLE, BOOKS_UNCLEAR_TITLE, LEGEND_FLAGS, TASK_STATES, status_word

logger = logging.getLogger("analiza_finance.desk")

# Chava's start (ADR-0168 §5): she records from it and does not backfill. A gap of a row or a document before it is the
# cleanup list Nadav runs once (backlog 0301 C1), never her "לטיפול" (spec §6.4.2).
ERA_FROM = date(2026, 10, 25)
# FR-7: Student Charge cites documents of this year only (A1's window, the same constant)
STUDENT_WINDOW_YEAR = a1.WINDOW_YEAR
# FR-D1: Defence's vouchers, counted apart until the vouchers board is a matching target
VOUCHER_CARD = "10091"
INVOICE, INVOICE_RECEIPT, PROFORMA = "invoice", "invoice_receipt", "proforma"
LISTED_ROLES = (INVOICE, INVOICE_RECEIPT)  # types 1 and 2: what a charge row cites (FR-C1, FR-D1)

# The marks, each from A1's buckets (the first that fits a row in a1.BUCKETS; one bucket a row)
MARKS = ("books_ref_invalid", "books_amount_differs", "books_receipt_unpaid")
MARK_OF_BUCKET = {"ref_not_in_books": "books_ref_invalid", "ref_ambiguous": "books_ref_invalid",
                  "ref_not_an_invoice": "books_ref_invalid", "amount_mismatch": "books_amount_differs"}
# `books_receipt_unpaid` is read off the group (`receipt_unpaid_facts`) of a row in one of these buckets, named and not
# found by their place in A1's order: A1 matched its group (every document found, in the window, not tangled), and the
# facts the mark needs - the sums, the types, the row's status - are read off the group and the row. Every other bucket
# is a number refused, a group that cannot be judged or is out of the window (FR-7: never a gap), or sums that differ
# (the amount's mark). Today they are A1's order from `dates_differ` on; a test holds that, so a bucket A1 adds is judged
RECEIPT_BUCKETS = ("dates_differ", "status_not_money_in", "date_unreadable", "filled_same", "filled_differs", "fill")

# Where a document of the books lands, the first that fits (spec §6.3.1: each in exactly one)
PLACES = ("outside", "cited", "missing", "vouchers", "students", "companies", "unclear")
# Why a card's side is not clear: the documents of such a card go to members of both charge boards only
UNCLEAR_WHY = {
    "no_invoice_receipt": "בכרטיס אין חשבונית מס/קבלה, והוא אינו מפתח חשבשבת של חברה במאנדיי",
    "invoice_receipt_and_proforma": "בכרטיס יש חשבונית מס/קבלה וגם חשבון עסקה, והוא אינו מפתח חשבשבת של חברה במאנדיי",
    # a key that names no card (`card_of`): the document alone, and a tax invoice alone says no side
    "key_masked": "המפתח של הכרטיס הוסתר בעותק של הספרים (מספר של שש ספרות ומעלה, כמו מספר זהות או ח.פ), ולכן החשבונית "
                  "נשפטת לבדה, וחשבונית מס לבדה לא אומרת של מי היא",
    "key_empty": "למסמך אין מפתח כרטיס בעותק של הספרים, ולכן החשבונית נשפטת לבדה, וחשבונית מס לבדה לא אומרת של מי היא",
}
# the books' cleaning masks a number of six digits or more that has no known shape (app/books/clean.py): a key holding it
# names no card
KEY_MASK = "#"

# The states of the part, and the closed reasons a part is not `ok` - each with the sentence the page and the files say
STATES = ("ok", "held", "none", "off")
REASONS = {
    "demo": "השירות מחובר לחברת הדמו בחשבשבת, ולכן אין כאן השוואה לספרים.",
    "company": "השירות לא מחובר לחברה של אנליזה בחשבשבת, ולכן אין כאן השוואה לספרים.",
    "not_configured": "לשירות אין מסד נתונים, ולכן אין כאן השוואה לספרים.",
    "never_completed": "העותק של חשבשבת עוד לא הושלם אף פעם.",
    "last_run_incomplete": "הקריאה האחרונה של חשבשבת לא הגיעה לסוף.",
    "last_run_not_ok": "הקריאה האחרונה של חשבשבת לא הייתה תקינה.",
    "retry_waiting": "יש מסמכים בחשבשבת שהקריאה שלהם נכשלה ועוד לא נקראו שוב.",
    "holes": "בעותק של חשבשבת חסרים מספרים בתוך סדרה של מסמכים.",
    "store_error": "לא הצלחנו לקרוא עכשיו את העותק של חשבשבת.",
    "accounts_unread": "לא הצלחנו לקרוא ממאנדיי את מפתחות חשבשבת של החברות.",
    "reading_incomplete": "הקריאה ממאנדיי לא שלמה.",
    # an answer at once (a reading past its span, answered while the next is read) on an instance that holds no Account's
    # key yet: never a read of monday in it - the page's own next request computes the part
    "pending": "ההשוואה לחשבשבת מתעדכנת.",
    "busy": "ההשוואה לחשבשבת עוד מחושבת.",
}
# What a part that is not fresh says after its reasons
HELD_SAID = "מוצגים הסימונים והרשימות מהעותק השלם האחרון."
NONE_SAID = "לכן אין כאן סימונים מהספרים ואין רשימה של מסמכים בלי חיוב."

# The lists' own sentences, above each (their headings are the legend's words, view.BOOKS_LIST_TITLE and the rest)
STUDENTS_ABOUT = ("חשבוניות מס וחשבוניות מס/קבלה תקפות מ-%s ועד סוף %d, בכרטיס של סטודנט/ית בחשבשבת, ששום שורה בלוחות "
                  "החיובים לא מפנה אליהן באסמכתת חשבונית. ליד מסמך: שורות ב-Student Charge ששמן זהה לשם בכרטיס, כל אחת "
                  "מועמדת לפי שם בלבד: התאמה נעשית רק לפי מספר המסמך באסמכתת חשבונית."
                  % (ERA_FROM.strftime("%d.%m.%Y"), STUDENT_WINDOW_YEAR))
COMPANIES_ABOUT = ("חשבוניות מס וחשבוניות מס/קבלה תקפות מ-%s ואילך, בכרטיס שהוא מפתח חשבשבת של חברה בלוח החברות, ששום "
                   "שורה בלוחות החיובים לא מפנה אליהן באסמכתת חשבונית." % ERA_FROM.strftime("%d.%m.%Y"))
VOUCHERS_SAID = "בכרטיס %s בחשבשבת (השוברים של משרד הביטחון): %s בלי חיוב במאנדיי, שנספרים בנפרד ולא מוצגים כאן."
UNCLEAR_ABOUT = ("חשבוניות מס וחשבוניות מס/קבלה תקפות מ-%s ואילך, ששום שורה בלוחות החיובים לא מפנה אליהן, בכרטיס שלא ברור "
                 "אם הוא של סטודנט/ית או של חברה. מוצגים רק למי שחבר בשני לוחות החיובים." % ERA_FROM.strftime("%d.%m.%Y"))

# How a mark says what it found (the chip's words are view.LEGEND_FLAGS', filled here from the row): a refused number in
# the chip, in a word, and in the row's details, in a sentence
REF_WHY = {
    "ref_not_in_books": ("לא נמצא", "אין בחשבשבת מסמך תקף במספר הזה"),
    "ref_ambiguous": ("בשני מסמכים", "שני מסמכים תקפים בחשבשבת נושאים את המספר הזה"),
}
# a number the copy holds only as a draft (spec §4.1: a draft carries its series' next number): no valid document (FR-2)
DRAFT_WHY = ("טיוטה", "בחשבשבת יש במספר הזה טיוטה, ולא מסמך תקף")
CANDIDATES_MAX = 10  # a common name shows its first ten rows, and how many more there are
REF_BUCKETS = ("ref_not_in_books", "ref_ambiguous", "ref_not_an_invoice")
# what a row's details say of a number it cites that is just past the end of its series (`not_yet_read`)
NOT_YET_SAID = "עוד לא בהעתק של הספרים"
# how far past the last number of a series a cited number may be and still be a document issued since the copy's last
# tick (the copy is read every hour; a few dozen documents a day): further, it is a mistyped number (review of dfa967b:
# unbounded, every wrong number above the lowest series was "not yet in the copy", and never marked)
NOT_YET_SPAN = 20

# The Accounts board's keys: read whole with one column, kept this long, and after a failed read not tried again sooner
KEYS_TTL_S = 600
KEYS_FAIL_GAP_S = 60
KEYS_DEADLINE_S = 20
# after the database failed, the desk does not ask it again sooner (an open is never held by a database that is down)
STORE_FAIL_GAP_S = 30
# the same reading asks again whether the copy moved (its last finished run) at most this often: a tick is hourly
RUN_CHECK_S = 15
# a request that finds the part being computed waits at most this long, then is answered with the last part
WAIT_S = 30


# ---- small readers ---------------------------------------------------------------------------------------------------
def _money(v: Any) -> Optional[Decimal]:
    if v is None or isinstance(v, bool):
        return None
    try:
        return Decimal(str(v))
    except Exception:  # noqa: BLE001 - not an amount
        return None


def money_text(v: Decimal) -> str:
    """An amount as the desk's rows and files write one: agorot, thousands with a comma, the shekel after it."""
    return "{:,.2f} ₪".format(v)


def _number(doc: dict) -> Optional[str]:
    """A document's number as A1 looks it up (`index_documents`): ASCII digits, no leading zero; None for one that has none."""
    text = str(doc.get("doc_number") or "")
    return str(int(text)) if text.isascii() and text.isdigit() else None


def type_name(doc_type: Any) -> str:
    try:
        return INFERRED[int(doc_type)][1]
    except (KeyError, TypeError, ValueError):
        return "סוג %s" % doc_type


def _day(v: Any) -> Optional[date]:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return a1.parse_day(v)


def norm_key(text: Any) -> str:
    """A card's key as both sides are compared: NFKC, trimmed, case folded."""
    return unicodedata.normalize("NFKC", str(text or "")).strip().casefold()


def name_words(text: Any) -> frozenset:
    """A name as a set of words (FR-C2's rule): split at anything that is not a letter or a digit, NFKC and case folded;
    words of two letters or more, none with a digit, in any order."""
    words = re.split(r"[\W_]+", unicodedata.normalize("NFKC", str(text or "")).casefold())
    return frozenset(w for w in words if len(w) >= 2 and not any(ch.isdigit() for ch in w))


def account_keys_of(rows: list) -> dict:
    """{card key: [the names of the Accounts whose "מפתח חשבשבת" names it]}. A cell may name more than one card,
    separated by commas, slashes or spaces (MON-43: it should not, and the desk does not lose one that does). A key the
    desk's reading masked (an ID-like number, `mask_ids`) names no card, as in the books."""
    out: dict = defaultdict(list)
    for r in rows:
        for key in re.split(r"[,;/\s]+", norm_key(r.get(AC["hash_key"]))):
            if key and KEY_MASK not in key:
                out[key].append(str(r.get("name") or ""))
    return dict(out)


def card_of(doc: dict) -> Optional[str]:
    """The card a document is on, by its key - or None when the key names no card: empty, or masked by the copy's cleaning
    ("#": every key of six digits or more that has no known shape reads so, and pooled they would be one card of many
    people's documents)."""
    key = norm_key(doc.get("account_key"))
    return None if not key or KEY_MASK in key else key


def no_card_why(doc: dict) -> str:
    """Why a document's key names no card: `key_empty`, or `key_masked`."""
    return "key_empty" if not norm_key(doc.get("account_key")) else "key_masked"


def cited_numbers(rows: list, column: str) -> set:
    """Every document number a charge board's rows cite in `column`: each number of a readable cell (A1's `parse_refs`),
    and every number an unreadable one may name (A1's `loose_numbers`) - a document a row may cite is never listed as
    one no row cites."""
    out: set = set()
    for r in rows:
        refs = a1.parse_refs(r.get(column))
        out.update(refs if refs is not None else a1.loose_numbers(r.get(column)))
    return out


def card_sides(documents: list, keys: dict) -> dict:
    """{card key: (side, why)} for every card that holds a valid document: ("company", "account_key"), ("student",
    "invoice_receipt"), or ("unclear", one of UNCLEAR_WHY). A draft is no evidence; a document that answered "not found"
    stays in every figure until a person confirms it gone (spec §4.2.5), so it is. A document whose key names no card
    (`card_of`) is on no card here: it is judged alone (`place_of`)."""
    roles: dict = defaultdict(set)
    for d in documents:
        card = card_of(d)
        if card is not None and d.get("status") == VALID_STATUS:
            roles[card].add(role(d.get("doc_type")))
    out = {}
    for card, held in roles.items():
        if card in keys:
            out[card] = ("company", "account_key")
        elif INVOICE_RECEIPT in held and PROFORMA not in held:
            out[card] = ("student", "invoice_receipt")
        elif INVOICE_RECEIPT in held:
            out[card] = ("unclear", "invoice_receipt_and_proforma")
        else:
            out[card] = ("unclear", "no_invoice_receipt")
    return out


# ---- the marks -------------------------------------------------------------------------------------------------------
def series_ends(documents: list) -> list:
    """[(first, last)] of the copy's series of tax invoices and invoice-receipts, lowest first: their status-1 numbers by
    the rule of the copy's own `series()` (app/books/store.py summarize_series)."""
    held = [(d.get("doc_type"), d.get("doc_number"), d.get("status")) for d in documents]
    return sorted((s["first"], s["last"]) for s in summarize_series(held)
                  if s["role"] in LISTED_ROLES and s["first"] is not None)


def not_yet_read(number: str, ends: list) -> bool:
    """Whether a number no document of the copy holds is just past the end of a series - last < n <= last + NOT_YET_SPAN,
    for the series of tax invoices or invoice-receipts whose last number is the nearest below it: a document issued since
    the copy's last tick (the books are read every hour), not yet in the copy. A number further on, between two series, or
    below every series is no such number: it is mistyped, and marked "לא נמצא"."""
    n = int(number)
    below = [last for _first, last in ends if last < n]
    return bool(below) and n - max(below) <= NOT_YET_SPAN


def index_copy(documents: list) -> dict:
    """What the marks read of one copy: its valid documents by number (A1's `index_documents`), the numbers it holds as a
    draft, every number it holds in any status, and the ends of its series of tax invoices and invoice-receipts."""
    drafts, held = set(), set()
    for d in documents:
        number = _number(d)
        if number is None:
            continue
        held.add(number)
        if d.get("status") == DRAFT_STATUS:
            drafts.add(number)
    return {"by_number": a1.index_documents(documents), "drafts": drafts, "held": held, "ends": series_ends(documents)}


def _not_yet(entry: dict, idx: dict) -> list:
    """The numbers of a row that no document of the copy holds, in any status, and that are just past the end of a series
    (`not_yet_read`). A number the copy holds as a draft is a draft, never "not yet in the copy"."""
    return [n for n, b in entry.get("refused") or [] if b == "ref_not_in_books" and n not in idx["held"]
            and not_yet_read(n, idx["ends"])]


def _flag_text(kind: str) -> str:
    """A mark's chip as the legend words it (view.LEGEND_FLAGS, the one place its words live), to be filled."""
    return next(t for k, t, _x in LEGEND_FLAGS["b2c"] if k == kind)


def _ref_reason(number: str, bucket: str, idx: dict) -> tuple:
    """(the chip's word, the details' sentence) for one number of the row that is no valid document."""
    if bucket == "ref_not_in_books" and number in idx["drafts"]:
        return "%s %s" % (number, DRAFT_WHY[0]), "%s: %s" % (number, DRAFT_WHY[1])
    by_number = idx["by_number"]
    if bucket in REF_WHY:
        word, sentence = REF_WHY[bucket]
        return "%s %s" % (number, word), "%s: %s" % (number, sentence)
    doc = (by_number.get(number) or [{}])[0]
    total, name = _money(doc.get("total_incl")), type_name(doc.get("doc_type"))
    if role(doc.get("doc_type")) == INVOICE and total is not None and total < 0:
        return "%s %s בסכום שלילי" % (number, name), "%s: %s בסכום שלילי, כלומר ביטול" % (number, name)
    if role(doc.get("doc_type")) in LISTED_ROLES and total is None:
        return "%s %s בלי סכום" % (number, name), "%s: %s בלי סכום" % (number, name)
    return "%s %s" % (number, name), "%s: %s, ולא חשבונית מס או חשבונית מס/קבלה" % (number, name)


def _docs_said(numbers: list, by_number: dict) -> str:
    return ", ".join("%s %s" % (type_name((by_number.get(n) or [{}])[0].get("doc_type")), n) for n in numbers)


def mark_of(entry: dict, row: dict, idx: dict) -> Optional[dict]:
    """The mark A1's judgement of one row gives, or None: {"kind", "chip" (the chip's words, filled), "text" (the sentence
    the row's details and the Excel file say), "documents" (the numbers it is about)}. `row` is the view's row (its
    status's label, by index); `idx`, the copy (`index_copy`): a number just past the end of its series is not yet in the
    copy, and no mark is given for it."""
    by_number = idx["by_number"]
    kind = MARK_OF_BUCKET.get(entry["bucket"])
    if kind == "books_ref_invalid":
        late = _not_yet(entry, idx)
        refused = [(n, b) for n, b in entry.get("refused") or [] if n not in late]
        if not refused:
            return None
        why = [_ref_reason(n, b, idx) for n, b in refused]
        chip = _flag_text(kind).replace("{why}", "; ".join(w for w, _s in why))
        return {"kind": kind, "chip": chip, "documents": [n for n, _b in refused],
                "text": "%s: %s." % (_flag_text(kind).split(":")[0], "; ".join(x for _w, x in why))}
    group = entry.get("group")
    if group is None:
        return None
    numbers = list(group["documents"])
    if kind is None:
        return _receipt_unpaid(entry, row, by_number)
    if kind == "books_amount_differs":
        rows, paid, total = group["rows"], group["paid"], group["total"]
        where = "בשורה" if len(rows) == 1 else "ב-%d השורות" % len(rows)
        chip = _flag_text(kind).replace("{row}", money_text(paid)).replace("{books}", money_text(total))
        if len(rows) > 1:
            chip = chip.replace("בשורה", where, 1)
        own = a1.parse_amount(row.get("paid_raw")) if row.get("paid_raw") is not None else None
        text = "%s %s, ובחשבשבת %s כולל מע\"מ (%s): הפער גדול משקל למסמך ומ-0.1%%." % (
            "סכום ששולם בשורה" if len(rows) == 1 else "סכום ששולם ב-%d השורות שמפנות לאותם מסמכים, יחד," % len(rows),
            money_text(paid), money_text(total), _docs_said(numbers, by_number))
        if len(rows) > 1 and own is not None:
            text += " בשורה הזאת %s." % money_text(own)
        return {"kind": kind, "chip": chip, "documents": numbers, "text": text}
    return None


def receipt_unpaid_facts(entry: dict, row: dict, by_number: dict) -> list:
    """The invoice-receipts that say the money came in on a row whose status says it did not, read off the row's group
    (A1's `group`), whatever A1's order of buckets: A1 matched the group (RECEIPT_BUCKETS), its two sums agree within the
    tolerance (1 ₪ a document, the contract §0.3; never the rounding band), every document of it is an invoice-receipt (a
    tax invoice waits for its receipt, R1), and the row's "סטטוס תשלום" is not one that says the money came in (A1's
    MONEY_IN, by index; an empty status says nothing came in). [] when any of them does not hold."""
    group = entry.get("group")
    if group is None or entry.get("bucket") not in RECEIPT_BUCKETS:
        return []
    numbers = list(group["documents"])
    if not numbers or abs(group["paid"] - group["total"]) > a1.TOLERANCE_PER_DOC * len(numbers):
        return []
    if str(row.get("status_idx") or "") in a1.MONEY_IN:
        return []
    receipts = [n for n in numbers if role((by_number.get(n) or [{}])[0].get("doc_type")) == INVOICE_RECEIPT]
    return receipts if len(receipts) == len(numbers) else []


def _receipt_unpaid(entry: dict, row: dict, by_number: dict) -> Optional[dict]:
    kind = "books_receipt_unpaid"
    receipts = receipt_unpaid_facts(entry, row, by_number)
    if not receipts:
        return None
    status = status_word(row)
    chip = _flag_text(kind).replace("{doc}", ", ".join(receipts)).replace("{status}", status)
    days = sorted({str(_day(by_number[n][0].get("value_date")) or "") for n in receipts})
    text = "בחשבשבת חשבונית מס/קבלה %s מ-%s על %s כולל מע\"מ, כלומר הכסף התקבל, וסטטוס התשלום בשורה %s." % (
        ", ".join(receipts), ", ".join(_dmy(x) for x in days if x),
        money_text(sum((_money(by_number[n][0].get("total_incl")) or Decimal(0) for n in receipts), Decimal(0))), status)
    return {"kind": kind, "chip": chip, "documents": receipts, "text": text}


def _dmy(iso: str) -> str:
    return "%s.%s.%s" % (iso[8:10], iso[5:7], iso[:4]) if len(iso) >= 10 else iso


def marks_of(analysis: dict, rows_by_id: dict, idx: dict) -> tuple:
    """({item id: [its marks]} for every row A1 judged into a bucket that gives one, {item id: the note of its details}
    for every row that cites a number not yet in the copy). `idx`: the copy (`index_copy`)."""
    marks, notes = {}, {}
    for item_id, entry in analysis["rows"].items():
        row = rows_by_id.get(item_id)
        if row is None:
            continue
        mark = mark_of(entry, row, idx)
        if mark is not None:
            marks[item_id] = [mark]
        late = _not_yet(entry, idx) if entry["bucket"] in REF_BUCKETS else []
        if late:
            notes[item_id] = "%s: %s" % (", ".join(late), NOT_YET_SAID)
    return marks, notes


# ---- the documents with no charge ----------------------------------------------------------------------------------------
def _in_era(day: Optional[date]) -> bool:
    return day is not None and day >= ERA_FROM


def place_of(doc: dict, cited: set, sides: dict) -> tuple:
    """(place, why) of one held document, the first of PLACES that fits."""
    if doc.get("status") != VALID_STATUS:
        return "outside", "status"
    r = role(doc.get("doc_type"))
    if r not in LISTED_ROLES:
        return "outside", "type"
    total = _money(doc.get("total_incl"))
    if total is None or total < 0:
        return "outside", "cancellation" if total is not None else "no_total"
    day = _day(doc.get("value_date"))
    if not _in_era(day):
        return "outside", "era"
    number = _number(doc)
    if number is not None and number in cited:
        return "cited", ""
    if doc.get("missing"):
        return "missing", ""
    card = card_of(doc)
    if card == VOUCHER_CARD:
        return "vouchers", ""
    if card is None:
        # a key that names no card: the document alone, by its own type (FR-6's interim rule), never pooled with another's
        side, why = ("student", "invoice_receipt") if r == INVOICE_RECEIPT else ("unclear", no_card_why(doc))
    else:
        side, why = sides.get(card, ("unclear", "no_invoice_receipt"))
    if side == "student":
        return ("students", "") if day.year == STUDENT_WINDOW_YEAR else ("outside", "student_window")
    if side == "company":
        return "companies", ""
    return "unclear", why


def _entry(doc: dict) -> dict:
    day = _day(doc.get("value_date"))
    return {"number": str(doc.get("doc_number") or ""), "type": doc.get("doc_type"), "type_name": type_name(doc.get("doc_type")),
            "date": day.isoformat() if day else "", "total_incl": _money(doc.get("total_incl")),
            "total_excl": _money(doc.get("total_excl")), "card": str(doc.get("account_name") or "")}


def candidates_of(card_name: str, by_words: dict) -> list:
    """The Student Charge rows whose name equals the card's name as a set of words: a candidate by name, never a match."""
    words = name_words(card_name)
    return list(by_words.get(words, ())) if words else []


def _candidate(row: dict) -> dict:
    return {"id": row["id"], "name": row.get("name", ""), "state": row.get("state", ""), "status": row.get("status", ""),
            "date": row.get("date", ""), "invoice": row.get("invoice", "")}


def _newest(entries: list) -> list:
    return sorted(entries, key=lambda e: (e["date"], int(e["number"]) if e["number"].isdigit() else 0), reverse=True)


def compute(reading: dict, view: dict, snapshot: dict, keys: dict) -> dict:
    """The books part of one reading and one whole copy: the marks, the three lists and the vouchers' count, and
    `places` (stock id -> place) and `counts` (by place), which tests hold and nothing sends."""
    documents = snapshot["documents"]
    sc_raw = reading["student_charge"]["rows"]
    b2_raw = reading["b2b_charge"]["rows"]
    view_rows = (view.get("b2c") or {}).get("rows") or []
    rows_by_id = {}
    raw_by_id = {str(r["id"]): r for r in sc_raw}
    for r in view_rows:
        raw = raw_by_id.get(r["id"]) or {}
        rows_by_id[r["id"]] = dict(r, paid_raw=raw.get(a1.AMOUNT_COLUMN))
    analysis = a1.analyse(sc_raw, documents)
    marks, notes = marks_of(analysis, rows_by_id, index_copy(documents))

    cited = cited_numbers(sc_raw, a1.REF_COLUMN) | cited_numbers(b2_raw, B2["invoice"])
    sides = card_sides(documents, keys)
    by_words: dict = defaultdict(list)
    for r in view_rows:
        words = name_words(r.get("name"))
        if words:
            by_words[words].append(r)
    places: dict = {}
    lists: dict = {"students": [], "companies": [], "unclear": []}
    vouchers = 0
    for d in documents:
        place, why = place_of(d, cited, sides)
        places[d["stock_id"]] = place
        if place == "vouchers":
            vouchers += 1
        elif place == "students":
            e = _entry(d)
            found = candidates_of(e["card"], by_words)
            e.update(candidates=[_candidate(r) for r in found[:CANDIDATES_MAX]],
                     candidates_more=max(0, len(found) - CANDIDATES_MAX))
            lists["students"].append(e)
        elif place == "companies":
            e = _entry(d)
            e["accounts"] = sorted(set(keys.get(norm_key(d.get("account_key")), [])))
            lists["companies"].append(e)
        elif place == "unclear":
            e = _entry(d)
            e.update(why=why, why_text=UNCLEAR_WHY.get(why, ""))
            lists["unclear"].append(e)
    return {"marks": marks, "notes": notes, "students": _newest(lists["students"]), "companies": _newest(lists["companies"]),
            "unclear": _newest(lists["unclear"]), "vouchers": vouchers, "places": places,
            "counts": dict(Counter(places.values())), "buckets": {i: e["bucket"] for i, e in analysis["rows"].items()}}


# ---- the part as the answer carries it, and laid over the view -------------------------------------------------------------
def _iso(t: Any) -> Optional[str]:
    if isinstance(t, datetime):
        return (t if t.tzinfo else t.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).isoformat(timespec="seconds")
    return None if t is None else str(t)


def said(state: str, reasons: list) -> str:
    """What the page and the files say beside "לפי הספרים נכון ל-": nothing when the part is fresh; else its reasons'
    sentences, and what is shown instead."""
    if state == "ok":
        return ""
    text = " ".join(REASONS.get(r, r) for r in reasons)
    if state == "held":
        return (text + " " + HELD_SAID).strip()
    if state == "none" and not set(reasons) <= {"pending", "busy"}:
        return (text + " " + NONE_SAID).strip()
    return text


def public(part: dict) -> dict:
    """What any reader of the desk may see of the part: its state, its copy's time, its reasons and their sentence, and
    its key (the totals are asked by it). No document, no row, no count."""
    return {"state": part["state"], "as_of": part.get("as_of"), "reasons": list(part.get("reasons") or []),
            "said": said(part["state"], part.get("reasons") or []), "key": part.get("key")}


def _tasks_of(rows: list, months: list) -> int:
    keep = set(TASK_STATES)
    return sum(1 for r in rows if (not months or r.get("month") in months)
               and (r["state"] in keep or r.get("books_task")))


def merge(view: dict, part: dict) -> dict:
    """The view with the part laid over it, for every reader (app/desk/view.py `visible_part` then removes what a reader
    may not see): the marks on their rows - each mark's kind added to the row's flags, so the page's column "מה לבדוק" and
    the files' (view.row_reasons) say it in their order, and `books`, the marks' own words; `books_note` on a row that cites
    a number not yet in the copy, said in its details and nowhere else; a row's state is never touched -
    `books_task` on a marked row of the era, which "לטיפול" holds; each period's count of "לטיפול" (`tasks`); the students'
    list in the students' tab, the companies' list and the vouchers' count in the companies' tab, the unclear documents
    under `books_unclear`, and the part's state under `books`."""
    out = dict(view)
    out["books"] = public(part)
    marks, notes = part.get("marks") or {}, part.get("notes") or {}
    era = ERA_FROM.isoformat()
    if view.get("b2c") is not None:
        b2c = dict(view["b2c"])
        rows = []
        for r in b2c["rows"]:
            mine = marks.get(r["id"])
            if mine:
                r = dict(r, flags=list(r["flags"]) + [m["kind"] for m in mine if m["kind"] not in r["flags"]],
                         books=[{k: m[k] for k in ("kind", "chip", "text", "documents")} for m in mine],
                         books_task=bool(r.get("date")) and r["date"] >= era)
            if notes.get(r["id"]):  # a number it cites is not yet in the copy: said in its details, no mark
                r = dict(r, books_note=notes[r["id"]])
            rows.append(r)
        b2c["rows"] = rows
        b2c["periods"] = [dict(p, tasks=_tasks_of(rows, p.get("months") or [])) for p in b2c["periods"]]
        b2c["books"] = {"title": BOOKS_LIST_TITLE, "about": STUDENTS_ABOUT, "documents": list(part.get("students") or [])}
        out["b2c"] = b2c
    if view.get("b2b") is not None:
        n = int(part.get("vouchers") or 0)
        out["b2b"] = dict(view["b2b"], books={
            "title": BOOKS_LIST_TITLE, "about": COMPANIES_ABOUT, "documents": list(part.get("companies") or []),
            "vouchers": n, "vouchers_said": (VOUCHERS_SAID % (VOUCHER_CARD, "מסמך אחד" if n == 1 else "%d מסמכים" % n))
            if n else ""})
    out["books_unclear"] = {"title": BOOKS_UNCLEAR_TITLE, "about": UNCLEAR_ABOUT, "documents": list(part.get("unclear") or [])}
    return out


def reading_whole(reading: dict) -> bool:
    """Whether a desk reading may be matched to the books: no error, and both charge boards read whole (a row missing
    from a board read short would turn its documents into documents no row cites)."""
    checks = reading.get("checks") or {}
    return not reading.get("error") and all((checks.get(k) or {}).get("ok") is True for k in ("student_charge", "b2b_charge"))


# ---- the Accounts' keys ----------------------------------------------------------------------------------------------
class AccountKeys:
    """Every Account's "מפתח חשבשבת" (`text_mm3yprqa`), read from the whole Accounts board through the desk's reader with
    that one column and the item's name - never the business number (`text_mm3y3gm7`, NEVER_READ: the reader refuses it) -
    and counted whole by R0's rule (`check_board`). Kept KEYS_TTL_S; a read that fails or comes short keeps the keys held
    (cards change rarely, and their time is in /health), and is not tried again for KEYS_FAIL_GAP_S. Names are masked as
    the desk's reading masks them (`masked`)."""

    def __init__(self, make_reader: Callable[[], MondayReader], clock: Callable[[], float] = time.monotonic,
                 now: Optional[Callable[[], datetime]] = None):
        self.make_reader, self.clock = make_reader, clock
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.keys: Optional[dict] = None
        self.read_at: Optional[str] = None
        self._at: Optional[float] = None
        self._failed_at: Optional[float] = None
        self.error: Optional[str] = None

    def get(self, may_read: bool = True) -> Optional[dict]:
        """The keys: held while young, else read now - unless `may_read` is false (an answer at once), which takes what is
        held, however old, or None."""
        t = self.clock()
        if self.keys is not None and self._at is not None and 0 <= t - self._at < KEYS_TTL_S:
            return self.keys
        if not may_read or (self._failed_at is not None and 0 <= t - self._failed_at < KEYS_FAIL_GAP_S):
            return self.keys
        try:
            self.keys, self._at = self._read(), self.clock()
            self.read_at, self.error, self._failed_at = _iso(self.now()), None, None
        except Exception as exc:  # noqa: BLE001 - said by its code or class, never its text
            self.error = getattr(exc, "code", None) or type(exc).__name__
            self._failed_at = self.clock()
            logger.warning("desk books: the Accounts' keys were not read (%s)", self.error)
        return self.keys

    def _read(self) -> dict:
        reader = self.make_reader()
        try:
            reader.deadline = reader.clock() + KEYS_DEADLINE_S
            cols = [AC["hash_key"]]
            reader.require_columns([(ACCOUNTS_BOARD, cols)])
            (count, ids, rows), = reader.read_boards([(ACCOUNTS_BOARD, cols)])
        finally:
            reader.close()
        check = check_board(Board("accounts", ACCOUNTS_BOARD, tuple(cols)), count, ids, rows)
        if not check["ok"]:
            raise MondayError("count_mismatch", "accounts: items_count %s, rows %d" % (count, len(rows)))
        return account_keys_of(masked("accounts", rows))

    def health(self) -> dict:
        return {"read_at": self.read_at, "error": self.error}


# ---- one per instance --------------------------------------------------------------------------------------------------
class DeskBooks:
    """The books part of the desk, one per instance: `answer(cfg, reading, view)` is the part for the reading shown,
    computed when the reading or the copy changed (the cache's key: the reading's time and the last finished run of the
    copy), else the part already computed. One computation at a time; a request that waits longer than WAIT_S gets the
    last part. `current()` is the part last answered (the totals and the files use it: they never read). `health()`: its
    state, its copy's time and its reasons, and the Accounts' keys' time - never a count."""

    def __init__(self, make_store: Callable[[str], Any], make_reader: Callable[[], MondayReader],
                 clock: Callable[[], float] = time.monotonic, now: Optional[Callable[[], datetime]] = None):
        self.make_store, self.clock = make_store, clock
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.keys = AccountKeys(make_reader, clock, self.now)
        self._lock = threading.Lock()
        self.last_ok: Optional[dict] = None  # the last part computed from a whole copy on a whole reading
        self.part: Optional[dict] = None  # the part last answered
        self._key: Optional[tuple] = None  # what self.part was answered for
        self._store_failed_at: Optional[float] = None
        self._checked_at: Optional[float] = None  # when the last finished run was last asked for
        self.trace: dict = {"computed_at": None, "seconds": None, "error": None}

    def current(self) -> Optional[dict]:
        return self.part

    def answer(self, cfg: Any, reading: dict, view: dict, may_read: bool = True) -> dict:
        """The part for this reading. `may_read` false (an answer at once, D2's `refreshing`): monday is never read for it -
        with no Account's key held, the part says `pending` and is not kept, and the page's next request computes it."""
        off = self._off(cfg)
        if off is not None:
            self.part = off
            return off
        if not self._lock.acquire(timeout=WAIT_S):
            return self.part or self._made("none", ["busy"])
        try:
            return self._answer(cfg, reading, view, may_read)
        finally:
            self._lock.release()

    def _off(self, cfg: Any) -> Optional[dict]:
        if not getattr(cfg, "database_url", ""):
            return self._made("off", ["not_configured"])
        if cfg.company != "analiza":
            return self._made("off", ["demo" if cfg.company == "demo" else "company"])
        return None

    def _made(self, state: str, reasons: list, base: Optional[dict] = None) -> dict:
        part = dict(base or {}, state=state, reasons=list(reasons))
        if base is None:
            part.update(as_of=None, marks={}, notes={}, students=[], companies=[], unclear=[], vouchers=0)
        part["key"] = "%s|%s|%s" % (state, part.get("as_of") or "", part.get("computed_at") or "")
        return part

    def _not_ok(self, reasons: list) -> dict:
        """The last part computed from a whole copy, `held`, or `none` when there is none - with why."""
        if self.last_ok is not None:
            return self._made("held", reasons, self.last_ok)
        return self._made("none", reasons)

    def _answer(self, cfg: Any, reading: dict, view: dict, may_read: bool = True) -> dict:
        t = self.clock()
        if self._store_failed_at is not None and 0 <= t - self._store_failed_at < STORE_FAIL_GAP_S:
            self.part = self._not_ok(["store_error"])
            return self.part
        if (self.part is not None and self._key is not None and self._key[0] == reading.get("read_at")
                and self._checked_at is not None and 0 <= t - self._checked_at < RUN_CHECK_S):
            return self.part  # the same reading, and the copy was asked about a moment ago
        store = self.make_store(cfg.database_url)
        try:
            run = store.last_finished_run(cfg.db)
        except BooksStoreError as exc:
            return self._store_failed(exc)
        self._checked_at = self.clock()
        key = (reading.get("read_at"), run[0] if run else None)
        if self._key == key and self.part is not None:
            return self.part
        if not reading_whole(reading):
            part = self._not_ok(["reading_incomplete"])
        else:
            part = self._compute(cfg, store, reading, view, may_read)
            if part is None:
                return self.part
            if part["reasons"] == ["pending"]:  # not kept under the key: the next request computes it
                self.part = part
                return part
        self._key, self.part = key, part
        return part

    def _store_failed(self, exc: BooksStoreError) -> dict:
        self._store_failed_at = self.clock()
        self.trace["error"] = exc.detail
        logger.warning("desk books: the copy was not read (%s)", exc.detail)
        self._key, self.part = None, self._not_ok(["store_error"])
        return self.part

    def _compute(self, cfg: Any, store: Any, reading: dict, view: dict, may_read: bool = True) -> Optional[dict]:
        started = self.clock()
        try:
            snapshot = store.desk_snapshot(cfg.db)
        except BooksStoreError as exc:
            self._store_failed(exc)
            return None
        reason = not_ready(snapshot)
        if reason is not None:
            return self._not_ok([reason])
        keys = self.keys.get(may_read)
        if keys is None:
            return self._made("none", ["pending"]) if not may_read else self._not_ok(["accounts_unread"])
        try:
            got = compute(reading, view, snapshot, keys)
        except Exception as exc:  # noqa: BLE001 - said by class: no document, name or amount reaches the log
            logger.error("desk books: the part was not computed (%s)", type(exc).__name__)
            self.trace["error"] = type(exc).__name__
            return self._not_ok(["store_error"])
        got.update(as_of=_iso(snapshot.get("last_run_finished_at")), computed_at=_iso(self.now()),
                   reading_at=reading.get("read_at"))
        part = self._made("ok", [], got)
        self.last_ok = part
        self.trace.update(computed_at=got["computed_at"], seconds=round(self.clock() - started, 2), error=None)
        return part

    def health(self) -> dict:
        """For the public /health: the state of the part last answered, its copy's time and its reasons (closed codes),
        when it was last computed and how long it took, and when the Accounts' keys were read. Never a count."""
        part = self.part
        return {"state": part["state"] if part else None, "as_of": part.get("as_of") if part else None,
                "reasons": list(part.get("reasons") or []) if part else [],
                "computed_at": self.trace["computed_at"], "seconds": self.trace["seconds"], "error": self.trace["error"],
                "accounts": self.keys.health()}
