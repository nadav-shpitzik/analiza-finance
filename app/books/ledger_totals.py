"""The ledger report's totals (backlog 0301 B3; ADR-0187 §2-§5; spec §3.5.3): a run reads whole months of "כרטסת לאנליזה"
(app/books/ledger.py) and keeps only category totals, never a row (app/books/ledger_store.py, migrations/007).

What a run keeps, per company:
(a) per month (by "תאריך", the reference's date) and sort code - a line: the debit and credit totals, the rows, the
    distinct accounts, and how many of their keys the map may not keep (`masked_keys`: counted, never kept);
(b) per sort code: its name ("שם קוד מיון"), cleaned like any text from the books (app/books/clean.py, spec §3.4);
(c) per account key: its sort code - only for a key the copy's own cleaning keeps whole and that holds fewer than six
    digits (`storable_key`). **Student cards are keyed by ID numbers (ADR-0173 §1)**, and an ID number is never stored
    server side: such a key is never stored, hashed or kept in any form. It is only counted, per line and per month. The
    copy masks the same keys (app/books/parse.py), so every key the copy can name (app/recon/cards.py `card_of`) is a key
    the map can hold, in the same cleaned form.
Never kept: an account's name, details, a reference, a counter-account, a cost code, or one account's amount.

**One person's money is never stored alone** (ADR-0187 "שארית ידועה": a code that holds one person's account exposes their
monthly amount). A line whose sort code holds one account in the month is not stored at all; it is counted as
`single_account`. And a side of a kept line - its debit or its credit total - that a single account alone fills is not
stored either (NULL, counted as `single_sides`): with two accounts on a code, one debited and one credited, each total would
be one account's own. What this does not stop, and the report of 26.11 says: the books balance, so when a month withholds
exactly one line, its net follows from the kept lines'.

**Batch 9998** (the income and expense transfer at the end of a year or a quarter, which zeroes the expense cards) never
enters a line: its rows and totals are counted apart, per month (ADR-0187 §5), even though the report is defined without
income transfers. Its debit equals its credit, so neither side is withheld.

**A run recomputes whole months and replaces them** (ADR-0187 §4), one transaction per month (LedgerStore.replace_month).
A month is replaced only when it was read whole:
1. every part answered under the report's `limit`. A part that reaches it (`hit_limit`) is split in two by sort code, at
   the median of the codes it carried, until every part is under the limit; a part of one sort code at the limit, or more
   than MAX_PARTS calls, is `cut`. The sort-code cut narrower than the whole range is unverified live (only the whole
   range was sent, 26.11), so a part whose rows fall outside its range is `outside`, never summed.
2. every row readable: a date in the month asked, a sort code in its part's range, a key, numeric debit and credit, a
   batch that is a whole number when it is there (`unreadable`, `outside`); and the rows carry every column the totals
   read (`columns`).
3. the month balances: its debits equal its credits (`unbalanced`). Every transaction balances and the date cut takes
   whole transactions, so a month that does not balance was not read whole. This is the one check that does not depend
   on knowing the cap the vendor applies: the External file's `limit` is not sent (app/books/ledger.py), so an answer cut
   by a lower cap of the server's would pass `hit_limit`. A hypothesis the first run settles.
4. an answer of no rows ("data not found") for a month the store holds rows of is doubted, not trusted (`emptied`).
The rows live only in the run's memory (ADR-0187 §3): read, summed and dropped, never logged, kept or returned. The run's
answer is counts and closed words; no message here ever carries a value of a row.
"""

from __future__ import annotations

import math
import re
import time
import unicodedata
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
MIN_ACCOUNTS = 2  # a line, or a side of one, filled by fewer accounts is never stored
KEY_DIGITS_MAX = 5  # a key with more digits is never stored, whatever shape the cleaning gave it
MAX_KEY = 64
MAX_NAME = 200
MAX_MONTHS = 12
MAX_PARTS = 16  # report calls for one month: a split that needs more is `cut`
MARGIN_S = 1.0
# Why a month was not stored. The run's reasons are these, after the run's own.
MONTH_REASONS = ("cut", "out_of_time", "wizcloud", "bad_answer", "columns", "unreadable", "outside", "unbalanced",
                 "emptied", "store")
RUN_REASONS = ("bad_request", "not_set", "bad_definition", "no_limit", "crashed") + MONTH_REASONS
# The fields a row is unreadable in (`unreadable`), as closed words.
FIELDS = ("date", "sort_code", "key", "debit", "credit", "batch")


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
    """A sort code (a number, 26.11), in the report's range, or None."""
    code = None if _empty(value) else whole_of(value)
    return code if code is not None and WHOLE_SORT[0] <= code <= WHOLE_SORT[1] else None


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
    """A kept line: one sort code in one month. `debit` or `credit` None: that side was one account's alone."""

    sort_code: int
    debit: Optional[Decimal]
    credit: Optional[Decimal]
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
    single_account: Optional[int] = None
    single_sides: Optional[int] = None
    masked_keys: Optional[int] = None
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
               "sort_codes": self.sort_codes, "kept_lines": len(self.lines) if self.computed else None,
               "single_account": self.single_account, "single_sides": self.single_sides, "masked_keys": self.masked_keys,
               "batch_9998_rows": self.batch.rows if self.computed else None}
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


def _side(accounts: dict, index: int) -> Optional[Decimal]:
    """One side's total, or None when a single account alone fills it (MIN_ACCOUNTS)."""
    total = sum((sums[index] for sums in accounts.values()), Decimal(0))
    filled = sum(1 for sums in accounts.values() if sums[index] != 0)
    return None if 0 < filled < MIN_ACCOUNTS else total


