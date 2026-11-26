"""B3's probe of the ledger report (backlog 0301; ADR-0187): the definition, a month's parameters and the shape of an
answer (app/books/ledger.py). No row is ever said: every answer here is scanned for every value of its fixture."""

import copy
import json
import re
from decimal import Decimal

import pytest

from app.books import ledger

CAPTIONS = ["תאריך", "תאריך ערך", "מפתח חשבון", "קוד מיון", "שם קוד מיון", "שם חשבון", "קוד תמחיר", "פרטים",
            "אסמכתא 1", "אסמכתא 2", "חובה שקל", "זכות שקל", "חשבון נגדי", "מספר מנה", "סוג תנועה", "מספר תנועה"]
DATAFILE = "5be2c0ffee" + "".join(f"{i:04x}{(i * 7919) % 65536:04x}" for i in range(400)) + "d00d"  # distinctive hex
PARAMETERS = [  # as Chava's External file exported them (26.11)
    {"p_name": "__MUSTACH_P0__", "id": "0", "type": "long", "name": "קוד מיון", "defVal": "-999999999", "opName": "מ..עד",
     "opOrigin": "from"},
    {"p_name": "__MUSTACH_P1__", "id": "500", "type": "long", "name": "קוד מיון1", "defVal": "999999999", "opName": "מ..עד",
     "opOrigin": "to"},
    {"p_name": "__MUSTACH_P2__", "id": "1", "type": "date", "name": "תאריך", "defVal": "'2000-01-01'", "opName": "מ..עד",
     "opOrigin": "from"},
    {"p_name": "__MUSTACH_P3__", "id": "501", "type": "date", "name": "תאריך1", "defVal": "'2090-12-31'", "opName": "מ..עד",
     "opOrigin": "to"},
]
DEFINITION = {
    "version": "1.2.0",
    "datafile": DATAFILE,
    "columns_params": [{"id": f"COL{i}", "caption": c, "visible": True} for i, c in enumerate(CAPTIONS)],
    "parameters": PARAMETERS,
    "order_by_parameters": [],
    "limit": "10000",
    "offset": "",
    "flags": {},
}
SECRET = json.dumps(DEFINITION, ensure_ascii=False)
# Four ledger lines of October: names, amounts, keys, references - everything describe() must never say.
LINES = [
    ["2026-10-05", "2026-10-07", "40017", 731, "הוצאות משכורת", "ישראל ישראלי", "PRJ-77", "משכורת אוקטובר", "50347", "",
     Decimal("48213.77"), 0, "60231", 7712, "שכר", 88123],
    ["2026-10-05", "2026-10-07", "60231", 942, "ספקי ציוד", "מרים כהן-לוי", "", "חשבונית ספק 88412", "61290", "7731",
     0, Decimal("48213.77"), "40017", 7712, "ספק", 88124],
    ["2026-10-19", "2026-10-20", "71884", 942, "ספקי ציוד", "אופק תקשורת בעמ", "PRJ-91", "ציוד רשת", "61377", "",
     Decimal("1935.40"), 0, "60231", 7740, "ספק", 88125],
    ["2026-10-28", "2026-10-28", "71884", 942, "ספקי ציוד", "אופק תקשורת בעמ", "PRJ-91", "החזר ציוד", "61402", "",
     0, Decimal("212.06"), "60231", 7741, "ספק", 88126],
]


def by_id(lines=LINES) -> list:
    return [{f"COL{i}": v for i, v in enumerate(line)} for line in lines]


def by_caption(lines=LINES) -> list:
    return [dict(zip(CAPTIONS, line)) for line in lines]


def definition(**changes):
    data = copy.deepcopy(DEFINITION)
    data.update(changes)
    return ledger.load_definition(json.dumps(data, ensure_ascii=False))


DEF = ledger.load_definition(SECRET)


# ---- the scan: every value of a fixture, and the proof that it sees a leak ----------------------------------------------


