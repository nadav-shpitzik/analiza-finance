"""Cleaning text from the books before it is stored (spec §3.4, backlog 0301 B1; rewritten after the two
reviews of 22.11).

An ID number is never stored server side (ADR-0173 §1, ADR-0171 §1), and text fields in a document - the
card's name, remarks, a line's details, even a short code field - can carry one, or a phone, a bank
account, a card number, an email. So every text field passes through `clean`:

1. Normalise: NFKC; a digit of any script becomes an ASCII digit; invisible characters go (format
   characters, the default-ignorables such as variation selectors and blank fillers, lone surrogates);
   control characters and every kind of whitespace become one space.
2. Emails become `@`: the run of address characters on each side of an at sign, one space allowed on
   either side, and "(at)" or "[at]" count as an at sign. A linear scan, never a backtracking regex
   (the review measured 14 s for one field with the old one).
3. A number is a run of digits joined by anything that is not a letter, at most three characters at a
   time: spaces, dots, dashes, slashes, colons, pipes, marks, symbols. It is split at spaces into pieces,
   and each piece gets a shape:
   - a document number (five digits, not 0-led), a range (50887-50891, also 50887-891 and 50887 - 50891),
     a list (50887,50888 or 50887/50888);
   - a date with a four-digit year (16.08.2026, 2026-08-16), a month (08/2026, 2026-08), a date range
     (16.08.2026-20.08.2026), a span of years (2025/2026, 2025-26), a date with a two-digit year (16/8/26);
   - an amount below a million (1,170.00, 550.85, 123,456), not 0-led;
   - small: at most two digits (2, 12, 1/3);
   - plain: anything else.
   A number of fewer than six digits is kept whole. A number of six digits or more is kept only when every
   piece has a known shape, or every piece but one small one in a number of at most seven digits
   (50887 2), and otherwise it is masked whole as `#`. When a number has more than one piece, a date needs
   its four-digit year and an amount two decimal places and three digits before them: a piece that could
   be the end of an ID or a phone (12345 6.7.89, 12345 67.89) masks the whole number.
4. The references are the document numbers, ranges and lists of the numbers that were kept (spec §3.4
   step 1): no piece of a masked number ever reaches `refs_in_text`.

A cost, chosen for the rule that an ID is never stored (22.11): a document number beside a bare
four-digit number (50347 2026, 50347 1170) is masked, because an ID is also written 51234 5678. `stats`
counts what masking cost, by reason, so books-check can show it on the real books.

Nothing here logs or returns the text it was given except as cleaned.
"""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal
from typing import Optional

MASK_DIGITS = 6
SMALL_DIGITS = 2
SMALL_WITH_MAX = 7  # a known number may carry one small piece only up to this many digits in all
MAX_RANGE = 1000  # a range of document numbers stays inside one series
MAX_JOIN = 3  # characters between two digits of one number
MAX_PIECE = 40  # a piece longer than this has no shape
MAX_RAW = 100_000  # characters of a field read at all
MAX_KEPT = 4_000  # characters of a cleaned field stored

# Default_Ignorable_Code_Point (Unicode's DerivedCoreProperties): invisible, and three of them (the Hangul
# fillers) are letters that would otherwise split a number. The tag characters (E0000-E0FFF) are checked apart.
_IGNORABLE = frozenset(
    cp
    for lo, hi in (
        (0x00AD, 0x00AD), (0x034F, 0x034F), (0x061C, 0x061C), (0x115F, 0x1160), (0x17B4, 0x17B5),
        (0x180B, 0x180F), (0x200B, 0x200F), (0x202A, 0x202E), (0x2060, 0x206F), (0x3164, 0x3164),
        (0xFE00, 0xFE0F), (0xFEFF, 0xFEFF), (0xFFA0, 0xFFA0), (0xFFF0, 0xFFF8), (0x1BCA0, 0x1BCA3),
        (0x1D173, 0x1D17A),
    )
    for cp in range(lo, hi + 1)
)
_DASHES = "-‐‑‒–—−־"
_REF = r"[1-9]\d{4}"
_RE_REF = re.compile(rf"^{_REF}$")
_RE_RANGE = re.compile(rf"^({_REF})[{_DASHES}](\d{{5}}|\d{{1,3}})$")
_RE_LIST = re.compile(rf"^{_REF}(?:[,/;]{_REF})+$")
_RE_DATE = re.compile(r"^(\d{1,2})([./-])(\d{1,2})\2((?:19|20)\d{2})$")
_RE_ISO = re.compile(r"^((?:19|20)\d{2})-(\d{1,2})-(\d{1,2})$")
_RE_DATE2 = re.compile(r"^(\d{1,2})([./-])(\d{1,2})\2(\d{2})$")
_RE_MONTH = re.compile(r"^(\d{1,2})[./-]((?:19|20)\d{2})$|^((?:19|20)\d{2})[./-](\d{1,2})$")
_RE_YEARS = re.compile(r"^((?:19|20)\d{2})[/-]((?:19|20)\d{2}|\d{2})$")  # 2025/2026, 2025-26
_RE_AMOUNT = re.compile(r"^(?:0|[1-9]\d{0,2})(?:,\d{3})+(?:\.\d{1,2})?$|^(?:0|[1-9]\d{0,5})\.\d{1,2}$")
_RE_AMOUNT_STRONG = re.compile(r"^[1-9][\d,]*\d{2}\.\d{2}$")  # 100.00 and up, two decimal places
_AT_WORDS = re.compile(r"\((?:at)\)|\[(?:at)\]", re.IGNORECASE)
_ADDRESS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._%+-'")
_DOMAIN = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-")
# The shapes a piece of a longer number may have. A piece outside them masks the number.
STRONG = frozenset({"ref", "range", "list", "date", "month", "date_range", "years", "amount"})
REF_SHAPES = frozenset({"ref", "range", "list"})


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    out = []
    for ch in text:
        if ch.isascii():
            out.append(ch if ch.isprintable() else " ")
            continue
        o = ord(ch)
        cat = unicodedata.category(ch)
        if cat in ("Cf", "Cs") or o in _IGNORABLE or 0xE0000 <= o <= 0xE0FFF:
            continue
        digit = unicodedata.digit(ch, None)
        if digit is not None:
            out.append(chr(48 + digit))
        elif cat in ("Cc", "Zs", "Zl", "Zp") or ch.isspace():
            out.append(" ")
        else:
            out.append(ch)
    return re.sub(r" {2,}", " ", "".join(out))


