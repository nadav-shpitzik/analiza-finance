"""From getDoc's answer to the rows the copy stores (spec §3.2, backlog 0301 B1).

getDoc answers `{"DocumentDetails": [[header], [lines]]}` (seen live 18.11). Every field is read through an
allowlist, case-insensitively: a field the vendor adds is recorded by name in `fields_seen` and never stored
until it is mapped here. `Address`, `City`, `Phone` and the full `email` are never stored (spec §3.2); text
fields pass through app/books/clean.py. Money is Decimal (app/wizcloud.read_envelope parses it that way).
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from decimal import Context, Decimal, InvalidOperation
from typing import Any, Optional

from .clean import clean, email_domain, extract_refs

# The vendor's "no date" (seen on paydate, 18.11).
NO_DATE = date(1990, 1, 1)
# Header fields, by lowercase vendor name. The value says how each is read.
HEADER = {
    "stockid": ("stock_id", "int"),
    "documentid": ("doc_type", "int"),
    "docnumber": ("doc_number", "number"),
    "status": ("status", "int"),
    "valuedate": ("value_date", "date"),
    "issuedate": ("issue_date", "date"),
    "duedate": ("due_date", "date"),
    "paydate": ("pay_date", "date"),
    "accountkey": ("account_key", "key"),
    "accountname": ("account_name", "text"),
    "tftal": ("total_incl", "money"),
    "tftalvat": ("total_excl", "money"),
    "tftalvatfree": ("total_vat_free", "money"),
    "vatprc": ("vat_pct", "money"),
    "currency": ("currency", "code"),
    "reference": ("reference", "text"),
    "remarks": ("remarks", "text"),
    "details": ("details", "text"),
    "email": ("email_domain", "domain"),
    "taxapinumber": ("tax_api_number_present", "present"),
    "batch": ("batch", "code"),
}
LINE = {
    "moveid": ("move_id", "int"),
    "basemoveid": ("base_move_id", "int0"),
    "basequantity": ("base_quantity", "money"),
    "itemkey": ("item_key", "key"),
    "itemname": ("item_name", "text"),
    "details": ("details", "text"),
    "quantity": ("quantity", "money"),
    "price": ("price", "money"),
    "tftal": ("total_excl", "money"),
    "vatexempt": ("vat_exempt", "bool"),
    "currencycode": ("currency_code", "code"),
    "status": ("status", "int"),
}
# Never stored, whatever they hold (spec §3.2). Listed so a test can prove it.
NEVER_STORED = ("address", "city", "phone", "email")
DOCUMENT_COLUMNS = tuple(col for col, _ in HEADER.values()) + ("refs_in_text",)
LINE_COLUMNS = tuple(col for col, _ in LINE.values())


class ParseError(Exception):
    """The answer is not a document as seen live: counted as a failed read, never stored."""


# A number from the books is between these powers of ten (an amount below 10^18, a rate or a price down to
# 10^-12): anything outside, or not finite, is not a document as seen live. Without the bound, 1E+999999
# would build a million-digit string, and Infinity broke int() outside the parser's errors (review 22.11).
MAX_EXP = 18
MIN_EXP = -12
_EXACT = Context(prec=60)  # canonical() never rounds: every value inside the bound has at most 31 digits
# A vendor field name is kept by name only when it is a plain identifier with at most two digits
# (MulFactor1); a key like ID_123456789 is counted, never kept (review 22.11).
_FIELD_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,31}")


def _number(v: Any) -> Optional[Decimal]:
    """A finite Decimal inside the bound, None for an empty or unreadable value, ParseError for a number
    no document holds."""
    if v is None or v == "" or isinstance(v, bool):
        return None
    try:
        d = Decimal(str(v).strip())
    except (InvalidOperation, ValueError):
        return None
    if not d.is_finite():
        raise ParseError("a number that is not finite")
    if d and not MIN_EXP <= d.adjusted() <= MAX_EXP:
        raise ParseError("a number out of range")
    return d


def _unwrap(value: Any) -> Any:
    if isinstance(value, str) and value.strip()[:1] in "[{":
        try:
            return json.loads(value, parse_float=Decimal)
        except ValueError:
            return value
    return value


def split(body: Any) -> tuple:
    """(header dict, list of line dicts) from getDoc's answer, or ParseError."""
    body = _unwrap(body)
    details = _unwrap(body.get("DocumentDetails")) if isinstance(body, dict) else None
    if not isinstance(details, list) or not details:
        raise ParseError("no DocumentDetails list")
    head = _unwrap(details[0])
    header = head[0] if isinstance(head, list) and head and isinstance(head[0], dict) else head
    if not isinstance(header, dict):
        raise ParseError("no header dict")
    lines = _unwrap(details[1]) if len(details) > 1 else []
    if not isinstance(lines, list) or any(not isinstance(x, dict) for x in lines):
        raise ParseError("lines are not a list of dicts")
    return header, lines


