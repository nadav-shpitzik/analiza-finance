"""Cleaning text from the books before it is stored (spec §3.4, backlog 0301 B1; rewritten after the two
reviews of 22.11).

An ID number is never stored server side (ADR-0173 §1, ADR-0171 §1), and text fields in a document - the
card's name, remarks, a line's details, even a short code field - can carry one, or a phone, a bank
account, a card number, an email. So every text field passes through `clean`:

1. Normalise: NFKC; a digit of any script becomes an ASCII digit; invisible characters go (format
   characters, the default-ignorables such as variation selectors and blank fillers, lone surrogates);
   control characters and every kind of whitespace become one space. Markup that hides a separator is
   undone and the text normalised again: HTML entities (`&nbsp;`, also escaped twice), line-break and
   formatting tags (`<br>`), and `\n`, `\r`, `\t` written as text. Between two digits, the two keys of the
   Hebrew layout that type '.' and ',' (ץ and ת) are read as the key they were. The length is bounded
   before and after normalising, since NFKC can make one character eighteen (third review 22.11).
2. Emails become `@`: the run of address characters on each side of an at sign, one space allowed on
   either side, and "(at)", "[@]", "{at}", "<at>" in any bracket, or "שטרודל" between two address
   characters, count as an at sign. A linear scan, never a backtracking regex (the review measured 14 s
   for one field with the old one).
3. A number is a run of digits joined by anything that is not a letter, at most three such characters at
   a time not counting spaces (12345 -- 6789 is one number), or with no space by at most three characters
   of which one is a letter (054ץ987ץ6543, 123אְ456, 123-א456: a typo splits nothing; third review 22.11).
   It is split at spaces into pieces, and each piece gets a shape:
   - a document number (five digits, not 0-led), a range (50887-50891, also 50887-891 and 50887 - 50891),
     a list (50887,50888, 50887/50888 or 50887+50888), and at the end of the field a list cut by the
     field's width after two whole numbers (50887,50888,508);
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
four-digit number (50347 2026, 50347 1170) is masked, because an ID is also written 51234 5678. The same
holds for any piece of at most four digits beside it that is not small (50347 1,170, 50347 08/26, 50347
1/12: confirmed by the third review's pass, which found them masked): a strong shape always has five
digits or more, so it can never be the end of an ID. `stats` counts what masking cost, by reason, so
books-check can show it on the real books (no reference lost in Analiza's, 22.11).

Nothing here logs or returns the text it was given except as cleaned.
"""

from __future__ import annotations

import html
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
_RE_REF_INSIDE = re.compile(rf"(?<![0-9]){_REF}(?![0-9])")
_RE_RANGE = re.compile(rf"^({_REF})[{_DASHES}](\d{{5}}|\d{{1,3}})$")
_RE_LIST = re.compile(rf"^{_REF}(?:[,/;+&]{_REF})+$")
# A list cut by the field's width: two whole numbers at least, then a stub (third review 22.11). Only as one
# piece with no space, and only at the end of the field: a stub after a space is how an ID's end is written.
_RE_LIST_CUT = re.compile(rf"^({_REF}(?:[,/;+&]{_REF})+)[,/;+&]\d{{1,4}}$")
_RE_DATE = re.compile(r"^(\d{1,2})([./-])(\d{1,2})\2((?:19|20)\d{2})$")
_RE_ISO = re.compile(r"^((?:19|20)\d{2})-(\d{1,2})-(\d{1,2})$")
_RE_DATE2 = re.compile(r"^(\d{1,2})([./-])(\d{1,2})\2(\d{2})$")
_RE_MONTH = re.compile(r"^(\d{1,2})[./-]((?:19|20)\d{2})$|^((?:19|20)\d{2})[./-](\d{1,2})$")
_RE_YEARS = re.compile(r"^((?:19|20)\d{2})[/-]((?:19|20)\d{2}|\d{2})$")  # 2025/2026, 2025-26
_RE_AMOUNT = re.compile(r"^(?:0|[1-9]\d{0,2})(?:,\d{3})+(?:\.\d{1,2})?$|^(?:0|[1-9]\d{0,5})\.\d{1,2}$")
_RE_AMOUNT_STRONG = re.compile(r"^[1-9][\d,]*\d{2}\.\d{2}$")  # 100.00 and up, two decimal places
_AT_WORDS = re.compile(r"[(\[{<] ?(?:at|@|שטרודל) ?[)\]}>]", re.IGNORECASE)
_AT_HEBREW = re.compile(r"(?<=[A-Za-z0-9._%+'-]) ?שטרודל ?(?=[A-Za-z0-9])")
# Markup that hides a separator between digits (third review 22.11): any tag but <at> (the fourth review found
# <wbr>, <sup>, <hr>, <a ...>, <o:p> and comments kept IDs whole when only a list of tags was undone), and an escape
# written as text. Entities go through html.unescape.
_TAGS = re.compile(r"</?(?!at\b)[A-Za-z][A-Za-z0-9:]{0,20}\b[^<>]{0,200}>|<!--[^<>]{0,200}-->", re.IGNORECASE)
_ESCAPED = re.compile(r"\\[nrt]")
_LAYOUT = (re.compile(r"(?<=[0-9])ץ(?=[0-9])"), re.compile(r"(?<=[0-9])ת(?=[0-9])"))
# The Hebrew layout's '.' and ',' are marks in a join, not letters: 054ת 987ת 6543 is 054, 987, 6543 (fourth review).
_LAYOUT_KEYS = frozenset("ץת")
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
    text = _AT_HEBREW.sub("@", _AT_WORDS.sub("@", text))
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
    """(start, end) of every number in a normalised text: digits joined by at most MAX_JOIN characters that are
    neither a digit nor a letter, spaces not counted (12345 -- 6789), or with no space by at most MAX_JOIN
    characters of which one is a letter (054ץ987ץ6543, 123אְ456: third review 22.11). Linear: each join looks at
    most 2 * MAX_JOIN + 1 characters ahead."""
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
            # Here text[j - 1] is a digit. A join is at most MAX_JOIN marks (neither a digit nor a letter), spaces not
            # counted; or, with no space in it, at most MAX_JOIN characters of which one is a letter (a typo, a key
            # of the Hebrew layout, a letter with its points: 054ץ987, 123אְ456, 123-א456).
            k, marks, letter, space, joins = j, 0, False, False, True
            while k < n and not _is_digit(text[k]):
                ch = text[k]
                if ch == " ":
                    space = True
                elif ch.isalpha() and ch not in _LAYOUT_KEYS:
                    if letter:
                        joins = False
                        break
                    letter = True
                else:
                    marks += 1
                if marks > MAX_JOIN or k - j > 2 * MAX_JOIN or (letter and (space or k - j >= MAX_JOIN)):
                    joins = False
                    break
                k += 1
            if joins and k < n:
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


