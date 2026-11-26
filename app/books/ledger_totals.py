"""The ledger report's totals (backlog 0301 B3; ADR-0187 §2-§5; spec §3.5.3): a run reads whole months of "כרטסת לאנליזה"
(app/books/ledger.py) and keeps only category totals, never a row (app/books/ledger_store.py, migrations/007).

What a run keeps, per company:
(a) per month (by "תאריך", the reference's date) and sort code, a line that may stand on its own (below): its debit and
    credit totals, its rows, its distinct accounts, and how many of their keys the map may not keep (`masked_keys`:
    counted, never kept). Every other line of the month is folded into ONE merged line, sort code MERGED_CODE (-1), named
    MERGED_NAME;
(b) per sort code: its name ("שם קוד מיון"), cleaned like any text from the books (app/books/clean.py, spec §3.4);
(c) per account key: its sort code - only for a key the copy's own cleaning keeps whole and that holds fewer than six
    digits (`storable_key`). **Student cards are keyed by ID numbers (ADR-0173 §1)**, and an ID number is never stored
    server side: such a key is never stored, hashed or kept in any form. It is only counted, per line and per month. The
    copy masks the same keys (app/books/parse.py), so every key the copy can name (app/recon/cards.py `card_of`) is a key
    the map can hold, in the same cleaned form.
Never kept: an account's name, details, a reference, a counter-account, a cost code, or one account's amount.

**The merge (the owner's decision, 26.11): salaries, and every line few accounts fill, are one line a month.** A month's
line goes into the merged line when
1. its sort code is a salary code: listed explicitly in LEDGER_SALARY_CODES (a Replit Secret, chosen from the dry
   listing), never guessed by a name. Until the list is set, a run stores nothing (`salary_codes_not_set`);
2. it has fewer than MIN_ACCOUNTS (3) distinct accounts (`small`): a code that holds one person's account exposes their
   monthly amount (ADR-0187 "שארית ידועה");
3. its debit or its credit is thin (`side`, `_thin`), and the whole line moves. A side is filled by the accounts whose sum
   on it is not zero, and it is thin when
   (a) one or two accounts fill it: with one account debited and others credited, the debit total is that one account's
       own; or
   (b) its total T is not zero and one account's sum, or two accounts' sum, lies strictly within REST_SHARE (a tenth) of
       it, |T - s| < REST_SHARE * |T|: the other accounts move the total by less than a tenth of it. Counting accounts is
       not enough: two accounts that cancel (+X and -X, a reclassification) fill a side and leave its total one account's
       own. This is the (2, 90%) dominance rule of statistical disclosure control, searched over every one and every two
       accounts (`_dominated`), not only the largest, because a sum here can be negative.
   An empty side, or one whose total is zero, exposes no one.
The merged line stands on the same terms as a line: MIN_ACCOUNTS accounts or more, and neither of its sides thin. While it
does not, the lines that stand are folded into it one at a time, the fewest accounts first and then the lowest sort code,
each counted as `balance`, until it stands. When no standing line is left and it still does not stand, the month keeps no
line at all (`merged: "withheld"`, `kept_lines` 0).

**What the balance still reveals: nothing.** A month's lines net to zero: every transaction balances, the date cut takes
whole transactions, and batch 9998 balances on its own and is kept apart. So a month that withheld one cell and kept the
rest would give the withheld cell away, as minus the kept lines' nets: a merged line of one person's account, withheld,
would be that person's monthly money again (ADR-0187's addendum: a hidden line is derived from the rest). That is why the
merged line is never withheld while another line stands: a month keeps every line, and their nets sum to zero and reveal
nothing that is not kept, or it keeps none, and there is nothing to derive from.

**Batch 9998** (the income and expense transfer at the end of a year or a quarter, which zeroes the expense cards) never
enters a line: its rows and totals are counted apart, per month (ADR-0187 §5), even though the report is defined without
income transfers. Its debit equals its credit.

**A run recomputes whole months and replaces them** (ADR-0187 §4), one transaction per month (LedgerStore.replace_month).
A month is replaced only when it was read whole:
1. every part answered under the report's `limit`. A part that reaches it (`hit_limit`) is split in two by sort code, at
   the median of the codes it carried, until every part is under the limit; a part of one sort code at the limit, or more
   than MAX_PARTS calls, is `cut`. The sort-code cut narrower than the whole range is unverified live (only the whole
   range was sent, 26.11), so a part whose rows fall outside its range is `outside`, never summed.
2. every row readable: a date in the month asked, a sort code in its part's range (never MERGED_CODE), a key, numeric
   debit and credit, a batch that is a whole number when it is there (`unreadable`, `outside`); and the rows carry every
   column the totals read (`columns`).
3. the month balances: its debits equal its credits (`unbalanced`). This is the one check that does not depend on knowing
   the cap the vendor applies: the External file's `limit` is not sent (app/books/ledger.py), so an answer cut by a lower
   cap of the server's would pass `hit_limit`. A hypothesis the first run settles.
4. an answer of no rows ("data not found") for a month the store holds rows of is doubted, not trusted (`emptied`).

**The dry listing** (`listing`) reads the same months the same way and stores nothing: per sort code its name and its
distinct accounts in those months, masked keys counted too - the categories a person chooses the salary codes from. Never
an amount.

The rows live only in the run's memory (ADR-0187 §3): read, summed and dropped, never logged, kept or returned. The answers
are counts and closed words; no message here ever carries a value of a row.
"""