def targets(obj) -> set:
    """Every text a value of `obj` could be said as: each text whole and its words (a word of digits only from five
    digits up, so a year is not mistaken for a leak), each number as written, normalized, as a float and with thousands
    separators."""
    out: set = set()

    def walk(value):
        if isinstance(value, dict):
            for v in value.values():
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)
        elif isinstance(value, bool) or value is None:
            return
        elif isinstance(value, int):
            if abs(value) >= 100:
                out.update({str(value), f"{value:,}"})
        elif isinstance(value, Decimal):
            if value:
                out.update({str(value), format(value.normalize(), "f"), str(float(value)), f"{value:,}"})
                whole = str(abs(int(value)))
                if len(whole) >= 5:
                    out.add(whole)
        elif isinstance(value, str):
            text = value.strip()
            if len(text) >= 3:
                out.add(text)
            for word in re.split(r"[\s\-]+", text):
                if len(word) >= 3 and (not word.isdigit() or len(word) >= 5):
                    out.add(word)

    walk(obj)
    return out


def scan(text: str, values: set) -> list:
    return sorted(v for v in values if v in text)


# What an answer may say: closed words, captions and ids from the definition, plain keys. A fixture value that is a piece
# of any of these could not be told from a leak, so the fixtures hold none (checked below).
LEGIT = " ".join(CAPTIONS + [f"COL{i}" for i in range(16)] + list(ledger.TYPE_WORDS) + list(ledger.BY_WORDS)
                 + [w for w, _, _ in ledger.DATE_FORMS] + list(ledger.VENDOR_WORDS) + list(ledger.ROW_KEYS)
                 + ["statusCode", "status", "present", "numeric", "distinct", "outside_month", "2026-10", "demo"])
VALUES = targets(LINES)


def test_no_fixture_value_could_pass_for_a_word_an_answer_may_say():
    assert len(VALUES) > 60
    assert [v for v in VALUES if v in LEGIT] == []
    assert "ישראל" in VALUES and "48213.77" in VALUES and "88412" in VALUES and "731" in VALUES


@pytest.mark.parametrize("leak", ["ישראל", "כהן-לוי", "48213.77", "48,213.77", "1935.4", "40017", "731", "2026-10-19",
                                  "PRJ-91", "88412", "88126", "משכורת"])
def test_the_scan_sees_a_value_wherever_it_is_put(leak):
    out = ledger.describe({"repdata": by_id()}, DEF, "2026-10")
    assert scan(json.dumps(out, ensure_ascii=False), VALUES) == []
    for place in ("key", "caption"):
        mutated = copy.deepcopy(out)
        mutated["columns"]["keys"].append({place: leak})
        assert scan(json.dumps(mutated, ensure_ascii=False), VALUES)
    mutated = copy.deepcopy(out)
    mutated["sort_code"]["as"] = f"text ({leak})"
    assert scan(json.dumps(mutated, ensure_ascii=False), VALUES)


def test_a_describe_that_says_a_value_is_caught(monkeypatch):
    """Mutants of describe() itself: each says something of a row, and the scan sees it."""
    clean = json.dumps(ledger.describe({"repdata": by_id()}, DEF, "2026-10"), ensure_ascii=False)
    assert scan(clean, VALUES) == []
    with monkeypatch.context() as m:  # a type word that is the value
        m.setattr(ledger, "type_word", lambda v: str(v))
        assert scan(json.dumps(ledger.describe({"repdata": by_id()}, DEF, "2026-10"), ensure_ascii=False), VALUES)
    with monkeypatch.context() as m:  # every key named, the values' too
        m.setattr(ledger, "nameable", lambda key: True)
        leaky = {"repdata": [dict(r, **{"ישראל ישראלי": 1}) for r in by_id()]}
        assert "ישראל" in scan(json.dumps(ledger.describe(leaky, DEF), ensure_ascii=False), VALUES)
    with monkeypatch.context() as m:  # a date's form said as the date (one row: several dates would say "mixed")
        m.setattr(ledger, "_month_of", lambda v: (str(v), (2026, 10)) if isinstance(v, str) else (None, None))
        assert scan(json.dumps(ledger.describe({"repdata": by_id(LINES[:1])}, DEF), ensure_ascii=False), VALUES)
    with monkeypatch.context() as m:  # the sort codes said instead of counted
        m.setattr(ledger, "_sort_block", lambda table: {"codes": sorted(map(str, table.values(ledger.SORT_CAPTION)[1]))})
        assert "731" in scan(json.dumps(ledger.describe({"repdata": by_id()}, DEF), ensure_ascii=False), VALUES)


