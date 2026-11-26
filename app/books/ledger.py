"""The ledger report (backlog 0301 B3, R-L; ADR-0187): its definition, the parameters of one month, and an answer's shape.

"כרטסת לאנליזה" is defined once by Chava in Hashavshevet's report designer and exported as "External": one JSON object
(spec §4.4) - `version`, `datafile` (hex, the report's structure), `columns_params` [{id, caption, visible}], `parameters`,
`order_by_parameters`, `limit`, `offset`, `flags`. It is the Replit Secret WIZ_LEDGER_REPORT, the whole JSON as one
string, never in git (ADR-0187 §6). The service runs it through `ExportDataApi/exportData` with its own dates and sort codes.

Nothing here keeps a row (ADR-0187 §3: the rows live only in a run's memory). This slice is the probe: one run, and
`describe` says the answer's shape - column names and counts, never a value from a row.

**The request, from the vendor's own documents (read 26.11).** The chapter "Export Report Data" of "Wizcloud API
Documentation.docx" in the vendor's npm client wizcloud-api, the same in all 16 versions (1.0.0 to 1.0.15), and its
ExportDataApiExample.js: the body is `{datafile, parameters}`, and `parameters` is the External file's list as exported,
every entry whole - "you should update one property value only on each property object. The property "defVal" includes a
default value for this property and this is the place to change it to the value that you want". A `long` goes bare ("100"
for "-999999999", "399" for "999999999": the docx, and the example's sort code cut in 1.0.0 to 1.0.11), a `txt` in single
quotes ("'777'": the example's bank account cut in 1.0.12 to 1.0.15). **The vendor shows no date.** Here a date is written
the way the External file writes its own default (`'2000-01-01'`), as `'2026-10-01'`: that form is a hypothesis the first
live answer settles. The vendor documents "params not found or not match" for parameters it cannot use (`vendor_word`:
`params_mismatch`), and a date cut it ignored shows in `describe` as more than one month, or as rows outside the month
asked. The body goes as JSON, as getDoc's does (seen live); the old npm client posts a form. The
External file's `limit`, `offset`, `columns_params`, `order_by_parameters` and `flags` are newer than the vendor's documents
and are not sent: if the live answer ignores the report's limit or its columns, sending them is the next hypothesis.

**The answer** is documented only as "repdata : report data found by report", with the statuses "No Permission" and "data
not found". Its shape has never been seen: `describe` reads it defensively and says what it found, in closed words and
counts. A key is said by name only when it is the definition's (a column's id or caption) or a plain field name
(`nameable`); any other key is counted, so a value that sits in a key's place is never said.
"""

from __future__ import annotations

import calendar
import json
import math
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Optional

from ..wizcloud import KINDS, WizError, mask_overlaps
from .parse import _FIELD_NAME

# ---- the definition ----------------------------------------------------------------------------------------------------

# The four parameters, found by name and origin, never by position. A name may be a path (the vendor's example:
# "חתך/שדה מרשומת החשבון/1. פרטי חשבון/קוד מיון"; Chava's file: "קוד מיון"), so its last segment is compared. The "to" half
# of a "מ..עד" cut carries the name with a "1" appended (spec §4.4: "קוד מיון1", "תאריך1").
SORT_NAME = "קוד מיון"
DATE_NAME = "תאריך"
SLOTS = {  # slot: (name, opOrigin, type)
    "sort_from": (SORT_NAME, "from", "long"),
    "sort_to": (SORT_NAME, "to", "long"),
    "date_from": (DATE_NAME, "from", "date"),
    "date_to": (DATE_NAME, "to", "date"),
}
# A `long` cut's whole range: the External file's own defaults and the vendor's example, so every sort code is in.
WHOLE_SORT = (-999999999, 999999999)

# Why a definition cannot be used: the only words load_definition answers. Never a field's value.
REASONS = (
    "missing",  # WIZ_LEDGER_REPORT is not set, or holds only spaces
    "not_json",  # not JSON (a part of the file, or a paste with something around it?)
    "not_object",  # JSON, but not one object
    "no_datafile",  # no `datafile`, or not a text
    "no_parameters",  # no `parameters` list of objects: the cuts were not saved as parameters
    "no_sort_from",  # no "קוד מיון" with opOrigin "from"
    "no_sort_to",  # no "קוד מיון1" with opOrigin "to"
    "no_date_from",  # no "תאריך" with opOrigin "from"
    "no_date_to",  # no "תאריך1" with opOrigin "to"
    "parameter_twice",  # two entries answer one of the four
    "parameter_type",  # a sort code that is not `long`, or a date that is not `date`: its value's form is unknown
)


