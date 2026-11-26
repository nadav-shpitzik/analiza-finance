"""R4: a reading the server KNOWS is not whole (a charge row, or a linked row, answered without a column it asked for:
reading.py check_board / by_id `short_rows`, `complete: False`) is held and drawn by the page as whole: "נקראו כל ...",
the Excel button on, no warning - because the page reads only `view.checks.ok` / `b2c_ok`, and build_view's checks never
see the reading's per-board checks. The Excel file built from the very same reading says "הקריאה ממאנדיי לא שלמה: אין
להסתמך על הסכומים" (routes.py desk_export_file: "stricter than analiza-metrics, whose reading could not come short and still
be held"). analiza-metrics could not hold such a reading, so its page never had to say it."""
from __future__ import annotations

import io
import sys


import openpyxl  # noqa: E402

from app.desk import reading as rd  # noqa: E402
from tests.test_desk import desk, session_token  # noqa: E402,F401 - `desk` is the fixture
from tests.test_desk_page import drive  # noqa: E402


def _auth(user=1):
    return {"Authorization": "Bearer " + session_token(user)}


def test_a_reading_known_short_is_not_drawn_as_whole(desk):  # noqa: F811
    # "סטטוס תשלום" gone from the rows after the columns check: every payment answered without it
    desk.w.drop_cells = {rd.SC["status"]}
    body = desk.get(session_token(1)).json()
    assert body["reading"]["complete"] is False                                  # the server knows
    assert body["reading"]["boards"]["student_charge"]["ok"] is False
    # ...and the file built from the same reading says so first
    link = desk.client.post("/api/desk/export-link", headers=_auth(),
                            json={"tab": "b2c", "taken": body["taken_at"]}).json()["url"]
    info = openpyxl.load_workbook(io.BytesIO(desk.client.get(link).content))["פרטים"]
    assert "חלקיים" in str(info.cell(row=1, column=2).value)
    # the page, given that same answer, says every payment was read and offers the file as whole
    html = drive(body)["first"]
    assert "הנתונים ממאנדיי חלקיים" in html, html[html.find("fin-foot"):][:400]