# ---- the definition ---------------------------------------------------------------------------------------------------


def test_the_four_parameters_are_found_by_name_and_origin_not_by_position():
    shuffled = [PARAMETERS[3], PARAMETERS[1], PARAMETERS[2], PARAMETERS[0]]
    d = definition(parameters=shuffled)
    assert {slot: d.parameters[i]["p_name"] for slot, i in d.slots.items()} == {
        "sort_from": "__MUSTACH_P0__", "sort_to": "__MUSTACH_P1__", "date_from": "__MUSTACH_P2__",
        "date_to": "__MUSTACH_P3__"}
    # a name written as the vendor's example writes it, a path, and a fifth cut kept as it is
    path = [dict(p, name="חתך/שדה מרשומת החשבון/1. פרטי חשבון/" + p["name"]) for p in PARAMETERS]
    fifth = {"p_name": "__MUSTACH_P4__", "id": "2", "type": "txt", "name": "תאריך ערך", "defVal": "'x'", "opName": "=",
             "opOrigin": "from"}
    d = definition(parameters=path + [fifth])
    assert sorted(d.slots.values()) == [0, 1, 2, 3] and len(d.parameters) == 5
    assert (DEF.limit, len(DEF.columns), DEF.columns[10].caption) == (10000, 16, "חובה שקל")


def test_the_definition_never_shows_its_datafile():
    assert repr(DEF) == "Definition(parameters=4, columns=16, limit=10000)"
    assert DATAFILE not in repr(DEF) and DATAFILE not in str(DEF)


def _without(name, origin):
    return [p for p in PARAMETERS if not (p["name"] == name and p["opOrigin"] == origin)]


BAD = [
    (None, "missing"),
    ("", "missing"),
    ("  \n", "missing"),
    ("{\"version\": \"1.2.0\", \"datafile\": ", "not_json"),
    ("encrypt_reportData = '0c61'", "not_json"),
    ("[1, 2]", "not_object"),
    (json.dumps(json.dumps(DEFINITION)), "not_object"),
    (json.dumps({**DEFINITION, "datafile": None}), "no_datafile"),
    (json.dumps({**DEFINITION, "datafile": "  "}), "no_datafile"),
    (json.dumps({k: v for k, v in DEFINITION.items() if k != "datafile"}), "no_datafile"),
    (json.dumps({k: v for k, v in DEFINITION.items() if k != "parameters"}), "no_parameters"),
    (json.dumps({**DEFINITION, "parameters": []}), "no_parameters"),
    (json.dumps({**DEFINITION, "parameters": PARAMETERS + ["__MUSTACH_P4__"]}), "no_parameters"),
    (json.dumps({**DEFINITION, "parameters": _without("קוד מיון", "from")}), "no_sort_from"),
    (json.dumps({**DEFINITION, "parameters": _without("קוד מיון1", "to")}), "no_sort_to"),
    (json.dumps({**DEFINITION, "parameters": _without("תאריך", "from")}), "no_date_from"),
    (json.dumps({**DEFINITION, "parameters": _without("תאריך1", "to")}), "no_date_to"),
    (json.dumps({**DEFINITION, "parameters": [dict(p, opOrigin="FROM ") if p["id"] == "1" else p for p in PARAMETERS]}),
     None),  # an origin's case and spaces do not matter
    (json.dumps({**DEFINITION, "parameters": PARAMETERS + [dict(PARAMETERS[2], p_name="__MUSTACH_P4__")]}),
     "parameter_twice"),
    (json.dumps({**DEFINITION, "parameters": [dict(p, type="txt") if p["id"] == "0" else p for p in PARAMETERS]}),
     "parameter_type"),
    (json.dumps({**DEFINITION, "parameters": [dict(p, type="long") if p["id"] == "501" else p for p in PARAMETERS]}),
     "parameter_type"),
]