class DefinitionError(ValueError):
    """The definition cannot be used; `reason` is one of REASONS, and the message is the reason alone."""

    def __init__(self, reason: str):
        assert reason in REASONS, reason
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class Column:
    id: str
    caption: str
    visible: bool


@dataclass(frozen=True, repr=False, eq=False)
class Definition:
    """The External file, as the probe uses it. Its repr says sizes only: `datafile` is the report itself."""

    datafile: str
    parameters: tuple  # the file's entries, as exported (dicts, never changed: month_parameters copies them)
    slots: dict  # slot (SLOTS) -> index into parameters
    columns: tuple  # Column per columns_params entry, in the file's order
    limit: Optional[int]

    def __repr__(self) -> str:
        return f"Definition(parameters={len(self.parameters)}, columns={len(self.columns)}, limit={self.limit})"


def _base(name: Any) -> str:
    return str(name or "").split("/")[-1].strip()


def _word(value: Any) -> str:
    return str(value or "").strip().lower()


def _answers(entry: dict, name: str, origin: str) -> bool:
    if _word(entry.get("opOrigin")) != origin:
        return False
    base = _base(entry.get("name"))
    return base == name or (origin == "to" and base == name + "1")


def _visible(value: Any) -> bool:
    if value is None or value is True:
        return True
    return str(value).strip().lower() in ("true", "1", "yes")


