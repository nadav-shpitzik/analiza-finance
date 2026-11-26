"""D4 (26.11): the students' Excel file with the ID number ("ייצוא לאקסל עם ת.ז") - the server's half: the table the page
builds the file from (app/desk/export.py `b2c_table`, POST /api/desk/export.json in app/desk/routes.py). The page's half -
the numbers read from monday as the person and the file written in the browser - is tests/test_desk_export_ids_page.py.

Held here: the table is the students' Excel file described as data - the same rows in the same order, the same columns
with "ת.ז" where analiza-metrics' file had it (right after "סטודנט/ית", text, every cell empty), the same "פרטים" but for
its line on the ID number, and the same books' lists - for every filter the link takes; each row carries its item id and
its leads in monday's order; the route is gated as the file's link is; and it never holds an ID number, not even one
monday answers unasked. And the refactor under it (one description, `book`, written by `write`) leaves every file the
server writes as it was."""
from __future__ import annotations

import io
import logging
import zipfile
from datetime import date, datetime
from decimal import Decimal
from unittest import mock

import openpyxl

from app.desk import export as fx
from app.desk import reading as rd
from app.desk import view as fd
from tests.desk_fixtures import TODAY, b2, lead, sc, served, snap
from tests.test_desk import desk, session_token  # noqa: F401 - `desk` is the fixture
from tests.test_desk_export import auth, export_link

TABLE = "/api/desk/export.json"


def table(desk, user: int = 1, **body):  # noqa: F811
    return desk.client.post(TABLE, headers=auth(user), json={"tab": "b2c", **body})


def ids_snapshot():
    """Payments of two months and every state the list draws: a name that opens with "=", a reference with leading zeros,
    a payment with two leads (in monday's order, the second first) and one with none, a refund."""
    return snap([sc(1, invoice="", receipt="1", paid="3950.5", paid_date="2026-10-04", lead=["L2", "L1"]),
                 sc(2, invoice="00123", created="2026-10-06T08:00:00Z", lead=["L3"]),
                 dict(sc(3, created="2026-11-02T08:00:00Z", lead=["L1"]), name='=HYPERLINK("https://example.invalid","x")'),
                 sc(4, st="4", refund_date="2026-11-02", created="2026-09-05T08:00:00Z", refund="4000"),
                 sc(5, receipt="0", created="2026-11-03T08:00:00Z")],
                [b2(1)], leads=[lead("L1", rep="סבטלנה"), lead("L2"), lead("L3")])


def as_file(v, kind: str):
    """A cell of the table as openpyxl reads the server's file: a date a datetime, an amount a number, a text a text."""
    if v is None:
        return None
    if kind == "date":
        return datetime.combine(date.fromisoformat(v), datetime.min.time())
    if kind in ("money", "amount"):
        n = Decimal(v)
        return int(n) if n == n.to_integral_value() else float(n)
    return v


def file_of(desk, body: dict):  # noqa: F811
    return openpyxl.load_workbook(io.BytesIO(desk.client.get(export_link(desk, **body).json()["url"]).content))


# --- the table is the file --------------------------------------------------------------------------------------------

def test_the_table_is_the_students_file_with_an_empty_id_column_where_the_old_file_had_it(desk, monkeypatch):  # noqa: F811
    served(monkeypatch, ids_snapshot())
    desk.get(session_token(1))
    for body in ({}, {"state": "tasks"}, {"period": "2026-10"}, {"q": "תלמיד"}, {"state": "done", "q": "3950"},
                 {"q": "00123"}, {"taken": "2026-11-24T08:00:00+00:00"}):
        r = table(desk, **body)
        assert r.status_code == 200 and r.headers["cache-control"] == "no-store", body
        got = r.json()
        main, info = got["sheets"][0], got["sheets"][1]
        heads = [c["head"] for c in main["columns"]]
        at = main["id_column"]
        # analiza-metrics' file (app/finance_export.py at da671b2): "ת.ז" right after the student, before "נציג מכירות",
        # as text, 12 wide - and nowhere else
        assert heads[at] == "ת.ז" and heads[at - 1] == "סטודנט/ית" and heads[at + 1] == "נציג מכירות", body
        assert main["columns"][at] == {"head": "ת.ז", "kind": "text", "width": 12} and heads.count("ת.ז") == 1
        assert all(row[at] is None for row in main["rows"])
        wb = file_of(desk, body)
        ws = wb["תקבולים"]
        # the file's columns, in its order, are the table's but "ת.ז"
        others = [c for i, c in enumerate(main["columns"]) if i != at]
        assert [c["head"] for c in others] == [x.value for x in ws[1]], body
        # the same rows in the same order, cell by cell
        assert len(main["rows"]) == ws.max_row - 3 if main["rows"] else ws.max_row <= 3
        for k, row in enumerate(main["rows"], start=2):
            cells = [v for i, v in enumerate(row) if i != at]
            assert [as_file(v, c["kind"]) for v, c in zip(cells, others)] == [x.value for x in ws[k]], (body, k)
        # each row's item id and its leads, in monday's order
        assert [k["id"] for k in main["keys"]] == [row[heads.index("מזהה במאנדיי")] for row in main["rows"]]
        assert got["count"] == len(main["rows"]) == len(main["keys"])
        # "פרטים": the file's, line by line, but its line on the ID number (and the minute it was made)
        mine = [(k, v) for k, v, _kind in info["pairs"]]
        theirs = [(r[0].value, r[1].value) for r in wb["פרטים"].iter_rows()]
        line = [k for k, _v in mine].index("ת.ז")       # right under what the file is, as the file says its own
        assert len(mine) == len(theirs) and mine[line - 1][0] == "הקובץ", body
        assert mine[line] == ("ת.ז", fx.ID_NUMBERS_IN_FILE) and theirs[line] == ("ת.ז", fx.ID_NUMBERS_NOT_IN_FILE)
        for (k, v, kind), (fk, fv) in zip(info["pairs"], theirs):
            if k in ("ת.ז", "הופק"):
                continue
            assert (k, as_file(v, kind) if kind == "money" else v) == (fk, fv), (body, k)
        assert got["name"].startswith("תקבולים-סטודנטים_") and got["name"].endswith(".xlsx")


