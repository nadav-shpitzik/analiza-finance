"""A1: the receipt date from the books to Student Charge (Analiza-Monday backlog 0301 A1; the rules FR-C1 and FR-C5,
with FR-3, FR-4, FR-7 and FR-L12 where they decide a date; spec §7).

`תאריך תקבול` (`date_mm8yh4bn`) is "the day the money came in" (the column's description), and the monthly commission
report counts a sale in that date's month. A row whose `אסמכתת חשבונית` (`text_mm6d6s97`) cites documents in the books
can take that day from them - but only where the books say it for certain:

1. **The match (FR-C1, FR-3, FR-4).** Every number the row cites (comma-separated, ADR-0088 §1) is exactly one status-1
   document, and that document is a tax invoice or an invoice-receipt with a total that is not negative. The rows and
   the documents they cite form groups, judged whole: a row is never judged on half the rows of its document. A group
   is matched only in the two shapes ADR-0088 §1 names - one document over several rows, one row over several
   documents - when its rows' paid amounts sum to its documents' `total_incl` within 1 ₪ per document (the contract
   §0.3); past that and up to 0.1% is the rounding band: shown, never filled. Several rows over several documents is a
   tangle: a sum that ties there can hide a row paid past its only document (review 23.11), so it is counted and left
   to a person. **A name is never read** (the law of §5).
2. **The day (FR-C5, FR-L12, FR-7).** An invoice-receipt pays itself, so its `value_date` is the day the money came
   in. A tax invoice is a demand: when it stands alone the money came on a separate receipt (ADR-0088 §1 cites the
   invoice then), whose day the linker of R1 finds - until then a group with a tax invoice gives no date. A group
   whose invoice-receipts carry different days gives none either (the contract §0.5: a rule that cannot decide says
   so), nor does a document dated outside Student Charge's window, 2026, or one that answered "not found".
3. **The fill (FR-C5).** Only an empty date, and only on a row whose status says the money came in. **A filled date
   is never replaced**, the same day or another.

Every row lands in exactly one bucket, the first that fits in `BUCKETS` (spec §6.3.1), so the counts add up to the
rows read. The plan is what the `fill` bucket holds; the read-back after a write asks the same question again of a
fresh reading, and a row is verified when it is in `filled_same`.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Iterable, Optional

from ..books.doctypes import VALID_STATUS, role
from .reading import SC, STUDENT_CHARGE, monday_side

KIND = "b2c_receipt_date_fill"
COLUMN = SC["paid_date"]  # date_mm8yh4bn, תאריך תקבול
REF_COLUMN = SC["invoice"]  # text_mm6d6s97, אסמכתת חשבונית
AMOUNT_COLUMN = SC["paid"]  # numeric_mm1kgbnt, סכום ששולם: gross, a refund is in its own column (ADR-0021)
STATUS_COLUMN = SC["status"]  # color_mm1kxpvt, סטטוס תשלום, matched by its index
ITEM_URL = "https://analiza-college-group.monday.com/boards/" + STUDENT_CHARGE + "/pulses/{}"

# סטטוס תשלום by index (verified live 2026-10-25, Analiza-Monday docs/data-model/b2c-flow.md): the ones that say the
# money came in (ADR-0035: a row means money received). לא שולם 0, יש בעיה 2 (the desk files it as "ללא תקבול") and
# מלגת אנליזה 3 do not, and get no receipt date from here.
MONEY_IN = {"1": "שולם", "4": "תשלום הוחזר", "5": "הוחזר חלקי", "9": "ביקש החזר"}
WINDOW_YEAR = 2026  # FR-7: Student Charge's window
TOLERANCE_PER_DOC = Decimal("1")  # the contract §0.3
ROUNDING_BAND = Decimal("0.001")  # 0.1% of the documents' total, past the tolerance: shown, never filled
INVOICE, INVOICE_RECEIPT = "invoice", "invoice_receipt"

# The first that fits wins; every row is in exactly one.
BUCKETS = (
    "not_active",              # archived or deleted between the ids and the rows: nothing is written to it
    "no_ref",                  # no number: FR-C2's (a number from a name), not this rule's
    "ref_unreadable",          # not a comma-separated list of numbers
    "ref_not_in_books",        # a number no status-1 document holds (a draft's included, FR-2)
    "ref_ambiguous",           # a number two status-1 documents hold (FR-3): neither is matched by it
    "ref_not_an_invoice",      # a credit, a receipt, a proforma, an own invoice, or a negative invoice (FR-4)
    "amount_unreadable",       # the row's paid amount is empty or not a number
    "shares_with_unmatched",   # its document is cited also by a row above: the group cannot be judged
    "document_missing",        # a document of the group answered "not found" in the books
    "out_of_window",           # a document of the group is dated outside 2026 (FR-7): never a gap, whatever the sums
    "group_tangled",           # several rows over several documents: the sums cannot say which row paid what
    "amount_mismatch",         # the group's rows and documents differ past the rounding band
    "rounding_band",           # past 1 ₪ a document, within 0.1%: a person looks, nothing is filled
    "invoice_needs_receipt",   # the group holds a tax invoice: its day is its receipt's, found by R1
    "dates_differ",            # the group's invoice-receipts carry different days
    "status_not_money_in",     # matched, and the row's status does not say the money came in
    "date_unreadable",         # the row's date is filled with something that is not a date
    "filled_same",             # filled, and the books say the same day
    "filled_differs",          # filled with another day: never replaced
    "fill",                    # empty, and the books say the day: the plan
)

# Marks a right-to-left editor leaves (zero-width, direction, BOM) are dropped, even inside a number; a no-break space
# is a space. Written as escapes: an invisible character in source is invisible in review too.
_INVISIBLE = re.compile("[\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff]")
_NBSP = re.compile("[\u00a0\u202f]")
# A number is 4 to 12 ASCII digits, as the books copy stores one (app/books/parse.py): `\d` alone also takes Arabic-Indic
# digits, and `62,894` is a thousands separator, not the numbers 62 and 894 (review 23.11). Every number cited live
# is 5 digits.
_NUMBER = re.compile(r"[0-9]{4,12}")
_AMOUNT = re.compile(r"-?(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?")
_DAY = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})")


def _clean(text) -> str:
    return _NBSP.sub(" ", _INVISIBLE.sub("", str(text or ""))).strip()


def parse_refs(text) -> Optional[list]:
    """The numbers a row cites, in order and once each: [] for an empty cell, None for one that is not a
    comma-separated list of numbers (ADR-0088 §1: `62894, 62914`). Invisible marks a right-to-left editor leaves
    are dropped first; a leading zero is not a different number."""
    cleaned = _clean(text)
    if not cleaned:
        return []
    out: list = []
    for piece in cleaned.split(","):
        piece = piece.strip()
        if not _NUMBER.fullmatch(piece):
            return None
        number = str(int(piece))
        if number not in out:
            out.append(number)
    return out


def loose_numbers(text) -> list:
    """Every number an unreadable cell may name, so that it still holds those documents (it is never resolved): each
    run of digits in any script (Arabic-Indic too), and each two neighbouring runs joined (`62,894` may be 62894).
    Holding too much only keeps a group from being filled; holding too little fills a row whose document is cited
    twice (review 23.11)."""
    raw = re.findall(r"\d{1,24}", _clean(text))
    joined = [a + b for a, b in zip(raw, raw[1:])]
    return list(dict.fromkeys(str(int(n)) for n in raw + joined))


def parse_amount(text) -> Optional[Decimal]:
    """monday's numbers cell as text: `4000`, `4,000` or `3347.46`; None for empty or anything else."""
    cleaned = _clean(text)
    if not _AMOUNT.fullmatch(cleaned):
        return None
    try:
        return Decimal(cleaned.replace(",", ""))
    except InvalidOperation:
        return None