from __future__ import annotations

import math
import re
import time
import unicodedata
from bisect import bisect_left, bisect_right
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Callable, Optional

from ..wizcloud import MIN_CALL_S, DeadlineReached, WizError
from .clean import clean
from .ledger import (CREDIT_CAPTION, DATE_CAPTION, DEBIT_CAPTION, SORT_CAPTION, WHOLE_SORT, DefinitionError, Definition,
                     _NUMBER_TEXT, _Table, _empty, _month_of, _vendor_in, find_rows, load_definition, month_bounds,
                     month_parameters, vendor_word)

# The columns read, by caption (the live answer's keys, 26.11). The name is optional: without it no name is updated.
KEY_CAPTION = "מפתח חשבון"
SORT_NAME_CAPTION = "שם קוד מיון"
BATCH_CAPTION = "מספר מנה"
NEEDED = (DATE_CAPTION, KEY_CAPTION, SORT_CAPTION, DEBIT_CAPTION, CREDIT_CAPTION, BATCH_CAPTION)
TRANSFER_BATCH = 9998
MIN_ACCOUNTS = 3  # a line, or a side of one, filled by fewer accounts never stands alone (the owner, 26.11)
REST_SHARE = Decimal("0.1")  # a side's total one or two accounts hold to within this share of it is theirs: thin (b)
MERGED_CODE = -1  # the merged line's sort code: never a real one (a row that carries it is unreadable)
MERGED_NAME = "שכר ונלווים וקודים קטנים"
MERGE_WHY = ("salary", "small", "side", "balance")  # why a line went into the merged line, in the order they are asked
MERGED_STATES = ("none", "kept", "withheld")  # "withheld": no line of the month is kept
KEY_DIGITS_MAX = 5  # a key with more digits is never stored, whatever shape the cleaning gave it
MAX_KEY = 64
MAX_NAME = 200
MAX_MONTHS = 12
MAX_PARTS = 16  # report calls for one month: a split that needs more is `cut`
MARGIN_S = 1.0
# The salary list's states (/health says them, never the list).
SALARY_STATES = ("set", "missing", "bad")
# Why a month was not stored. The run's reasons are the run's own, then these.
MONTH_REASONS = ("salary_codes_not_set", "salary_codes_bad", "cut", "out_of_time", "wizcloud", "bad_answer", "columns",
                 "unreadable", "outside", "unbalanced", "emptied", "store")
RUN_REASONS = ("bad_request", "not_set", "bad_definition", "no_limit", "crashed") + MONTH_REASONS
# The fields a row is unreadable in (`unreadable`), as closed words.
FIELDS = ("date", "sort_code", "key", "debit", "credit", "batch")


# ---- the salary codes --------------------------------------------------------------------------------------------------

_CODE = re.compile(r"-?\d{1,9}")


