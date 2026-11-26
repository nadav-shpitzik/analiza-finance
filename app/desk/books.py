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
   - `books_receipt_unpaid` (the desk move plan §1a.2.1: the row says "לא שולם" while the books hold an invoice-receipt
     for its number): read off the row itself, never off A1's order of buckets - its "סטטוס תשלום" is "לא שולם", "יש
     בעיה" or empty (never "מלגת אנליזה", never a label the desk does not know), every number it cites is a valid
     invoice-receipt of the window (FR-7) that the books did not answer "not found", and its "סכום ששולם" is empty (the
     usual unpaid row: A1's `amount_unreadable`, which has no group) or its group's two sums agree within the tolerance.
     The books say the money came in (FR-L12) and the row says it did not: the case of 23.11, also when the
     invoice-receipts carry different days (A1's `dates_differ`, which A1 judges before the row's status).
   No other row is marked. **A mark moves no row out of its state** (משבצת), so no state's count or sum changes;
   a marked row whose date is from ERA_FROM on (the row's date as the desk dates it: "תאריך תקבול", else the day it was
   made) also joins "לטיפול" (`books_task`); an older one keeps its mark on the row only (spec §6.4.2, ADR-0168 §5).
2. **"מסמכים בחשבשבת בלי חיוב במאנדיי"** (spec §6.1.2; FR-C4, FR-D1): status-1 tax invoices and invoice-receipts of the era
   that no charge row cites. On a student's card, dated within Student Charge's window (FR-7), each with the Student
   Charge rows whose name equals the card's name as a set of words - **a candidate, never a match, never a link, never a
   fill** (the law of the rules' §5). A row's name is compared as the person's (`person_name`: before its first "|", so
   the balance payment "X | השלמת תשלום" is X's candidate) and shown whole. On a company's card, with the Accounts that
   name it. Card VOUCHER_CARD is counted apart (FR-D1).
3. **The side of a card** (FR-6, the plan §1a.2.3, until B3's sort codes): an Account's "מפתח חשבשבת" (read from the whole
   Accounts board, `AccountKeys`) is a company's card; otherwise a card that holds an invoice-receipt and no proforma
   (type 78: students are sent none) is a student's; anything else is `unclear`, and its documents are shown only to a
   member of both charge boards (spec §6.4.1). **A key that is empty or masked names no card** (`card_of`): the copy cleans
   a key like any text, so a key of six digits or more - a student's card keyed by an ID number, a company's by its ח.פ - is
   stored "#", and pooled by key every such card would be one. **Such a document's side is judged by its card's name**
   (`side_card`: NFKC, case folded, spaces collapsed - the same on every document of a card, spec §3.4), so a company's
   invoice-receipts keyed by its ח.פ meet the proforma beside them (FR-C4, the incident of 18.11). The name is evidence of
   the side only: never matched to an Account (a company's card is an Account's key, and a masked key is none) or to a
   row. A document with neither a key nor a name is judged alone, by its own type - an invoice-receipt a student's, as
   FR-6's interim rule; a tax invoice unclear (`key_masked`, `key_empty`).

AND ONE THING THE BOOKS PROPOSE (D4, "רישום מחשבשבת", 26.11; spec §6.2 `ref_fill` and `receipt_date_fill`, §7): a fill of
a Student Charge row's two empty cells, which the person approves and writes in the browser (app/desk/view.py
DESK_ACTIONS `books_fill`; the server writes nothing, ADR-0188). Never a rule of its own: `proposals` runs the two rules as
they are, on the desk's own reading and the whole copy:
- the number (FR-C2): app/recon/ref_fill.py `analyse` - the plan's question, on Student Charge's rows, B2B Charge's (a
  number they cite is no student's) and the Accounts' keys the desk already reads (`AccountKeys`) - its `fill` bucket;
- the date (FR-C5): app/recon/receipt_dates.py `analyse` (A1's `b2c_receipt_date_fill`) on the rows as they would be with
  every proposed number written - its `fill` bucket, the document's `value_date` for an empty "תאריך תקבול". So a row
  proposed a number is proposed its document's date with it when its date is empty, and a row that already cites a valid
  invoice-receipt and has an empty date is proposed the date alone. A filled date is never proposed again.
**Only the desk's era** (ERA_FROM, Chava's start, ADR-0168 §5): a row the desk dates from ERA_FROM on, with a document
dated from it on. Anything older - a row, or a row's document - is the cleanup a session runs (backlog 0301 C1), never a
proposal here (spec §6.4.2). A proposal and a mark never meet on one row: every mark is of a row that cites a number,
and FR-C2 proposes only to an empty cell; a date comes only from A1's `fill`, which is no bucket a mark comes from, and
only to a row whose status says the money came in, where `books_receipt_unpaid` is only of one whose status says it did
not. A proposal is laid on the view (`merge`) only from a part that is current (`ok`) and computed on the very reading
shown, never from a held one: the page writes what it shows. And a candidate by name that the books propose nothing to
says why (the QA of 26.11, 9; `candidate_why`): FR-C2's bucket for the row, from the very analysis the proposals use, or
that the row is proposed another document, or that it is before the era - in closed words (CANDIDATE_WHY), never a name.

EVERY DOCUMENT OF THE ERA LANDS IN ONE PLACE (spec §6.3.1), the first that fits in PLACES: outside (another status or
type, a cancellation, before the era, or a student's outside Student Charge's window), cited by a charge row, answered
"not found" (missing), counted apart (the vouchers' card), or listed - the students', the companies' or the unclear.

ONLY A WHOLE COPY CHANGES WHAT IS SHOWN (spec §6.1.5). A result is computed only on a desk reading whose two charge boards
were read whole and on a copy that is whole by A1's own rule (app/books/store.py `not_ready`, judged on one repeatable-
read snapshot, `desk_snapshot`). Otherwise the last result this instance computed is shown, `held`, with the time of its
copy and why it is not newer; with none, `none` and why. A whole copy older than COPY_STALE_S is `held` too
(`copy_stale`): the books are read every hour, so its marks never pose as current on a sync that stopped. Never on Demo
(`demo`: the books of the Demo company must never meet the real boards, the rules' §0.6), nor without a database
(`not_configured`): `off`.

WHAT LEAVES. The result lives in this instance's memory and in the desk's gated answer only: the marks, the proposals and
the students' list in the students' tab (Student Charge's members), the companies' list in the companies' tab (B2B
Charge's), the unclear documents only to a member of both (app/desk/view.py `visible_part`). Nothing of it is logged,
stored or put in /health, which says the state, the copy's time and the reasons - never a count. A card's name from the books was cleaned
of ID numbers before it was stored (spec §3.4), and no card key, no ID number and no business number is in an answer.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo

from ..books.doctypes import DRAFT_STATUS, INFERRED, role
from ..books.store import BooksStoreError, not_ready, summarize_series
from ..monday.client import MondayError, MondayReader
from ..recon import receipt_dates as a1
from ..recon import ref_fill as fr_c2
# The cards, the names and where a document lands are shared with FR-C2 (app/recon/ref_fill.py) since 26.11: moved from
# here, never copied, so the desk's list and the plan's fills judge a document by one code
from ..recon.cards import (ERA_FROM, INVOICE, INVOICE_RECEIPT, KEY_MASK, LISTED_ROLES, NAME_CARD, PLACES,  # noqa: F401
                           PROFORMA, STUDENT_WINDOW_YEAR, VOUCHER_CARD, account_keys_of, card_of, card_sides,
                           cited_numbers, name_words, no_card_why, norm_key, norm_name, person_name, place_of, side_card,
                           side_of)
from ..recon.cards import day_of as _day
from ..recon.cards import money_of as _money
from ..recon.cards import number_of as _number
from ..recon.reading import Board, check_board
from . import dismissals as dz
from .dismissals_store import DismissalRefused
from .reading import AC, ACCOUNTS_BOARD, B2, masked
from .view import (BOOKS_FILL_BUTTON, BOOKS_LIST_TITLE, BOOKS_UNCLEAR_TITLE, DESK_TZ, LEGEND_FLAGS, P_PROBLEM, P_UNPAID,
                   TASK_STATES, status_word)

logger = logging.getLogger("analiza_finance.desk")

# ERA_FROM (Chava's start), STUDENT_WINDOW_YEAR (FR-7), VOUCHER_CARD (FR-D1), the roles, PLACES, KEY_MASK and NAME_CARD
# live in app/recon/cards.py, imported above

# The marks, each from A1's buckets (the first that fits a row in a1.BUCKETS; one bucket a row)
MARKS = ("books_ref_invalid", "books_amount_differs", "books_receipt_unpaid")
MARK_OF_BUCKET = {"ref_not_in_books": "books_ref_invalid", "ref_ambiguous": "books_ref_invalid",
                  "ref_not_an_invoice": "books_ref_invalid", "amount_mismatch": "books_amount_differs"}
# D4: what the books propose on a row, as a chip of its own (its words view.LEGEND_FLAGS', filled here): the number (and
# with it the date, when that is empty too), or the date alone
FILLS = ("books_ref_fill", "books_date_fill")
FILL_ACTION = "books_fill"  # app/desk/view.py DESK_ACTIONS: the one action that writes what the books propose
# `books_receipt_unpaid` is read off the row itself (`receipt_unpaid_facts`), whatever A1's bucket: the statuses that say
# no money came in and are no decision of their own - "לא שולם", "יש בעיה", or empty. Never "מלגת אנליזה" (a scholarship:
# nothing is due) and never a label the desk does not know (the review of dfa967b: noise)
UNPAID_STATUSES = (P_UNPAID, P_PROBLEM)

# Why a card's side is not clear (cards.UNCLEAR_CODES): the documents of such a card go to members of both charge boards
# only
UNCLEAR_WHY = {
    "no_invoice_receipt": "בכרטיס אין חשבונית מס/קבלה, והוא אינו מפתח חשבשבת של חברה במאנדיי",
    "invoice_receipt_and_proforma": "בכרטיס יש חשבונית מס/קבלה וגם חשבון עסקה, והוא אינו מפתח חשבשבת של חברה במאנדיי",
    # a key that names no card (`card_of`), judged by the card's name (`side_card`)
    "name_no_invoice_receipt": "המפתח של הכרטיס הוסתר בעותק של הספרים (מספר של שש ספרות ומעלה, כמו מספר זהות או ח.פ) או "
                               "שאין לו מפתח, ולכן הוא נשפט לפי השם שבו, ובמסמכים בשם הזה אין חשבונית מס/קבלה",
    "name_invoice_receipt_and_proforma": "המפתח של הכרטיס הוסתר בעותק של הספרים (מספר של שש ספרות ומעלה, כמו מספר זהות או "
                                         "ח.פ) או שאין לו מפתח, ולכן הוא נשפט לפי השם שבו, ובשם הזה יש חשבונית מס/קבלה "
                                         "וגם חשבון עסקה, כמו בכרטיס של חברה",
    # neither a key nor a name: the document alone, and a tax invoice alone says no side
    "key_masked": "המפתח של הכרטיס הוסתר בעותק של הספרים (מספר של שש ספרות ומעלה, כמו מספר זהות או ח.פ) ואין בו שם, ולכן "
                  "החשבונית נשפטת לבדה, וחשבונית מס לבדה לא אומרת של מי היא",
    "key_empty": "למסמך אין בעותק של הספרים מפתח כרטיס ולא שם, ולכן החשבונית נשפטת לבדה, וחשבונית מס לבדה לא אומרת של מי "
                 "היא",
}

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
    "copy_stale": "העותק של חשבשבת לא התעדכן יותר משלוש שעות.",
    "store_error": "לא הצלחנו לקרוא עכשיו את העותק של חשבשבת.",
    "accounts_unread": "לא הצלחנו לקרוא ממאנדיי את מפתחות חשבשבת של החברות.",
    "reading_incomplete": "הקריאה ממאנדיי לא שלמה.",
    # an answer at once (a reading past its span, answered while the next is read) on an instance that holds no Account's
    # key yet: never a read of monday in it - the page's own next request computes the part
    "pending": "ההשוואה לחשבשבת מתעדכנת.",
    "busy": "ההשוואה לחשבשבת עוד מחושבת.",
    # a note, not a reason the part is not current (NOTES): Student Charge is compared to the books within its window only
    # (FR-7), so from the year after it no student's document is listed or marked - said, never an empty list without a word
    "student_window_ended": "ההשוואה של Student Charge לחשבשבת היא של %d בלבד, ולכן מסמכים של סטודנטים מ-%d ואילך לא "
                            "מסומנים ולא מופיעים ברשימה." % (STUDENT_WINDOW_YEAR, STUDENT_WINDOW_YEAR + 1),
}
# the reasons that note something of a part that may still be current (`ok`): said after the others
NOTES = ("student_window_ended",)
# What a part that is not fresh says after its reasons
HELD_SAID = "מוצגים הסימונים והרשימות מהעותק השלם האחרון."
NONE_SAID = "לכן אין כאן סימונים מהספרים ואין רשימה של מסמכים בלי חיוב."

# The lists' own sentences, above each (their headings are the legend's words, view.BOOKS_LIST_TITLE and the rest)
STUDENTS_ABOUT = ("חשבוניות מס וחשבוניות מס/קבלה תקפות מ-%s ועד סוף %d, בכרטיס של סטודנט/ית בחשבשבת, ששום שורה בלוחות "
                  "החיובים לא מפנה אליהן באסמכתת חשבונית. ליד מסמך: שורות ב-Student Charge ששמן זהה לשם בכרטיס, כל אחת "
                  "מועמדת לפי שם בלבד: התאמה נעשית רק לפי מספר המסמך באסמכתת חשבונית. כשחשבשבת מציעה שורה אחת למסמך, "
                  "ההצעה כתובה מתחת, ו\"%s\" כותב במאנדיי את המספר בשורה אחרי אישור שלך."
                  % (ERA_FROM.strftime("%d.%m.%Y"), STUDENT_WINDOW_YEAR, BOOKS_FILL_BUTTON))
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
# QA of 26.11 (D): a mark's chip on the page is short - "חשבשבת: " and a word or two (view.LEGEND_FLAGS, the legend's term)
# - since the narrow "שיבוץ" column wrapped the whole words to four lines. The whole words, filled as before (`long`), are
# what the Excel file's "מה לבדוק" says, as it did; the row's details say the sentence (`text`), which is also the chip's
# title on the page
MARK_WORDS = {"books_ref_invalid": "מספר החשבונית שבשורה לא נמצא בחשבשבת כמסמך תקף: {why}",
              "books_amount_differs": "בשורה {row}, בחשבשבת {books}",
              "books_receipt_unpaid": "בחשבשבת חשבונית מס/קבלה {doc}, והסטטוס {status}"}
# the short word of a number the row cites that is no valid document, by why; two numbers refused for two reasons: "מספר
# לא תקף"
REF_SHORT = {"ref_not_in_books": "לא נמצא", "draft": "טיוטה", "ref_ambiguous": "מספר כפול", "negative": "סכום שלילי",
             "no_total": "בלי סכום", "other_type": "לא חשבונית", "several": "לא תקף"}
CANDIDATES_MAX = 10  # a common name shows its first ten rows, and how many more there are
# D4 (QA of 26.11, 9): why the books propose nothing for a listed document on a candidate row: FR-C2's bucket for the row
# (app/recon/ref_fill.py BUCKETS, the very analysis the proposals come from), in a few words the page draws beside the
# candidate - never a name or an ID number. "other_document": the row is proposed another document; "era": the row is
# before the desk's era, so the desk proposes nothing to it (`proposals`)
CANDIDATE_WHY = {
    "not_active": "השורה בארכיון או שנמחקה",
    "not_money_in": "סטטוס התשלום של השורה לא אומר שהכסף נכנס",
    "has_ref": "לשורה כבר יש אסמכתה %s",
    "ref_unreadable": "באסמכתת החשבונית של השורה יש טקסט שאינו רשימת מספרים",
    "amount_unreadable": "סכום ששולם בשורה ריק או אינו מספר",
    "out_of_window": "השורה אינה מתוארכת ב-%d" % STUDENT_WINDOW_YEAR,
    "no_candidate": "אין בחשבשבת מסמך פנוי בשם של השורה",
    "amount_differs": "אין בחשבשבת מסמך בשם של השורה ובסכום ששולם בה",
    "date_far": "המסמכים בשם ובסכום של השורה רחוקים יותר מ-%d יום מהתאריך שלה" % fr_c2.FILL_WINDOW_DAYS,
    "tax_invoice_waits": "בשם ובסכום של השורה יש בחשבשבת חשבונית מס, שאינה הוכחה שהכסף נכנס",
    "two_candidates": "בשם ובסכום של השורה יש בחשבשבת יותר מחשבונית מס/קבלה אחת",
    "number_ambiguous": "מספר החשבונית שמתאימה לשורה לא מזהה אותה לבדה בחשבשבת",
    "document_wanted_twice": "גם שורה אחרת מתאימה לאותה חשבונית",
    "other_document": "לשורה מתאימה חשבונית אחרת: %s",
    "era": "השורה מלפני %s, ולכן אין כאן הצעה" % ERA_FROM.strftime("%d.%m.%Y"),
}
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
# a whole copy whose last run finished longer ago than this is no longer current (`copy_stale`): three ticks missed
COPY_STALE_S = 3 * 3600
# the longest a part's key may be (`DeskBooks._made`): what the totals and the files are asked by is refused past it
BOOKS_KEY_MAX = 400
# a request that may read and finds the part being computed waits at most this long, then is answered with the last
# part; an answer at once (D2's `refreshing`) never waits for it
WAIT_S = 30


# ---- small readers ---------------------------------------------------------------------------------------------------
# `_money`, `_number` and `_day`, and the names, keys and sides of cards, are app/recon/cards.py's (imported above)
def money_text(v: Decimal) -> str:
    """An amount as the desk's rows and files write one: agorot, thousands with a comma, the shekel after it."""
    return "{:,.2f} ₪".format(v)


def type_name(doc_type: Any) -> str:
    try:
        return INFERRED[int(doc_type)][1]
    except (KeyError, TypeError, ValueError):
        return "סוג %s" % doc_type


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
    """(the long words' part, the details' sentence, why in a word: a key of REF_SHORT) for one number of the row that is
    no valid document."""
    if bucket == "ref_not_in_books" and number in idx["drafts"]:
        return "%s %s" % (number, DRAFT_WHY[0]), "%s: %s" % (number, DRAFT_WHY[1]), "draft"
    by_number = idx["by_number"]
    if bucket in REF_WHY:
        word, sentence = REF_WHY[bucket]
        return "%s %s" % (number, word), "%s: %s" % (number, sentence), bucket
    doc = (by_number.get(number) or [{}])[0]
    total, name, r = _money(doc.get("total_incl")), type_name(doc.get("doc_type")), role(doc.get("doc_type"))
    if r in LISTED_ROLES and total is not None and total < 0:
        # a negative sum, said as one - never as another type (A1 refuses a negative invoice-receipt as it refuses a
        # negative tax invoice); FR-4 names a negative tax invoice a cancellation
        return "%s %s בסכום שלילי" % (number, name), "%s: %s בסכום שלילי%s" % (
            number, name, ", כלומר ביטול" if r == INVOICE else ""), "negative"
    if role(doc.get("doc_type")) in LISTED_ROLES and total is None:
        return "%s %s בלי סכום" % (number, name), "%s: %s בלי סכום" % (number, name), "no_total"
    return "%s %s" % (number, name), "%s: %s, ולא חשבונית מס או חשבונית מס/קבלה" % (number, name), "other_type"


def _docs_said(numbers: list, by_number: dict) -> str:
    return ", ".join("%s %s" % (type_name((by_number.get(n) or [{}])[0].get("doc_type")), n) for n in numbers)


def mark_of(entry: dict, row: dict, idx: dict) -> Optional[dict]:
    """The mark A1's judgement of one row gives, or None: {"kind", "chip" (the page's short chip, filled: the legend's
    term), "long" (the whole words, filled: the Excel file's "מה לבדוק"), "text" (the sentence the row's details say, and
    the chip's title), "documents" (the numbers it is about)}. `row` is the view's row (its status's label, by index);
    `idx`, the copy (`index_copy`): a number just past the end of its series is not yet in the copy, and no mark is given
    for it."""
    by_number = idx["by_number"]
    kind = MARK_OF_BUCKET.get(entry["bucket"])
    if kind == "books_ref_invalid":
        late = _not_yet(entry, idx)
        refused = [(n, b) for n, b in entry.get("refused") or [] if n not in late]
        if not refused:
            return None
        why = [_ref_reason(n, b, idx) for n, b in refused]
        keys = {k for _w, _s, k in why}
        short = REF_SHORT[keys.pop() if len(keys) == 1 else "several"]
        return {"kind": kind, "chip": _flag_text(kind).replace("{why}", short),
                "long": MARK_WORDS[kind].replace("{why}", "; ".join(w for w, _s, _k in why)),
                "documents": [n for n, _b in refused],
                "text": "%s: %s." % (MARK_WORDS[kind].split(":")[0], "; ".join(x for _w, x, _k in why))}
    group = entry.get("group")
    if kind == "books_amount_differs" and group is not None:
        numbers = list(group["documents"])
        rows, paid, total = group["rows"], group["paid"], group["total"]
        own = a1.parse_amount(row.get("paid_raw")) if row.get("paid_raw") is not None else None
        # several rows over the same documents: the row's own sum, and the rows' together in brackets - inside the legend's
        # one term ("בשורה ..., בחשבשבת ..."), never a chip of other words (the review of dfa967b: "ב-2 השורות X" was a
        # label neither monday's nor the legend's)
        said_row = money_text(paid) if len(rows) == 1 else "%s(ב-%d השורות %s)" % (
            money_text(own) + " " if own is not None else "", len(rows), money_text(paid))
        long = MARK_WORDS[kind].replace("{row}", said_row).replace("{books}", money_text(total))
        text = "%s %s, ובחשבשבת %s כולל מע\"מ (%s): הפער גדול משקל למסמך ומ-0.1%%." % (
            "סכום ששולם בשורה" if len(rows) == 1 else "סכום ששולם ב-%d השורות שמפנות לאותם מסמכים, יחד," % len(rows),
            money_text(paid), money_text(total), _docs_said(numbers, by_number))
        if len(rows) > 1 and own is not None:
            text += " בשורה הזאת %s." % money_text(own)
        return {"kind": kind, "chip": _flag_text(kind), "long": long, "documents": numbers, "text": text}
    if kind is None:
        return _receipt_unpaid(entry, row, idx)
    return None


def _unpaid_status(row: dict) -> bool:
    """Whether the row's "סטטוס תשלום" says no money came in and is no decision of its own: "לא שולם" or "יש בעיה" by
    index, or empty (no index and no label). "מלגת אנליזה", a status that says money came in, and a label the desk does
    not know are not."""
    s = str(row.get("status_idx") or "")
    return s in UNPAID_STATUSES or (s == "" and not row.get("status"))


def receipt_unpaid_facts(entry: dict, row: dict, idx: dict) -> list:
    """The invoice-receipts the row cites, which say the money came in, when the row says it did not - read off the row
    itself, whatever A1's bucket (A1's `documents`, `refused` and `group`); [] when any of these does not hold:
    1. its "סטטוס תשלום" is "לא שולם", "יש בעיה" or empty (`_unpaid_status`);
    2. every number it cites is a valid invoice-receipt: A1 refused none (one status-1 document each, of type 2, not
       negative), each dated in Student Charge's window (FR-7: outside it, never a gap) and not answered "not found";
    3. its "סכום ששולם" is empty - the usual unpaid row, A1's `amount_unreadable`, which has no group - or its group's two
       sums agree within the tolerance (1 ₪ a document, the contract §0.3; never the rounding band: that is the amount's)."""
    if not _unpaid_status(row):
        return []
    numbers = list(entry.get("documents") or [])
    if not numbers or entry.get("refused"):
        return []
    for n in numbers:
        docs = idx["by_number"].get(n) or []
        day = _day(docs[0].get("value_date")) if len(docs) == 1 else None
        if (len(docs) != 1 or role(docs[0].get("doc_type")) != INVOICE_RECEIPT or docs[0].get("missing")
                or day is None or day.year != STUDENT_WINDOW_YEAR):
            return []
    if not a1._clean(row.get("paid_raw")):  # empty as A1 reads a cell: an invisible mark alone is empty too
        return numbers
    group = entry.get("group")
    if group is None or abs(group["paid"] - group["total"]) > a1.TOLERANCE_PER_DOC * len(group["documents"]):
        return []
    return numbers


def _receipt_unpaid(entry: dict, row: dict, idx: dict) -> Optional[dict]:
    kind = "books_receipt_unpaid"
    by_number = idx["by_number"]
    receipts = receipt_unpaid_facts(entry, row, idx)
    if not receipts:
        return None
    status = status_word(row)
    long = MARK_WORDS[kind].replace("{doc}", ", ".join(receipts)).replace("{status}", status)
    days = sorted({str(_day(by_number[n][0].get("value_date")) or "") for n in receipts})
    text = "בחשבשבת חשבונית מס/קבלה %s מ-%s על %s כולל מע\"מ, כלומר הכסף התקבל, וסטטוס התשלום בשורה %s." % (
        ", ".join(receipts), ", ".join(_dmy(x) for x in days if x),
        money_text(sum((_money(by_number[n][0].get("total_incl")) or Decimal(0) for n in receipts), Decimal(0))), status)
    return {"kind": kind, "chip": _flag_text(kind), "long": long, "documents": receipts, "text": text}


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


# ---- what the books propose to fill (D4) ----------------------------------------------------------------------------------
def proposals(sc_raw: list, b2_raw: list, documents: list, keys: dict, rows_by_id: dict,
              c2: Optional[dict] = None) -> dict:
    """{item id: {"number"?, "date"?}} for every row of the desk's era that the books propose a fill to - FR-C2's number
    (ref_fill.analyse, its `fill` bucket, on the desk's own reading: Student Charge's rows, B2B Charge's and the Accounts'
    keys) and A1's date (receipt_dates.analyse, its `fill` bucket, on the rows as they would be with every proposed number
    written: a proposed number stands alone in its group, cited by no other row, so no other row's judgement moves). The
    era: the row's date as the desk dates it (`rows_by_id`, the view's rows: "תאריך תקבול", else the day it was made) and
    the document's, both from ERA_FROM on; an older one is a session's cleanup (backlog 0301 C1), and a row whose number is
    not proposed for its document's age is proposed no date of that document either. `c2`: FR-C2's rows of this very
    reading and copy when the caller holds them (`compute`, which says from them why a candidate has none)."""
    era = ERA_FROM.isoformat()
    if c2 is None:
        c2 = fr_c2.analyse(sc_raw, documents, b2_raw, keys)["rows"]
    numbers = {i: e["proposed"] for i, e in c2.items() if e["bucket"] == "fill"}
    after = [dict(r, **{a1.REF_COLUMN: numbers[str(r.get("id"))]}) if str(r.get("id")) in numbers else r for r in sc_raw]
    dated = a1.analyse(after, documents)["rows"]
    out: dict = {}
    for item_id, row in rows_by_id.items():
        if not row.get("date") or row["date"] < era:
            continue
        fill: dict = {}
        if item_id in numbers:
            if c2[item_id]["document_date"] < era:
                continue
            fill["number"] = numbers[item_id]
        entry = dated.get(item_id)
        if entry is not None and entry["bucket"] == "fill" and entry["books_day"] >= era:
            fill["date"] = entry["books_day"]
        if fill:
            out[item_id] = {"values": fill, "documents": [fill["number"]] if "number" in fill else list(entry["documents"])}
    return out


def fill_mark(fill: dict, idx: dict) -> dict:
    """A proposal as a chip of the row (FILLS, its words view.LEGEND_FLAGS', filled) and the sentence its details say: the
    documents it rests on, their day and their total, and what "רישום מחשבשבת" writes."""
    values, numbers, by_number = fill["values"], fill["documents"], idx["by_number"]
    docs = [by_number[n][0] for n in numbers]
    days = sorted({_day(d.get("value_date")).isoformat() for d in docs})
    total = sum((_money(d.get("total_incl")) or Decimal(0) for d in docs), Decimal(0))
    said = "%s מ-%s על %s כולל מע\"מ" % (_docs_said(numbers, by_number), ", ".join(_dmy(x) for x in days), money_text(total))
    if "number" in values:
        kind = FILLS[0]
        chip = _flag_text(kind).replace("{number}", values["number"])
        text = ("בחשבשבת %s: היחידה בשם שבשורה, בסכום ששולם ובתוך %d יום מהתאריך שלה, ששום שורה לא מפנה אליה. רישום "
                "מחשבשבת כותב את המספר באסמכתת חשבונית%s." % (said, fr_c2.FILL_WINDOW_DAYS,
                                                           " ואת התאריך בתאריך תקבול" if "date" in values else ""))
    else:
        kind = FILLS[1]
        chip = _flag_text(kind).replace("{date}", _dmy(values["date"]))
        text = "באסמכתת חשבונית: %s בחשבשבת. תאריך תקבול ריק, ורישום מחשבשבת כותב בו את התאריך הזה." % said
    return {"kind": kind, "chip": chip, "text": text, "documents": list(numbers)}


def candidate_why(item_id: str, number: str, c2: dict, fills: dict) -> Optional[dict]:
    """Why the books propose nothing for this document (`number`) on a candidate row (QA of 26.11, 9): FR-C2's bucket for
    the row (`c2`, ref_fill.analyse's rows, the analysis the proposals come from), or that the row is proposed another
    document, or that it is before the desk's era - {"key", "text"}; None when the row is proposed this very document (the
    proposal line says it) or is not in the analysis. Never a rule of its own, never a name or an ID number: a number the
    row cites is said as A1 reads the cell, numbers only."""
    entry = c2.get(item_id)
    if entry is None or ((fills.get(item_id) or {}).get("values") or {}).get("number") == number:
        return None
    key, arg = entry["bucket"], None
    if key == "fill":
        key, arg = ("era", None) if entry["proposed"] == number else ("other_document", entry["proposed"])
    elif key == "has_ref":
        arg = ", ".join(a1.parse_refs(entry["current"]) or [])
    text = CANDIDATE_WHY[key]
    return {"key": key, "text": text % arg if arg is not None else text}


# ---- the documents with no charge ----------------------------------------------------------------------------------------
# `place_of` (where a held document lands, the first of PLACES that fits) is app/recon/cards.py's: the desk asks it from
# ERA_FROM, FR-C2 from the first day of Student Charge's window
def _entry(doc: dict) -> dict:
    day = _day(doc.get("value_date"))
    return {"number": str(doc.get("doc_number") or ""), "type": doc.get("doc_type"), "type_name": type_name(doc.get("doc_type")),
            "date": day.isoformat() if day else "", "total_incl": _money(doc.get("total_incl")),
            "total_excl": _money(doc.get("total_excl")), "card": str(doc.get("account_name") or "")}


def candidates_of(card_name: str, by_words: dict) -> list:
    """The Student Charge rows whose name equals the card's name as a set of words: a candidate by name, never a match.
    `by_words` is keyed by each row's name as the person's (`person_name`); the card's name is taken whole."""
    words = name_words(card_name)
    return list(by_words.get(words, ())) if words else []


def _candidate(row: dict) -> dict:
    return {"id": row["id"], "name": row.get("name", ""), "state": row.get("state", ""), "status": row.get("status", ""),
            "date": row.get("date", ""), "invoice": row.get("invoice", "")}


def _with_why(candidate: dict, why: Optional[dict]) -> dict:
    return dict(candidate, why=why["text"], why_key=why["key"]) if why else candidate


def dismissable(e: dict, doc: dict, place: str, side: str) -> dict:
    """R5: a listed document's entry with what a press of "לא פער" names - its kind (the list's, dz.DOC_KINDS), its subject
    (its number as A1 reads one) and its evidence (dz.document_evidence, `side` its list or why its side is unclear). A
    document with no number of digits has none: it cannot be dismissed."""
    subject = _number(doc)
    if subject is None or len(subject) > dz.SUBJECT_MAX:
        return e
    kind = dz.DOC_KINDS[place]
    e.update(kind=kind, subject=subject, evidence=dz.digest(dz.document_evidence(kind, doc, side)))
    return e


def _newest(entries: list) -> list:
    return sorted(entries, key=lambda e: (e["date"], int(e["number"]) if e["number"].isdigit() else 0), reverse=True)


def compute(reading: dict, view: dict, snapshot: dict, keys: dict) -> dict:
    """The books part of one reading and one whole copy: the marks, the proposals (D4: `fills`, {item id: {"values",
    "documents", "mark"}}, and on each students' document proposed, its `proposal`), the three lists and the vouchers'
    count, and `places` (stock id -> place) and `counts` (by place), which tests hold and nothing sends."""
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
    idx = index_copy(documents)
    marks, notes = marks_of(analysis, rows_by_id, idx)
    c2 = fr_c2.analyse(sc_raw, documents, b2_raw, keys)["rows"]       # FR-C2, once: the proposals and why none (QA 9)
    # R5: each alert's evidence ("לא פער" keeps it, app/desk/dismissals.py) and its row's name (the hidden list says it)
    held = dz.held_by_number(documents)
    for item_id, ms in marks.items():
        for m in ms:
            m.update(name=rows_by_id[item_id].get("name", ""), evidence=dz.digest(dz.mark_evidence(
                m["kind"], item_id, analysis["rows"][item_id], rows_by_id[item_id], held)))
    fills = proposals(sc_raw, b2_raw, documents, keys, rows_by_id, c2)
    for item_id, fill in fills.items():
        fill["mark"] = fill_mark(fill, idx)
        fill["mark"].update(name=rows_by_id[item_id].get("name", ""), evidence=dz.digest(dz.fill_evidence(
            fill["mark"]["kind"], item_id, fill["values"])))
    # the document each proposed number names (FR-3: one status-1 document carries it), and the row it is proposed to
    proposed = {idx["by_number"][f["values"]["number"]][0]["stock_id"]: (i, f["values"]["number"])
                for i, f in fills.items() if "number" in f["values"]}

    cited = cited_numbers(sc_raw, a1.REF_COLUMN) | cited_numbers(b2_raw, B2["invoice"])
    sides = card_sides(documents, keys)
    by_words: dict = defaultdict(list)
    for r in view_rows:
        words = name_words(person_name(r.get("name")))  # "X | השלמת תשלום" is X's row; its full name is shown
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
            e.update(candidates=[_with_why(_candidate(r), candidate_why(r["id"], e["number"], c2, fills))
                                 for r in found[:CANDIDATES_MAX]],
                     candidates_more=max(0, len(found) - CANDIDATES_MAX))
            if d["stock_id"] in proposed:  # D4: the row the books propose to write its number in
                item_id, number = proposed[d["stock_id"]]
                e["proposal"] = {"item_id": item_id, "name": rows_by_id[item_id].get("name", ""), "number": number}
            lists["students"].append(dismissable(e, d, place, place))
        elif place == "companies":
            e = _entry(d)
            e["accounts"] = sorted(set(keys.get(norm_key(d.get("account_key")), [])))
            lists["companies"].append(dismissable(e, d, place, place))
        elif place == "unclear":
            e = _entry(d)
            e.update(why=why, why_text=UNCLEAR_WHY.get(why, ""))
            lists["unclear"].append(dismissable(e, d, place, "unclear:" + why))
    # R5: a number two documents of one list carry (FR-3: a number names one document; A1 calls one so held ambiguous) names
    # neither - a press would name the other's evidence - so neither carries evidence: neither is pressed, hidden or "חזר"
    for entries in lists.values():
        held = Counter(e.get("subject") for e in entries if e.get("subject"))
        for e in entries:
            if held[e.get("subject")] > 1:
                e.pop("evidence", None)
    return {"marks": marks, "notes": notes, "fills": fills, "reading_at": reading.get("read_at"),
            "students": _newest(lists["students"]), "companies": _newest(lists["companies"]),
            "unclear": _newest(lists["unclear"]), "vouchers": vouchers, "places": places,
            "counts": dict(Counter(places.values())), "buckets": {i: e["bucket"] for i, e in analysis["rows"].items()}}


# ---- the part as the answer carries it, and laid over the view -------------------------------------------------------------
def _iso(t: Any) -> Optional[str]:
    if isinstance(t, datetime):
        return (t if t.tzinfo else t.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).isoformat(timespec="seconds")
    return None if t is None else str(t)


def said(state: str, reasons: list) -> str:
    """What the page and the files say beside "לפי הספרים נכון ל-": nothing when the part is fresh; else its reasons'
    sentences, and what is shown instead; and last its notes (NOTES), which a fresh part says too."""
    notes = " ".join(REASONS.get(r, r) for r in reasons if r in NOTES)
    main = [r for r in reasons if r not in NOTES]
    text = "" if state == "ok" else " ".join(REASONS.get(r, r) for r in main)
    if state == "held":
        text = (text + " " + HELD_SAID).strip()
    elif state == "none" and not set(main) <= {"pending", "busy"}:
        text = (text + " " + NONE_SAID).strip()
    return (text + " " + notes).strip()


def public(part: dict) -> dict:
    """What any reader of the desk may see of the part: its state, its copy's time, its reasons and their sentence, and
    its key (the totals are asked by it). No document, no row, no count."""
    return {"state": part["state"], "as_of": part.get("as_of"), "reasons": list(part.get("reasons") or []),
            "said": said(part["state"], part.get("reasons") or []), "key": part.get("key")}


def _tasks_of(rows: list, months: list) -> int:
    keep = set(TASK_STATES)
    return sum(1 for r in rows if (not months or r.get("month") in months)
               and (r["state"] in keep or r.get("books_task")))


# what a row's `books` says of each mark and proposal: its words, its documents, and since R5 its evidence (what a press of
# "לא פער" or "לא להציע" names) and, when it came back from one, `returned` (who, when); since the QA of 26.11 (D) a mark's
# whole words too (`long`: its chip is short, and the Excel file's "מה לבדוק" says these)
BOOK_KEYS = ("kind", "chip", "long", "text", "documents", "evidence", "returned")


def lays_fills(part: dict, read_at: Any) -> bool:
    """D4: what the books propose is laid only from a current part computed on this very reading - never from a held one,
    and never from one of another reading laid over this one (an answer at once may carry the last part computed)."""
    return part.get("state") == "ok" and bool(part.get("reading_at")) and part.get("reading_at") == read_at


def _laid(m: dict) -> dict:
    return {k: m[k] for k in BOOK_KEYS if k in m}


def _dismissed(part: dict, tab: str, laid: bool) -> list:
    """R5: what the part holds hidden ("לא פער") of one tab or of the unclear list - a proposal only where proposals are
    laid."""
    return [x for x in (part.get("hidden") or {}).get(tab) or [] if laid or x["kind"] not in dz.FILL_KINDS]


def _by_kind(items: list) -> dict:
    return dict(Counter(x["kind"] for x in items))


def merge(view: dict, part: dict) -> dict:
    """The view with the part laid over it, for every reader (app/desk/view.py `visible_part` then removes what a reader
    may not see): the marks on their rows - each mark's kind added to the row's flags, so the page's column "מה לבדוק" and
    the files' (view.row_reasons) say it in their order, and `books`, the marks' own words; `books_note` on a row that cites
    a number not yet in the copy, said in its details and nowhere else; a row's state is never touched -
    `books_task` on a marked row of the era, which "לטיפול" holds; each period's count of "לטיפול" (`tasks`); the students'
    list in the students' tab, the companies' list and the vouchers' count in the companies' tab, the unclear documents
    under `books_unclear`, and the part's state under `books`. D4: on a row the books propose a fill to, its chip (a flag,
    beside the marks' in `books`), its values (`books_fill`: {number?, date?}) and the action that writes them, and on its
    document in the students' list its `proposal` - only from a current part of this very reading (`laid`); a proposal
    neither moves a row nor joins "לטיפול". R5: each list's part also carries what is hidden as "לא פער" of it (`hidden`,
    with who and when; app/desk/dismissals.py `hide` took them out of the marks and the lists, so out of "לטיפול" and its
    counts too) and, by kind, how many are hidden and how many came back (`hidden_counts`, `returned_counts`): the
    students' tab its marks, proposals and documents, the companies' tab its documents, `books_unclear` its own."""
    out = dict(view)
    out["books"] = public(part)
    marks, notes = part.get("marks") or {}, part.get("notes") or {}
    era = ERA_FROM.isoformat()
    laid = lays_fills(part, view.get("read_at"))
    fills = (part.get("fills") or {}) if laid else {}
    if view.get("b2c") is not None:
        b2c = dict(view["b2c"])
        rows = []
        for r in b2c["rows"]:
            mine = marks.get(r["id"])
            if mine:
                r = dict(r, flags=list(r["flags"]) + [m["kind"] for m in mine if m["kind"] not in r["flags"]],
                         books=[_laid(m) for m in mine], books_task=bool(r.get("date")) and r["date"] >= era)
            if notes.get(r["id"]):  # a number it cites is not yet in the copy: said in its details, no mark
                r = dict(r, books_note=notes[r["id"]])
            fill = fills.get(r["id"])
            if fill:  # D4: its chip beside the row's others, its values for the action, and the action itself
                m = fill["mark"]
                r = dict(r, flags=list(r["flags"]) + [m["kind"]], books=list(r.get("books") or []) + [_laid(m)],
                         books_fill=dict(fill["values"]), actions=list(r.get("actions") or []) + [FILL_ACTION])
            rows.append(r)
        b2c["rows"] = rows
        b2c["periods"] = [dict(p, tasks=_tasks_of(rows, p.get("months") or [])) for p in b2c["periods"]]
        documents = [e if laid else {k: v for k, v in e.items() if k != "proposal"} for e in part.get("students") or []]
        hidden = _dismissed(part, "b2c", laid)
        came_back = [b for x in rows for b in x.get("books") or [] if b.get("returned")] + [
            e for e in documents if e.get("returned")]
        b2c["books"] = {"title": BOOKS_LIST_TITLE, "about": STUDENTS_ABOUT, "documents": documents, "hidden": hidden,
                        "hidden_counts": _by_kind(hidden), "returned_counts": _by_kind(came_back)}
        out["b2c"] = b2c
    if view.get("b2b") is not None:
        n = int(part.get("vouchers") or 0)
        documents, hidden = list(part.get("companies") or []), _dismissed(part, "b2b", laid)
        out["b2b"] = dict(view["b2b"], books={
            "title": BOOKS_LIST_TITLE, "about": COMPANIES_ABOUT, "documents": documents,
            "vouchers": n, "vouchers_said": (VOUCHERS_SAID % (VOUCHER_CARD, "מסמך אחד" if n == 1 else "%d מסמכים" % n))
            if n else "", "hidden": hidden, "hidden_counts": _by_kind(hidden),
            "returned_counts": _by_kind([e for e in documents if e.get("returned")])})
    documents, hidden = list(part.get("unclear") or []), _dismissed(part, "unclear", laid)
    out["books_unclear"] = {"title": BOOKS_UNCLEAR_TITLE, "about": UNCLEAR_ABOUT, "documents": documents, "hidden": hidden,
                            "hidden_counts": _by_kind(hidden),
                            "returned_counts": _by_kind([e for e in documents if e.get("returned")])}
    return out


def reading_whole(reading: dict) -> bool:
    """Whether a desk reading may be matched to the books: no error, and both charge boards read whole (a row missing
    from a board read short would turn its documents into documents no row cites)."""
    checks = reading.get("checks") or {}
    return not reading.get("error") and all((checks.get(k) or {}).get("ok") is True for k in ("student_charge", "b2b_charge"))


# ---- R5: what a press may name ---------------------------------------------------------------------------------------------
HIDDEN = object()   # `alerts_of`: an alert the part holds hidden
# what a press of "לא פער" and of "החזרה" may be answered (app/desk/routes.py says each with its status)
DISMISS_ERRORS = ("dismissals_unavailable", "busy", "other_reading", "not_shown", "already_hidden", "evidence_changed")
UNDO_ERRORS = ("dismissals_unavailable", "busy", "not_shown", "not_live", "not_yours")


def alerts_of(part: dict, read_at: Any) -> dict:
    """{(kind, subject): its evidence} of every alert of the books the part shows - its marks, the documents of its three
    lists, and its proposals where it lays them on the reading of `read_at` (`lays_fills`) - and HIDDEN for each it holds
    hidden. What a press of "לא פער" may name, and the evidence kept for it (the server's, never the page's)."""
    out: dict = {}
    laid = lays_fills(part, read_at)
    for items in (part.get("hidden") or {}).values():
        for x in items:
            if laid or x["kind"] not in dz.FILL_KINDS:
                out[(x["kind"], x["subject"])] = HIDDEN
    for item_id, ms in (part.get("marks") or {}).items():
        for m in ms:
            if m.get("evidence"):
                out[(m["kind"], str(item_id))] = m["evidence"]
    for item_id, fill in ((part.get("fills") or {}) if laid else {}).items():
        if fill["mark"].get("evidence"):
            out[(fill["mark"]["kind"], str(item_id))] = fill["mark"]["evidence"]
    for place in dz.DOC_KINDS:
        for e in part.get(place) or []:
            if e.get("evidence"):
                out[(e["kind"], e["subject"])] = e["evidence"]
    return out


def hidden_of(part: dict, kind: str, read_at: Any) -> list:
    """What the part holds hidden of one kind, as its list shows it (a proposal only where proposals are laid)."""
    return [x for x in (part.get("hidden") or {}).get(dz.TAB[kind]) or []
            if x["kind"] == kind and (x["kind"] not in dz.FILL_KINDS or lays_fills(part, read_at))]


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
    last part, and an answer at once does not wait at all. `current()` is the part last answered (the totals and the files use it: they never read). `health()`: its
    state, its copy's time and its reasons, and the Accounts' keys' time - never a count.

    R5, "לא פער" (app/desk/dismissals.py): the live dismissals are read once per computation - with the copy's last run, at
    most every RUN_CHECK_S, so another instance's press shows here within that - and the part shown is the last whole
    computation with what they hold hidden (`hide`). When only the dismissals changed, nothing of the books is computed
    again: the same computation is shown by them (`_reshown`), under a key of its own. A read that fails keeps the ones
    held (with none held, nothing is hidden) and is said in /health. `dismiss` and `undo` are a press's: in the one lock,
    against the part shown now, the evidence the server's own; after the write this instance shows the part by the
    dismissals read again at once."""

    def __init__(self, make_store: Callable[[str], Any], make_reader: Callable[[], MondayReader],
                 clock: Callable[[], float] = time.monotonic, now: Optional[Callable[[], datetime]] = None,
                 make_dismissals: Optional[Callable[[str], Any]] = None):
        self.make_store, self.clock = make_store, clock
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.keys = AccountKeys(make_reader, clock, self.now)
        self._lock = threading.Lock()
        self.last_ok: Optional[dict] = None  # the last part computed from a whole copy on a whole reading, as it is shown
        # R5: that computation with nothing hidden (what `hide` starts from), the store of the dismissals (None: nothing
        # is ever hidden), the live ones last read (None before a read worked), and the last read's time and error
        self._raw_ok: Optional[dict] = None
        self.make_dismissals = make_dismissals
        self._live: Optional[tuple] = None
        self._live_trace: dict = {"read_at": None, "error": None}
        self.part: Optional[dict] = None  # the part last answered
        self._key: Optional[tuple] = None  # what self.part was answered for
        self._store_failed_at: Optional[float] = None
        self._checked_at: Optional[float] = None  # when the last finished run was last asked for
        self._computations = 0  # how many parts this instance computed: each names its own (the part's key)
        self.trace: dict = {"computed_at": None, "seconds": None, "error": None}

    def current(self) -> Optional[dict]:
        return self.part

    def answer(self, cfg: Any, reading: dict, view: dict, may_read: bool = True) -> dict:
        """The part for this reading. `may_read` false (an answer at once, D2's `refreshing`): monday is never read for it -
        with no Account's key held, the part says `pending` and is not kept, and the page's next request computes it - and
        it never waits for another request's computation: with the lock taken it is answered with the part held, or
        `pending` (the review of dfa967b: it waited up to WAIT_S behind a computation that may read the whole Accounts
        board and the copy, on a cold instance the common case)."""
        off = self._off(cfg)
        if off is not None:
            self.part = off
            return off
        got = self._lock.acquire(timeout=WAIT_S) if may_read else self._lock.acquire(blocking=False)
        if not got:
            return self._current(self.part) or self._made("none", ["busy" if may_read else "pending"])
        try:
            self.part = self._current(self._answer(cfg, reading, view, may_read))
            return self.part
        finally:
            self._lock.release()

    def _current(self, part: Optional[dict]) -> Optional[dict]:
        """The part as it may be shown now (the review of dfa967b): one computed from a copy whose last run finished
        longer ago than COPY_STALE_S is `held`, with `copy_stale` - marks must never pose as current on a stopped sync;
        and one that shows what the books say (`ok`, `held`) once today is past Student Charge's window says so
        (`student_window_ended`). The same part made so has one key, so it is laid over the view once."""
        if part is None or part["state"] not in ("ok", "held"):
            return part
        state, reasons = part["state"], list(part.get("reasons") or [])
        if state == "ok":
            try:
                age = (self.now() - datetime.fromisoformat(part["as_of"])).total_seconds()
            except (KeyError, TypeError, ValueError):
                age = 0
            if age > COPY_STALE_S:
                state, reasons = "held", ["copy_stale"]
        if self._window_ended() and "student_window_ended" not in reasons:
            reasons.append("student_window_ended")
        if (state, reasons) == (part["state"], list(part.get("reasons") or [])):
            return part
        return self._made(state, reasons, part)

    def _window_ended(self) -> bool:
        """Whether today, the desk's day (DESK_TZ), is past Student Charge's window (FR-7)."""
        return self.now().astimezone(ZoneInfo(DESK_TZ)).year > STUDENT_WINDOW_YEAR

    def _off(self, cfg: Any) -> Optional[dict]:
        if not getattr(cfg, "database_url", ""):
            return self._made("off", ["not_configured"])
        if cfg.company != "analiza":
            return self._made("off", ["demo" if cfg.company == "demo" else "company"])
        return None

    def _made(self, state: str, reasons: list, base: Optional[dict] = None) -> dict:
        part = dict(base or {}, state=state, reasons=list(reasons))
        if base is None:
            part.update(as_of=None, marks={}, notes={}, fills={}, students=[], companies=[], unclear=[], vouchers=0)
        # the key names the part (the merged view is kept by it, the totals and the files are asked by it): two parts differ
        # in it whenever what they say differs - their state, their copy's time, the computation they come from (when, and
        # which of this instance's: two in one second, a copy whose run moved and kept its time, are two) and their reasons
        # (the review of dfa967b: two `held` parts with other reasons had one key, so the page was shown the reason of the
        # part before). Bounded by construction: the reasons are a closed list (BOOKS_KEY_MAX)
        # R5: and the live dismissals it is shown by (a signature of their ids): one computation shown before and after a
        # press is two parts
        part["key"] = "%s|%s|%s|%s|%s|%s" % (state, part.get("as_of") or "", part.get("computed_at") or "",
                                             part.get("computation") or "", ",".join(reasons), part.get("dismissals") or "")
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
        self._read_live(cfg)
        self._sync()
        key = (reading.get("read_at"), run[0] if run else None)
        if self._key == key and self.part is not None:
            self.part = self._reshown(self.part)   # the same computation, shown by the dismissals read now
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
        # an answer at once takes the keys held, and with none held it is `pending` before the copy is read: the copy is
        # every document of the company with its card's name, and an answer that keeps nothing must not pay for it (the
        # review of dfa967b: every cold open read the whole copy and threw it away)
        held = self.keys.get(False) if not may_read else None
        if not may_read and held is None:
            return self._made("none", ["pending"])
        try:
            snapshot = store.desk_snapshot(cfg.db)
        except BooksStoreError as exc:
            self._store_failed(exc)
            return None
        reason = not_ready(snapshot)
        if reason is not None:
            return self._not_ok([reason])
        keys = held if not may_read else self.keys.get(True)
        if keys is None:
            return self._not_ok(["accounts_unread"])
        try:
            got = compute(reading, view, snapshot, keys)
        except Exception as exc:  # noqa: BLE001 - said by class: no document, name or amount reaches the log
            logger.error("desk books: the part was not computed (%s)", type(exc).__name__)
            self.trace["error"] = type(exc).__name__
            return self._not_ok(["store_error"])
        self._computations += 1
        got.update(as_of=_iso(snapshot.get("last_run_finished_at")), computed_at=_iso(self.now()),
                   reading_at=reading.get("read_at"), computation=self._computations)
        self._raw_ok = got
        part = self.last_ok = self._shown_ok()
        self.trace.update(computed_at=got["computed_at"], seconds=round(self.clock() - started, 2), error=None)
        return part

    # ---- R5: "לא פער" ----------------------------------------------------------------------------------------------------
    def _read_live(self, cfg: Any) -> None:
        """The live dismissals, read again. A read that fails keeps the ones held - with none held, nothing is hidden - and
        is said in /health by its kind or class, never its text."""
        if self.make_dismissals is None:
            return
        try:
            rows = self.make_dismissals(cfg.database_url).live(cfg.db)
        except Exception as exc:  # noqa: BLE001 - said by its kind or class: a dismissal never takes the books part down
            self._live_trace["error"] = getattr(exc, "kind", None) or type(exc).__name__
            logger.warning("desk books: the dismissals were not read (%s)", self._live_trace["error"])
            return
        self._live = tuple(rows)
        self._live_trace.update(read_at=_iso(self.now()), error=None)

    def _shown_ok(self) -> Optional[dict]:
        """The last whole computation as it is shown: an `ok` part, with what the live dismissals held now hold hidden."""
        if self._raw_ok is None:
            return None
        shown = dz.hide(self._raw_ok, self._live or ())
        shown["dismissals"] = dz.live_signature(self._live)
        return self._made("ok", [], shown)

    def _sync(self) -> None:
        """`last_ok` shown by the live dismissals held now (a `held` part is made of it)."""
        if self._raw_ok is not None and (self.last_ok or {}).get("dismissals") != dz.live_signature(self._live):
            self.last_ok = self._shown_ok()

    def _reshown(self, part: Optional[dict]) -> Optional[dict]:
        """`part` as the live dismissals held now show it: itself when they showed it already; else the same computation
        shown by them, in the part's own state and with its own reasons (an `ok` part and a `held` one are both the last
        whole computation; `none` and `off` hold nothing to hide)."""
        self._sync()
        if part is None or part["state"] not in ("ok", "held") or self.last_ok is None:
            return part
        if part.get("dismissals") == self.last_ok.get("dismissals"):
            return part
        if (part["state"], list(part.get("reasons") or [])) == ("ok", []):
            return self.last_ok
        return self._made(part["state"], part.get("reasons") or [], self.last_ok)

    def dismiss(self, cfg: Any, kind: str, subject: str, seen: str, key: str, read_at: Any, user_id: int) -> dict:
        """A press of "לא פער" (or "לא להציע"): {"ok": True, "id", "replaced"} or {"error": a word of DISMISS_ERRORS}.
        Against the part shown now and only it: the page's part (`key`) must be it (`other_reading`), the alert must be one
        it shows (`not_shown`; `already_hidden` when it holds it hidden), and what the page saw (`seen`) must be the
        evidence the server computed for it (`evidence_changed`: a hash the page made up, or a part that moved). The
        evidence kept is the server's own. Then the write (app/desk/dismissals_store.py), and this instance shows the part
        by the dismissals read again at once - with the one just kept, whatever that read finds."""
        if self.make_dismissals is None or not getattr(cfg, "database_url", ""):
            return {"error": "dismissals_unavailable"}
        if not self._lock.acquire(timeout=WAIT_S):
            return {"error": "busy"}
        try:
            part = self._current(self.part)
            if part is None or part.get("key") != key:
                return {"error": "other_reading"}
            evidence = alerts_of(part, read_at).get((kind, subject))
            if evidence is None:
                return {"error": "not_shown"}
            if evidence is HIDDEN:
                return {"error": "already_hidden"}
            if seen != evidence:
                return {"error": "evidence_changed"}
            try:
                got = self.make_dismissals(cfg.database_url).dismiss(cfg.db, kind, subject, evidence, user_id)
            except DismissalRefused as refused:
                self._read_live(cfg)   # another press kept one a moment ago: shown as it is now
                self.part = self._reshown(part)
                return {"error": refused.code}
            except Exception as exc:  # noqa: BLE001 - said by its kind or class, never its text
                return self._not_kept(exc)
            self._live = tuple(d for d in self._live or () if d["id"] != got["replaced"]) + (
                {"id": got["id"], "kind": kind, "subject": subject, "evidence": evidence, "by": int(user_id),
                 "at": got["at"]},)
            self._read_live(cfg)
            self.part = self._reshown(part)
            return {"ok": True, "id": got["id"], "replaced": got["replaced"]}
        finally:
            self._lock.release()

    def undo(self, cfg: Any, kind: str, subject: str, dismissal_id: int, read_at: Any, user_id: int) -> dict:
        """A press of "החזרה" in the hidden list: {"ok": True} or {"error": a word of UNDO_ERRORS}. Only a dismissal the
        part shown now holds hidden (`not_shown`), and only by its own dismisser (`not_yours`, the store's rule: ADR-0190
        §6's norm, and there is no admin); `not_live` when it ended since. This instance then shows the part by the
        dismissals read again at once."""
        if self.make_dismissals is None or not getattr(cfg, "database_url", ""):
            return {"error": "dismissals_unavailable"}
        if not self._lock.acquire(timeout=WAIT_S):
            return {"error": "busy"}
        try:
            part = self._current(self.part)
            if part is None or not any(x["subject"] == subject and x["id"] == dismissal_id
                                       for x in hidden_of(part, kind, read_at)):
                return {"error": "not_shown"}
            try:
                self.make_dismissals(cfg.database_url).undo(cfg.db, kind, subject, dismissal_id, user_id)
            except DismissalRefused as refused:
                self._read_live(cfg)
                self.part = self._reshown(part)
                return {"error": refused.code}
            except Exception as exc:  # noqa: BLE001 - said by its kind or class, never its text
                return self._not_kept(exc)
            self._live = tuple(d for d in self._live or () if d["id"] != dismissal_id)
            self._read_live(cfg)
            self.part = self._reshown(part)
            return {"ok": True}
        finally:
            self._lock.release()

    def _not_kept(self, exc: Exception) -> dict:
        code = getattr(exc, "kind", None) or type(exc).__name__
        logger.warning("desk books: a dismissal was not written (%s)", code)
        return {"error": "dismissals_unavailable"}

    def health(self) -> dict:
        """For the public /health: the state of the part last answered, as it may be shown now (a copy grown stale since
        is `copy_stale`), its copy's time and its reasons (closed codes), when it was last computed and how long it took,
        and when the Accounts' keys were read. Never a count."""
        part = self._current(self.part)
        return {"state": part["state"] if part else None, "as_of": part.get("as_of") if part else None,
                "reasons": list(part.get("reasons") or []) if part else [],
                "computed_at": self.trace["computed_at"], "seconds": self.trace["seconds"], "error": self.trace["error"],
                "accounts": self.keys.health(), "dismissals": dict(self._live_trace)}