def _mask_emails(text: str) -> tuple:
    """(text with every address as '@', how many). Linear: each at sign looks back only to the last cut."""
    text = _AT_WORDS.sub("@", text)
    if "@" not in text:
        return text, 0
    out, last, count, n = [], 0, 0, len(text)
    pos = text.find("@")
    while pos != -1:
        left = pos - 1 if pos - 1 >= last and text[pos - 1] == " " else pos
        k = left
        while k - 1 >= last and text[k - 1] in _ADDRESS:
            k -= 1
        right = pos + 2 if pos + 1 < n and text[pos + 1] == " " else pos + 1
        m = right
        while m < n and text[m] in _DOMAIN:
            m += 1
        if k == left and m == right:  # an at sign alone, with no address on either side
            pos = text.find("@", pos + 1)
            continue
        start = k if k < left else pos
        end = m if m > right else pos + 1
        out.append(text[last:start])
        out.append("@")
        count += 1
        last = end
        pos = text.find("@", end)
    out.append(text[last:])
    return "".join(out), count


def _is_digit(ch: str) -> bool:
    return "0" <= ch <= "9"


def _numbers(text: str):
    """(start, end) of every number in a normalised text: digits joined by at most MAX_JOIN characters that
    are neither a digit nor a letter. Linear: each join looks at most MAX_JOIN characters ahead."""
    n, i = len(text), 0
    while i < n:
        if not _is_digit(text[i]):
            i += 1
            continue
        start = end = i
        j = i
        while j < n:
            if _is_digit(text[j]):
                j += 1
                end = j
                continue
            k = j
            while k < n and k - j < MAX_JOIN and not _is_digit(text[k]) and not text[k].isalpha():
                k += 1
            if k > j and k < n and _is_digit(text[k]):
                j = k
                continue
            break
        yield start, end
        i = end


def _valid_day(day: int, month: int) -> bool:
    return 1 <= day <= 31 and 1 <= month <= 12


def _is_date(core: str) -> bool:
    m = _RE_DATE.match(core)
    if m:
        return _valid_day(int(m.group(1)), int(m.group(3)))
    m = _RE_ISO.match(core)
    return bool(m) and _valid_day(int(m.group(3)), int(m.group(2)))


def _range(core: str) -> Optional[str]:
    """'50887-50891' for 50887-50891 or 50887-891, or None."""
    m = _RE_RANGE.match(core)
    if not m:
        return None
    first, tail = m.group(1), m.group(2)
    last = first[: 5 - len(tail)] + tail
    return f"{first}-{last}" if 0 < int(last) - int(first) <= MAX_RANGE else None


def shape(core: str) -> str:
    """The shape of one piece of a number (its leading and trailing non-digits already stripped)."""
    digits = sum(1 for ch in core if _is_digit(ch))
    if digits == 0:
        return "sep"
    if len(core) > MAX_PIECE:
        return "plain"
    if _RE_REF.match(core):
        return "ref"
    if _range(core):
        return "range"
    if _RE_LIST.match(core):
        return "list"
    if _is_date(core):
        return "date"
    m = _RE_MONTH.match(core)
    if m and 1 <= int(m.group(1) or m.group(4)) <= 12:
        return "month"
    for i, ch in enumerate(core):
        if ch in _DASHES and _is_date(core[:i]) and _is_date(core[i + 1:]):
            return "date_range"
    m = _RE_YEARS.match(core)
    if m and 0 < (int(m.group(2)) - int(m.group(1))) % 100 <= 5:
        return "years"
    m = _RE_DATE2.match(core)
    if m and _valid_day(int(m.group(1)), int(m.group(3))):
        return "date2"
    if digits <= SMALL_DIGITS:
        return "small"
    if _RE_AMOUNT.match(core) and Decimal(core.replace(",", "")) < 1_000_000:
        return "amount" if _RE_AMOUNT_STRONG.match(core) else "amount_weak"
    return "plain"