def salary_codes(text: Optional[str]) -> tuple:
    """(state, codes): LEDGER_SALARY_CODES as a frozenset of sort codes. "missing" when it is not set; "bad" when an entry
    is not a whole number in the report's range, names MERGED_CODE, or the list names none; "set" otherwise. Entries are
    comma separated, spaces around them allowed, an empty entry skipped."""
    raw = (text or "").strip()
    if not raw:
        return "missing", frozenset()
    entries = [entry.strip() for entry in raw.split(",")]
    codes = set()
    for entry in entries:
        if not entry:
            continue
        if not _CODE.fullmatch(entry):
            return "bad", frozenset()
        code = int(entry)
        if code == MERGED_CODE or not WHOLE_SORT[0] <= code <= WHOLE_SORT[1]:
            return "bad", frozenset()
        codes.add(code)
    return ("set", frozenset(codes)) if codes else ("bad", frozenset())


# ---- one cell ----------------------------------------------------------------------------------------------------------

_WHOLE = re.compile(r"-?\d{1,12}")


def whole_of(value: Any) -> Optional[int]:
    """A whole number as an answer may carry it - an int, a whole Decimal or float, digits as text - or None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, Decimal):
        return int(value) if value.is_finite() and value == value.to_integral_value() and abs(value) < 10 ** 12 else None
    if isinstance(value, float):
        return int(value) if math.isfinite(value) and value.is_integer() and abs(value) < 1e12 else None
    if isinstance(value, str) and _WHOLE.fullmatch(value.strip()):
        return int(value.strip())
    return None


def sort_of(value: Any) -> Optional[int]:
    """A sort code (a number, 26.11), in the report's range and never the merged line's, or None."""
    code = None if _empty(value) else whole_of(value)
    if code is None or code == MERGED_CODE:
        return None
    return code if WHOLE_SORT[0] <= code <= WHOLE_SORT[1] else None


def money_of(value: Any) -> Optional[Decimal]:
    """A debit or a credit as Decimal: an empty cell is 0 (on each row one of the two is empty, 26.11); a number, or a
    number as text (1,234.50, a trailing minus); None when it is neither. Money is never a float (spec §2.1)."""
    if _empty(value):
        return Decimal(0)
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    if isinstance(value, float):
        return Decimal(repr(value)) if math.isfinite(value) else None
    if isinstance(value, str):
        text = value.strip()
        if not _NUMBER_TEXT.fullmatch(text):
            return None
        amount = Decimal(text.strip("-").replace(",", ""))
        return -amount if text.startswith("-") or text.endswith("-") else amount
    return None


def key_text(value: Any) -> Optional[str]:
    """An account key as text - as it came, or a whole number's digits - or None when there is none."""
    if _empty(value) or isinstance(value, bool):
        return None
    if isinstance(value, str):
        return value
    number = whole_of(value)
    return None if number is None else str(number)


def identity(text: str) -> str:
    """One account as the run tells accounts apart, in memory only: NFKC, trimmed, case folded."""
    return unicodedata.normalize("NFKC", text).strip().casefold()


def storable_key(text: str) -> Optional[str]:
    """The key as the map keeps it - the copy's cleaned form (app/books/parse.py), so books_documents.account_key joins to
    it - or None when it may not be kept: the cleaning masked something in it (an ID number, a ח.פ, an email: ADR-0173
    §1), it holds the mask '#' (no card is named by one, app/recon/cards.py), or it holds more than KEY_DIGITS_MAX digits
    whatever their shape (an ID written as an amount, 393374.23, is a shape the cleaning keeps)."""
    cleaned, _refs, masks = clean(text)
    if masks or not cleaned or "#" in cleaned or len(cleaned) > MAX_KEY:
        return None
    if sum(ch.isdigit() for ch in cleaned) > KEY_DIGITS_MAX:
        return None
    return cleaned


def name_of(value: Any) -> Optional[str]:
    """A sort code's name, cleaned like any text from the books, or None."""
    if _empty(value) or isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        return None
    cleaned = clean(str(value))[0]
    if not cleaned:
        return None
    return cleaned[:MAX_NAME].strip() or None