def parse_day(text) -> Optional[date]:
    """A date cell's day (`2026-03-15`, or with a time after it); None when there is none."""
    m = _DAY.match(str(text or "").strip())
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _as_day(value) -> Optional[date]:
    if isinstance(value, date):
        return value
    return parse_day(value)


def _as_money(value) -> Optional[Decimal]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def index_documents(documents: Iterable[dict]) -> dict:
    """number -> the status-1 documents that carry it (FR-3: a number is looked up among status-1 documents only).
    A gone document is not in the books and is not here; the caller passes the held ones."""
    by_number: dict = defaultdict(list)
    for doc in documents:
        if doc.get("gone_at") is not None:
            continue
        number = str(doc.get("doc_number") or "")
        if doc.get("status") == VALID_STATUS and number.isascii() and number.isdigit():
            by_number[str(int(number))].append(doc)
    return dict(by_number)


def _resolve(number: str, by_number: dict) -> tuple:
    """(bucket or None, the document) for one cited number."""
    docs = by_number.get(number) or []
    if not docs:
        return "ref_not_in_books", None
    if len(docs) > 1:
        return "ref_ambiguous", None
    doc = docs[0]
    total = _as_money(doc.get("total_incl"))
    if role(doc.get("doc_type")) not in (INVOICE, INVOICE_RECEIPT) or total is None or total < 0:
        return "ref_not_an_invoice", None
    return None, doc