def _limit(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    text = str(value or "").strip()
    return int(text) if text.isdigit() and int(text) > 0 else None


def load_definition(text: Optional[str]) -> Definition:
    """The Secret's JSON as a Definition, or DefinitionError with a closed reason. The four parameters are found by name and
    opOrigin (SLOTS), wherever they sit in the list; every other entry is kept as exported and sent as is."""
    raw = (text or "").strip().lstrip("\ufeff").strip()
    if not raw:
        raise DefinitionError("missing")
    try:
        data = json.loads(raw)
    except (ValueError, RecursionError):
        raise DefinitionError("not_json") from None
    if not isinstance(data, dict):
        raise DefinitionError("not_object")
    datafile = data.get("datafile")
    if not isinstance(datafile, str) or not datafile.strip():
        raise DefinitionError("no_datafile")
    params = data.get("parameters")
    if not isinstance(params, list) or not params or not all(isinstance(p, dict) for p in params):
        raise DefinitionError("no_parameters")
    slots = {}
    for slot, (name, origin, kind) in SLOTS.items():
        hits = [i for i, entry in enumerate(params) if _answers(entry, name, origin)]
        if not hits:
            raise DefinitionError(f"no_{slot}")
        if len(hits) > 1:
            raise DefinitionError("parameter_twice")
        if _word(params[hits[0]].get("type")) != kind:
            raise DefinitionError("parameter_type")
        slots[slot] = hits[0]
    columns = tuple(
        Column(id=str(c.get("id") or "").strip(), caption=str(c.get("caption") or "").strip(),
               visible=_visible(c.get("visible")))
        for c in (data.get("columns_params") if isinstance(data.get("columns_params"), list) else [])
        if isinstance(c, dict)
    )
    return Definition(datafile=datafile.strip(), parameters=tuple(dict(p) for p in params), slots=slots, columns=columns,
                      limit=_limit(data.get("limit")))


def definition_state(text: Optional[str]) -> str:
    """What /health says: "set", "missing" or "bad". Never a field of the definition, never why (the probe says why)."""
    if not (text or "").strip():
        return "missing"
    try:
        load_definition(text)
    except DefinitionError:
        return "bad"
    return "set"


# ---- the parameters of one month ---------------------------------------------------------------------------------------

_MONTH = re.compile(r"(\d{4})-(\d{2})")
_SORT_CODE = re.compile(r"-?\d{1,9}")


def month_bounds(month: str) -> tuple:
    """(the first day, the last day) of "YYYY-MM", or ValueError."""
    m = _MONTH.fullmatch(str(month or "").strip())
    if not m or not 2000 <= int(m.group(1)) <= 2099 or not 1 <= int(m.group(2)) <= 12:
        raise ValueError("month must be YYYY-MM")
    year, mon = int(m.group(1)), int(m.group(2))
    return date(year, mon, 1), date(year, mon, calendar.monthrange(year, mon)[1])


def _sort_code(value: Any) -> int:
    text_ok = isinstance(value, str) and _SORT_CODE.fullmatch(value.strip())
    if isinstance(value, bool) or not (isinstance(value, int) or text_ok):
        raise ValueError("a sort code is a whole number")
    code = int(value)
    if not WHOLE_SORT[0] <= code <= WHOLE_SORT[1]:
        raise ValueError("a sort code is out of the report's range")
    return code


def month_parameters(definition: Definition, month: str, sort_from: Any = None, sort_to: Any = None) -> list:
    """The `parameters` for one calendar month: the file's entries whole, in its order, with only `defVal` changed in the
    four (the vendor's documented shape; the module's docstring). The dates are the month's first and last day, quoted as
    the file quotes its own (`'2026-10-01'`, `'2026-10-31'`: the hypothesis); the sort codes bare, by default the whole
    range, so every sort code is included. The definition itself is never changed."""
    first, last = month_bounds(month)
    low = WHOLE_SORT[0] if sort_from is None else _sort_code(sort_from)
    high = WHOLE_SORT[1] if sort_to is None else _sort_code(sort_to)
    if low > high:
        raise ValueError("sort_from is above sort_to")
    values = {"sort_from": str(low), "sort_to": str(high), "date_from": f"'{first.isoformat()}'",
              "date_to": f"'{last.isoformat()}'"}
    out = [dict(entry) for entry in definition.parameters]
    for slot, index in definition.slots.items():
        out[index]["defVal"] = values[slot]
    return out


# ---- the shape of an answer --------------------------------------------------------------------------------------------

# The columns describe() reads, by caption (columns_params), never by their place in the definition.
DATE_CAPTION = "תאריך"
SORT_CAPTION = "קוד מיון"
DEBIT_CAPTION = "חובה שקל"
CREDIT_CAPTION = "זכות שקל"
# Where rows are looked for first: `repdata` is the vendor's word, the rest what other answers use (app/main.py `_items`).
ROW_KEYS = ("repdata", "data", "rows", "result", "items", "records")
MAX_DEPTH = 3
# The closed words of describe(): a value's type ("_in_text" added when it was JSON inside a string; "mixed" for a column
# of more than one), the form of a date, and how the rows name their columns.
TYPE_WORDS = ("null", "bool", "number", "text", "object", "list", "other", "mixed")
DATE_FORMS = (  # word, pattern (whole, stripped), order of the parts
    ("iso", re.compile(r"(\d{4})-(\d{2})-(\d{2})"), "ymd"),
    ("iso_time", re.compile(r"(\d{4})-(\d{2})-(\d{2})[T ][0-9:.]+(?:Z|[+-]\d{2}:?\d{2})?"), "ymd"),
    ("dmy_slash", re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})"), "dmy"),
    ("dmy_dot", re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{4})"), "dmy"),
    ("dmy_dash", re.compile(r"(\d{1,2})-(\d{1,2})-(\d{4})"), "dmy"),
    ("dmy_slash_yy", re.compile(r"(\d{1,2})/(\d{1,2})/(\d{2})"), "dmy"),
    ("ymd_digits", re.compile(r"(\d{4})(\d{2})(\d{2})"), "ymd"),
)
BY_WORDS = ("id", "caption", "position", "other", "mixed")
# The vendor's documented statuses and error (wizcloud-api's docx), as closed words.
VENDOR_WORDS = {
    "params_mismatch": "params not found or not match",
    "data_not_found": "data not found",
    "no_permission": "no permission",
}
# A number as text: 1234, -1234.50, 1,234.50, or a trailing minus (1234.50-).
_NUMBER_TEXT = re.compile(r"-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?-")


def nameable(key: Any) -> bool:
    """A key the answer may say by name: a plain identifier with at most two digits, the rule of app/books/parse.py's
    `fields_seen` (review 22.11: a key like ID_123456789 is counted, never kept). Anything else is counted."""
    return isinstance(key, str) and bool(_FIELD_NAME.fullmatch(key)) and sum(ch.isdigit() for ch in key) <= 2


def type_word(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float, Decimal)):
        return "number"
    if isinstance(value, str):
        return "text"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "list"
    return "other"