# ---- one month -----------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Line:
    """A kept line: one sort code in one month, or the merged line (MERGED_CODE)."""

    sort_code: int
    debit: Decimal
    credit: Decimal
    rows: int
    accounts: int
    masked_keys: int

    def __repr__(self) -> str:  # never a total: a repr reaches tracebacks
        return f"Line(rows={self.rows}, accounts={self.accounts})"


@dataclass(frozen=True)
class Batch:
    rows: int = 0
    debit: Decimal = Decimal(0)
    credit: Decimal = Decimal(0)

    def __repr__(self) -> str:
        return f"Batch(rows={self.rows})"


@dataclass(repr=False)
class MonthResult:
    """One month of a run: what is stored (lines, names, keys, batch 9998) and what is said (`answer`, counts only)."""

    month: str
    stored: bool = False
    reason: Optional[str] = None
    kind: Optional[str] = None  # WizCloud's KINDS word, with reason "wizcloud"
    vendor: Optional[str] = None  # the vendor's documented word (ledger.VENDOR_WORDS), when it said one
    cut: bool = False
    parts: int = 0
    rows: Optional[int] = None
    sort_codes: Optional[int] = None
    masked_keys: Optional[int] = None
    batch_rows: Optional[int] = None
    kept_lines: Optional[int] = None  # the lines stored, the merged line among them when it is kept
    merged_codes: Optional[int] = None
    merged: Optional[str] = None  # MERGED_STATES
    merged_by: Optional[dict] = None  # MERGE_WHY -> codes
    key_conflicts: int = 0
    unreadable: dict = field(default_factory=dict)
    outside: int = 0
    lines: list = field(default_factory=list)  # Line, kept lines only
    names: dict = field(default_factory=dict)  # sort code -> cleaned name
    keys: dict = field(default_factory=dict)  # storable key -> sort code
    batch: Batch = field(default_factory=Batch)
    computed: bool = False

    def __repr__(self) -> str:
        return f"MonthResult({self.month}, stored={self.stored}, reason={self.reason}, parts={self.parts})"

    @property
    def ready(self) -> bool:
        """Computed whole and not refused: what may replace the month."""
        return self.computed and self.reason is None

    def refuse(self, reason: str, **counts) -> "MonthResult":
        assert reason in MONTH_REASONS, reason
        self.reason = reason
        self.stored = False
        for name, value in counts.items():
            setattr(self, name, value)
        return self

    def drop(self) -> None:
        """Forget what was computed (the totals, names and keys): the answer keeps its counts."""
        self.lines, self.names, self.keys, self.batch = [], {}, {}, Batch(rows=self.batch.rows)

    def answer(self) -> dict:
        out = {"stored": self.stored, "reason": self.reason, "cut": self.cut, "parts": self.parts, "rows": self.rows,
               "sort_codes": self.sort_codes, "kept_lines": self.kept_lines, "merged_codes": self.merged_codes,
               "merged": self.merged, "merged_by": self.merged_by, "masked_keys": self.masked_keys,
               "batch_9998_rows": self.batch_rows}
        if self.kind is not None:
            out["kind"] = self.kind
        if self.vendor is not None:
            out["vendor"] = self.vendor
        if self.unreadable:
            out["unreadable"] = {word: self.unreadable[word] for word in FIELDS if self.unreadable.get(word)}
        if self.outside:
            out["outside"] = self.outside
        if self.key_conflicts:
            out["key_conflicts"] = self.key_conflicts
        return out


class _Reader:
    """The cells the totals read, out of one answer's rows: by the definition's column id, caption or place
    (app/books/ledger.py `_Table`)."""

    def __init__(self, rows: list, definition: Definition):
        table = _Table(rows, definition)
        self.read: dict = {}
        for caption in NEEDED + (SORT_NAME_CAPTION,):
            column = table.by_caption.get(caption)
            self.read[caption] = table.reader(column) if column is not None else None

    def missing(self) -> list:
        return [caption for caption in NEEDED if self.read[caption] is None]

    def get(self, caption: str, row: Any) -> Any:
        read = self.read[caption]
        return None if read is None else read(row)


