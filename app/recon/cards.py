"""The cards of the books and the names on them, shared by the desk (D3, app/desk/books.py) and the reconciliation's FR-C2
(app/recon/ref_fill.py): moved here from app/desk/books.py on 26.11, so the documents the desk lists and the fills a plan
proposes are judged by one code, never by two copies (a test holds the two in agreement, tests/test_recon_ref_fill.py).

1. **The side of a card** (FR-6, the desk move plan §1a.2.3, until B3's sort codes; `card_sides`, `side_of`): an Account's
   "מפתח חשבשבת" (`account_keys_of`) is a company's card; otherwise a card that holds an invoice-receipt and no proforma
   (type 78: students are sent none) is a student's; anything else is `unclear`. **A key that is empty or masked names no
   card** (`card_of`): the copy cleans a key like any text, so a key of six digits or more - a student's card keyed by an ID
   number, a company's by its ח.פ - is stored KEY_MASK, and pooled by key every such card would be one. Such a document's
   side is judged by its card's name (`side_card`: NFKC, case folded, spaces collapsed - the same on every document of a
   card, spec §3.4), so a company's invoice-receipts keyed by its ח.פ meet the proforma beside them (FR-C4, the incident of
   18.11). The name is evidence of the side only: never matched to an Account. A document with neither a key nor a name is
   judged alone, by its own type - an invoice-receipt a student's, as FR-6's interim rule; anything else unclear.
2. **A name as a set of words** (`name_words`, FR-C2's rule): what the desk's candidates and FR-C2's fill compare. A name
   is only ever a candidate (the law of the rules' §5): it proposes, a person approves, and a number written makes the match.
3. **Where a document lands** (`place_of`, spec §6.3.1), the first of PLACES that fits: outside (another status or type, a
   cancellation, before the era, or a student's outside Student Charge's window), cited by a charge row (`cited_numbers`),
   answered "not found" (missing), counted apart (the vouchers' card), or the students', the companies' or the unclear.
   The era begins on ERA_FROM for the desk's lists (Chava's start); FR-C2 asks it from the first day of Student Charge's
   window, so its students' documents are the desk's of the whole of 2026.
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from ..books.doctypes import VALID_STATUS, role
from . import receipt_dates as a1
from .reading import AC

# Chava's start (ADR-0168 §5): she records from it and does not backfill. A gap of a row or a document before it is the
# cleanup list Nadav runs once (backlog 0301 C1), never her "לטיפול" (spec §6.4.2).
ERA_FROM = date(2026, 10, 25)
# FR-7: Student Charge cites documents of this year only (A1's window, the same constant)
STUDENT_WINDOW_YEAR = a1.WINDOW_YEAR
# FR-D1: Defence's vouchers, counted apart until the vouchers board is a matching target
VOUCHER_CARD = "10091"
INVOICE, INVOICE_RECEIPT, PROFORMA = "invoice", "invoice_receipt", "proforma"
LISTED_ROLES = (INVOICE, INVOICE_RECEIPT)  # types 1 and 2: what a charge row cites (FR-C1, FR-D1)
# Where a document of the books lands, the first that fits (spec §6.3.1: each in exactly one)
PLACES = ("outside", "cited", "missing", "vouchers", "students", "companies", "unclear")
# Why a card's side is not clear (`card_sides`, `side_of`): closed, each with its sentence in the desk (UNCLEAR_WHY there)
UNCLEAR_CODES = ("no_invoice_receipt", "invoice_receipt_and_proforma", "name_no_invoice_receipt",
                 "name_invoice_receipt_and_proforma", "key_masked", "key_empty")
# the books' cleaning masks a number of six digits or more that has no known shape (app/books/clean.py): a key holding it
# names no card
KEY_MASK = "#"
# a card a document's side is judged by when its key names none: its name, after this prefix - which holds KEY_MASK, so it
# is never a card's key (`card_of`) and never an Account's (`account_keys_of`)
NAME_CARD = "#name:"


# ---- small readers ---------------------------------------------------------------------------------------------------
def money_of(v: Any) -> Optional[Decimal]:
    if v is None or isinstance(v, bool):
        return None
    try:
        return Decimal(str(v))
    except Exception:  # noqa: BLE001 - not an amount
        return None


def number_of(doc: dict) -> Optional[str]:
    """A document's number as A1 looks it up (`index_documents`): ASCII digits, no leading zero; None for one that has
    none."""
    text = str(doc.get("doc_number") or "")
    return str(int(text)) if text.isascii() and text.isdigit() else None


def day_of(v: Any) -> Optional[date]:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return a1.parse_day(v)


# ---- names and keys --------------------------------------------------------------------------------------------------
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


def norm_name(text: Any) -> str:
    """A card's name as cards are told apart by it: NFKC, case folded, every run of spaces one space, trimmed."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(text or "")).casefold()).strip()