def vendor_word(text: Any) -> Optional[str]:
    """The vendor's documented status or error a text carries, as a closed word, or None."""
    lowered = str(text or "").lower()
    return next((word for word, phrase in VENDOR_WORDS.items() if phrase in lowered), None)


def _open(value: Any) -> tuple:
    """(value, opened): JSON inside a string, opened (the vendor nests JSON in strings: app/main.py `_maybe_json`)."""
    if isinstance(value, str) and value.strip()[:1] in ("[", "{"):
        try:
            return json.loads(value, parse_float=Decimal), True
        except (ValueError, RecursionError):
            return value, False
    return value, False


def _is_rows(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(row, (dict, list)) for row in value)


def find_rows(answer: Any) -> tuple:
    """(rows, path, in_text): the answer's list of rows - a list of objects or of lists, empty included - or (None, None,
    False). A list under `repdata` (the vendor's word) wins at any depth, then a list that holds rows over an empty one,
    the shallower first. The path names a key only when `nameable`, else "*"."""
    found = []
    queue = [(answer, (), False)]
    while queue:
        value, path, in_text = queue.pop(0)
        value, opened = _open(value)
        in_text = in_text or opened
        if _is_rows(value):
            found.append(((path[-1:] != ("repdata",), not value, len(path), len(found)), value, path, in_text))
        elif isinstance(value, dict) and len(path) < MAX_DEPTH:
            order = sorted(value, key=lambda k: ROW_KEYS.index(k) if k in ROW_KEYS else len(ROW_KEYS))
            queue.extend((value[k], path + (k,), in_text) for k in order)
    if not found:
        return None, None, False
    _, rows, path, in_text = min(found, key=lambda item: item[0])
    return rows, [k if nameable(k) else "*" for k in path], in_text


def _top(answer: Any) -> dict:
    """The answer's type, and for an object its keys that can be named, each with its value's type word."""
    out: dict = {"type": type_word(answer)}
    if isinstance(answer, dict):
        named = {}
        for key, value in answer.items():
            if nameable(key):
                inner, opened = _open(value)
                named[key] = type_word(inner) + ("_in_text" if opened else "")
        out["keys"] = dict(sorted(named.items()))
        out["unnamed_keys"] = len(answer) - len(named)
    elif isinstance(answer, list):
        out["length"] = len(answer)
    return out


def _vendor_in(answer: Any) -> Optional[str]:
    """A documented vendor status in the answer's own texts (an object's values, two levels, never inside a list)."""
    level = [answer]
    for _ in range(2):
        nxt = []
        for value in level:
            if isinstance(value, dict):
                for inner in value.values():
                    word = vendor_word(inner) if isinstance(inner, str) and len(inner) < 200 else None
                    if word:
                        return word
                    if isinstance(inner, dict):
                        nxt.append(inner)
        level = nxt
    return None


_ABSENT = object()


def _empty(value: Any) -> bool:
    return value is None or value is _ABSENT or (isinstance(value, str) and not value.strip())


def _as(words: set) -> Optional[str]:
    if not words:
        return None
    return next(iter(words)) if len(words) == 1 else "mixed"


def _is_number(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, Decimal):
        return value.is_finite()
    if isinstance(value, str):
        return bool(_NUMBER_TEXT.fullmatch(value.strip()))
    return False


def _key_of(value: Any) -> str:
    """One sort code as a comparable text: 105, Decimal("105") and "105" are one code. Only ever counted."""
    exact = isinstance(value, (int, Decimal)) and not isinstance(value, bool)
    if exact or (isinstance(value, float) and math.isfinite(value)):
        try:
            return format(Decimal(str(value)).normalize(), "f")
        except InvalidOperation:
            return str(value)
    if isinstance(value, str):
        return value.strip()
    return json.dumps(value, sort_keys=True, default=str)


def _month_of(value: Any) -> tuple:
    """(the form's word, (year, month)) of a date as text, or (None, None)."""
    if not isinstance(value, str):
        return None, None
    text = value.strip()
    for word, pattern, order in DATE_FORMS:
        m = pattern.fullmatch(text)
        if not m:
            continue
        a, b, c = (int(m.group(i)) for i in (1, 2, 3))
        year, mon, day = (a, b, c) if order == "ymd" else (c, b, a)
        if year < 100:
            year += 2000
        try:
            date(year, mon, day)
        except ValueError:
            return None, None
        return word, (year, mon)
    return None, None