def _dominated(sums: list) -> bool:
    """Rule (b) of a thin side: its total T is not zero, and one account's sum, or the sum of two accounts, lies strictly
    within REST_SHARE of it, |T - s| < REST_SHARE * |T|. `sums` are the side's non-zero sums, one per account. Every one
    and every two accounts are searched, not only the largest: a sum can be negative, so two accounts that cancel can
    stand between the largest and the total. Sorted once: the singles in one pass, and for each account the partners that
    bring it into the window by bisection, O(n log n)."""
    total = sum(sums, Decimal(0))
    if total == 0:
        return False
    room = REST_SHARE * abs(total)
    low, high = total - room, total + room  # s is near when low < s < high
    values = sorted(sums)
    if any(low < value < high for value in values):
        return True
    for index, value in enumerate(values):
        first = bisect_right(values, low - value)  # the partners w with low - value < w < high - value
        end = bisect_left(values, high - value)
        if end - first > 1 or (end - first == 1 and first != index):  # a partner that is another account
            return True
    return False


def _thin(accounts: dict) -> bool:
    """Whether a side - the debit or the credit - is thin: (a) filled by some accounts but fewer than MIN_ACCOUNTS, or (b)
    held by one or two of them to within REST_SHARE of its total (`_dominated`). An account fills a side when its sum on
    that side is not zero; a side no account fills is empty and exposes no one."""
    for index in (0, 1):
        sums = [held[index] for held in accounts.values() if held[index] != 0]
        if 0 < len(sums) < MIN_ACCOUNTS or _dominated(sums):
            return True
    return False


def _stands(accounts: dict) -> bool:
    """Whether a line of these accounts may be kept: MIN_ACCOUNTS accounts or more, and neither side thin."""
    return len(accounts) >= MIN_ACCOUNTS and not _thin(accounts)


def _fold(into: dict, accounts: dict) -> None:
    """Add a line's accounts' own sums into the merged line's, account by account."""
    for who, sums in accounts.items():
        held = into.setdefault(who, [Decimal(0), Decimal(0)])
        held[0] += sums[0]
        held[1] += sums[1]


def _total(accounts: dict, index: int) -> Decimal:
    return sum((sums[index] for sums in accounts.values()), Decimal(0))


