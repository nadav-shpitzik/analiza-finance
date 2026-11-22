"""Cleaning text from the books before it is stored (spec §3.4, backlog 0301 B1).

An ID number is never stored server side (ADR-0173 §1, ADR-0171 §1), and text fields in a document -
the card's name, remarks, a line's details - can carry one, or a phone, a bank account, a card number,
an email. So every text field passes through here, in this order:

1. Extract what the rules read: document numbers and ranges (`עח: 50347`, `עח: 50887-50891`) go to
   `refs_in_text`. Only a digit group that is exactly one 5-digit number, or two joined by a hyphen, is a
   reference: a 5-digit piece of a longer group (a bank account 12-345-12345) is not, so no fragment of a
   number that gets masked ever leaks through the references.
2. Normalise: NFKC (a non-breaking space becomes a space) and strip invisible format characters
   (right-to-left and left-to-right marks, embeddings, zero-width characters, a BOM).
3. Mask: a group of digits joined by spaces or hyphens that totals six digits or more becomes `#`, and an
   email address becomes `@`. A lone document number (five digits) survives; a date (16.08.2026), an
   amount (1,170.00) or a percentage survives, because dots, commas and slashes do not join a group.

Nothing here logs or returns the text it was given except as cleaned.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Optional

# Invisible format characters: zero-width (200B-200D), LRM/RLM (200E-200F), embeddings and overrides
# (202A-202E), word joiner and invisible operators (2060-2064), isolates (2066-2069), BOM (FEFF).
_FORMAT = re.compile("[​-‏‪-‮⁠-⁤⁦-⁩﻿]")
# Separators that join digits into one group. After NFKC, a non-breaking space is a space; the dashes a
# keyboard or a word processor produce are listed one by one.
_JOINERS = " \t-‐‑‒–—−"
_GROUP = re.compile(r"\d(?:[%s]*\d)*" % re.escape(_JOINERS))
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", re.UNICODE)
MASK_DIGITS = 6
REF_DIGITS = 5


def normalize(text: str) -> str:
    return _FORMAT.sub("", unicodedata.normalize("NFKC", text))


def _digits(group: str) -> str:
    return "".join(ch for ch in group if ch.isdigit())


_DASHES = "-\u2010\u2011\u2012\u2013\u2014\u2212"
# A range of document numbers stays inside one series: a span wider than this is not a range (a phone
# written as two halves, 12345-67890, is not read as one).
MAX_RANGE = 1000


def extract_refs(text: Optional[str]) -> list:
    """Document numbers and ranges cited in a text field, as strings: '50347', '50887-50891'. Only from a
    digit group whose every part is exactly five digits: a piece of a longer number is never taken."""
    if not text:
        return []
    refs = []
    for m in _GROUP.finditer(normalize(text)):
        tokens = re.findall(r"\d+|[^\d]+", m.group(0))
        parts = [t for t in tokens if t.isdigit()]
        if not parts or any(len(t) != REF_DIGITS for t in parts):
            continue
        items: list = []
        dash_before = False
        for t in tokens:
            if not t.isdigit():
                dash_before = any(ch in _DASHES for ch in t)
                continue
            if dash_before and items and "-" not in items[-1] and 0 <= int(t) - int(items[-1]) <= MAX_RANGE:
                items[-1] = f"{items[-1]}-{t}"
            elif dash_before:
                items = []  # a dash that makes no range: not document numbers
                break
            else:
                items.append(t)
            dash_before = False
        refs += items
    return refs


def mask(text: str) -> tuple:
    """(masked text, how many digit groups and emails were masked)."""
    count = 0

    def email(m):
        nonlocal count
        count += 1
        return "@"

    def group(m):
        nonlocal count
        if len(_digits(m.group(0))) >= MASK_DIGITS:
            count += 1
            return "#"
        return m.group(0)

    text = _EMAIL.sub(email, text)
    text = _GROUP.sub(group, text)
    return text, count


def clean(text: Optional[str]) -> tuple:
    """(cleaned text or None, refs found before masking, number of masks)."""
    if text is None:
        return None, [], 0
    text = str(text)
    refs = extract_refs(text)
    cleaned, masked = mask(normalize(text))
    cleaned = cleaned.strip()
    return (cleaned or None), refs, masked


def email_domain(email: Optional[str]) -> Optional[str]:
    """The domain only: the part a reconciliation can use (a company's domain), never the person."""
    if not email or "@" not in str(email):
        return None
    domain = normalize(str(email)).strip().rsplit("@", 1)[1].strip().lower()
    return domain if re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)+", domain) else None