def test_each_row_carries_its_leads_in_mondays_order_and_none_when_it_has_none(desk, monkeypatch):  # noqa: F811
    served(monkeypatch, ids_snapshot())
    desk.get(session_token(1))
    keys = {k["id"]: k["lead_ids"] for k in table(desk).json()["sheets"][0]["keys"]}
    assert keys == {"1": ["L2", "L1"], "2": ["L3"], "3": ["L1"], "4": [], "5": []}


def test_money_and_dates_go_as_text_never_as_floats(desk, monkeypatch):  # noqa: F811
    """An amount must round-trip exactly (view.json_safe): the page writes it into the file as it came."""
    served(monkeypatch, ids_snapshot())
    desk.get(session_token(1))
    main = table(desk).json()["sheets"][0]
    heads = [c["head"] for c in main["columns"]]
    first = next(r for r in main["rows"] if r[heads.index("מזהה במאנדיי")] == "1")
    assert first[heads.index("סכום ששולם")] == "3950.5" and first[heads.index("תאריך תקבול")] == "2026-10-04"


def test_the_table_carries_the_books_lists_and_the_lines_of_its_file():
    """`b2c_table` is `b2c_book` of the same rows: the books' lists of "לטיפול" (D3) as the file has them, and "פרטים"'s
    warnings - another reading on the screen, a reading that came short, another part of the books."""
    rows = fd.filter_b2c(fd.build_view(ids_snapshot(), TODAY)["b2c"], "", "", "")
    about = {"taken_at": "24/11/2026 10:00:00", "screen_taken_at": "24/11/2026 09:00:00", "complete": False,
             "books_other": True, "state_label": "לטיפול", "q": "", "books_lines": [("לפי הספרים", "נכון ל-24/11")]}
    sheets = [("מסמכים בלי חיוב", fx._DOC_COLUMNS + [("שם בכרטיס", lambda e: e["card"], "text", 26)],
               [{"number": "0045", "type_name": "חשבונית מס קבלה", "date": "2026-10-01", "card": "=1+1"}],
               ["מסמכים בלי חיוב", "משפט מתחת"])]
    got = fx.b2c_table(rows, about, sheets)
    wb = openpyxl.load_workbook(io.BytesIO(fx.b2c_workbook(rows, about, sheets)))
    listed = got["sheets"][2]
    assert listed["title"] == "מסמכים בלי חיוב" and listed["lines"] == ["מסמכים בלי חיוב", "משפט מתחת"]
    assert listed["rows"] == [["0045", "חשבונית מס קבלה", date(2026, 10, 1), "=1+1"]]
    assert [x.value for x in wb["מסמכים בלי חיוב"][2]] == ["0045", "חשבונית מס קבלה", datetime(2026, 10, 1), "=1+1"]
    said = [p[1] for p in got["sheets"][1]["pairs"] if p[0] == "שימו לב"]
    assert len(said) == 3 and [r[1].value for r in wb["פרטים"].iter_rows() if r[0].value == "שימו לב"] == said


# --- the gate ---------------------------------------------------------------------------------------------------------

