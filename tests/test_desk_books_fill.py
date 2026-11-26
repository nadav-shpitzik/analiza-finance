"""D4 (Analiza-Monday backlog 0301): "רישום מחשבשבת" - the desk proposes, on Student Charge rows, the invoice number and the
receipt date the books give (app/desk/books.py `proposals`), and the person writes them from the browser (app/desk/view.py
DESK_ACTIONS `books_fill`; the server writes nothing, ADR-0188).

Never a rule of its own: the number is FR-C2's (app/recon/ref_fill.py), the date A1's (app/recon/receipt_dates.py), each
run as it is on the desk's own reading - so the desk's proposals are held here to the plans a session asks for on the same
rows (`ref_fill.plan`, `receipt_dates.plan`). And the desk's own conditions: the era (ADR-0168 §5: a row and a document
from 25.10.2026 on), a date only into an empty cell, a proposal and a mark never on one row, a proposal laid only from a
current part of the very reading shown.

Fixtures are synthetic and shaped like the incidents (the rules' contract §0.2): numbers, amounts and dates mirror the
records, names are invented, and no card carries an ID number."""
from __future__ import annotations

import json
import re

from app.desk import books as db
from app.desk import reading as rd
from app.desk import view as fd
from app.monday.client import flatten
from app.recon import cards
from app.recon import receipt_dates as a1
from app.recon import ref_fill as rf
from tests.desk_fixtures import TODAY, b2, reading_of, sc, snap
from tests.test_desk_books import copy
from tests.test_recon_ref_fill import a_reading, accounts_of, doc

ERA = cards.ERA_FROM.isoformat()
PAID = "3517"  # an amount that no id, number or date of these fixtures holds


def pay(id_, name: str, ref: str = "", paid: str = PAID, st: str = "1", made: str = "2026-11-10T08:00:00Z",
        day: str = "") -> dict:
    """A Student Charge row as the desk's reading holds it (and R0's: the same reader, `flatten`), active."""
    r = sc(id_, st=st, paid=paid, invoice=ref, paid_date=day, created=made, lead=["L%s" % id_])
    r.update(name=name, state="active")
    return r


# One fixture, every case of the desk's conditions: what the desk proposes, what the plans propose and the desk leaves to a
# session (before the era), what no rule proposes, and a row of every mark
ROWS = [pay(1, "נועה ברק"),                                            # number and date: its date is empty
        pay(2, "יואב שטרן", day="2026-11-11"),                          # number only: its date is typed
        pay(3, "מאיה גולן", ref="62603"),                               # date only: cites a valid invoice-receipt
        pay(4, "עידו רז | השלמת תשלום", paid="8000"),                   # the helper's balance payment: number and date
        pay(5, "שירה אלון", made="2026-10-26T08:00:00Z"),               # of the era, its document is not: none
        pay(6, "תומר נחום"),                                           # two invoice-receipts of its name and amount
        pay(7, "ליאת כרמי", ref="62607", paid="", st="0"),              # "לא שולם" citing an invoice-receipt: a mark
        pay(8, "אורי פז", ref="40000"),                                 # a number no document holds: a mark
        pay(9, "רוני שגב", ref="62609", paid="2000"),                   # the sums differ: a mark
        pay(10, "גל עמית", made="2026-09-10T08:00:00Z"),               # before the era: the plan's, never the desk's
        pay(11, "דור לב", ref="62611", made="2026-09-01T08:00:00Z"),    # before the era, its date: A1's, never the desk's
        pay(12, "אפרת חן", ref="62612", day="2026-11-05"),             # cites one, its date filled: nothing
        pay(13, "חברה בדויה")]                                         # named as a company's card: never its document