@pytest.mark.parametrize("text,reason", BAD)
def test_a_definition_that_cannot_be_used_says_why_in_a_closed_word(text, reason):
    if reason is None:
        assert ledger.load_definition(text).slots
        return
    with pytest.raises(ledger.DefinitionError) as err:
        ledger.load_definition(text)
    assert err.value.reason == reason and str(err.value) == reason
    assert DATAFILE not in repr(err.value)
    assert ledger.definition_state(text) == ("missing" if reason == "missing" else "bad")


def test_every_reason_is_reached_and_the_list_is_closed():
    assert {reason for _, reason in BAD if reason} == set(ledger.REASONS)
    with pytest.raises(AssertionError):
        ledger.DefinitionError("something_new")
    assert ledger.definition_state(SECRET) == "set"
    assert ledger.load_definition("\ufeff" + SECRET + "\n").datafile == DATAFILE  # a BOM and a newline from the paste


# ---- the parameters of one month --------------------------------------------------------------------------------------


@pytest.mark.parametrize("month,first,last", [
    ("2026-10", "'2026-10-01'", "'2026-10-31'"),
    ("2026-11", "'2026-11-01'", "'2026-11-30'"),
    ("2026-02", "'2026-02-01'", "'2026-02-28'"),
    ("2028-02", "'2028-02-01'", "'2028-02-29'"),
    ("2026-12", "'2026-12-01'", "'2026-12-31'"),
])
def test_a_month_is_its_first_and_last_day_and_every_sort_code(month, first, last):
    params = ledger.month_parameters(DEF, month)
    assert [p["defVal"] for p in params] == ["-999999999", "999999999", first, last]
    # the documented shape: every entry whole, in the file's order, only defVal changed
    for sent, exported in zip(params, PARAMETERS):
        assert {k: v for k, v in sent.items() if k != "defVal"} == {k: v for k, v in exported.items() if k != "defVal"}
    assert [p["defVal"] for p in DEF.parameters] == ["-999999999", "999999999", "'2000-01-01'", "'2090-12-31'"]


def test_a_sort_code_range_and_the_entries_the_probe_does_not_touch():
    shuffled = [PARAMETERS[2], PARAMETERS[0], {"p_name": "__MUSTACH_P4__", "id": "7", "type": "txt", "name": "פרטים",
                                                 "defVal": "'abc'", "opName": "=", "opOrigin": "from"},
                PARAMETERS[3], PARAMETERS[1]]
    params = ledger.month_parameters(definition(parameters=shuffled), "2026-10", sort_from=100, sort_to="399")
    assert [p["defVal"] for p in params] == ["'2026-10-01'", "100", "'abc'", "'2026-10-31'", "399"]


@pytest.mark.parametrize("month", ["2026-13", "2026-00", "2026-1", "26-10", "2026-10-01", "1999-12", "", None, "2026/10"])
def test_a_month_that_is_not_one_is_refused(month):
    with pytest.raises(ValueError):
        ledger.month_parameters(DEF, month)


@pytest.mark.parametrize("low,high", [(400, 100), ("1e3", None), (True, None), (None, 1000000000), ("10.5", None)])
def test_a_sort_code_range_that_is_not_one_is_refused(low, high):
    with pytest.raises(ValueError):
        ledger.month_parameters(DEF, "2026-10", sort_from=low, sort_to=high)


# ---- the shape of an answer -------------------------------------------------------------------------------------------