def test_the_table_is_gated_as_the_files_link_is_and_is_the_students_list_only(desk, monkeypatch):  # noqa: F811
    served(monkeypatch, ids_snapshot())
    assert desk.client.post(TABLE, json={"tab": "b2c"}).status_code == 401           # no sessionToken
    assert table(desk, 3).status_code == 403                                          # B2B Charge only
    assert table(desk, 9).status_code == 403                                          # neither board
    assert table(desk, 2).status_code == 200                                          # Student Charge is enough
    for tab in ("b2b", "b2b_customers", "b2c_cohorts", "both"):
        resp = table(desk, tab=tab)
        assert resp.status_code == 400 and resp.json() == {"error": "unknown_tab"}, tab
    for bad in ({"state": "nope"}, {"period": "x" * 33}, {"q": "a‏b"}, {"customer": "1; drop"}, {"taken": "x" * 41}):
        resp = table(desk, **bad)
        assert resp.status_code == 400 and resp.json() == {"error": "bad_filter"}, bad
    resp = table(desk, lead_ids=["L1"])                                               # a field the link does not know
    assert resp.status_code == 400 and resp.json() == {"error": "invalid_filter"}
    assert desk.client.post(TABLE, headers=auth(), content=b"not json").json() == {"error": "invalid_filter"}


def test_a_period_the_view_cannot_read_is_400_and_no_reading_is_503_with_its_code(desk, monkeypatch):  # noqa: F811
    served(monkeypatch, ids_snapshot())
    resp = table(desk, period="october")                  # passes the link's check, not the view's (view.period_months)
    assert resp.status_code == 400 and resp.json() == {"error": "bad_filter"}


def test_no_reading_to_build_from_is_503_with_the_readings_code(desk):  # noqa: F811
    desk.w.columns_missing[rd.LEADS_BOARD] = {rd.LD["rep"]}   # every reading ends at the columns check
    resp = table(desk)
    assert resp.status_code == 503 and resp.json() == {"error": "finance_unavailable", "reason": "unknown_column"}


def test_the_audit_line_names_the_reader_and_a_count_never_a_value(desk, monkeypatch, caplog):  # noqa: F811
    served(monkeypatch, ids_snapshot())
    with caplog.at_level(logging.INFO, logger="analiza_finance.desk"):
        assert table(desk).status_code == 200
    lines = [r.getMessage() for r in caplog.records if "desk-export-table" in r.getMessage()]
    assert lines == ["desk user=1 endpoint=desk-export-table outcome=table results=5"]


# --- never an ID number -----------------------------------------------------------------------------------------------

def test_the_table_never_holds_an_id_number_not_even_one_monday_answers_unasked(desk):  # noqa: F811
    desk.w.extra_cells = {"301": {rd.LD["id_number"]: {"text": "012345678"}}}   # payment 101's lead
    desk.get(session_token(1))
    resp = table(desk)
    assert resp.status_code == 200 and "012345678" not in resp.text
    keys = {k["id"]: k["lead_ids"] for k in resp.json()["sheets"][0]["keys"]}
    assert keys["101"] == ["301"]                      # the lead it is read by, in the browser, as the person
    asked = {c for _q, v in desk.w.queries_of("items(ids:") for c in v["cols"]}
    assert rd.LD["id_number"] not in asked


# --- the refactor under it: every file the server writes, as it was ---------------------------------------------------

def test_the_file_is_its_description_written_and_the_description_has_its_shape():
    """`_workbook` is `write(book(...))` since D4: the students' file is its description written, part for part (the
    sheets, the styles, the workbook), and the description has its shape - the list with its total, "פרטים" with its two
    widths. (That every file the server writes stayed the file it was is held by the files' own tests, and was checked once
    against 33bd24a's module, part for part, for the students', the companies', the aging and the cohorts' files.)"""
    rows = fd.filter_b2c(fd.build_view(ids_snapshot(), TODAY)["b2c"], "", "", "")
    fixed = datetime(2026, 11, 26, 12, 0)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed

    def parts(data: bytes) -> dict:
        z = zipfile.ZipFile(io.BytesIO(data))
        return {n: z.read(n) for n in z.namelist() if not n.startswith("docProps")}

    with mock.patch.object(fx, "datetime", Clock):
        described = fx.b2c_book(rows, {"taken_at": "x"})
        assert parts(fx.b2c_workbook(rows, {"taken_at": "x"})) == parts(fx.write(described))
    assert [s["title"] for s in described["sheets"]] == ["תקבולים", "פרטים"]
    assert described["sheets"][0]["total"] == fx.TOTAL_UNDER and "lines" not in described["sheets"][0]
    assert described["sheets"][1]["widths"] == list(fx.INFO_WIDTHS)
    # a text is cleaned as the file cleans it, and an empty one is no cell
    assert fx._cell("text", "ישראל\x0bישראלי") == "ישראלישראלי" and fx._cell("text", "\x00") is None
    assert fx._cell("money", Decimal("1.5")) == Decimal("1.5") and fx._cell("text", None) is None