DOCS = [doc(62601, name="נועה ברק", card="20001", day="2026-11-12"),
        doc(62602, name="יואב שטרן", card="20002", day="2026-11-12"),
        doc(62603, name="מאיה גולן", card="20003", day="2026-11-03"),
        doc(62604, name="עידו רז", card="20004", total="8000", day="2026-11-18"),
        doc(62605, name="שירה אלון", card="20005", day="2026-10-20"),
        doc(62606, name="תומר נחום", card="20006", day="2026-11-12"),
        doc(62607, name="ליאת כרמי", card="20007", day="2026-11-04"),
        doc(62609, name="רוני שגב", card="20009", day="2026-11-06"),
        doc(62610, name="גל עמית", card="20010", day="2026-09-12"),
        doc(62611, name="דור לב", card="20011", day="2026-09-02"),
        doc(62612, name="אפרת חן", card="20012", day="2026-11-05"),
        doc(62613, name="תומר נחום", card="20013", day="2026-11-13"),
        doc(62614, name="חברה בדויה", card="30001", day="2026-11-12"),     # a company's card: never a student's
        doc(62615, name="נועה ברק", card="20015", day="2026-11-13")]       # B2B Charge cites it: never a student's
B2B = [b2(1, invoice="62615")]
KEYS = {"30001": ["חברה בדויה"]}
WANT = {"1": {"number": "62601", "date": "2026-11-12"}, "2": {"number": "62602"}, "3": {"date": "2026-11-03"},
        "4": {"number": "62604", "date": "2026-11-18"}}


def part_of(rows=ROWS, docs=DOCS, b2b=B2B, keys=None) -> tuple:
    """(the part compute gives, the view, the reading) for these rows and this copy, as the desk computes it."""
    reading = reading_of(snap(list(rows), list(b2b)))
    view = fd.build_view(reading, TODAY)
    return db.compute(reading, view, copy(list(docs)), KEYS if keys is None else keys), view, reading


def values(part: dict) -> dict:
    return {i: f["values"] for i, f in part["fills"].items()}


def row_date(view: dict, item_id: str) -> str:
    return {r["id"]: r for r in view["b2c"]["rows"]}[item_id]["date"]


# ---- the desk's proposals are the plans' -------------------------------------------------------------------------------
def test_the_desks_numbers_are_the_ref_fill_plans_actions_of_the_era_on_the_same_rows():
    """FR-C2 on the desk's own reading: the same rows, the same B2B rows, the same Accounts' keys and the same copy give
    the plan a session asks for (`ref_fill.plan`, R0's reading) and the desk's proposals - the desk's are exactly the plan's
    actions whose row and document are of the era; the plan's others are a session's cleanup."""
    part, view, _r = part_of()
    plan = rf.plan("analiza", a_reading(ROWS, B2B, accounts_of(KEYS)), DOCS)
    actions = {a["item_id"]: a for a in plan["actions"]}
    assert set(actions) == {"1", "2", "4", "5", "10"}             # the fixture reaches the era's two ways out
    era = {i: a["value_proposed"] for i, a in actions.items() if row_date(view, i) >= ERA and a["document_date"] >= ERA}
    assert {i: v["number"] for i, v in values(part).items() if "number" in v} == era == {
        "1": "62601", "2": "62602", "4": "62604"}
    # the two the desk leaves: a row of the era whose document is older (5), and a row older than the era (10)
    assert actions["5"]["document_date"] < ERA <= row_date(view, "5") and row_date(view, "10") < ERA