def test_the_shape_of_rows_keyed_by_column_id_and_nothing_else():
    answer = {"statusCode": 200, "repdata": by_id(), "ID_123456789": "x"}
    out = ledger.describe(answer, DEF, "2026-10")
    assert out["answer"] == {"type": "object", "keys": {"repdata": "list", "statusCode": "number"}, "unnamed_keys": 1}
    assert (out["rows_at"], out["in_text"], out["rows"]) == (["repdata"], False, 4)
    assert (out["limit"], out["hit_limit"]) == (10000, False)
    assert out["row_type"] == "object" and out["vendor"] is None
    cols = out["columns"]
    assert (cols["by"], cols["unnamed_keys"], cols["missing"], cols["uneven_rows"]) == ("id", 0, [], 0)
    assert cols["keys"][0] == {"key": "COL0", "caption": "תאריך"}
    assert cols["keys"][15] == {"key": "COL15", "caption": "מספר תנועה"}
    assert out["date"] == {"present": True, "as": "text", "format": "iso", "months": 1, "outside_month": 0,
                           "unreadable": 0, "empty": 0}
    assert out["sort_code"] == {"present": True, "as": "number", "distinct": 2, "empty": 0}
    assert out["debit"] == {"present": True, "numeric": True, "as": "number", "not_numeric": 0, "empty": 0}
    assert out["credit"] == out["debit"]
    text = json.dumps(out, ensure_ascii=False)
    assert scan(text, VALUES | {"123456789"}) == []


def test_rows_keyed_by_caption_and_rows_as_lists():
    out = ledger.describe({"repdata": by_caption()}, DEF, "2026-10")
    assert out["columns"]["by"] == "caption" and out["columns"]["missing"] == []
    assert out["sort_code"]["distinct"] == 2 and out["debit"]["numeric"] is True
    lists = ledger.describe({"repdata": [list(line) for line in LINES]}, DEF, "2026-10")
    assert lists["row_type"] == "list" and lists["columns"]["by"] == "position" and lists["columns"]["width"] == 16
    assert lists["date"]["months"] == 1 and lists["sort_code"]["distinct"] == 2 and lists["credit"]["numeric"] is True
    short = ledger.describe({"repdata": [list(line[:15]) for line in LINES]}, DEF)
    assert short["columns"]["by"] == "other" and short["debit"] == {"present": False, "in_definition": True}
    ragged = ledger.describe({"repdata": [list(LINES[0]), list(LINES[1][:15]), list(LINES[2])]}, DEF)
    assert (ragged["columns"]["by"], ragged["columns"]["uneven_rows"], ragged["columns"]["width"]) == ("other", 1, 16)
    for o in (out, lists, short, ragged):
        assert scan(json.dumps(o, ensure_ascii=False), VALUES) == []


def test_a_key_that_is_not_the_definitions_is_named_only_when_it_is_a_plain_field_name():
    rows = by_id()
    rows[0]["ישראל ישראלי"] = "x"  # a value where a key should be
    rows[1]["AccountKey"] = "40017"
    rows[2]["ID_123456789"] = 1
    out = ledger.describe({"repdata": rows}, DEF)
    cols = out["columns"]
    assert cols["unnamed_keys"] == 2 and cols["uneven_rows"] == 3 and cols["by"] == "mixed"
    assert {"key": "AccountKey", "caption": None} in cols["keys"]
    assert scan(json.dumps(out, ensure_ascii=False), VALUES | {"ישראל ישראלי", "ID_123456789", "123456789"}) == []


def test_hit_limit_is_at_exactly_the_limit():
    small = definition(limit="4")
    assert ledger.describe({"repdata": by_id()}, small)["hit_limit"] is True
    assert ledger.describe({"repdata": by_id()[:3]}, small)["hit_limit"] is False
    assert ledger.describe({"repdata": by_id()}, definition(limit="5"))["hit_limit"] is False
    assert ledger.describe({"repdata": by_id()}, definition(limit=3))["hit_limit"] is True  # past it: may be cut too
    unknown = ledger.describe({"repdata": by_id()}, definition(limit=""))
    assert (unknown["limit"], unknown["hit_limit"]) == (None, None)


