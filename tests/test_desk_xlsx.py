"""D4 (26.11): frontend/xlsx.js, the desk's own .xlsx writer - the students' file with the ID number is written in the
person's browser, since the server never reads an ID number. Run in node and read back with openpyxl (and the zip with
zipfile); needs node, skipped without it.

What is held: the description app/desk/export.py writes its files from (`book`, here the students' file's `b2c_book` with
its books' list), written by xlsx.js, reads back as the very file the server's openpyxl writer makes of it - every sheet,
every cell's value, type and format, the bold and the fill, the widths, right to left, the frozen header, the filter, the
total that follows it; a text is always a string (a name that opens with "=" too, a reference with leading zeros), what
XML cannot carry is dropped, a number or a date that is not one is an error and never an empty cell; and the zip is
stored with a right CRC for every part."""
from __future__ import annotations

import base64
import io
import json
import re
import shutil
import subprocess
import zipfile
from datetime import datetime
from pathlib import Path

import openpyxl
import pytest

from app.desk import export as fx
from app.desk import view as fd
from app.desk.view import json_safe
from tests.desk_fixtures import TODAY, lead, sc, snap

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")
WRITER = Path(__file__).resolve().parents[1] / "frontend" / "xlsx.js"
RUN = r"""
const vm = require("vm"), fs = require("fs");
global.window = {};
vm.runInThisContext(fs.readFileSync(process.argv[1], "utf8"));
const book = JSON.parse(fs.readFileSync(0, "utf8"));
try {
  process.stdout.write(JSON.stringify({ b64: Buffer.from(window.DeskXlsx.write(book)).toString("base64") }));
} catch (e) {
  process.stdout.write(JSON.stringify({ error: String(e.message), code: e.code || "" }));
}
"""


