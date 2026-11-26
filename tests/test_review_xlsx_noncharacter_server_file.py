"""Hostile review of the line-break fix (76f193a), LOW: the server's Excel file still depends on lxml, and on a text the
browser's file writes, it is no file at all.

76f193a says "neither file depends on [lxml]" and makes app/desk/export.py `file_text` "a text as every file holds it". It
drops only openpyxl's ILLEGAL_CHARACTERS_RE (control characters). XML 1.0 also cannot hold U+FFFE and U+FFFF, which
frontend/xlsx.js `clean` drops (its own comment lists them) and nothing on the server does: monday's reader replaces only a
lone surrogate (app/monday/client.py `_text`), and the books' cleaning keeps a noncharacter (app/books/clean.py `normalize`
drops Cf and Cs; U+FFFF is Cn). One such character in any student's name, note or card name, and the server's file of every
list holding that row:
- with lxml (this machine): openpyxl raises ValueError, and GET /api/desk/export.xlsx answers 500;
- without lxml (CI, and by requirements.txt the deployment): it writes the raw character, and the file is not well-formed
  XML - Excel calls it corrupt (openpyxl cannot read it back either).
The browser's file of the same description is fine and drops it. So the two files differ, and the server's depends on lxml.

Fails on 76f193a: both the in-process write (lxml) and the write with lxml blocked (a subprocess) fail as above."""
from __future__ import annotations

import io
import subprocess
import sys
import textwrap
from pathlib import Path

import openpyxl

from app.desk import export as fx
from tests.test_desk_xlsx import written

REPO = Path(fx.__file__).resolve().parents[2]
TEXTS = ["נועה￿ ברק", "יואב￾ שטרן"]
WANT = ["נועה ברק", "יואב שטרן"]


def _book() -> dict:
    return {"sheets": [{"title": "תקבולים", "columns": [{"head": "t", "kind": "text", "width": 9}],
                        "rows": [[t] for t in TEXTS], "total": "סה\"כ"},
                       {"title": "פרטים", "pairs": [["k", "v", "text"]], "widths": [10, 20]}]}


def _cells(data: bytes) -> list:
    ws = openpyxl.load_workbook(io.BytesIO(data))["תקבולים"]
    return [ws.cell(row=i + 2, column=1).value for i in range(len(TEXTS))]


def _browsers() -> list:
    import base64
    got = written(_book())
    assert "b64" in got, got
    return _cells(base64.b64decode(got["b64"]))


def test_the_servers_file_with_lxml_is_the_browsers():
    assert _browsers() == WANT           # the browser's file drops the two characters
    try:
        data = fx.write(_book())
    except ValueError as exc:   # lxml refuses the character: the export route answers 500
        raise AssertionError("the server's writer refused the text: %s" % exc) from None
    assert _cells(data) == WANT


def test_the_servers_file_without_lxml_is_the_browsers(tmp_path):
    assert _browsers() == WANT
    (tmp_path / "lxml.py").write_text("raise ImportError('lxml blocked')\n", encoding="utf-8")
    code = textwrap.dedent("""
        import io, json, sys
        import openpyxl
        from openpyxl.xml import LXML
        assert not LXML
        from app.desk import export as fx
        book = json.loads(sys.stdin.read())
        data = fx.write(book)
        try:
            ws = openpyxl.load_workbook(io.BytesIO(data))["תקבולים"]
            print(json.dumps([ws.cell(row=i + 2, column=1).value for i in range(%d)]))
        except Exception as exc:
            print(json.dumps({"unreadable": type(exc).__name__ + ": " + str(exc)[:80]}))
    """ % len(TEXTS))
    import json
    run = subprocess.run([sys.executable, "-c", code], input=json.dumps(_book()), capture_output=True, text=True,
                         timeout=120, cwd=str(REPO), env={"PYTHONPATH": "%s:%s" % (tmp_path, REPO), "PATH": "/usr/bin:/bin"})
    assert run.returncode == 0, run.stderr[-2000:]
    assert json.loads(run.stdout.strip().splitlines()[-1]) == WANT, run.stdout