class _Month:
    """The month being summed: lines by sort code, each with its accounts' own sums (in memory only, for the
    single-account rules), batch 9998 apart, and the whole month's debit and credit for the balance."""

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

    def finish(self, result: MonthResult) -> MonthResult:
        """The lines to keep, once every part was read: or `unbalanced`."""
        result.rows = self.rows
        if self.debit != self.credit:
            return result.refuse("unbalanced")
        lines, single, sides = [], 0, 0
        for code in sorted(self.lines):
            rows, accounts = self.lines[code]
            if len(accounts) < MIN_ACCOUNTS:
                single += 1
                continue
            debit, credit = _side(accounts, 0), _side(accounts, 1)
            sides += (debit is None) + (credit is None)
            lines.append(Line(code, debit, credit, rows, len(accounts), sum(1 for who in accounts if who in self.masked)))
        result.lines = lines
        result.sort_codes = len(self.lines)
        result.single_account = single
        result.single_sides = sides
        # the month's accounts outside batch 9998 whose key the map may not keep
        result.masked_keys = len({who for _, accounts in self.lines.values() for who in accounts if who in self.masked})
        result.batch = Batch(self.batch_rows, self.batch_debit, self.batch_credit)
        result.names = {code: sorted(names.items(), key=lambda item: (-item[1], item[0]))[0][0]
                        for code, names in self.names.items()}
        result.keys = {key: next(iter(codes)) for key, codes in self.keys.items() if len(codes) == 1}
        result.key_conflicts = sum(1 for codes in self.keys.values() if len(codes) > 1)
        result.computed = True
        return result


Call = Callable[[list, float], Any]


def read_month(call: Call, definition: Definition, month: str, deadline: float,
               clock: Callable[[], float] = time.monotonic) -> MonthResult:
    """One month read whole and summed, or refused (MonthResult.reason). `call(parameters, deadline)` runs the report
    (WizClient.export_report with the definition's datafile). A part that reaches the limit is split by sort code
    (the module's docstring); every answer is summed and dropped as it comes."""
    result = MonthResult(month)
    acc = _Month(month)
    pending = [WHOLE_SORT]
    while pending:
        low, high = pending.pop(0)
        if result.parts >= MAX_PARTS:
            return result.refuse("cut", cut=True)
        if deadline - clock() <= MIN_CALL_S + MARGIN_S:
            return result.refuse("out_of_time")
        result.parts += 1
        try:
            answer = call(month_parameters(definition, month, low, high), deadline)
        except DeadlineReached:
            return result.refuse("out_of_time")
        except WizError as exc:
            word = vendor_word(exc.message)
            if word == "data_not_found":
                continue  # no rows in this part
            return result.refuse("wizcloud", kind=exc.kind, vendor=word)
        rows = find_rows(answer)[0]
        if rows is None:
            word = _vendor_in(answer)
            if word == "data_not_found":
                continue
            return result.refuse("bad_answer", vendor=word)
        del answer
        if not rows:
            continue
        reader = _Reader(rows, definition)
        if reader.missing():
            return result.refuse("columns")
        if len(rows) >= definition.limit:  # hit_limit: this part may be cut
            codes = acc.codes_in(rows, reader, low, high)
            del rows, reader
            if len(codes) < 2:
                return result.refuse("cut", cut=True)
            middle = codes[len(codes) // 2 - 1]
            pending[:0] = [(low, middle), (middle + 1, high)]
            continue
        problem = acc.add(rows, reader, low, high)
        del rows, reader
        if problem:
            return result.refuse(problem, unreadable=dict(acc.unreadable), outside=acc.outside, rows=acc.rows)
    return acc.finish(result)


# ---- the run -------------------------------------------------------------------------------------------------------------


def _reasons(words) -> list:
    words = set(words)
    return [word for word in RUN_REASONS if word in words]


def run(client, store, company: str, months: list, text: Optional[str], deadline: float, trigger: str = "http",
        clock: Callable[[], float] = time.monotonic) -> dict:
    """One run: each month asked, read whole and replaced, in order, inside the box (`deadline`, time.monotonic). Always a
    dict with `ok`, `skipped`, `reasons` (RUN_REASONS) and per month its counts (MonthResult.answer). `store` is a
    LedgerStore; a second run of the company is skipped. No session is opened for a run that cannot run."""
    if not (text or "").strip():
        return {"ok": False, "skipped": False, "reasons": ["not_set"], "months": {},
                "detail": "WIZ_LEDGER_REPORT is not set (a Replit Secret: the External file of the report, whole)"}
    try:
        definition = load_definition(text)
    except DefinitionError as exc:
        return {"ok": False, "skipped": False, "reasons": ["bad_definition"], "definition": exc.reason, "months": {}}
    if definition.limit is None:
        return {"ok": False, "skipped": False, "reasons": ["no_limit"], "months": {},
                "detail": "the report's External file names no limit: an answer cut short could not be told"}
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
                    result = read_month(
                        lambda parameters, until: client.export_report(definition.datafile, parameters, deadline=until),
                        definition, month, deadline, clock)
                    if result.ready:
                        try:
                            why = held.replace_month(company, run_id, result, budget_s=deadline - clock())
                        except LedgerStoreError:
                            why = "store"
                        if why is None:
                            result.stored = True
                        else:
                            result.refuse(why)
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