class _Month:
    """The month being summed: lines by sort code, each with its accounts' own sums (in memory only, for the merge),
    batch 9998 apart, and the whole month's debit and credit for the balance."""

    def __init__(self, month: str):
        first = month_bounds(month)[0]
        self.asked = (first.year, first.month)
        self.rows = 0
        self.lines: dict = {}  # sort code -> [rows, {identity: [debit, credit]}]
        self.masked: set = set()  # identities whose key the map may not keep
        self.batch_rows = 0
        self.batch_debit = Decimal(0)
        self.batch_credit = Decimal(0)
        self.names: dict = {}  # sort code -> Counter of names
        self.keys: dict = {}  # storable key -> set of sort codes
        self.debit = Decimal(0)
        self.credit = Decimal(0)
        self.unreadable: Counter = Counter()
        self.outside = 0
        self._storable: dict = {}  # raw key -> storable key or None: the cleaning runs once a key
        self._names: dict = {}

    @staticmethod
    def codes_in(rows: list, reader: _Reader, low: int, high: int) -> list:
        """The distinct readable sort codes of a cut answer, in its part's range, sorted: where a split may fall."""
        codes = {sort_of(reader.get(SORT_CAPTION, row)) for row in rows}
        return sorted(c for c in codes if c is not None and low <= c <= high)

    def add(self, rows: list, reader: _Reader, low: int, high: int) -> Optional[str]:
        """Sum one part's rows; a reason (MONTH_REASONS) when the month cannot be stored."""
        for row in rows:
            self.rows += 1
            when = reader.get(DATE_CAPTION, row)
            month = _month_of(when)[1] if isinstance(when, str) else None
            code = sort_of(reader.get(SORT_CAPTION, row))
            raw = key_text(reader.get(KEY_CAPTION, row))
            debit = money_of(reader.get(DEBIT_CAPTION, row))
            credit = money_of(reader.get(CREDIT_CAPTION, row))
            cell = reader.get(BATCH_CAPTION, row)
            batch = None if _empty(cell) else whole_of(cell)
            bad = [word for word, value in zip(FIELDS[:5], (month, code, raw, debit, credit)) if value is None]
            if batch is None and not _empty(cell):
                bad.append("batch")
            if bad:
                self.unreadable.update(bad)
                continue
            if month != self.asked or not low <= code <= high:
                self.outside += 1
                continue
            self._sum(row, reader, code, raw, debit, credit, batch == TRANSFER_BATCH)
        if self.unreadable:
            return "unreadable"
        if self.outside:
            return "outside"
        return None

    def _sum(self, row: Any, reader: _Reader, code: int, raw: str, debit: Decimal, credit: Decimal, transfer: bool) -> None:
        self.debit += debit
        self.credit += credit
        if raw not in self._storable:
            self._storable[raw] = storable_key(raw)
        kept = self._storable[raw]
        who = identity(raw)
        if kept is None:
            self.masked.add(who)
        else:
            self.keys.setdefault(kept, set()).add(code)
        name = reader.get(SORT_NAME_CAPTION, row)
        if not _empty(name):
            text = str(name)
            if text not in self._names:
                self._names[text] = name_of(name)
            if self._names[text]:
                self.names.setdefault(code, Counter())[self._names[text]] += 1
        if transfer:
            self.batch_rows += 1
            self.batch_debit += debit
            self.batch_credit += credit
            return
        line = self.lines.setdefault(code, [0, {}])
        line[0] += 1
        sums = line[1].setdefault(who, [Decimal(0), Decimal(0)])
        sums[0] += debit
        sums[1] += credit

    def balanced(self) -> bool:
        return self.debit == self.credit

    def counts(self, result: MonthResult) -> MonthResult:
        """What the month says whatever the salary list: its rows, its codes, its masked keys, batch 9998's rows."""
        result.rows = self.rows
        result.sort_codes = len(self.lines)
        # the month's accounts outside batch 9998 whose key the map may not keep
        result.masked_keys = len({who for _, accounts in self.lines.values() for who in accounts if who in self.masked})
        result.batch_rows = self.batch_rows
        return result

    def finish(self, result: MonthResult, salary: frozenset) -> MonthResult:
        """The lines to keep: each line that stands on its own and the merged line, or no line at all (the module's
        docstring)."""
        self.counts(result)
        standing, merged, merged_rows, why_counts = [], {}, 0, Counter()
        for code in sorted(self.lines):
            rows, accounts = self.lines[code]
            why = ("salary" if code in salary else "small" if len(accounts) < MIN_ACCOUNTS
                   else "side" if _thin(accounts) else None)
            if why is None:
                standing.append(code)
                continue
            why_counts[why] += 1
            merged_rows += rows
            _fold(merged, accounts)
        stands = True
        if why_counts:
            # the merged line withheld beside lines kept would be minus their nets: while it does not stand, the standing
            # lines go into it, the fewest accounts first, then the lowest code; none left, the month keeps nothing
            standing.sort(key=lambda code: (len(self.lines[code][1]), code))
            stands = _stands(merged)
            while not stands and standing:
                rows, accounts = self.lines[standing.pop(0)]
                why_counts["balance"] += 1
                merged_rows += rows
                _fold(merged, accounts)
                stands = _stands(merged)
        lines = []
        for code in sorted(standing):
            rows, accounts = self.lines[code]
            lines.append(Line(code, _total(accounts, 0), _total(accounts, 1), rows, len(accounts),
                              sum(1 for who in accounts if who in self.masked)))
        if not why_counts:
            result.merged = "none"
        elif stands:
            result.merged = "kept"
            lines.append(Line(MERGED_CODE, _total(merged, 0), _total(merged, 1), merged_rows, len(merged),
                              sum(1 for who in merged if who in self.masked)))
        else:  # every standing line went in, and it still does not stand
            result.merged = "withheld"
        result.lines = lines
        result.kept_lines = len(lines)
        result.merged_codes = sum(why_counts.values())
        result.merged_by = {why: why_counts[why] for why in MERGE_WHY}
        result.batch = Batch(self.batch_rows, self.batch_debit, self.batch_credit)
        result.names = {code: _best(names) for code, names in self.names.items()}
        result.keys = {key: next(iter(codes)) for key, codes in self.keys.items() if len(codes) == 1}
        result.key_conflicts = sum(1 for codes in self.keys.values() if len(codes) > 1)
        result.computed = True
        return result