def test_the_desks_dates_are_a1s_plan_on_the_rows_as_they_would_be_with_the_numbers_written():
    """FR-C5 (A1's `b2c_receipt_date_fill`) on the desk's own reading: a row that already cites a valid invoice-receipt with
    an empty date is proposed A1's date as the rows are (3), and a row proposed a number is proposed the date A1 gives once
    that number is in its cell (1, 4) - a filled date never (2: typed; 12: the same day). Of the era only (11 is older)."""
    part, view, _r = part_of()
    reading = a_reading(ROWS, B2B, accounts_of(KEYS))
    now = {a["item_id"]: a["proposed"] for a in a1.plan("analiza", reading, DOCS)["actions"]}
    assert now == {"3": "2026-11-03", "11": "2026-09-02"}
    numbers = {a["item_id"]: a["value_proposed"] for a in rf.plan("analiza", reading, DOCS)["actions"]}
    written = [dict(r, **{rf.COLUMN: numbers[r["id"]]}) if r["id"] in numbers else r for r in ROWS]
    after = {a["item_id"]: a["proposed"] for a in a1.plan("analiza", a_reading(written, B2B, accounts_of(KEYS)), DOCS)["actions"]}
    era = {i: d for i, d in after.items() if row_date(view, i) >= ERA and d >= ERA}
    assert {i: v["date"] for i, v in values(part).items() if "date" in v} == era == {
        "1": "2026-11-12", "3": "2026-11-03", "4": "2026-11-18"}
    assert values(part) == WANT


def test_the_date_alone_is_proposed_when_the_row_cites_its_number_and_only_into_an_empty_cell():
    """A row that cites a valid invoice-receipt is proposed no number (FR-C1 is A1's), only its date, and only when its
    "תאריך תקבול" is empty: typed, or holding what A1 cannot read, it is never proposed again."""
    rows = [pay(1, "מאיה גולן", ref="62603"), pay(2, "יואב שטרן", ref="62602", day="2026-11-01"),
            pay(3, "אפרת חן", ref="62612", day="2026-11-05")]
    part, _v, _r = part_of(rows, DOCS, (), {})
    assert values(part) == {"1": {"date": "2026-11-03"}}
    # and A1's own reading of the cell: an unreadable date is no empty cell
    weird = [dict(pay(1, "מאיה גולן", ref="62603"), **{a1.COLUMN: "בקרוב"})]
    assert values(part_of(weird, DOCS, (), {})[0]) == {}


def test_only_the_eras_rows_and_documents_are_proposed():
    """ADR-0168 §5: Chava records from 25.10.2026 and does not backfill. A row the desk dates before it, or whose document
    is dated before it, is proposed nothing here - not its number and not its date - though the plans propose them: a
    session's cleanup (backlog 0301 C1). The era's first day is in it."""
    rows = [pay(1, "נועה ברק", made="2026-10-24T20:00:00Z"),          # 24.10 in UTC, 24.10 in Israel: before
            pay(2, "יואב שטרן", made="2026-10-24T21:30:00Z"),          # 22:30 UTC on 24.10 is 25.10 in Israel: of the era
            pay(3, "מאיה גולן", made="2026-11-20T08:00:00Z"),          # of the era, its document of 24.10: before
            pay(4, "עידו רז", ref="62604", paid="8000", made="2026-10-25T08:00:00Z"),   # the era's first day, its date
            pay(5, "שירה אלון", ref="62605", made="2026-11-01T08:00:00Z")]             # of the era, citing a document of 20.10
    docs = [doc(62601, name="נועה ברק", card="20001", day="2026-10-25"),
            doc(62602, name="יואב שטרן", card="20002", day="2026-10-26"),
            doc(62603, name="מאיה גולן", card="20003", day="2026-10-24"),
            doc(62604, name="עידו רז", card="20004", total="8000", day="2026-10-25"),
            doc(62605, name="שירה אלון", card="20005", day="2026-10-20")]
    part, view, _r = part_of(rows, docs, (), {})
    assert row_date(view, "1") == "2026-10-24" and row_date(view, "2") == "2026-10-25"
    plan = {a["item_id"] for a in rf.plan("analiza", a_reading(rows, (), ()), docs)["actions"]}
    assert plan == {"1", "2", "3"}                                    # every one of them the plan proposes
    assert values(part) == {"2": {"number": "62602", "date": "2026-10-26"}, "4": {"date": "2026-10-25"}}