class _Table:
    """How the definition's columns are read in these rows. A row that is an object is read by the column's id when the
    rows carry it, else by its caption; a row that is a list by the column's place among the definition's visible columns,
    and only when every list row has exactly that many cells (`position`)."""

    def __init__(self, rows: list, definition: Definition):
        self.rows = rows
        self.definition = definition
        self.by_id = {c.id: c for c in definition.columns if c.id}
        self.by_caption: dict = {}
        for column in definition.columns:
            self.by_caption.setdefault(column.caption, column)
        self.visible = [c for c in definition.columns if c.visible]
        objects = [r for r in rows if isinstance(r, dict)]
        lists = [r for r in rows if isinstance(r, list)]
        self.keys: dict = {}  # a key as the rows carry it -> "id" | "caption" | "other"
        self.uneven = 0
        first = None
        for row in objects:
            keys = frozenset(map(str, row.keys()))
            if first is None:
                first = keys
            elif keys != first:
                self.uneven += 1
            for key in keys:
                if key not in self.keys:
                    bare = key.strip()
                    self.keys[key] = "id" if bare in self.by_id else "caption" if bare in self.by_caption else "other"
        widths = {len(r) for r in lists}
        self.uneven += sum(1 for r in lists[1:] if len(r) != len(lists[0]))
        self.width = len(lists[0]) if lists else None
        self.positional = bool(lists) and widths == {len(self.visible)} and bool(self.visible)
        self.bare = {key.strip(): key for key in self.keys}
        classes = set(self.keys.values())
        if lists:
            classes.add("position" if self.positional else "other")
        self.by = _as(classes)  # one of BY_WORDS

    def carried(self, column: Column) -> bool:
        return self.reader(column) is not None

    def reader(self, column: Column) -> Optional[Callable]:
        key = self.bare.get(column.id) if column.id in self.bare and column.id else self.bare.get(column.caption)
        index = self.visible.index(column) if self.positional and column in self.visible else None
        if key is None and index is None:
            return None

        def read(row: Any) -> Any:
            if isinstance(row, dict):
                return row.get(key, _ABSENT) if key is not None else _ABSENT
            return row[index] if index is not None else _ABSENT

        return read

    def columns(self) -> dict:
        named, unnamed = [], 0
        order = {c.id: i for i, c in enumerate(self.definition.columns)}
        order.update({c.caption: i for i, c in enumerate(self.definition.columns)})
        for key, cls in self.keys.items():
            bare = key.strip()
            if cls == "id":
                named.append({"key": key, "caption": self.by_id[bare].caption})
            elif cls == "caption":
                named.append({"key": key, "caption": bare})
            elif nameable(key):
                named.append({"key": key, "caption": None})
            else:
                unnamed += 1
        named.sort(key=lambda item: (order.get(item["key"].strip(), len(order)), item["key"]))
        out = {"by": self.by, "keys": named, "unnamed_keys": unnamed,
               "missing": [c.caption for c in self.visible if not self.carried(c)], "uneven_rows": self.uneven}
        if self.width is not None:
            out["width"] = self.width
        return out

    def values(self, caption: str) -> tuple:
        """(present, the column's values in every row), or (False, []) when the definition or the rows lack it."""
        column = self.by_caption.get(caption)
        read = self.reader(column) if column is not None else None
        if read is None:
            return False, []
        return True, [read(row) for row in self.rows]


def _absent(table: _Table, caption: str) -> dict:
    return {"present": False, "in_definition": caption in table.by_caption}


def _date_block(table: _Table, asked: Optional[tuple]) -> dict:
    present, values = table.values(DATE_CAPTION)
    if not present:
        return _absent(table, DATE_CAPTION)
    kinds, forms, months = set(), set(), set()
    empty = unreadable = outside = 0
    for value in values:
        if _empty(value):
            empty += 1
            continue
        kinds.add(type_word(value))
        form, month = _month_of(value)
        if form is None:
            unreadable += 1
            continue
        forms.add(form)
        months.add(month)
        if asked is not None and month != asked:
            outside += 1
    return {"present": True, "as": _as(kinds), "format": _as(forms), "months": len(months),
            "outside_month": outside if asked is not None else None, "unreadable": unreadable, "empty": empty}


def _sort_block(table: _Table) -> dict:
    present, values = table.values(SORT_CAPTION)
    if not present:
        return _absent(table, SORT_CAPTION)
    kinds, codes = set(), set()
    empty = 0
    for value in values:
        if _empty(value):
            empty += 1
            continue
        kinds.add(type_word(value))
        codes.add(_key_of(value))
    return {"present": True, "as": _as(kinds), "distinct": len(codes), "empty": empty}