def test_a_cut_the_vendor_ignored_shows_as_months_outside_the_one_asked():
    lines = [list(line) for line in LINES]
    lines[0][0], lines[1][0] = "2026-09-30", "2025-10-05"
    out = ledger.describe({"repdata": by_id(lines)}, DEF, "2026-10")
    assert (out["date"]["months"], out["date"]["outside_month"]) == (3, 2)
    assert ledger.describe({"repdata": by_id(lines)}, DEF)["date"]["outside_month"] is None


@pytest.mark.parametrize("dates,form,months,unreadable", [
    (["05/10/2026", "19/10/2026"], "dmy_slash", 1, 0),
    (["05.10.2026", "28.10.2026"], "dmy_dot", 1, 0),
    (["05-10-2026"], "dmy_dash", 1, 0),
    (["05/10/26", "06/10/26"], "dmy_slash_yy", 1, 0),
    (["2026-10-05T00:00:00", "2026-10-06 00:00:00.000"], "iso_time", 1, 0),
    (["20261005"], "ymd_digits", 1, 0),
    (["2026-10-05", "05/10/2026"], "mixed", 1, 0),
    (["10/31/2026", "/Date(1791158400000)/", "2026-02-30"], None, 0, 3),
])
def test_a_dates_form_is_said_by_a_closed_word(dates, form, months, unreadable):
    lines = [list(LINES[0]) for _ in dates]
    for line, d in zip(lines, dates):
        line[0] = d
    out = ledger.describe({"repdata": by_id(lines)}, DEF)["date"]
    assert (out["format"], out["months"], out["unreadable"]) == (form, months, unreadable)
    assert form is None or form in [w for w, _, _ in ledger.DATE_FORMS] + ["mixed"]


def test_one_sort_code_however_written_and_an_empty_date_is_empty():
    lines = [list(LINES[0]) for _ in range(5)]
    for line, code in zip(lines, [942, "942", " 942 ", Decimal("942"), Decimal("942.0")]):
        line[3] = code
    lines[0][0], lines[1][0] = "", "   "
    out = ledger.describe({"repdata": by_id(lines)}, DEF)
    assert out["sort_code"] == {"present": True, "as": "mixed", "distinct": 1, "empty": 0}
    assert (out["date"]["empty"], out["date"]["unreadable"], out["date"]["months"]) == (2, 0, 1)


def test_money_as_text_is_numeric_and_text_that_is_not_a_number_is_counted():
    lines = [list(line) for line in LINES]
    lines[0][10], lines[1][10], lines[2][10], lines[3][10] = "48,213.77", "1935.40-", "", None
    lines[0][11], lines[1][11] = "ש\"ח 5", "abc"
    out = ledger.describe({"repdata": by_id(lines)}, DEF)
    assert out["debit"] == {"present": True, "numeric": True, "as": "text", "not_numeric": 0, "empty": 2}
    assert out["credit"] == {"present": True, "numeric": False, "as": "mixed", "not_numeric": 2, "empty": 0}
    blank = [list(line) for line in LINES]
    for line in blank:
        line[11] = ""
    assert ledger.describe({"repdata": by_id(blank)}, DEF)["credit"]["numeric"] is None


def test_where_the_rows_are_found():
    inside = ledger.describe({"status": {"repdata": json.dumps(by_id(), default=str, ensure_ascii=False)}}, DEF)
    assert (inside["rows_at"], inside["in_text"], inside["rows"]) == (["status", "repdata"], True, 4)
    assert ledger.describe(by_id(), DEF)["rows_at"] == []
    # repdata wins over another list of rows, and rows over an empty list
    both = ledger.describe({"errors": [], "items": [{"a": 1}], "repdata": by_id()}, DEF)
    assert (both["rows_at"], both["rows"]) == (["repdata"], 4)
    deeper = ledger.describe({"items": [{"a": 1}], "status": {"repdata": by_id()}}, DEF)
    assert (deeper["rows_at"], deeper["rows"]) == (["status", "repdata"], 4)
    assert ledger.describe({"errors": [], "result": {"rows": by_id()}}, DEF)["rows_at"] == ["result", "rows"]
    hidden = ledger.describe({"תוצאה": by_id()}, DEF)
    assert hidden["rows_at"] == ["*"] and hidden["answer"]["unnamed_keys"] == 1 and "תוצאה" not in json.dumps(
        hidden, ensure_ascii=False)
    empty = ledger.describe({"statusCode": 200, "repdata": []}, DEF)
    assert (empty["rows"], empty["hit_limit"], empty["columns"], empty["debit"]) == (0, False, None, None)