def test_a_proposal_and_a_mark_never_meet_on_one_row():
    """Every mark is of a row that cites a number (A1's buckets of a cited number, and `books_receipt_unpaid` of a row whose
    status says no money came in), and a number is proposed only into an empty cell, a date only to a row whose status says
    the money came in: the fixture reaches all three marks and all three kinds of proposal, on different rows."""
    part, _v, _r = part_of()
    marks = {i: [m["kind"] for m in ms] for i, ms in part["marks"].items()}
    assert marks == {"7": ["books_receipt_unpaid"], "8": ["books_ref_invalid"], "9": ["books_amount_differs"]}
    assert set(part["fills"]) == set(WANT) and not set(part["marks"]) & set(part["fills"])


def test_each_proposed_document_on_the_list_names_its_row_and_no_other_does():
    """The students' list (documents of the era no charge cites) carries, on a document the desk proposes, the row and the
    number - the row's name shown whole; a document with no proposal (two candidates of one name and amount) carries none;
    a proposal's document older than the era is not on the list at all."""
    part, _v, _r = part_of()
    listed = {e["number"]: e for e in part["students"]}
    assert set(listed) == {"62601", "62602", "62604", "62606", "62613"}
    assert {n: e["proposal"] for n, e in listed.items() if "proposal" in e} == {
        "62601": {"item_id": "1", "name": "נועה ברק", "number": "62601"},
        "62602": {"item_id": "2", "name": "יואב שטרן", "number": "62602"},
        "62604": {"item_id": "4", "name": "עידו רז | השלמת תשלום", "number": "62604"}}
    assert part["places"][62605] == "outside" and part["places"][62615] == "cited" and part["places"][62614] == "companies"


def test_a_proposal_says_its_chip_and_its_sentence_in_the_legends_words():
    """The chip is the legend's (view.LEGEND_FLAGS), filled on the server: the number, or the date alone; the sentence names
    the document, its day and its total including VAT, and what "רישום מחשבשבת" writes - never a card's name."""
    part, _v, _r = part_of()
    marks = {i: f["mark"] for i, f in part["fills"].items()}
    assert {i: (m["kind"], m["chip"], m["documents"]) for i, m in marks.items()} == {
        "1": ("books_ref_fill", "בחשבשבת: 62601", ["62601"]), "2": ("books_ref_fill", "בחשבשבת: 62602", ["62602"]),
        "3": ("books_date_fill", "תאריך מחשבשבת: 03.11.2026", ["62603"]),
        "4": ("books_ref_fill", "בחשבשבת: 62604", ["62604"])}
    texts = {k: t for k, t, _x in fd.LEGEND_FLAGS["b2c"]}
    assert texts["books_ref_fill"] == "בחשבשבת: {number}" and texts["books_date_fill"] == "תאריך מחשבשבת: {date}"
    assert marks["1"]["text"] == (
        "בחשבשבת חשבונית מס/קבלה 62601 מ-12.11.2026 על 3,517.00 ₪ כולל מע\"מ: היחידה בשם שבשורה, בסכום ששולם ובתוך 30 יום "
        "מהתאריך שלה, ששום שורה לא מפנה אליה. רישום מחשבשבת מעדכן את המספר באסמכתת חשבונית ואת התאריך בתאריך תקבול.")
    assert marks["2"]["text"].endswith("רישום מחשבשבת מעדכן את המספר באסמכתת חשבונית.")
    assert marks["3"]["text"] == ("באסמכתת חשבונית: חשבונית מס/קבלה 62603 מ-03.11.2026 על 3,517.00 ₪ כולל מע\"מ בחשבשבת. "
                                  "תאריך תקבול ריק, ורישום מחשבשבת מעדכן בו את התאריך הזה.")
    for m in marks.values():
        assert not any(d["account_name"] in m["text"] for d in DOCS), m["text"]