def written(book: dict) -> dict:
    out = subprocess.run([NODE, "-e", RUN, str(WRITER)], input=json.dumps(json_safe(book), ensure_ascii=False),
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def xlsx(book: dict) -> bytes:
    got = written(book)
    assert "b64" in got, got
    return base64.b64decode(got["b64"])


def snapshot():
    return snap([sc(1, invoice="", receipt="1", paid="3950.5", paid_date="2026-10-04", lead=["L1"]),
                 sc(2, invoice="00123", created="2026-10-06T08:00:00Z"),
                 dict(sc(3, created="2026-11-02T08:00:00Z"), name='=HYPERLINK("https://example.invalid/?d="&E3,"x")'),
                 dict(sc(4, created="2026-11-03T08:00:00Z", invoice="+972-1"), name="  ישראל\x0bישראלי <&\"> "),
                 sc(5, st="4", refund_date="2026-11-02", created="2026-09-05T08:00:00Z", refund="4000"),
                 sc(6, st="5", paid="100.05", refund="50", refund_date="2026-10-02", credit_ref="@ZK-1")],
                [], leads=[lead("L1", rep="סבטלנה")])


def described() -> dict:
    rows = fd.filter_b2c(fd.build_view(snapshot(), TODAY)["b2c"], "", "", "")
    about = {"taken_at": "24/11/2026 10:00:00", "screen_taken_at": "24/11/2026 09:00:00", "complete": False,
             "state_label": "לטיפול", "q": "", "books_lines": [("לפי הספרים", "נכון ל-24/11")], "period_label": "הכל"}
    sheets = [("מסמכים בלי חיוב", fx._DOC_COLUMNS + [("שם בכרטיס", lambda e: e["card"], "text", 26)],
               [{"number": "0045", "type_name": "חשבונית מס קבלה", "date": "2026-10-01", "card": "=1+1"},
                {"number": "0046", "type_name": "קבלה", "date": None, "card": ""}],
               ["מסמכים בלי חיוב", "משפט מתחת לרשימה"])]
    return fx.b2c_book(rows, about, sheets, with_id=True)


def look(wb) -> list:
    """Every sheet as openpyxl reads it: its name and view, and every cell's value, type, format, bold and fill."""
    out = []
    for ws in wb:
        cells = {}
        for r in ws.iter_rows():
            for c in r:
                if c.value is None and c.number_format == "General" and not c.font.b:
                    continue
                cells[c.coordinate] = (c.value, c.data_type, c.number_format, bool(c.font.b),
                                       c.fill.fgColor.rgb[-6:] if c.fill.fill_type == "solid" else None,
                                       c.alignment.horizontal, bool(c.alignment.wrap_text))
        widths = {k: d.width for k, d in ws.column_dimensions.items() if d.width}
        out.append((ws.title, ws.sheet_view.rightToLeft, ws.freeze_panes, ws.auto_filter.ref, widths, cells))
    return out


def test_the_browsers_file_is_the_servers_file_of_the_same_description():
    book = described()
    server = openpyxl.load_workbook(io.BytesIO(fx.write(book)))
    browser = openpyxl.load_workbook(io.BytesIO(xlsx(book)))
    mine, theirs = look(browser), look(server)
    assert [s[0] for s in mine] == ["תקבולים", "פרטים", "מסמכים בלי חיוב"]
    for a, b in zip(mine, theirs):
        assert a[:5] == b[:5], (a[:5], b[:5])
        assert a[5] == b[5], {k: (a[5].get(k), b[5].get(k)) for k in set(a[5]) | set(b[5]) if a[5].get(k) != b[5].get(k)}
    ws = browser["תקבולים"]
    heads = [c.value for c in ws[1]]
    # every sheet right to left; the list's header frozen with the filter on it; the total under the rows follows it
    assert all(s.sheet_view.rightToLeft for s in browser) and ws.freeze_panes == "A2"
    paid = heads.index("סכום ששולם") + 1
    col = openpyxl.utils.get_column_letter(paid)
    assert ws.cell(row=ws.max_row, column=paid).value == "=SUBTOTAL(109,%s2:%s7)" % (col, col)
    assert ws.cell(row=ws.max_row, column=2).value == fx.TOTAL_UNDER


def test_a_text_is_always_a_string_never_a_formula_an_error_or_a_number():
    data = xlsx(described())
    sheet = zipfile.ZipFile(io.BytesIO(data)).read("xl/worksheets/sheet1.xml").decode()
    # the only formulas are the totals; no error cell; a name that opens with "=" is the text it is
    assert sheet.count("<f>") == sheet.count("<f>SUBTOTAL(109,") > 0 and 't="e"' not in sheet
    ws = openpyxl.load_workbook(io.BytesIO(data))["תקבולים"]
    texts = {c.value: (c.data_type, c.number_format) for r in ws.iter_rows(min_row=2) for c in r if isinstance(c.value, str)}
    assert texts['=HYPERLINK("https://example.invalid/?d="&E3,"x")'] == ("s", "@")
    assert texts["00123"] == ("s", "@") and texts["+972-1"] == ("s", "@") and texts["@ZK-1"] == ("s", "@")
    # what XML cannot carry is dropped, the rest escaped and kept, its spaces too
    assert "  ישראלישראלי <&\"> " in texts and "&lt;&amp;&quot;&gt;" in sheet and 'xml:space="preserve"' in sheet
    lists = openpyxl.load_workbook(io.BytesIO(data))["מסמכים בלי חיוב"]
    assert [c.value for c in lists[2]] == ["0045", "חשבונית מס קבלה", datetime(2026, 10, 1), "=1+1"]
    assert lists.cell(row=2, column=4).data_type == "s"


def test_a_number_or_a_date_that_is_not_one_is_an_error_never_an_empty_cell():
    base = {"title": "t", "columns": [{"head": "a", "kind": "money", "width": 9}]}
    for kind, bad in (("money", "12,5"), ("money", "NaN"), ("amount", "1 000"), ("int", "x"), ("date", "2026-02-30"),
                      ("date", "26.11.2026")):
        got = written({"sheets": [dict(base, columns=[{"head": "a", "kind": kind, "width": 9}], rows=[[bad]])]})
        assert got.get("code") == "file" and "b64" not in got, (kind, bad, got)
    assert written({"sheets": []}).get("code") == "file"
    # null and "" are an empty cell in its column's format; a Decimal's exponent form is a number
    ws = openpyxl.load_workbook(io.BytesIO(xlsx({"sheets": [dict(base, rows=[[None], [""], ["1E+3"], ["-4000.50"]])]})))["t"]
    assert [(c.value, c.number_format) for c in ws["A"][1:]] == [(None, "#,##0.00"), (None, "#,##0.00"), (1000, "#,##0.00"),
                                                                (-4000.5, "#,##0.00")]


def test_the_zip_stores_every_part_with_its_crc_and_the_workbook_names_every_sheet():
    data = xlsx(described())
    z = zipfile.ZipFile(io.BytesIO(data))
    assert z.testzip() is None
    assert {i.compress_type for i in z.infolist()} == {zipfile.ZIP_STORED}
    assert z.namelist() == ["[Content_Types].xml", "_rels/.rels", "xl/workbook.xml", "xl/_rels/workbook.xml.rels",
                            "xl/styles.xml", "xl/worksheets/sheet1.xml", "xl/worksheets/sheet2.xml",
                            "xl/worksheets/sheet3.xml"]
    workbook = z.read("xl/workbook.xml").decode()
    assert "<definedName name=\"_xlnm._FilterDatabase\" localSheetId=\"0\" hidden=\"1\">'תקבולים'!$A$1:$AA$7" in workbook
    assert 'fullCalcOnLoad="1"' in workbook


def test_the_writer_reaches_nothing_and_keeps_nothing():
    """No request, no storage, no timer: it returns bytes, and that is all it does."""
    code = WRITER.read_text(encoding="utf-8")
    for word in (r"\bfetch\(", r"XMLHttpRequest", r"sendBeacon", r"localStorage", r"sessionStorage", r"indexedDB",
                 r"\bdocument\.", r"\bwindow\.(?!DeskXlsx\b)", r"setTimeout", r"console\.", r"\bimport\(", r"eval\(",
                 r"new Function"):
        assert not re.search(word, code), word


# ---- the review of 26.11: two texts whose browser file was not the server's -----------------------------------------------
def _one_column(texts: list) -> tuple:
    """(the server's cells, the browser's cells) of one text column holding these texts, read back with openpyxl."""
    import base64 as _b64
    book = {"sheets": [{"title": "תקבולים", "columns": [{"head": "t", "kind": "text", "width": 9}],
                        "rows": [[t] for t in texts], "total": "סה\"כ"},
                       {"title": "פרטים", "pairs": [["k", "v", "text"]], "widths": [10, 20]}]}
    server = openpyxl.load_workbook(io.BytesIO(fx.write(book)))["תקבולים"]
    got = written(book)
    assert "b64" in got, got
    browser = openpyxl.load_workbook(io.BytesIO(_b64.b64decode(got["b64"])))["תקבולים"]
    return ([server.cell(row=i + 2, column=1).value for i in range(len(texts))],
            [browser.cell(row=i + 2, column=1).value for i in range(len(texts))])


def test_a_carriage_return_reads_back_as_the_servers_file_holds_it():
    """XML reads a raw carriage return back as a line feed; xlsx.js writes it as a character reference, as the server's
    openpyxl (with lxml) does, so "\r\n" and a lone "\r" stay as typed."""
    server, browser = _one_column(["62601\r\n62602", "שורה\rשנייה", "\rבראש", "בסוף\r"])
    assert browser == server == ["62601\r\n62602", "שורה\rשנייה", "\rבראש", "בסוף\r"]


def test_a_text_longer_than_a_cell_holds_is_cut_as_the_server_cuts_it():
    """Excel's limit is 32,767 characters a cell (it refuses a longer one); openpyxl cuts to it, in code points."""
    astral = "\U0001F600"
    server, browser = _one_column(["א" * 40000, "ב" * 32767, astral * 32768, "ג" * 32766 + astral * 2])
    assert [len(v) for v in server] == [32767, 32767, 32767, 32767]
    assert browser == server