class _Groups:
    """Rows and the documents they cite, joined (union-find over row and document keys)."""

    def __init__(self):
        self.parent: dict = {}

    def find(self, key):
        self.parent.setdefault(key, key)
        while self.parent[key] != key:
            self.parent[key] = self.parent[self.parent[key]]
            key = self.parent[key]
        return key

    def join(self, a, b) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def _group_verdict(amounts: list, docs: dict) -> tuple:
    """(bucket or None, the day) for a group whose every row resolved, in the order of BUCKETS: a document not found,
    the window (FR-7: never a gap, whatever the sums), the shape, the sum, then the day."""
    if any(d.get("missing") for d in docs.values()):
        return "document_missing", None
    days = {_as_day(d.get("value_date")) for d in docs.values()}
    if None in days or any(day.year != WINDOW_YEAR for day in days):
        return "out_of_window", None
    if len(amounts) > 1 and len(docs) > 1:
        return "group_tangled", None
    paid = sum(amounts, Decimal(0))
    total = sum((_as_money(d["total_incl"]) for d in docs.values()), Decimal(0))
    diff = abs(paid - total)
    if diff > TOLERANCE_PER_DOC * len(docs):
        return ("rounding_band" if diff <= total * ROUNDING_BAND else "amount_mismatch"), None
    if any(role(d.get("doc_type")) == INVOICE for d in docs.values()):
        return "invoice_needs_receipt", None
    if len(days) != 1:
        return "dates_differ", None
    return None, days.pop()


def analyse(rows: list, documents: Iterable[dict]) -> dict:
    """Every Student Charge row in one bucket, with the day the books give where they give one. Returns
    {"rows": {item_id: {"bucket", "books_day", "current", "documents"}}, "counts": {bucket: n}}."""
    by_number = index_documents(documents)
    out: dict = {}
    pending: list = []  # rows whose own numbers and amount are fine, to be judged by their group
    loose: dict = {}  # an unreadable cell's digit runs: never resolved, but they hold the documents they may name
    for row in rows:
        item_id = str(row.get("id"))
        entry = {"bucket": None, "books_day": None, "current": str(row.get(COLUMN) or "").strip(), "documents": []}
        out[item_id] = entry
        if row.get("state", "active") != "active":
            entry["bucket"] = "not_active"
            continue
        refs = parse_refs(row.get(REF_COLUMN))
        if refs == []:
            entry["bucket"] = "no_ref"
            continue
        if refs is None:
            entry["bucket"] = "ref_unreadable"
            loose[item_id] = loose_numbers(row.get(REF_COLUMN))
            continue
        entry["documents"] = refs
        resolved, problems = [], []
        for number in refs:
            bucket, doc = _resolve(number, by_number)
            if bucket is not None:
                problems.append(bucket)
            else:
                resolved.append(doc)
        if problems:  # the first in BUCKETS, whatever order the numbers were typed in (review 23.11)
            entry["bucket"] = min(problems, key=BUCKETS.index)
            continue
        amount = parse_amount(row.get(AMOUNT_COLUMN))
        if amount is None:
            entry["bucket"] = "amount_unreadable"
            continue
        pending.append((item_id, row, amount, resolved))

    # The groups: every row that cites a document - the ones that failed on their own too, so a document shared with
    # one of them is never judged on half its rows.
    groups = _Groups()
    cites: dict = defaultdict(set)  # document number -> the rows citing it
    for item_id, entry in out.items():
        for number in entry["documents"] + loose.get(item_id, []):
            cites[number].add(item_id)
            groups.join(("row", item_id), ("doc", number))
    members: dict = defaultdict(list)
    for item_id, entry in out.items():
        if entry["documents"] or loose.get(item_id):
            members[groups.find(("row", item_id))].append(item_id)

    judged = {item_id: (row, amount, docs) for item_id, row, amount, docs in pending}
    for root, item_ids in members.items():
        candidates = [i for i in item_ids if i in judged]
        if not candidates:
            continue
        if len(candidates) != len(item_ids):  # a row of the group failed on its own
            for i in candidates:
                out[i]["bucket"] = "shares_with_unmatched"
            continue
        docs = {}
        for i in candidates:
            for doc in judged[i][2]:
                docs[str(int(str(doc["doc_number"])))] = doc
        bucket, day = _group_verdict([judged[i][1] for i in candidates], docs)
        for i in candidates:
            out[i]["bucket"] = bucket
            out[i]["books_day"] = day.isoformat() if day else None
        if bucket is not None:
            continue
        for i in candidates:
            row = judged[i][0]
            entry = out[i]
            if str(row.get(STATUS_COLUMN + "#", "")) not in MONEY_IN:
                entry["bucket"] = "status_not_money_in"
            elif not entry["current"]:
                entry["bucket"] = "fill"
            else:
                current = parse_day(entry["current"])
                if current is None:
                    entry["bucket"] = "date_unreadable"
                else:
                    entry["bucket"] = "filled_same" if current.isoformat() == entry["books_day"] else "filled_differs"

    counts = {b: 0 for b in BUCKETS}
    for entry in out.values():
        counts[entry["bucket"]] += 1
    return {"rows": out, "counts": counts}