def test_a_documents_proposal_leaves_the_server_only_from_a_current_part_of_the_reading_shown():
    """`merge`: a proposal is laid from a part that is `ok` and was computed on this very reading; a held part (a copy
    that is not whole now, or stale), one of another reading (an answer at once may carry the last part), or none, lays
    none - the list keeps its documents, without a proposal."""
    part, view, reading = part_of()

    def proposals(p: dict) -> dict:
        return {e["number"]: e["proposal"]["item_id"] for e in db.merge(view, p)["b2c"]["books"]["documents"]
                if "proposal" in e}

    assert part["reading_at"] == view["read_at"] == reading["read_at"]
    assert proposals(dict(part, state="ok")) == {"62601": "1", "62602": "2", "62604": "4"}
    for other in (dict(part, state="held"), dict(part, state="ok", reading_at="2026-11-24T09:00:00+00:00"),
                  dict(part, state="ok", reading_at=None), dict(part, state="none")):
        merged = db.merge(view, other)["b2c"]["books"]["documents"]
        assert proposals(other) == {} and len(merged) == 5, other["state"]


def test_the_desks_reading_holds_every_cell_the_rules_read():
    """FR-C2 and A1 read of a Student Charge row its name, its status by index, "סכום ששולם", "אסמכתת חשבונית", "תאריך
    תקבול", the day it was made and its state; of B2B Charge, its "אסמכתת חשבונית". The desk's reading asks for every one
    (ROW_COLUMNS), and its reader gives the rest of every row (`flatten`) - so the desk's proposals are the rules' on the
    rows the desk shows."""
    for col in (rf.COLUMN, a1.AMOUNT_COLUMN, a1.COLUMN, a1.STATUS_COLUMN):
        assert col in rd.ROW_COLUMNS["student_charge"], col
    assert rf.B2B_REF_COLUMN in rd.ROW_COLUMNS["b2b_charge"]
    item = {"id": 7, "name": "נועה ברק", "state": "active", "created_at": "2026-11-10T08:00:00Z", "board": {"id": "1"},
            "column_values": [{"id": a1.STATUS_COLUMN, "text": "שולם", "label": "שולם", "index": 1}]}
    row = flatten(item, rd.ROW_COLUMNS["student_charge"])
    assert {k: row[k] for k in ("name", "state", "created_at", a1.STATUS_COLUMN + "#")} == {
        "name": "נועה ברק", "state": "active", "created_at": "2026-11-10T08:00:00Z", a1.STATUS_COLUMN + "#": "1"}


# ---- the action (D4): what the page may write, and how --------------------------------------------------------------------
def test_books_fill_writes_only_what_the_books_propose_and_each_cell_only_when_it_is_empty():
    """"רישום מחשבשבת" is one step of two writes, nothing typed and nothing of its own: the number into "אסמכתת חשבונית"
    (text) and the date into "תאריך תקבול" (date), each the value the books propose under its key, each only into an empty
    cell (`if_empty`, as "חויב" writes its demand day) - never today's date, never a box."""
    a = fd.DESK_ACTIONS[db.FILL_ACTION]
    assert a["board"] == "sc" and a["button"] == "רישום מחשבשבת" == fd.BOOKS_FILL_BUTTON
    step, = a["steps"]
    assert step == [
        {"column": "text_mm6d6s97", "title": "אסמכתת חשבונית", "kind": "text", "books": "number", "if_empty": True},
        {"column": "date_mm8yh4bn", "title": "תאריך תקבול", "kind": "date", "books": "date", "if_empty": True}]
    assert a["after"] == "אסמכתת חשבונית ותאריך תקבול נכתבים רק כשהם ריקים במאנדיי, ורק מה שהשולחן מציע לפי נתוני חשבשבת."
    # the allowlist gains exactly the date, as a date (spec §7.3); the number's column was on it (sc_invoice)
    assert fd.desk_writes()[fd.STUDENT_CHARGE]["date_mm8yh4bn"] == "date"
    assert fd.desk_writes()[fd.STUDENT_CHARGE]["text_mm6d6s97"] == "text"
    # the view's own rules never offer it: a row offers it only when the books propose something for it (books.merge)
    rows = [pay(1, "נועה ברק"), pay(2, "מאיה גולן", ref="62603")]
    view = fd.build_view(reading_of(snap(rows, [])), TODAY)
    assert all(db.FILL_ACTION not in r["actions"] for r in view["b2c"]["rows"])