def _best(names: Counter) -> str:
    """The name a code carried most, the first in order on a tie."""
    return sorted(names.items(), key=lambda item: (-item[1], item[0]))[0][0]


Call = Callable[[list, float], Any]


def read_month(call: Call, definition: Definition, month: str, deadline: float,
               clock: Callable[[], float] = time.monotonic) -> tuple:
    """(MonthResult, _Month): one month read whole, balanced and summed in memory - or (refused MonthResult, None).
    `call(parameters, deadline)` runs the report (WizClient.export_report with the definition's datafile). A part that
    reaches the limit is split by sort code (the module's docstring); every answer is summed and dropped as it comes."""
    result = MonthResult(month)
    acc = _Month(month)
    pending = [WHOLE_SORT]
    while pending:
        low, high = pending.pop(0)
        if result.parts >= MAX_PARTS:
            return result.refuse("cut", cut=True), None
        if deadline - clock() <= MIN_CALL_S + MARGIN_S:
            return result.refuse("out_of_time"), None
        result.parts += 1
        try:
            answer = call(month_parameters(definition, month, low, high), deadline)
        except DeadlineReached:
            return result.refuse("out_of_time"), None
        except WizError as exc:
            word = vendor_word(exc.message)
            if word == "data_not_found":
                continue  # no rows in this part
            return result.refuse("wizcloud", kind=exc.kind, vendor=word), None
        rows = find_rows(answer)[0]
        if rows is None:
            word = _vendor_in(answer)
            if word == "data_not_found":
                continue
            return result.refuse("bad_answer", vendor=word), None
        del answer
        if not rows:
            continue
        reader = _Reader(rows, definition)
        if reader.missing():
            return result.refuse("columns"), None
        if len(rows) >= definition.limit:  # hit_limit: this part may be cut
            codes = acc.codes_in(rows, reader, low, high)
            del rows, reader
            if len(codes) < 2:
                return result.refuse("cut", cut=True), None
            middle = codes[len(codes) // 2 - 1]
            pending[:0] = [(low, middle), (middle + 1, high)]
            continue
        problem = acc.add(rows, reader, low, high)
        del rows, reader
        if problem:
            return result.refuse(problem, unreadable=dict(acc.unreadable), outside=acc.outside, rows=acc.rows), None
    if not acc.balanced():
        return result.refuse("unbalanced", rows=acc.rows), None
    return result, acc


# ---- the run -------------------------------------------------------------------------------------------------------------


def _reasons(words) -> list:
    words = set(words)
    return [word for word in RUN_REASONS if word in words]


def _definition(text: Optional[str]) -> tuple:
    """(Definition, None), or (None, the answer of a run that cannot run): not_set, bad_definition, no_limit."""
    if not (text or "").strip():
        return None, {"ok": False, "skipped": False, "reasons": ["not_set"], "months": {},
                      "detail": "WIZ_LEDGER_REPORT is not set (a Replit Secret: the External file of the report, whole)"}
    try:
        definition = load_definition(text)
    except DefinitionError as exc:
        return None, {"ok": False, "skipped": False, "reasons": ["bad_definition"], "definition": exc.reason, "months": {}}
    if definition.limit is None:
        return None, {"ok": False, "skipped": False, "reasons": ["no_limit"], "months": {},
                      "detail": "the report's External file names no limit: an answer cut short could not be told"}
    return definition, None


def _caller(client, definition: Definition) -> Call:
    return lambda parameters, until: client.export_report(definition.datafile, parameters, deadline=until)


def run(client, store, company: str, months: list, text: Optional[str], deadline: float, trigger: str = "http",
        clock: Callable[[], float] = time.monotonic, salary_text: Optional[str] = None) -> dict:
    """One run: each month asked, read whole and replaced, in order, inside the box (`deadline`, time.monotonic). Always a
    dict with `ok`, `skipped`, `reasons` (RUN_REASONS) and per month its counts (MonthResult.answer). `store` is a
    LedgerStore; a second run of the company is skipped. No session is opened for a run that cannot run. Without the
    salary list (`salary_text`, LEDGER_SALARY_CODES) the months are read and counted, and nothing of them is stored."""
    definition, refused = _definition(text)
    if refused is not None:
        return refused
    state, salary = salary_codes(salary_text)
    from .ledger_store import LedgerStoreError

    with store.session(company) as held:
        if held is None:
            return {"ok": True, "skipped": True, "reasons": [], "months": {},
                    "detail": "another ledger run of this company is in progress"}
        run_id = held.start_run(company, trigger, months)
        out: dict = {}
        try:
            with client.connection():
                for month in months:
                    result, acc = read_month(_caller(client, definition), definition, month, deadline, clock)
                    if acc is not None and state != "set":
                        acc.counts(result).refuse(f"salary_codes_{'not_set' if state == 'missing' else 'bad'}")
                    elif acc is not None:
                        acc.finish(result, salary)
                        try:
                            why = held.replace_month(company, run_id, result, budget_s=deadline - clock())
                        except LedgerStoreError:
                            why = "store"
                        if why is None:
                            result.stored = True
                        else:
                            result.refuse(why)
                    del acc
                    out[month] = result.answer()
                    result.drop()
        except Exception as exc:
            try:
                held.finish_run(run_id, False, ["crashed"], f"the run stopped on {type(exc).__name__}")
            except Exception:  # noqa: BLE001 - the run's own error is the one said
                pass
            raise
        reasons = _reasons(answer["reason"] for answer in out.values() if answer["reason"])
        ok = not reasons
        held.finish_run(run_id, ok, reasons)
    return {"ok": ok, "skipped": False, "reasons": reasons, "run_id": run_id, "months": out}


def listing(client, months: list, text: Optional[str], deadline: float, clock: Callable[[], float] = time.monotonic,
            salary_text: Optional[str] = None) -> dict:
    """The dry listing: the months read whole the way a run reads them, and nothing stored, logged or kept. Per sort code
    seen outside batch 9998 in those months: its name, its distinct accounts (masked keys counted too, and how many), and,
    when the salary list is set, whether it is on it - categories and counts, never an amount. A month that cannot be read
    whole says why and lists nothing."""
    definition, refused = _definition(text)
    if refused is not None:
        return {**refused, "dry": True}
    state, salary = salary_codes(salary_text)
    seen: dict = {}  # sort code -> [identities, masked identities, Counter of names]
    out: dict = {}
    with client.connection():
        for month in months:
            result, acc = read_month(_caller(client, definition), definition, month, deadline, clock)
            if acc is not None:
                acc.counts(result)
                for code, (_rows, accounts) in acc.lines.items():
                    entry = seen.setdefault(code, [set(), set(), Counter()])
                    entry[0].update(accounts)
                    entry[1].update(who for who in accounts if who in acc.masked)
                    entry[2].update(acc.names.get(code, {}))
            del acc
            answer = result.answer()
            out[month] = {"read": result.reason is None, "reason": result.reason, "cut": result.cut, "parts": result.parts,
                          "rows": result.rows, "sort_codes": result.sort_codes,
                          **{k: answer[k] for k in ("kind", "vendor", "unreadable", "outside") if k in answer}}
    codes = []
    for code in sorted(seen):
        who, masked, names = seen[code]
        entry = {"sort_code": code, "name": _best(names) if names else None, "accounts": len(who), "masked_keys": len(masked)}
        if state == "set":
            entry["salary"] = code in salary
        codes.append(entry)
    seen.clear()
    reasons = _reasons(answer["reason"] for answer in out.values() if answer["reason"])
    answer = {"ok": not reasons, "dry": True, "reasons": reasons, "salary_codes": state, "months": out, "codes": codes}
    if state == "set":
        answer["salary_codes_unseen"] = len(salary - {entry["sort_code"] for entry in codes})
    return answer