def _core(piece: str) -> str:
    start, end = 0, len(piece)
    while start < end and not _is_digit(piece[start]):
        start += 1
    while end > start and not _is_digit(piece[end - 1]):
        end -= 1
    return piece[start:end]


def _decide(pieces: list) -> Optional[str]:
    """None to keep a number, or why it is masked. `pieces`: [(shape, digits)] of its pieces with digits."""
    total = sum(d for _, d in pieces)
    if total < MASK_DIGITS:
        return None
    if len(pieces) == 1:
        return "plain" if pieces[0][0] == "plain" else None
    weak = [s for s, _ in pieces if s not in STRONG]
    if not weak:
        return None
    if weak == ["small"] and total <= SMALL_WITH_MAX:
        return None
    for reason in ("plain", "date2", "amount_weak"):
        if reason in weak:
            return reason
    return "small"


def _refs_of(kind: str, core: str) -> list:
    if kind == "ref":
        return [core]
    if kind == "range":
        return [_range(core)]
    if kind == "list":
        return re.findall(_REF, core)
    return []


def _process(text: str, stats: Optional[dict]) -> tuple:
    """(cleaned text, refs, numbers masked) for a normalised text with its emails already masked."""
    refs: list = []
    masks = 0
    pieces_out = []
    last = 0
    for start, end in _numbers(text):
        raw = [(_core(p), p) for p in text[start:end].split(" ")]
        raw = [(shape(core), core, p) for core, p in raw]
        pieces = []
        i = 0
        while i < len(raw):
            if (i + 2 < len(raw) and raw[i][0] == "ref" and raw[i + 1][2] in tuple(_DASHES)
                    and raw[i + 2][0] == "ref" and _range(f"{raw[i][1]}-{raw[i + 2][1]}")):
                pieces.append(("range", 10, f"{raw[i][1]}-{raw[i + 2][1]}"))
                i += 3
                continue
            kind, core, _ = raw[i]
            if kind != "sep":
                pieces.append((kind, sum(1 for ch in core if _is_digit(ch)), core))
            i += 1
        reason = _decide([(k, d) for k, d, _ in pieces])
        if reason is None:
            for kind, _, core in pieces:
                refs.extend(_refs_of(kind, core))
            continue
        masks += 1
        if stats is not None:
            stats["numbers_masked"] = stats.get("numbers_masked", 0) + 1
            lost = sum(len(_refs_of(k, c)) for k, _, c in pieces if k in REF_SHAPES)
            if lost:
                by = stats.setdefault("refs_lost", {})
                by[reason] = by.get(reason, 0) + lost
        pieces_out.append(text[last:start])
        pieces_out.append("#")
        last = end
    pieces_out.append(text[last:])
    return "".join(pieces_out), refs, masks


def _bounded(text: str) -> str:
    """At most MAX_RAW characters, cut back to a letter so no number is cut in two."""
    if len(text) <= MAX_RAW:
        return text
    head = text[:MAX_RAW]
    cut = len(head)
    while cut > 0 and not head[cut - 1].isalpha():
        cut -= 1
    return head[:cut]


def clean(text: Optional[str], stats: Optional[dict] = None) -> tuple:
    """(cleaned text or None, refs that kept their shape, number of masks). `stats` (optional) gathers
    counts: numbers_masked, refs_lost by reason, and fields_cut (longer than MAX_RAW read or MAX_KEPT kept)."""
    if text is None:
        return None, [], 0
    raw = str(text)
    norm = normalize(_bounded(raw))
    norm, emails = _mask_emails(norm)
    cleaned, refs, masks = _process(norm, stats)
    cleaned = cleaned.strip()
    if stats is not None and (len(raw) > MAX_RAW or len(cleaned) > MAX_KEPT):
        stats["fields_cut"] = stats.get("fields_cut", 0) + 1
    cleaned = cleaned[:MAX_KEPT].strip()
    return (cleaned or None), refs, masks + emails


def extract_refs(text: Optional[str]) -> list:
    """Document numbers and ranges cited in a text field that kept their shape: '50347', '50887-50891'."""
    return clean(text)[1]


def email_domain(email: Optional[str]) -> Optional[str]:
    """The domain only: the part a reconciliation can use (a company's domain), never the person."""
    if not email or "@" not in str(email):
        return None
    domain = normalize(str(email)).strip().rsplit("@", 1)[1].strip().lower()
    return domain if re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)+", domain) else None