def test_what_a_proposal_rests_on_is_the_rules_own_status_and_amount_read_never_written():
    """The hostile review of D4: the page read only the two cells it writes before writing, while the legend promised that a
    row changed in monday since the reading is not written. The action names what a proposal rests on besides them -
    FR-C2's conditions 1 and 3, A1's columns: the status by its index, the amount - and the row's field the reading holds
    each in, so the page reads them with the two cells and holds them to the reading. Read, never written: not on
    desk_writes."""
    rests = fd.DESK_ACTIONS[db.FILL_ACTION]["rests_on"]
    assert [(c["column"], c["title"], c["kind"]) for c in rests] == [
        (a1.STATUS_COLUMN, "סטטוס תשלום", "status"), (a1.AMOUNT_COLUMN, "סכום ששולם", "number")]
    row = {r["id"]: r for r in fd.build_view(reading_of(snap([pay(1, "נועה ברק", st="9", paid="3517.5")], [])), TODAY)["b2c"]["rows"]}["1"]
    assert {c["field"]: fd.json_safe(row[c["field"]]) for c in rests} == {"status_idx": "9", "paid_typed": "3517.5"}
    # it adds nothing to what the page may write: the amount is not on it, the status only with the refunds' two indexes
    writes = fd.desk_writes()[fd.STUDENT_CHARGE]
    assert a1.AMOUNT_COLUMN not in writes and writes[a1.STATUS_COLUMN] == [int(fd.P_REFUNDED), int(fd.P_PART)]


def test_the_legend_says_exactly_what_the_page_checks_before_it_writes_a_proposal():
    """The legend's promise (the hostile review of D4) names each cell the page reads and holds to the reading - the two it
    writes and what the proposal rests on - another row citing the number, and a newer reading; and nothing it does not."""
    text = dict(fd.LEGEND["b2c"])["%s · %s · %s" % (fd.BOOKS_FILL_BUTTON, fd.BOOKS_PROPOSAL, fd.BOOKS_FILL_ALL)]
    a = fd.DESK_ACTIONS[db.FILL_ACTION]
    said = text[text.index("שורה שהשתנתה במאנדיי מאז הקריאה לא נכתבת"):text.index("תא שאינו ריק לא נכתב")]
    titles = [w["title"] for s in a["steps"] for w in s] + [c["title"] for c in a["rests_on"]]
    assert "שורה שהשתנתה במאנדיי מאז הקריאה לא נכתבת: שורה ש%s, %s, %s או %s שבה אינם מה שהמסך קרא." % tuple(titles) in said
    assert "כשאסמכתת חשבונית של שורה אחרת בלוח Student Charge היא כבר המספר שהיא מקבלת" in said
    assert "וכשקריאה חדשה של הלוחות, שנחתה בזמן שהאישור פתוח, כבר לא מציעה לה את אותו הדבר" in said
    assert "וממש לפני השמירה קורא כל שורה שוב" in text and "שם" not in said.split()