def side_card(doc: dict) -> Optional[str]:
    """The card a document's side is judged by: its key (`card_of`); for a key that names none (masked or empty), its
    card's name (NAME_CARD and `norm_name`), so the documents of one card keyed by an ID number or a ח.פ are judged together
    (FR-C4: a proforma beside them) - evidence of the side only, never matched to an Account or a row; None with neither
    (the document is judged alone)."""
    card = card_of(doc)
    if card is not None:
        return card
    name = norm_name(doc.get("account_name"))
    return NAME_CARD + name if name else None


def cited_numbers(rows: list, column: str) -> set:
    """Every document number a charge board's rows cite in `column`: each number of a readable cell (A1's `parse_refs`),
    and every number an unreadable one may name (A1's `loose_numbers`) - a document a row may cite is never listed as
    one no row cites."""
    out: set = set()
    for r in rows:
        refs = a1.parse_refs(r.get(column))
        out.update(refs if refs is not None else a1.loose_numbers(r.get(column)))
    return out


# ---- the side of a card ------------------------------------------------------------------------------------------------
def card_sides(documents: list, keys: dict) -> dict:
    """{card: (side, why)} for every card that holds a valid document, by `side_card` (a key, or a name for a key that
    names none): ("company", "account_key"), ("student", "invoice_receipt"), or ("unclear", one of UNCLEAR_CODES). A card
    judged by its name is never a company's (no Account names it). A draft is no evidence; a document that answered "not
    found" stays in every figure until a person confirms it gone (spec §4.2.5), so it is. A document with neither a key
    nor a name is on no card here: it is judged alone (`side_of`)."""
    roles: dict = defaultdict(set)
    for d in documents:
        card = side_card(d)
        if card is not None and d.get("status") == VALID_STATUS:
            roles[card].add(role(d.get("doc_type")))
    out = {}
    for card, held in roles.items():
        named = card.startswith(NAME_CARD)
        if not named and card in keys:
            out[card] = ("company", "account_key")
        elif INVOICE_RECEIPT in held and PROFORMA not in held:
            out[card] = ("student", "invoice_receipt")
        elif INVOICE_RECEIPT in held:
            out[card] = ("unclear", "name_invoice_receipt_and_proforma" if named else "invoice_receipt_and_proforma")
        else:
            out[card] = ("unclear", "name_no_invoice_receipt" if named else "no_invoice_receipt")
    return out


def side_of(doc: dict, sides: dict) -> tuple:
    """(side, why) of one document: its card's (`side_card`, in `sides` from `card_sides`; a card not there is unclear);
    with neither a key nor a name, the document alone, by its own type (FR-6's interim rule), never pooled with another's:
    an invoice-receipt a student's, anything else unclear (`no_card_why`)."""
    card = side_card(doc)
    if card is None:
        if role(doc.get("doc_type")) == INVOICE_RECEIPT:
            return "student", "invoice_receipt"
        return "unclear", no_card_why(doc)
    return sides.get(card, ("unclear", "no_invoice_receipt"))


# ---- where a document lands ------------------------------------------------------------------------------------------
def place_of(doc: dict, cited: set, sides: dict, era_from: date = ERA_FROM) -> tuple:
    """(place, why) of one held document, the first of PLACES that fits. `era_from`: the era's first day - the desk's
    lists ERA_FROM; FR-C2 the first day of Student Charge's window."""
    if doc.get("status") != VALID_STATUS:
        return "outside", "status"
    r = role(doc.get("doc_type"))
    if r not in LISTED_ROLES:
        return "outside", "type"
    total = money_of(doc.get("total_incl"))
    if total is None or total < 0:
        return "outside", "cancellation" if total is not None else "no_total"
    day = day_of(doc.get("value_date"))
    if day is None or day < era_from:
        return "outside", "era"
    number = number_of(doc)
    if number is not None and number in cited:
        return "cited", ""
    if doc.get("missing"):
        return "missing", ""
    if card_of(doc) == VOUCHER_CARD:
        return "vouchers", ""
    side, why = side_of(doc, sides)
    if side == "student":
        return ("students", "") if day.year == STUDENT_WINDOW_YEAR else ("outside", "student_window")
    if side == "company":
        return "companies", ""
    return "unclear", why