def _plan_body(analysis: dict) -> dict:
    actions = []
    by_month: dict = defaultdict(int)
    for item_id, entry in sorted(analysis["rows"].items(), key=lambda kv: (kv[1]["books_day"] or "", kv[0])):
        if entry["bucket"] != "fill":
            continue
        actions.append({"item_id": item_id, "url": ITEM_URL.format(item_id), "column": COLUMN, "current": entry["current"],
                        "proposed": entry["books_day"], "documents": entry["documents"]})
        by_month[entry["books_day"][:7]] += 1
    return {"kind": KIND, "class": "A", "board": STUDENT_CHARGE, "column": COLUMN,
            "rows": len(analysis["rows"]), "counts": analysis["counts"], "fill": len(actions),
            "fill_by_month": dict(sorted(by_month.items())),
            "ids_by_bucket": {b: sorted(i for i, e in analysis["rows"].items() if e["bucket"] == b)
                              for b in BUCKETS if b not in ("fill", "no_ref") and analysis["counts"][b]},
            "actions": actions}


@monday_side
def plan(company: str, reading: dict, documents: Iterable[dict]) -> dict:
    """The dry run (spec §7.1.1): every empty receipt date the books give, with the item's link, the value now and the
    value proposed, and the count of every bucket. Refuses (NotReady) on Demo and on a reading that is not whole."""
    return _plan_body(analyse(reading["boards"]["student_charge"], documents))


@monday_side
def verify(company: str, reading: dict, documents: Iterable[dict], item_ids: Iterable) -> dict:
    """The read-back (spec §7.1.4), on a reading made after the write: each asked row is `verified` when its date is
    the day the books give (`filled_same`), else it says what it is - `empty`, `differs`, or the bucket that stopped
    the books from giving a day - and `not_found` when the reading does not hold it."""
    analysis = analyse(reading["boards"]["student_charge"], documents)
    states, counts = {}, defaultdict(int)
    for item_id in dict.fromkeys(str(i) for i in item_ids):  # once each: a repeated id counted twice said "all verified"
        entry = analysis["rows"].get(item_id)
        if entry is None:
            state = "not_found"
        elif entry["bucket"] == "filled_same":
            state = "verified"
        elif entry["bucket"] == "fill":
            state = "empty"
        elif entry["bucket"] == "filled_differs":
            state = "differs"
        else:
            state = entry["bucket"]
        states[item_id] = {"state": state, "current": entry["current"] if entry else None,
                           "books": entry["books_day"] if entry else None}
        counts[state] += 1
    return {"kind": KIND, "asked": len(states), "verified": counts.get("verified", 0), "counts": dict(counts),
            "items": states}
