"""Cleaning text from the books before it is stored (spec §3.4, backlog 0301 B1; rewritten after the review
of 22.11).

An ID number is never stored server side (ADR-0173 §1, ADR-0171 §1), and text fields in a document - the
card's name, remarks, a line's details, even a short code field - can carry one, or a phone, a bank
account, a card number, an email. So every text field passes through `clean`, which keeps what has a
known shape and masks every other number of six digits or more:

1. Normalise: NFKC, then remove every invisible format character (Unicode category Cf: direction marks,
   soft hyphens, zero-width characters, a BOM), control characters and lone surrogates; every kind of
   whitespace and line break becomes one space.
2. Emails become `@`.
3. A number region is a run of digits joined by any separators - spaces, dots, slashes, commas, hyphens,
   dashes, the Hebrew maqaf, underscores. It is split at spaces into tokens, and each token is kept only
   for its shape:
   - a date (16.08.2026, 16/8/26, 2026-08-16);
   - an amount (1,170.00, 550.85, 123,456) below a million;
   - a document number: five digits that do not start with 0 (series run 10001-89999), or a range of two
     within one series (50887-50891, also written 50887 - 50891).
   Every other token is plain digits. Consecutive plain tokens are one number, and become `#` when they
   hold six digits or more - so 012 345 678 is one ID, not three short numbers. A document number next to
   plain digits that would make a phone or an ID with it (a 0-led neighbour, or 9-10 digits together:
   05012 34567, 51234 5678) loses its shape and is masked with them.
4. The references are the document numbers and ranges that kept their shape (spec §3.4 step 1): no piece
   of a masked number ever reaches `refs_in_text`.

Nothing here logs or returns the text it was given except as cleaned.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Optional

_SEPARATORS = " .,/\\_-‐‑‒–—−־"
_REGION = re.compile(r"\d(?:[%s]*\d)*" % re.escape(_SEPARATORS))
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", re.UNICODE)
_DASHES = "-‐‑‒–—−־"
_DATE = re.compile(r"^(\d{1,2})[./\-](\d{1,2})[./\-](\d{2}|\d{4})$|^(\d{4})-(\d{1,2})-(\d{1,2})$")
_AMOUNT = re.compile(r"^(?:\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d{1,6}\.\d{1,2})$")
_REF = re.compile(r"^[1-9]\d{4}$")
_RANGE = re.compile(r"^([1-9]\d{4})[%s]([1-9]\d{4})$" % re.escape(_DASHES))
MASK_DIGITS = 6
# A range of document numbers stays inside one series: a span wider than this is not a range (a phone
# written as two halves, 12345-67890, is not read as one).
MAX_RANGE = 1000
_TRIM = ",.;:!?)(\"'"


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    out = []
    for ch in text:
        cat = unicodedata.category(ch)
        if cat == "Cf" or cat == "Cs":  # format characters, and halves of a cut emoji
            continue
        if cat in ("Cc", "Zs", "Zl", "Zp") or ch.isspace():
            out.append(" ")
        else:
            out.append(ch)
    return re.sub(r" {2,}", " ", "".join(out))


def _digits(text: str) -> int:
    return sum(ch.isdigit() for ch in text)


def _shape(token: str) -> str:
    core = token.strip(_TRIM)
    if not core:
        return "sep"
    if not any(ch.isdigit() for ch in core):
        return "sep" if all(ch in _SEPARATORS + _TRIM for ch in core) else "other"
    m = _DATE.match(core)
    if m:
        d, mo = (int(m.group(1)), int(m.group(2))) if m.group(1) else (int(m.group(6)), int(m.group(5)))
        if 1 <= d <= 31 and 1 <= mo <= 12:
            return "date"
    if _AMOUNT.match(core) and float(core.replace(",", "")) < 1_000_000:
        return "amount"
    if _REF.match(core):
        return "ref"
    m = _RANGE.match(core)
    if m and 0 < int(m.group(2)) - int(m.group(1)) <= MAX_RANGE:
        return "range"
    return "plain"


def _tokens(region: str) -> list:
    """[(start, end, text, shape)] with ref - dash - ref joined into one range token."""
    raw = [(m.start(), m.end(), m.group(0)) for m in re.finditer(r"\S+", region)]
    toks = [[s, e, t, _shape(t)] for s, e, t in raw]
    out: list = []
    i = 0
    while i < len(toks):
        if (i + 2 < len(toks) and toks[i][3] == "ref" and toks[i + 1][2] in tuple(_DASHES) and toks[i + 2][3] == "ref"
                and 0 < int(toks[i + 2][2].strip(_TRIM)) - int(toks[i][2].strip(_TRIM)) <= MAX_RANGE):
            out.append([toks[i][0], toks[i + 2][1], region[toks[i][0]:toks[i + 2][1]], "range"])
            i += 3
            continue
        out.append(toks[i])
        i += 1
    return out


def _decide(toks: list) -> None:
    """Demote a document number that makes a phone or an ID with the plain digits beside it."""
    for i, tok in enumerate(toks):
        if tok[3] != "ref":
            continue
        for j in (i - 1, i + 1):
            if 0 <= j < len(toks) and toks[j][3] == "plain":
                neighbour = toks[j][2].strip(_TRIM)
                together = _digits(neighbour) + 5
                if neighbour.startswith("0") or together in (9, 10):
                    tok[3] = "plain"
                    break


def _process(text: str) -> tuple:
    """(cleaned text, refs, masks) for a normalised text."""
    refs: list = []
    masks = 0
    pieces = []
    last = 0
    for m in _REGION.finditer(text):
        region = m.group(0)
        toks = _tokens(region)
        _decide(toks)
        # runs of plain tokens (separator-only tokens between them do not break a run)
        out_region = []
        pos = 0
        i = 0
        while i < len(toks):
            tok = toks[i]
            if tok[3] in ("date", "amount", "ref", "range", "other"):
                if tok[3] == "ref":
                    refs.append(tok[2].strip(_TRIM))
                elif tok[3] == "range":
                    a, b = re.findall(r"\d{5}", tok[2])
                    refs.append(f"{a}-{b}")
                i += 1
                continue
            if tok[3] == "sep":
                i += 1
                continue
            j = i
            digits = 0
            end_tok = i
            while j < len(toks) and toks[j][3] in ("plain", "sep"):
                if toks[j][3] == "plain":
                    digits += _digits(toks[j][2])
                    end_tok = j
                j += 1
            if digits >= MASK_DIGITS:
                out_region.append(region[pos:toks[i][0]])
                out_region.append("#")
                pos = toks[end_tok][1]
                masks += 1
            i = end_tok + 1
        out_region.append(region[pos:])
        pieces.append(text[last:m.start()])
        pieces.append("".join(out_region))
        last = m.end()
    pieces.append(text[last:])
    return "".join(pieces), refs, masks


def extract_refs(text: Optional[str]) -> list:
    """Document numbers and ranges cited in a text field that kept their shape: '50347', '50887-50891'."""
    if not text:
        return []
    return _process(_EMAIL.sub("@", normalize(str(text))))[1]


def mask(text: str) -> tuple:
    """(masked text, how many numbers and emails were masked). The text is normalised first."""
    text = normalize(text)
    emails = len(_EMAIL.findall(text))
    cleaned, _refs, masks = _process(_EMAIL.sub("@", text))
    return cleaned, emails + masks


def clean(text: Optional[str]) -> tuple:
    """(cleaned text or None, refs that kept their shape, number of masks)."""
    if text is None:
        return None, [], 0
    norm = normalize(str(text))
    emails = len(_EMAIL.findall(norm))
    cleaned, refs, masks = _process(_EMAIL.sub("@", norm))
    cleaned = cleaned.strip()
    return (cleaned or None), refs, masks + emails


def email_domain(email: Optional[str]) -> Optional[str]:
    """The domain only: the part a reconciliation can use (a company's domain), never the person."""
    if not email or "@" not in str(email):
        return None
    domain = normalize(str(email)).strip().rsplit("@", 1)[1].strip().lower()
    return domain if re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)+", domain) else None