def _int(v: Any) -> Optional[int]:
    d = _number(v)
    return None if d is None else int(d)


def _money(v: Any) -> Optional[Decimal]:
    return _number(v)


def _date(v: Any) -> Optional[date]:
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", str(v or ""))
    if not m:
        return None
    try:
        d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None
    return None if d == NO_DATE else d


def _cleaned(value: Any, col: str, stats: dict) -> Optional[str]:
    cleaned, _refs, masked = clean(None if value is None else str(value), stats.setdefault("clean", {}))
    if masked:
        stats["masked"] = stats.get("masked", 0) + masked
        by = stats.setdefault("masked_fields", {})
        by[col] = by.get(col, 0) + 1
    return cleaned


def _read(value: Any, how: str, stats: dict, col: str = "") -> Any:
    if how == "int":
        return _int(value)
    if how == "int0":
        n = _int(value)
        return n or None
    if how == "money":
        return _money(value)
    if how == "date":
        return _date(value)
    if how == "number":  # a document number: ASCII digits only, or nothing
        text = str(value).strip() if value is not None else ""
        return text if text.isascii() and text.isdigit() and len(text) <= 12 else None
    if how == "code":  # a short code (currency, batch): cleaned like any text, and capped
        cleaned = _cleaned(value, col, stats)
        return cleaned[:20] if cleaned else None
    if how == "bool":  # true/false, 1/0, also 1.0 (money arrives as Decimal); anything else is None
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower() if value is not None else ""
        if text in ("true", "false"):
            return text == "true"
        try:
            n = Decimal(text)
        except (InvalidOperation, ValueError):
            return None
        return True if n == 1 else False if n == 0 else None
    if how == "present":
        return value not in (None, "", 0, "0")
    if how == "domain":
        return email_domain(value)
    if how in ("text", "key"):
        return _cleaned(value, col, stats)
    raise ValueError(how)


def _map(src: dict, table: dict, stats: dict, prefix: str = "") -> dict:
    lower = {str(k).lower(): v for k, v in src.items()}
    return {col: _read(lower.get(name), how, stats, prefix + col) for name, (col, how) in table.items()}


def document_rows(body: Any) -> tuple:
    """(document row, line rows, stats) for one getDoc answer. stats: masked (fields masked), masked_fields
    (by column), clean (what masking cost: numbers_masked, refs_lost by reason), fields_unnamed."""
    header, lines = split(body)
    stats: dict = {}
    doc = _map(header, HEADER, stats)
    # Document numbers cited in the document's remarks and details and in its lines' details, read
    # before masking (spec §3.4 step 1): how a credit names the invoice it cancels.
    lower = {str(k).lower(): v for k, v in header.items()}
    raw = [lower.get("remarks"), lower.get("details"), lower.get("reference")]
    raw += [{str(k).lower(): v for k, v in line.items()}.get("details") for line in lines]
    doc["refs_in_text"] = sorted({r for text in raw if text is not None for r in extract_refs(str(text))})
    line_rows = []
    for line in lines:
        row = _map(line, LINE, stats, "line.")
        if row["move_id"] is not None:
            line_rows.append(row)
    # The vendor's field names, as names only: a key that is not a plain identifier is counted, never kept.
    keys = {str(k) for k in header} | {str(k) for line in lines for k in line}
    doc["fields_seen"] = sorted(
        k for k in keys if _FIELD_NAME.fullmatch(k) and sum(ch.isdigit() for ch in k) <= 2)
    stats["fields_unnamed"] = len(keys) - len(doc["fields_seen"])
    if doc["stock_id"] is None:
        raise ParseError("no StockID in the header")
    doc["content_hash"] = content_hash(doc, line_rows)
    stats.setdefault("masked", 0)
    return doc, line_rows, stats


def canonical(value: Any) -> Any:
    """One spelling per value, so 650, 650.0 and 650.00 hash alike (review 22.11)."""
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, Decimal)):
        d = Decimal(value).normalize(_EXACT)
        return format(d, "f")
    if isinstance(value, (list, tuple)):
        return [canonical(v) for v in value]
    return str(value)


def line_key(line: dict) -> tuple:
    return tuple(canonical(line.get(k)) for k in LINE_COLUMNS)


def content_hash(doc: dict, lines: list) -> str:
    """What the copy holds of a document, as a hash: equal hashes, nothing to write (spec §4.1)."""
    payload = {
        "doc": {k: canonical(doc.get(k)) for k in DOCUMENT_COLUMNS},
        "lines": sorted((list(line_key(line)) for line in lines), key=lambda x: str(x)),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