def _cut_list(core: str) -> Optional[str]:
    """The whole numbers of a list cut by the field's width (50887,50888,508 -> 50887,50888), or None. The stub must
    begin a number within MAX_RANGE of the last whole one, as the list's next number would: 50347,51234,5678 is no cut
    list but an ID's end (fourth review 22.11)."""
    m = _RE_LIST_CUT.match(core)
    if not m:
        return None
    last = int(re.findall(_REF, m.group(1))[-1])
    stub = core[len(m.group(1)) + 1:]
    scale = 10 ** (5 - len(stub))
    low, high = int(stub) * scale, (int(stub) + 1) * scale - 1  # every five-digit number this stub begins
    return m.group(1) if low <= last + MAX_RANGE and high >= last - MAX_RANGE else None


def _process(text: str, stats: Optional[dict]) -> tuple:
    """(cleaned text, refs, numbers masked) for a normalised text with its emails already masked."""
    refs: list = []
    masks = 0
    pieces_out = []
    last = 0
    for start, end in _numbers(text):
        raw = [(_core(p), p) for p in text[start:end].split(" ")]
        raw = [(shape(core), core, p) for core, p in raw]
        if " " not in text[start:end] and not text[end:].strip() and raw[0][0] == "plain" and _cut_list(raw[0][1]):
            whole = _cut_list(raw[0][1])
            raw = [("list", whole, whole)]  # the stub the width cut is no number: set aside, nothing kept from it
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
        if stats is not None and any(ch.isalpha() for ch in text[start:end]):  # joined through a letter (22.11)
            key = "letter_joined" if reason is None else "letter_joined_masked"
            stats[key] = stats.get(key, 0) + 1
        if reason is None:
            for kind, _, core in pieces:
                refs.extend(_refs_of(kind, core))
            continue
        masks += 1
        if stats is not None:
            stats["numbers_masked"] = stats.get("numbers_masked", 0) + 1
            lost = sum(len(_refs_of(k, c)) for k, _, c in pieces if k in REF_SHAPES)
            if any(ch.isalpha() and ch not in _LAYOUT_KEYS for ch in text[start:end]):
                # a reference glued to a letter sits inside a piece of no shape: counted by its digits (fourth review)
                reason, lost = "letter", len(_RE_REF_INSIDE.findall(text[start:end]))
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


def _unmark(text: str) -> str:
    """HTML entities (twice escaped too), line-break and formatting tags, and escapes written as text, undone:
    each hid a separator between digits behind letters (third review 22.11)."""
    for _ in range(3):
        undone = html.unescape(text)
        if undone == text:
            break
        text = undone
    return _ESCAPED.sub(" ", _TAGS.sub(" ", text))


def _prepare(raw: str) -> tuple:
    """(the text the scans read, whether a bound cut it, whether markup was undone, Hebrew-layout keys read): bounded, normalised and bounded again - NFKC can make
    one character eighteen (third review 22.11, finding 8) - with its markup undone, and the Hebrew layout's
    '.' and ',' between digits read as the keys they were."""
    cut = len(raw) > MAX_RAW
    text = normalize(_bounded(raw))
    cut = cut or len(text) > MAX_RAW
    text = _bounded(text)
    undone = _unmark(text)
    marked = undone != text
    if marked:
        text = normalize(undone)
        cut = cut or len(text) > MAX_RAW
        text = _bounded(text)
    dot, comma = _LAYOUT
    text, dots = dot.subn(".", text)
    text, commas = comma.subn(",", text)
    return text, cut, marked, dots + commas


def clean(text: Optional[str], stats: Optional[dict] = None) -> tuple:
    """(cleaned text or None, refs that kept their shape, number of masks). `stats` (optional) gathers
    counts: numbers_masked, refs_lost by reason, fields_cut (longer than MAX_RAW read or MAX_KEPT kept), and what the
    third review's rules touched: markup_undone, layout_keys, letter_joined (kept) and letter_joined_masked, and
    emails_masked."""
    if text is None:
        return None, [], 0
    raw = str(text)
    norm, cut, marked, keys = _prepare(raw)
    norm, emails = _mask_emails(norm)
    cleaned, refs, masks = _process(norm, stats)
    cleaned = cleaned.strip()
    if stats is not None:
        for name, add in (("fields_cut", cut or len(cleaned) > MAX_KEPT), ("markup_undone", marked), ("layout_keys", keys),
                          ("emails_masked", emails)):
            if add:
                stats[name] = stats.get(name, 0) + int(add)
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