def _money_block(table: _Table, caption: str) -> dict:
    present, values = table.values(caption)
    if not present:
        return _absent(table, caption)
    kinds = set()
    empty = numeric = not_numeric = 0
    for value in values:
        if _empty(value):
            empty += 1
            continue
        kinds.add(type_word(value))
        if _is_number(value):
            numeric += 1
        else:
            not_numeric += 1
    return {"present": True, "numeric": None if numeric + not_numeric == 0 else not_numeric == 0, "as": _as(kinds),
            "not_numeric": not_numeric, "empty": empty}


def describe(answer: Any, definition: Definition, month: Optional[str] = None) -> dict:
    """The shape of an exportData answer, and nothing else (ADR-0187 §3): the answer's type and its keys with their types;
    where the rows are and how many; `hit_limit` (at or past the report's limit: the answer may be cut); how the rows name
    their columns, the keys they carry and the definition's columns they lack; the date column's form and its count of
    distinct months (and, with `month`, of rows outside it); the count of distinct sort codes; and whether debit and credit
    are present and numeric. Closed words, counts, booleans, and names that are the definition's or plain field names -
    never a value from a row (tests/test_ledger.py scans it for every value of its fixtures)."""
    rows, path, in_text = find_rows(answer)
    out: dict = {
        "answer": _top(answer),
        "vendor": _vendor_in(answer),
        "rows_at": path,
        "in_text": in_text,
        "rows": None if rows is None else len(rows),
        "limit": definition.limit,
        "hit_limit": None if rows is None or definition.limit is None else len(rows) >= definition.limit,
    }
    if rows is None:
        return out
    if not rows:  # an empty list: nothing to say of columns, and "every column missing" would be a false finding
        return {**out, **dict.fromkeys(("row_type", "columns", "date", "sort_code", "debit", "credit"))}
    table = _Table(rows, definition)
    first = month_bounds(month)[0] if month else None
    out["row_type"] = _as({"object" if isinstance(r, dict) else "list" for r in rows})
    out["columns"] = table.columns()
    out["date"] = _date_block(table, (first.year, first.month) if first else None)
    out["sort_code"] = _sort_block(table)
    out["debit"] = _money_block(table, DEBIT_CAPTION)
    out["credit"] = _money_block(table, CREDIT_CAPTION)
    return out


# ---- the probe ---------------------------------------------------------------------------------------------------------

# Every `error` the probe answers (POST /api/books/ledger/probe). bad_request and other_company are the route's.
PROBE_ERRORS = ("bad_request", "other_company", "not_set", "bad_definition", *KINDS, "bad_answer", "crashed")


def run_probe(client_of: Callable, text: Optional[str], month: str, deadline: Optional[float] = None) -> dict:
    """One run of the report for `month` on the client's company, and its shape. `client_of` gives the WizClient, only
    once the definition is good (no session is opened for a probe that cannot run). The answer is held only here: it is
    described and dropped, never logged, kept or returned. On a WizError: its kind, status and message - the client's
    redacted text, with any stretch of the datafile masked too - and the vendor's documented word when it says one."""
    if not (text or "").strip():
        return {"ok": False, "error": "not_set",
                "detail": "WIZ_LEDGER_REPORT is not set (a Replit Secret: the External file of the report, whole)"}
    try:
        definition = load_definition(text)
    except DefinitionError as exc:
        return {"ok": False, "error": "bad_definition", "reason": exc.reason}
    sizes = {"parameters": len(definition.parameters), "columns": len(definition.columns)}
    parameters = month_parameters(definition, month)
    try:
        answer = client_of().export_report(definition.datafile, parameters, deadline=deadline)
    except WizError as exc:
        out: dict = {"ok": False, "error": exc.kind, "definition": sizes, "vendor": vendor_word(exc.message)}
        if exc.status is not None:
            out["status"] = exc.status
        if exc.message:
            out["detail"] = mask_overlaps(exc.message, [definition.datafile])
        return out
    shape = describe(answer, definition, month)
    del answer
    ok = shape["rows"] is not None
    return {"ok": ok, "error": None if ok else "bad_answer", "definition": sizes, "vendor": shape["vendor"],
            "describe": shape}