# ---- the merge (D4): a proposal on its row, from a current part of the reading shown only -------------------------------
def test_each_proposal_is_laid_on_its_row_as_its_chip_its_values_and_the_action_and_moves_no_row():
    """`merge` lays, on a row the books propose a fill to, its chip among the row's flags (in FLAG_ORDER's words, beside
    the marks' in `books`), its values (`books_fill`) and the action that writes them (`books_fill`, appended to what its
    state offers) - never `books_task`: a proposal joins no list, changes no state, no sum and no count of "לטיפול"."""
    part, view, _r = part_of()
    merged = db.merge(view, dict(part, state="ok"))
    rows = {r["id"]: r for r in merged["b2c"]["rows"]}
    plain = {r["id"]: r for r in view["b2c"]["rows"]}
    for item_id, values in WANT.items():
        r, kind = rows[item_id], "books_ref_fill" if "number" in values else "books_date_fill"
        assert r["books_fill"] == values and r["actions"] == plain[item_id]["actions"] + ["books_fill"]
        assert r["flags"] == plain[item_id]["flags"] + [kind] and [m["kind"] for m in r["books"]] == [kind]
        assert not r.get("books_task") and r["state"] == plain[item_id]["state"]
    assert not any(r.get("books_fill") or "books_fill" in r["actions"] for i, r in rows.items() if i not in WANT)
    assert merged["b2c"]["periods"] == [dict(p, tasks=p["tasks"]) for p in db.merge(view, dict(part, state="ok", fills={}))["b2c"]["periods"]]
    # the files' "מה לבדוק" says the chip in its words, as the page's column does
    assert "בחשבשבת: 62601" in fd.row_reasons("b2c", rows["1"]) and "תאריך מחשבשבת: 03.11.2026" in fd.row_reasons("b2c", rows["3"])
    # a held part, one of another reading, lays nothing on a row either
    for other in (dict(part, state="held"), dict(part, state="ok", reading_at="2026-11-24T09:00:00+00:00")):
        rows = db.merge(view, other)["b2c"]["rows"]
        assert not any(r.get("books_fill") or "books_fill" in r["actions"] or set(r["flags"]) & set(db.FILLS) for r in rows)


# ---- who sees a proposal (spec §6.4.1): the students' tab's, through the route --------------------------------------------
from tests.test_desk_books import booked  # noqa: E402,F401 - the fixture


def test_a_proposal_reaches_a_member_of_student_charge_and_never_a_member_of_b2b_charge_only(booked):  # noqa: F811
    """GET /api/desk on D3's route fixture, its row 103 now paid as its card's invoice-receipt 62503 (name, amount and day
    of the era): a member of Student Charge receives the proposal on the row and on the document; a member of B2B Charge
    only receives the companies' tab and nothing of it - no chip, no value, no proposal, not the document's number."""
    booked.w.set(103, touch=False, paid="4000")
    for user in (1, 2):
        v = booked.get(user).json()["view"]
        rows = {r["id"]: r for r in v["b2c"]["rows"]}
        assert rows["103"]["books_fill"] == {"number": "62503", "date": "2026-11-03"}, user
        assert "books_fill" in rows["103"]["actions"] and "books_ref_fill" in rows["103"]["flags"]
        assert [e.get("proposal") for e in v["b2c"]["books"]["documents"]] == [
            {"item_id": "103", "name": "תלמיד-גימל", "number": "62503"}]
    text = booked.get(3).text
    v = json.loads(text)["view"]
    assert v["b2c"] is None and v["books_unclear"] is None and v["b2b"]["books"]["documents"]
    for word in ("books_fill", "proposal", "books_ref_fill", "62503", "תלמיד-גימל", "בחשבשבת: 62503"):
        assert word not in text, word


# ---- QA 26.11 (9): why a candidate has no proposal -------------------------------------------------------------------------
# Each row named like a document of the list, and the reason FR-C2 gives it, none proposed this document but one
WHY_ROWS = [pay(21, "מיכל דגן", ref="62555"),                               # cites another number: has_ref
            pay(22, "יונתן ברק", paid="5000"),                              # another amount: amount_differs
            pay(23, "שקד לוין"),                                           # its document 40 days on: date_far
            pay(24, "נגה שחר", paid="", st="0"),                            # "לא שולם": not_money_in
            pay(25, "עומר טל"), pay(26, "עומר טל"),                          # two rows on one invoice: document_wanted_twice
            pay(27, "רון גל", made="2026-10-20T08:00:00Z"),                 # FR-C2's, before the desk's era: era
            pay(28, "דפנה רוז"),                                           # proposed 62708, a candidate of 62709 too
            pay(29, "אלה כץ"),                                             # a tax invoice of its name and sum
            pay(30, "עידן פרץ")]                                           # two invoice-receipts: two_candidates