def test_an_answer_without_rows_says_its_top_and_the_vendors_word():
    out = ledger.describe({"statusCode": 200, "status": "Data not found", "names": ["ישראל ישראלי"]}, DEF)
    assert (out["rows"], out["rows_at"], out["vendor"]) == (None, None, "data_not_found")
    assert out["answer"]["keys"] == {"names": "list", "status": "text", "statusCode": "number"}
    assert "ישראל" not in json.dumps(out, ensure_ascii=False)
    assert ledger.describe("a text", DEF)["answer"] == {"type": "text"}
    assert [ledger.vendor_word(t) for t in ("Error: params not found or not match", "No Permission", "ok", None)] == [
        "params_mismatch", "no_permission", None, None]


# ---- the probe, with a client that is not one ----------------------------------------------------------------------------


class Client:
    """export_report only: answers `answer` or raises `error`, and keeps what it was asked."""

    def __init__(self, answer=None, error=None):
        self.answer, self.error, self.calls = answer, error, []

    def export_report(self, datafile, parameters, deadline=None):
        self.calls.append((datafile, parameters, deadline))
        if self.error is not None:
            raise self.error
        return self.answer


def test_the_probe_asks_for_a_client_only_with_a_good_definition():
    asked = []
    out = ledger.run_probe(lambda: asked.append(1), "", "2026-10")
    assert (out["ok"], out["error"], asked) == (False, "not_set", [])
    out = ledger.run_probe(lambda: asked.append(1), json.dumps({**DEFINITION, "datafile": ""}), "2026-10")
    assert (out["error"], out["reason"], asked) == ("bad_definition", "no_datafile", [])


def test_the_probe_runs_the_month_with_the_deadline_and_says_the_shape():
    client = Client({"statusCode": 200, "repdata": by_id()})
    out = ledger.run_probe(lambda: client, SECRET, "2026-10", deadline=123.0)
    assert (out["ok"], out["error"], out["vendor"], out["definition"]) == (True, None, None, {"parameters": 4, "columns": 16})
    assert out["describe"]["rows"] == 4 and out["describe"]["date"]["outside_month"] == 0
    assert client.calls == [(DATAFILE, ledger.month_parameters(DEF, "2026-10"), 123.0)]
    assert scan(json.dumps(out, ensure_ascii=False), VALUES) == [] and DATAFILE[:12] not in json.dumps(out)
    out = ledger.run_probe(lambda: Client({"statusCode": 200, "status": "Data not found"}), SECRET, "2026-10")
    assert (out["ok"], out["error"], out["vendor"], out["describe"]["rows"]) == (False, "bad_answer", "data_not_found", None)


def test_a_vendor_error_is_its_kind_with_any_stretch_of_the_datafile_masked():
    from app.wizcloud import WizError

    echo = f"params not found or not match {DATAFILE[100:120]} and {DATAFILE[200:213]}"
    out = ledger.run_probe(lambda: Client(error=WizError("http_error", echo, 500)), SECRET, "2026-10")
    assert (out["ok"], out["error"], out["status"], out["vendor"]) == (False, "http_error", 500, "params_mismatch")
    assert out["detail"] == "params not found or not match *** and ***"
    out = ledger.run_probe(lambda: Client(error=WizError("network", "timeout")), SECRET, "2026-10")
    assert (out["error"], out["detail"], out["vendor"]) == ("network", "timeout", None) and "status" not in out


def test_the_probes_errors_are_one_closed_list():
    from app.wizcloud import KINDS

    assert len(set(ledger.PROBE_ERRORS)) == len(ledger.PROBE_ERRORS) == len(KINDS) + 6
    assert set(KINDS) < set(ledger.PROBE_ERRORS)