WHY_DOCS = [doc(62701, name="מיכל דגן", card="20101"), doc(62702, name="יונתן ברק", card="20102"),
            doc(62703, name="שקד לוין", card="20103", day="2026-12-20"), doc(62704, name="נגה שחר", card="20104"),
            doc(62705, name="עומר טל", card="20105"), doc(62707, name="רון גל", card="20107", day="2026-10-26"),
            doc(62708, name="דפנה רוז", card="20108"), doc(62709, name="דפנה רוז", card="20109", total="5000"),
            doc(62710, name="אלה כץ", card="20110", doc_type=1), doc(62711, name="אלה כץ", card="20110", total="5000"),
            doc(62712, name="עידן פרץ", card="20112"), doc(62713, name="עידן פרץ", card="20113")]
WHY_WANT = {"62701": [("21", "has_ref")], "62702": [("22", "amount_differs")], "62703": [("23", "date_far")],
            "62704": [("24", "not_money_in")], "62705": [("26", "document_wanted_twice"), ("25", "document_wanted_twice")],
            "62707": [("27", "era")], "62708": [("28", None)], "62709": [("28", "other_document")],
            "62710": [("29", "tax_invoice_waits")], "62711": [("29", "tax_invoice_waits")],
            "62712": [("30", "two_candidates")], "62713": [("30", "two_candidates")]}


def test_a_candidate_with_no_proposal_says_why_in_fr_c2s_words_for_the_row():
    """QA 26.11 (9): a document with a candidate by name and no proposal said nothing of why (the candidate row already
    cited another number, so FR-C2 proposed nothing). Each candidate carries the reason now, computed with the proposals,
    from FR-C2's own analysis of this reading: the row's bucket in a few words - or that it is proposed another document, or
    that it is before the desk's era. The row the document is proposed to carries none (its proposal says it)."""
    part, view, _r = part_of(WHY_ROWS, WHY_DOCS, (), {})
    listed = {e["number"]: e for e in part["students"]}
    assert {n: [(c["id"], c.get("why_key")) for c in e["candidates"]] for n, e in listed.items()} == WHY_WANT
    assert values(part) == {"28": {"number": "62708", "date": "2026-11-12"}}
    whys = {(n, c["id"]): c["why"] for n, e in listed.items() for c in e["candidates"] if "why" in c}
    assert whys[("62701", "21")] == "לשורה כבר יש אסמכתה 62555"
    assert whys[("62709", "28")] == "לשורה מתאימה חשבונית אחרת: 62708"
    assert whys[("62707", "27")] == "השורה מלפני 25.10.2026, ולכן אין כאן הצעה"
    assert whys[("62703", "23")] == db.CANDIDATE_WHY["date_far"] and "30 יום" in whys[("62703", "23")]
    # never a rule of its own: the key is FR-C2's bucket for the row, on the same rows and copy ("fill" is said as another
    # document, or the era)
    c2 = rf.analyse(WHY_ROWS, WHY_DOCS, (), {})["rows"]
    for (number, item_id), _text in whys.items():
        key = next(c["why_key"] for c in listed[number]["candidates"] if c["id"] == item_id)
        assert key == c2[item_id]["bucket"] or (key in ("era", "other_document") and c2[item_id]["bucket"] == "fill")
    # every word of it is closed, and none is a name or an ID number
    names = {r["name"] for r in WHY_ROWS} | {d["account_name"] for d in WHY_DOCS}
    assert set(db.CANDIDATE_WHY) == set(rf.BUCKETS) - {"fill"} | {"other_document", "era"}
    for text in whys.values():
        assert not any(n in text for n in names) and not re.search(r"\d{8,}", text), text
