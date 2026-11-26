"""R5 (Analiza-Monday backlog 0301): "לא פער" - the spec §6.1 item 4 and §2.4. An alert of the books a person marks as not
a gap stays hidden while its evidence is the same, and comes back when it changes: the evidence is a hash over a fixed set
of fields per kind, computed by the server (app/desk/dismissals.py); DeskBooks reads the live dismissals once per
computation and hides by them (app/desk/books.py); the routes POST /api/alerts/not-a-gap and /undo (app/desk/routes.py).
The table and its store: tests/test_desk_dismissals_store.py.

Each rule with its test: each kind hidden and returned by each of its fields and by nothing else, membership per kind, the
evidence recomputed on the server (a forged hash refused), an alert the part does not show refused, undo by its dismisser
only, one live dismissal per subject through the route. Fixtures are synthetic: the rows and documents of
tests/test_desk_books_fill.py (every mark and proposal of D3 and D4) and one unclear document."""
from __future__ import annotations

import json
from decimal import Decimal

import pytest

from app.desk import books as db
from app.desk import dismissals as dz
from app.desk import dismissals_store as ds
from app.desk import view as fd
from tests.conftest import needs_pg
from tests.dismissal_fixtures import MemoryDismissals, failing
from tests.test_desk_dismissals_store import _rows, pg  # noqa: F401 - `pg` is the fixture

COMPANY = "wizdb2245n5"


# ---- the evidence: each kind's fields, each from its source, and nothing else -----------------------------------------------
from tests.desk_fixtures import TODAY, reading_of, snap  # noqa: E402
from tests.test_desk_books import copy  # noqa: E402
from tests.test_desk_books_fill import B2B, DOCS, KEYS, ROWS, pay  # noqa: E402
from tests.test_recon_ref_fill import doc  # noqa: E402

UNCLEAR_DOC = doc(50501, doc_type=1, card="20099", name="כרטיס לא ברור", total="5900", day="2026-11-08")
ALL_DOCS = DOCS + [UNCLEAR_DOC]
# one alert of each kind in ROWS and ALL_DOCS: (kind, its subject)
ONE_OF_EACH = {"books_receipt_unpaid": "7", "books_ref_invalid": "8", "books_amount_differs": "9", "books_ref_fill": "1",
               "books_date_fill": "3", "books_doc_students": "62606", "books_doc_companies": "62614",
               "books_doc_unclear": "50501"}


def computed(rows=ROWS, docs=ALL_DOCS, b2b=B2B, keys=KEYS) -> tuple:
    """(the part compute gives - nothing hidden - the view, the reading)."""
    reading = reading_of(snap(list(rows), list(b2b)))
    view = fd.build_view(reading, TODAY)
    return db.compute(reading, view, copy(list(docs)), keys), view, reading


def alerts(part: dict) -> dict:
    """{(kind, subject): evidence} of every alert in a part compute gave."""
    out = {(m["kind"], i): m["evidence"] for i, ms in part["marks"].items() for m in ms}
    out.update({(f["mark"]["kind"], i): f["mark"]["evidence"] for i, f in part["fills"].items()})
    out.update({(e["kind"], e["subject"]): e["evidence"] for p in dz.DOC_KINDS for e in part[p]})
    return out


def evidence_of(kind: str, rows=ROWS, docs=ALL_DOCS, b2b=B2B, keys=KEYS):
    return alerts(computed(rows, docs, b2b, keys)[0]).get((kind, ONE_OF_EACH[kind]))


def with_row(rows: list, item_id: str, **cells) -> list:
    """The rows, one of them with other cells (by the desk's keys, as tests/desk_fixtures.sc takes them; a status column by
    its index, with its label)."""
    from tests.desk_fixtures import status

    out = []
    for r in rows:
        if r["id"] == item_id:
            r = dict(r)
            for k, v in cells.items():
                if k in ("status", "receipt"):
                    status(r, fd.SC[k], v, fd.PAY if k == "status" else fd.RECEIPT)
                else:
                    r[k if k == "name" else fd.SC[k]] = v
        out.append(r)
    return out


def with_doc(docs: list, number: int, **fields) -> list:
    return [dict(d, **fields) if d["doc_number"] == str(number) else d for d in docs]


def test_every_kind_is_in_the_part_once_and_carries_its_evidence():
    part = computed()[0]
    got = alerts(part)
    for kind, subject in ONE_OF_EACH.items():
        assert len(got.get((kind, subject)) or "") == 64, kind
    assert {k for k, _s in got} == set(dz.KINDS)
    # and each document's subject is its number as A1 reads one, its kind its list's
    for place, kind in dz.DOC_KINDS.items():
        assert all(e["kind"] == kind and e["subject"] == e["number"] for e in part[place]), place


def test_each_kinds_fields_are_its_evidence_list():
    part, view, reading = computed()
    rows = {r["id"]: dict(r, paid_raw=None) for r in view["b2c"]["rows"]}
    entry = {"documents": ["40000"], "group": None}
    for kind in dz.MARK_KINDS:
        assert tuple(dz.mark_evidence(kind, "8", entry, rows["8"], {})) == dz.EVIDENCE[kind]
    for kind in dz.DOC_KINDS.values():
        assert tuple(dz.document_evidence(kind, UNCLEAR_DOC, "unclear")) == dz.EVIDENCE[kind]
    for kind in dz.FILL_KINDS:
        assert tuple(dz.fill_evidence(kind, "1", {"number": "62601"})) == dz.EVIDENCE[kind]


# A mark's fields, each moved at its source - the row's cells, the copy's documents - with the mark still on the row
MARK_MOVES = [
    ("cited", "books_ref_invalid", {"invoice": "40001"}, None),
    ("cited", "books_amount_differs", {"invoice": "62609, 62606"}, None),
    ("amount", "books_ref_invalid", {"paid": "3600"}, None),
    ("amount", "books_amount_differs", {"paid": "2100"}, None),
    ("amount", "books_receipt_unpaid", {"paid": "3517"}, None),          # empty, then equal to the document: still marked
    ("status", "books_ref_invalid", {"status": "5"}, None),
    ("status", "books_receipt_unpaid", {"status": "2"}, None),           # "יש בעיה": still no money in, still marked
    ("documents", "books_ref_invalid", None, ("add", doc(40000, status=0, day="2026-11-02"))),     # a draft: "טיוטה"
    ("documents", "books_amount_differs", None, ("set", 62609, {"total_incl": Decimal("3000")})),
    ("documents", "books_receipt_unpaid", None, ("set", 62607, {"value_date": TODAY.replace(day=5)})),
    ("group", "books_amount_differs", None, ("row", pay(20, "רוני שגב", ref="62609", paid="500"))),
]


@pytest.mark.parametrize("field, kind, cells, change", MARK_MOVES, ids=["%s-%s" % (m[0], m[1]) for m in MARK_MOVES])
def test_a_marks_evidence_moves_with_each_of_its_fields(field, kind, cells, change):
    subject = ONE_OF_EACH[kind]
    rows, docs = list(ROWS), list(ALL_DOCS)
    if cells:
        rows = with_row(rows, subject, **cells)
    if change and change[0] == "add":
        docs = docs + [change[1]]
    if change and change[0] == "set":
        docs = with_doc(docs, change[1], **change[2])
    if change and change[0] == "row":
        rows = rows + [change[1]]
    before, after = evidence_of(kind), evidence_of(kind, rows, docs)
    assert after is not None, "the change took the mark away: it moves no field of a mark still shown"
    assert after != before, field


@pytest.mark.parametrize("kind", dz.MARK_KINDS)
def test_a_marks_evidence_stays_when_only_its_name_its_date_its_receipt_or_a_cards_name_moves(kind):
    subject = ONE_OF_EACH[kind]
    rows = with_row(ROWS, subject, name="שם אחר לגמרי", paid_date="2026-11-20", receipt="1")
    number = {"books_receipt_unpaid": 62607, "books_amount_differs": 62609}.get(kind)
    docs = with_doc(ALL_DOCS, number, account_name="כרטיס בשם אחר") if number else ALL_DOCS
    assert evidence_of(kind, rows, docs) == evidence_of(kind)


DOC_FIELDS = [("number", {"doc_number": "50502"}), ("type", {"doc_type": 2}), ("status", {"status": 0}),
              ("total", {"total_incl": Decimal("5901")}), ("total_excl", {"total_excl": Decimal("5000.01")}),
              ("date", {"value_date": TODAY.replace(day=9)}), ("card", {"account_name": "כרטיס אחר"})]


@pytest.mark.parametrize("field, change", DOC_FIELDS, ids=[f for f, _c in DOC_FIELDS])
def test_a_documents_evidence_moves_with_each_of_its_fields(field, change):
    kind = "books_doc_unclear"
    assert dz.digest(dz.document_evidence(kind, dict(UNCLEAR_DOC, **change), "unclear:no_invoice_receipt")) != \
        dz.digest(dz.document_evidence(kind, UNCLEAR_DOC, "unclear:no_invoice_receipt")), field


def test_a_documents_evidence_moves_with_its_side_and_not_with_its_key_its_stock_id_or_a_cards_spacing():
    kind, base = "books_doc_unclear", dz.digest(dz.document_evidence("books_doc_unclear", UNCLEAR_DOC, "unclear:no_invoice_receipt"))
    assert dz.digest(dz.document_evidence(kind, UNCLEAR_DOC, "unclear:invoice_receipt_and_proforma")) != base
    for same in ({"account_key": "20100"}, {"stock_id": 999}, {"account_name": "  כרטיס   לא ברור "},
                 {"total_incl": Decimal("5900.00")}):
        assert dz.digest(dz.document_evidence(kind, dict(UNCLEAR_DOC, **same), "unclear:no_invoice_receipt")) == base, same


def test_a_listed_documents_evidence_is_its_own_doc_and_list_and_moves_in_the_part_too():
    """Bound at its source: the part's entries carry the evidence of their own document and list; a proforma on the unclear
    document's card moves its side's why, and a re-totalled students' document its total - each brings it back."""
    part = computed()[0]
    by = {(e["kind"], e["subject"]): e for p in dz.DOC_KINDS for e in part[p]}
    assert by[("books_doc_unclear", "50501")]["evidence"] == dz.digest(dz.document_evidence(
        "books_doc_unclear", UNCLEAR_DOC, "unclear:no_invoice_receipt"))
    student = next(d for d in ALL_DOCS if d["doc_number"] == "62606")
    assert by[("books_doc_students", "62606")]["evidence"] == dz.digest(dz.document_evidence(
        "books_doc_students", student, "students"))
    company = next(d for d in ALL_DOCS if d["doc_number"] == "62614")
    assert by[("books_doc_companies", "62614")]["evidence"] == dz.digest(dz.document_evidence(
        "books_doc_companies", company, "companies"))
    proforma = doc(50601, doc_type=78, card="20099", name="כרטיס לא ברור", total="1180", day="2026-11-09")
    assert evidence_of("books_doc_unclear", docs=ALL_DOCS + [doc(50602, card="20099", name="כרטיס לא ברור",
                                                                day="2026-11-09"), proforma]) != evidence_of("books_doc_unclear")
    assert evidence_of("books_doc_students", docs=with_doc(ALL_DOCS, 62606, total_incl=Decimal("3518"))) != \
        evidence_of("books_doc_students")


def test_a_proposals_evidence_is_what_it_would_write():
    ref, date_only = dz.fill_evidence("books_ref_fill", "1", {"number": "62601", "date": "2026-11-12"}), \
        dz.fill_evidence("books_date_fill", "3", {"date": "2026-11-03"})
    assert ref == {"kind": "books_ref_fill", "row": "1", "number": "62601", "date": "2026-11-12"}
    assert date_only == {"kind": "books_date_fill", "row": "3", "number": None, "date": "2026-11-03"}
    part = computed()[0]
    assert part["fills"]["1"]["mark"]["evidence"] == dz.digest(ref)
    assert part["fills"]["3"]["mark"]["evidence"] == dz.digest(date_only)
    # another document of the row's name and amount, nearer: another number proposed, another evidence
    moved = with_doc(ALL_DOCS, 62601, value_date=TODAY.replace(day=13))
    assert evidence_of("books_ref_fill", docs=moved) != evidence_of("books_ref_fill")
    # the row's name moves nothing of what would be written
    assert evidence_of("books_ref_fill", rows=with_row(ROWS, "1", name="ברק נועה")) == evidence_of("books_ref_fill")


def test_one_amount_in_three_forms_is_one_evidence_and_a_digest_takes_json_only():
    assert dz.amount_text("4000") == dz.amount_text(Decimal("4000.00")) == dz.amount_text("4000.0") == "4000"
    assert dz.amount_text("3347.460") == "3347.46" and dz.amount_text(None) is None and dz.amount_text("NaN") is None
    with pytest.raises(TypeError):
        dz.digest({"amount": Decimal("1")})
    assert dz.digest({"a": 1, "b": [2]}) == dz.digest({"b": [2], "a": 1})


# ---- hiding: each kind hidden while its evidence is the same, back ("חזר") when it changes --------------------------------
AT = "2026-11-26T09:00:00+00:00"
# for each kind, a change at its source that keeps the alert and moves its evidence
MOVES = {
    "books_receipt_unpaid": ("rows", "7", {"status": "2"}),
    "books_ref_invalid": ("rows", "8", {"paid": "3600"}),
    "books_amount_differs": ("rows", "9", {"paid": "2100"}),
    "books_ref_fill": ("docs", 62601, {"value_date": TODAY.replace(day=13)}),
    "books_date_fill": ("docs", 62603, {"value_date": TODAY.replace(day=4)}),
    "books_doc_students": ("docs", 62606, {"total_incl": Decimal("3518")}),
    "books_doc_companies": ("docs", 62614, {"total_incl": Decimal("3518")}),
    "books_doc_unclear": ("docs", 50501, {"total_incl": Decimal("5901")}),
}


def live_row(kind: str, subject: str, evidence: str, id_: int = 1, by: int = 7) -> dict:
    return {"id": id_, "kind": kind, "subject": subject, "evidence": evidence, "by": by, "at": AT}


def merged(raw: dict, view: dict, live: list) -> dict:
    """The view with the part laid over it, as GET /api/desk lays a current part of this very reading."""
    part = dict(dz.hide(raw, live), state="ok", reasons=[], as_of=AT, key="k")
    return db.merge(view, part)


def tab_of(v: dict, kind: str) -> dict:
    tab = dz.TAB[kind]
    return v["books_unclear"] if tab == "unclear" else v[tab]["books"]


def shown_in(v: dict, kind: str, subject: str) -> list:
    """Where the merged view still shows the alert: on its row (its `books`), or in its list."""
    if kind in dz.DOC_KINDS.values():
        return [e for e in tab_of(v, kind)["documents"] if e["subject"] == subject]
    row = next(r for r in v["b2c"]["rows"] if r["id"] == subject)
    return [b for b in row.get("books") or [] if b["kind"] == kind]


def changed(kind: str) -> tuple:
    where, key, change = MOVES[kind]
    if where == "rows":
        return with_row(ROWS, key, **change), ALL_DOCS
    return ROWS, with_doc(ALL_DOCS, key, **change)


@pytest.mark.parametrize("kind", dz.KINDS)
def test_each_kind_hides_while_its_evidence_is_the_same_and_comes_back_when_it_changes(kind):
    subject = ONE_OF_EACH[kind]
    raw, view, _reading = computed()
    live = [live_row(kind, subject, alerts(raw)[(kind, subject)])]
    v = merged(raw, view, live)
    assert shown_in(v, kind, subject) == []
    part = tab_of(v, kind)
    assert [(x["kind"], x["subject"], x["id"], x["by"], x["at"], x["word"]) for x in part["hidden"]] == [
        (kind, subject, 1, 7, AT, dz.WORD[kind])]
    assert part["hidden_counts"] == {kind: 1} and part["returned_counts"] == {}
    # the evidence moved: shown again, with who dismissed it and when, and counted as come back
    raw2, view2, _r = computed(*changed(kind))
    v2 = merged(raw2, view2, live)
    back = shown_in(v2, kind, subject)
    assert len(back) == 1 and back[0]["returned"] == {"id": 1, "by": 7, "at": AT}
    assert tab_of(v2, kind)["hidden"] == [] and tab_of(v2, kind)["returned_counts"] == {kind: 1}
    # nothing else of the part moved: every other alert is shown as before
    others = {k: e for k, e in alerts(raw).items() if k != (kind, subject)}
    assert {k: e for k, e in alerts(dz.hide(raw, live)).items()} == others


def test_a_word_is_said_for_each_press_and_a_proposal_is_not_to_be_proposed():
    assert {dz.WORD[k] for k in dz.FILL_KINDS} == {"לא להציע"}
    assert {dz.WORD[k] for k in dz.KINDS if k not in dz.FILL_KINDS} == {"לא פער"}


def test_a_hidden_mark_leaves_tasks_and_its_count_and_its_row_keeps_its_state():
    # row 7 with no receipt sent, so its own state ("ללא תקבול") is no task: only its mark puts it in "לטיפול"
    raw, view, _reading = computed(rows=with_row(ROWS, "7", receipt="0"))
    v0 = merged(raw, view, [])
    marked = {r["id"]: r for r in v0["b2c"]["rows"]}["7"]
    assert marked["books_task"] is True and "books_receipt_unpaid" in marked["flags"]
    v = merged(raw, view, [live_row("books_receipt_unpaid", "7", alerts(raw)[("books_receipt_unpaid", "7")])])
    row = {r["id"]: r for r in v["b2c"]["rows"]}["7"]
    assert not row.get("books_task") and "books_receipt_unpaid" not in row["flags"] and row["state"] == marked["state"]
    tasks = lambda x: x["b2c"]["periods"][0]["tasks"]  # noqa: E731
    assert tasks(v) == tasks(v0) - 1
    assert "7" not in [r["id"] for r in fd.filter_b2c(v["b2c"], "tasks", "", "")]


def test_a_dismissed_proposal_leaves_its_row_and_its_documents_line_and_the_document_stays():
    raw, view, _reading = computed()
    v = merged(raw, view, [live_row("books_ref_fill", "1", alerts(raw)[("books_ref_fill", "1")])])
    row = {r["id"]: r for r in v["b2c"]["rows"]}["1"]
    assert not row.get("books_fill") and db.FILL_ACTION not in row["actions"] and "books_ref_fill" not in row["flags"]
    entry = next(e for e in v["b2c"]["books"]["documents"] if e["number"] == "62601")
    assert "proposal" not in entry                                       # the document is still uncited: it stays
    assert [x["word"] for x in v["b2c"]["books"]["hidden"]] == ["לא להציע"]


def test_a_proposal_hidden_is_listed_only_where_proposals_are_laid():
    raw, view, _reading = computed()
    live = [live_row("books_ref_fill", "1", alerts(raw)[("books_ref_fill", "1")])]
    held = db.merge(view, dict(dz.hide(raw, live), state="held", reasons=["holes"], as_of=AT, key="k"))
    assert held["b2c"]["books"]["hidden"] == [] and held["b2c"]["books"]["hidden_counts"] == {}


def test_a_dismissal_whose_alert_is_gone_hides_nothing_and_hides_it_again_when_the_same_evidence_comes_back():
    raw, view, _reading = computed()
    live = [live_row("books_ref_invalid", "8", alerts(raw)[("books_ref_invalid", "8")])]
    fixed, fview, _r = computed(rows=with_row(ROWS, "8", invoice=""))     # the number was taken off: no mark at all
    v = merged(fixed, fview, live)
    assert v["b2c"]["books"]["hidden"] == [] and v["b2c"]["books"]["returned_counts"] == {}
    again, aview, _r = computed()
    assert shown_in(merged(again, aview, live), "books_ref_invalid", "8") == []


# ---- DeskBooks: read once per computation, shown by them, and the two presses ------------------------------------------
from types import SimpleNamespace  # noqa: E402

from tests.test_desk_books import NOW, FakeCopy, FakeKeys, Tick  # noqa: E402

CFG = SimpleNamespace(database_url="postgresql://books.test/db", company="analiza", db=COMPANY)
# a whole copy needs its series without a hole: 62608, before the era, on a card of its own, lands outside every list
WHOLE_DOCS = ALL_DOCS + [doc(62608, card="20008", name="מסמך ישן", day="2026-09-15")]


def desk_books(mem: MemoryDismissals, docs=WHOLE_DOCS, **over) -> tuple:
    """(DeskBooks over a whole copy of these documents and `mem`, its copy, its clock)."""
    store, tick = FakeCopy(copy(list(docs), **over)), Tick()
    b = db.DeskBooks(lambda _url: store, lambda: None, clock=tick, now=lambda: NOW, make_dismissals=lambda _url: mem)
    b.keys = FakeKeys(KEYS)
    return b, store, tick


def a_reading(rows=ROWS, n: int = 1) -> tuple:
    reading = reading_of(snap(list(rows), list(B2B)), n)
    return reading, fd.build_view(reading, TODAY)


def test_the_dismissals_are_read_with_the_copys_run_and_a_change_of_them_alone_computes_nothing_again():
    """Read once per computation - with the copy's last run, at most every RUN_CHECK_S - so another instance's press shows
    here within that; when only they changed, the same computation is shown by them under a key of its own."""
    mem = MemoryDismissals()
    b, store, tick = desk_books(mem)
    reading, view = a_reading()
    part = b.answer(CFG, reading, view)
    assert part["state"] == "ok" and mem.calls == {"live": 1} and ("books_ref_invalid", "8") in alerts(part)
    mem.dismiss(COMPANY, "books_ref_invalid", "8", alerts(part)[("books_ref_invalid", "8")], 9)   # another instance's press
    assert b.answer(CFG, reading, view) is part and mem.calls["live"] == 1        # asked a moment ago: not yet
    tick.t += db.RUN_CHECK_S + 1
    shown = b.answer(CFG, reading, view)
    assert shown is not part and shown["key"] != part["key"] and mem.calls["live"] == 2
    assert ("books_ref_invalid", "8") not in alerts(shown) and [x["by"] for x in shown["hidden"]["b2c"]] == [9]
    assert (store.runs, store.snapshots) == (2, 1)                               # nothing of the books computed again
    assert shown["computation"] == part["computation"] and shown["as_of"] == part["as_of"]
    tick.t += db.RUN_CHECK_S + 1
    assert b.answer(CFG, reading, view) is shown and b.current() is shown        # nothing moved: the same part


def test_a_held_part_is_shown_by_the_dismissals_too():
    mem = MemoryDismissals()
    b, store, tick = desk_books(mem)
    reading, view = a_reading()
    part = b.answer(CFG, reading, view)
    store.snapshot = copy(list(WHOLE_DOCS), last_run_id=8, last_run_ok=False)      # the next run failed: the last whole part
    tick.t += db.RUN_CHECK_S + 1
    held = b.answer(CFG, reading, view)
    assert held["state"] == "held" and ("books_doc_unclear", "50501") in alerts(held)
    mem.dismiss(COMPANY, "books_doc_unclear", "50501", alerts(part)[("books_doc_unclear", "50501")], 9)
    tick.t += db.RUN_CHECK_S + 1
    again = b.answer(CFG, reading, view)
    assert again["state"] == "held" and again["reasons"] == ["last_run_not_ok"]
    assert ("books_doc_unclear", "50501") not in alerts(again) and again["hidden"]["unclear"][0]["subject"] == "50501"


def test_a_read_that_fails_keeps_the_ones_held_and_health_says_it_by_its_kind():
    mem = MemoryDismissals()
    b, store, tick = desk_books(mem)
    reading, view = a_reading()
    part = b.answer(CFG, reading, view)
    mem.dismiss(COMPANY, "books_doc_students", "62606", alerts(part)[("books_doc_students", "62606")], 9)
    tick.t += db.RUN_CHECK_S + 1
    hidden = b.answer(CFG, reading, view)
    assert ("books_doc_students", "62606") not in alerts(hidden)
    mem.fail = failing("store_timeout", "57014")
    tick.t += db.RUN_CHECK_S + 1
    still = b.answer(CFG, reading, view)
    assert still is hidden and b.health()["dismissals"]["error"] == "store_timeout"
    assert b.health()["dismissals"]["read_at"] and "62606" not in json.dumps(b.health())
    # a cold instance whose first read fails hides nothing, and says so the same way
    cold, _store, _tick = desk_books(mem)
    first = cold.answer(CFG, reading, view)
    assert first["state"] == "ok" and ("books_doc_students", "62606") in alerts(first)
    assert cold.health()["dismissals"] == {"read_at": None, "error": "store_timeout"}


def test_without_a_store_of_dismissals_nothing_is_hidden_and_a_press_is_unavailable():
    b, _store, _tick = desk_books(MemoryDismissals())
    b.make_dismissals = None
    reading, view = a_reading()
    part = b.answer(CFG, reading, view)
    assert part["hidden"] == {"b2c": [], "b2b": [], "unclear": []}
    assert b.dismiss(CFG, "books_ref_invalid", "8", alerts(part)[("books_ref_invalid", "8")], part["key"],
                     reading["read_at"], 7) == {"error": "dismissals_unavailable"}


def test_a_press_is_against_the_part_shown_now_and_keeps_the_servers_evidence():
    mem = MemoryDismissals()
    b, store, _tick = desk_books(mem)
    reading, view = a_reading()
    part = b.answer(CFG, reading, view)
    ev = alerts(part)[("books_amount_differs", "9")]
    press = lambda kind="books_amount_differs", subject="9", seen=ev, key=part["key"], at=reading["read_at"], user=7: \
        b.dismiss(CFG, kind, subject, seen, key, at, user)  # noqa: E731
    assert press(key="another-part") == {"error": "other_reading"}
    assert press(subject="10") == {"error": "not_shown"}                          # a row with no mark
    assert press(kind="books_doc_companies", subject="62606") == {"error": "not_shown"}   # a students' document
    assert press(seen="0" * 64) == {"error": "evidence_changed"}                  # a hash the page made up
    assert press(seen=dz.digest(dict(dz.mark_evidence("books_amount_differs", "9", {}, {}, {})))) == {
        "error": "evidence_changed"}
    assert mem.rows == [] and mem.calls.get("dismiss") is None                    # nothing was written for any of them
    done = press()
    assert done == {"ok": True, "id": 1, "replaced": None}
    assert [(r["kind"], r["subject"], r["evidence"], r["by"]) for r in mem.rows] == [("books_amount_differs", "9", ev, 7)]
    now = b.current()
    assert now["key"] != part["key"] and ("books_amount_differs", "9") not in alerts(now) and store.snapshots == 1
    assert press(key=now["key"]) == {"error": "already_hidden"}
    # its undo: only by whoever dismissed it, only of one the part holds hidden
    assert b.undo(CFG, "books_amount_differs", "9", 1, reading["read_at"], 8) == {"error": "not_yours"}
    assert b.undo(CFG, "books_amount_differs", "9", 2, reading["read_at"], 7) == {"error": "not_shown"}
    assert b.undo(CFG, "books_ref_invalid", "8", 1, reading["read_at"], 7) == {"error": "not_shown"}
    assert b.undo(CFG, "books_amount_differs", "9", 1, reading["read_at"], 7) == {"ok": True}
    back = b.current()
    assert alerts(back)[("books_amount_differs", "9")] == ev and back["hidden"]["b2c"] == []
    assert mem.rows[0]["undone_by"] == 7 and len(mem.rows) == 1


def test_a_proposal_is_pressed_only_where_it_is_laid_and_a_press_that_came_back_replaces_the_old_one():
    mem = MemoryDismissals()
    b, store, tick = desk_books(mem)
    reading, view = a_reading()
    part = b.answer(CFG, reading, view)
    ev = alerts(part)[("books_ref_fill", "1")]
    assert b.dismiss(CFG, "books_ref_fill", "1", ev, part["key"], "2026-01-01T00:00:00+00:00", 7) == {"error": "not_shown"}
    assert b.dismiss(CFG, "books_ref_fill", "1", ev, part["key"], reading["read_at"], 7)["ok"]
    # the proposal moved (another day of its document): it came back, and a second press replaces the first
    store.snapshot = copy(with_doc(WHOLE_DOCS, 62601, value_date=TODAY.replace(day=13)), last_run_id=8)
    tick.t += db.RUN_CHECK_S + 1
    came = b.answer(CFG, reading, view)
    assert came["fills"]["1"]["mark"]["returned"]["id"] == 1
    got = b.dismiss(CFG, "books_ref_fill", "1", alerts(came)[("books_ref_fill", "1")], came["key"], reading["read_at"], 8)
    assert got == {"ok": True, "id": 2, "replaced": 1}
    assert [(r["id"], r["undone_by"], r["replaced_by"]) for r in mem.rows] == [(1, 8, 2), (2, None, None)]


def test_a_press_waits_for_the_computation_in_progress_and_never_past_wait_s(monkeypatch):
    mem = MemoryDismissals()
    b, _store, _tick = desk_books(mem)
    reading, view = a_reading()
    part = b.answer(CFG, reading, view)
    monkeypatch.setattr(db, "WAIT_S", 0.05)
    with b._lock:
        assert b.dismiss(CFG, "books_ref_invalid", "8", alerts(part)[("books_ref_invalid", "8")], part["key"],
                         reading["read_at"], 7) == {"error": "busy"}
        assert b.undo(CFG, "books_ref_invalid", "8", 1, reading["read_at"], 7) == {"error": "busy"}
    assert mem.rows == []


def test_a_write_that_fails_is_unavailable_and_shows_nothing_hidden():
    mem = MemoryDismissals()
    b, _store, _tick = desk_books(mem)
    reading, view = a_reading()
    part = b.answer(CFG, reading, view)
    mem.fail = failing("store_unreachable", "OperationalError")
    assert b.dismiss(CFG, "books_ref_invalid", "8", alerts(part)[("books_ref_invalid", "8")], part["key"],
                     reading["read_at"], 7) == {"error": "dismissals_unavailable"}
    assert ("books_ref_invalid", "8") in alerts(b.current())


# ---- the routes: POST /api/alerts/not-a-gap and /undo ----------------------------------------------------------------------
from fastapi.testclient import TestClient  # noqa: E402

from app import main  # noqa: E402
from tests.test_desk import SECRET, TOKEN, session_token  # noqa: E402
from tests.test_desk_books import ROUTE_DOCS, books_world  # noqa: E402
from tests.test_desk_mirror import MemoryStore  # noqa: E402

# D3's route world (tests/test_desk_books.py books_world): 101's mark, 103's proposal (paid as its card's invoice-receipt
# 62503, D4), and the documents of the three lists. User 1 is on both charge boards, 2 on Student Charge, 3 on B2B Charge
ROUTE_ALERTS = {"books_receipt_unpaid": "101", "books_ref_fill": "103", "books_doc_students": "62503",
                "books_doc_companies": "50500", "books_doc_unclear": "50501"}


@pytest.fixture
def nag(monkeypatch):
    w = books_world()
    w.set(103, touch=False, paid="4000")
    monkeypatch.setenv("MONDAY_CLIENT_SECRET", SECRET)
    monkeypatch.setenv("MONDAY_TOKEN", TOKEN)
    monkeypatch.setenv("DATABASE_URL", "postgresql://nobody@127.0.0.1:1/none")
    monkeypatch.setenv("WIZ_COMPANY", "analiza")
    mem = MemoryDismissals()
    main.app.state.monday_transport = w.transport()
    for k, v in dict(desk=None, usage=None, frontend_dir=None, desk_store=MemoryStore(), books_store=FakeCopy(copy(ROUTE_DOCS)),
                     books_now=lambda: NOW, dismissal_store=mem).items():
        setattr(main.app.state, k, v)
    d = SimpleNamespace(w=w, mem=mem, client=TestClient(main.app))

    def auth(user, **dat):
        return {"Authorization": "Bearer " + session_token(user, **dat)}

    d.auth = auth
    d.get = lambda user=1: d.client.get("/api/desk", headers=auth(user)).json()
    d.post = lambda path, body, user=1, **dat: d.client.post(path, json=body, headers=auth(user, **dat))
    yield d
    for k in ("monday_transport", "desk", "usage", "desk_store", "books_store", "books_now", "dismissal_store"):
        setattr(main.app.state, k, None)


def body_of(answer: dict, kind: str, subject: str, view_of: dict = None) -> dict:
    """What the page sends for one alert of the answer it was given: the alert, its evidence, its reading and its part."""
    v = (view_of or answer)["view"]
    if kind in dz.DOC_KINDS.values():
        part = v["books_unclear"] if kind == "books_doc_unclear" else v[dz.TAB[kind]]["books"]
        evidence = next(e["evidence"] for e in part["documents"] if e["subject"] == subject)
    else:
        row = next(r for r in v["b2c"]["rows"] if r["id"] == subject)
        evidence = next(b["evidence"] for b in row["books"] if b["kind"] == kind)
    return {"kind": kind, "subject": subject, "evidence": evidence, "taken": answer["taken_at"],
            "books": answer["view"]["books"]["key"]}


NAG, UNDO = "/api/alerts/not-a-gap", "/api/alerts/not-a-gap/undo"


def test_a_press_hides_the_alert_for_everyone_and_answers_the_desk_after_it(nag):
    first = nag.get(1)
    row = next(r for r in first["view"]["b2c"]["rows"] if r["id"] == "101")
    tasks = first["view"]["b2c"]["periods"][0]["tasks"]
    sent = body_of(first, "books_receipt_unpaid", "101")
    resp = nag.post(NAG, sent)
    assert resp.status_code == 200, resp.text
    got = resp.json()
    assert got["dismissed"] == {"kind": "books_receipt_unpaid", "subject": "101", "id": 1}
    after = next(r for r in got["view"]["b2c"]["rows"] if r["id"] == "101")
    assert row["books"] and not after.get("books") and not after.get("books_task")
    assert got["view"]["b2c"]["periods"][0]["tasks"] == tasks - 1
    books = got["view"]["b2c"]["books"]
    assert [(x["kind"], x["subject"], x["by"], x["word"], x["name"]) for x in books["hidden"]] == [
        ("books_receipt_unpaid", "101", 1, "לא פער", "תלמידה-אלף")]
    assert books["hidden_counts"] == {"books_receipt_unpaid": 1} and got["view"]["books"]["key"] != sent["books"]
    # kept: the server's evidence (equal to what the page was sent), who, and nothing else
    assert [(r["kind"], r["subject"], r["evidence"], r["by"]) for r in nag.mem.rows] == [
        ("books_receipt_unpaid", "101", sent["evidence"], 1)]
    # everyone's desk: a member of Student Charge only opens it hidden too
    other = nag.get(2)["view"]["b2c"]
    assert not next(r for r in other["rows"] if r["id"] == "101").get("books") and other["books"]["hidden"][0]["by"] == 1


MEMBERS = [(1, k, 200) for k in ROUTE_ALERTS] + [
    (2, "books_receipt_unpaid", 200), (2, "books_ref_fill", 200), (2, "books_doc_students", 200),
    (2, "books_doc_companies", 403), (2, "books_doc_unclear", 403),
    (3, "books_doc_companies", 200), (3, "books_receipt_unpaid", 403), (3, "books_ref_fill", 403),
    (3, "books_doc_students", 403), (3, "books_doc_unclear", 403)]


@pytest.mark.parametrize("user, kind, status", MEMBERS, ids=["%d-%s" % m[:2] for m in MEMBERS])
def test_a_press_is_a_member_of_each_of_its_alerts_boards(nag, user, kind, status):
    """The students' marks, proposals and documents are Student Charge's, the companies' documents B2B Charge's, the unclear
    ones both (spec §6.4.1). A reader who may not see an alert is refused before anything about it is looked up - with the
    evidence a member of both was sent, so the refusal is not for want of it - and nothing is kept."""
    whole, own = nag.get(1), nag.get(user)
    sent = dict(body_of(whole, kind, ROUTE_ALERTS[kind]), taken=own["taken_at"], books=own["view"]["books"]["key"])
    resp = nag.post(NAG, sent, user)
    assert resp.status_code == status, resp.text
    if status == 403:
        assert resp.json() == {"error": "not_a_board_member"} and nag.mem.rows == []
    else:
        assert [(r["kind"], r["by"]) for r in nag.mem.rows] == [(kind, user)]


def test_the_evidence_is_the_servers_a_forged_or_moved_one_is_refused_and_nothing_is_kept(nag):
    first = nag.get(1)
    sent = body_of(first, "books_doc_students", "62503")
    for forged in ("0" * 64, dz.digest({"kind": "books_doc_students", "number": "62503"}),
                   body_of(first, "books_doc_unclear", "50501")["evidence"]):
        resp = nag.post(NAG, dict(sent, evidence=forged))
        assert (resp.status_code, resp.json()) == (409, {"error": "evidence_changed"})
    assert nag.mem.rows == [] and nag.mem.calls.get("dismiss") is None
    assert nag.post(NAG, sent).status_code == 200 and nag.mem.rows[0]["evidence"] == sent["evidence"]


def test_an_alert_the_part_does_not_show_or_of_another_reading_or_part_is_refused(nag):
    first = nag.get(1)
    sent = body_of(first, "books_receipt_unpaid", "101")
    for over, word in (({"subject": "102"}, "not_shown"), ({"kind": "books_ref_invalid"}, "not_shown"),
                       ({"kind": "books_doc_companies", "subject": "62503"}, "not_shown"),
                       ({"taken": "2026-11-24T09:00:00+00:00"}, "other_reading"), ({"taken": ""}, "other_reading"),
                       ({"books": "ok|another-part"}, "other_reading")):
        resp = nag.post(NAG, dict(sent, **over))
        assert (resp.status_code, resp.json()) == (409, {"error": word}), over
    assert nag.mem.rows == []


def test_one_live_dismissal_per_subject_through_the_route(nag):
    first = nag.get(1)
    sent = body_of(first, "books_doc_companies", "50500")
    after = nag.post(NAG, sent).json()
    again = dict(sent, books=after["view"]["books"]["key"], taken=after["taken_at"])
    for user in (1, 3):
        resp = nag.post(NAG, again, user)
        assert (resp.status_code, resp.json()) == (409, {"error": "already_hidden"})
    assert len(nag.mem.rows) == 1


def test_a_viewer_may_not_press_nor_undo(nag):
    first = nag.get(1)
    resp = nag.post(NAG, body_of(first, "books_doc_unclear", "50501"), 1, is_view_only=True)
    assert (resp.status_code, resp.json()) == (403, {"error": "view_only"}) and nag.mem.rows == []
    done = nag.post(NAG, body_of(first, "books_doc_unclear", "50501")).json()
    resp = nag.post(UNDO, {"kind": "books_doc_unclear", "subject": "50501", "id": done["dismissed"]["id"]}, 1,
                    is_view_only=True)
    assert (resp.status_code, resp.json()) == (403, {"error": "view_only"}) and nag.mem.rows[0]["undone_at"] is None


def test_undo_is_the_dismissers_own_and_the_alert_comes_back(nag):
    first = nag.get(1)
    done = nag.post(NAG, body_of(first, "books_ref_fill", "103"), 2).json()
    undo = {"kind": "books_ref_fill", "subject": "103", "id": done["dismissed"]["id"]}
    resp = nag.post(UNDO, undo, 1)
    assert (resp.status_code, resp.json()) == (403, {"error": "not_yours"}) and nag.mem.rows[0]["undone_at"] is None
    for wrong, word in (({"id": 99}, "not_shown"), ({"subject": "101"}, "not_shown")):
        resp = nag.post(UNDO, dict(undo, **wrong), 2)
        assert (resp.status_code, resp.json()) == (409, {"error": word})
    resp = nag.post(UNDO, undo, 2)
    assert resp.status_code == 200 and resp.json()["undone"] == undo
    row = next(r for r in resp.json()["view"]["b2c"]["rows"] if r["id"] == "103")
    assert row["books_fill"] == {"number": "62503", "date": "2026-11-03"} and resp.json()["view"]["b2c"]["books"]["hidden"] == []
    assert (nag.mem.rows[0]["undone_by"], len(nag.mem.rows)) == (2, 1)       # kept, not deleted
    resp = nag.post(UNDO, undo, 2)
    assert (resp.status_code, resp.json()) == (409, {"error": "not_shown"})


@pytest.mark.parametrize("path", [NAG, UNDO])
def test_the_gate_comes_first_and_a_body_of_another_shape_is_refused(nag, path):
    resp = nag.client.post(path, content=b"{not json")                       # no token: 401 before the body is read
    assert (resp.status_code, resp.json()) == (401, {"error": "unauthorized"})
    for raw in (b"{not json", b"[]", json.dumps({"kind": "books_doc_students", "extra": 1}).encode()):
        resp = nag.client.post(path, content=raw, headers=nag.auth(1))
        assert (resp.status_code, resp.json()) == (400, {"error": "invalid_filter"}), raw
    good = {"kind": "books_doc_students", "subject": "62503"}
    shapes = ([{"kind": "books_doc_student"}, {"subject": "62503a"}, {"subject": 62503}, {"subject": "1" * 21},
               {"kind": None}] + ([{"evidence": "A" * 64}, {"evidence": "a" * 63}, {"taken": "x" * 41},
                                   {"books": "k" * (db.BOOKS_KEY_MAX + 1)}, {"evidence": None}] if path == NAG
                                  else [{"id": "1"}, {"id": 0}, {"id": True}, {"id": 1.5}, {"id": 2 ** 63}]))
    base = dict(good, evidence="a" * 64, taken="", books="") if path == NAG else dict(good, id=1)
    for over in shapes:
        resp = nag.post(path, dict(base, **over))
        assert (resp.status_code, resp.json()) == (400, {"error": "bad_alert"}), over
    assert nag.mem.rows == [] and nag.mem.calls.get("dismiss") is None


def test_without_a_database_a_press_is_unavailable(nag, monkeypatch):
    sent = body_of(nag.get(1), "books_doc_students", "62503")
    monkeypatch.setenv("DATABASE_URL", "")
    shown = nag.get(1)                                                       # the books part is off: nothing to hide
    assert shown["view"]["books"]["state"] == "off" and shown["view"]["b2c"]["books"]["hidden"] == []
    resp = nag.post(NAG, dict(sent, taken=shown["taken_at"], books=shown["view"]["books"]["key"]))
    assert (resp.status_code, resp.json()) == (503, {"error": "dismissals_unavailable"}) and nag.mem.rows == []


def test_a_press_writes_nothing_to_monday_and_its_log_says_ids_and_words_only(nag, caplog):
    import logging

    first = nag.get(1)
    before = len(nag.w.queries)
    with caplog.at_level(logging.INFO, logger="analiza_finance"):
        sent = body_of(first, "books_doc_students", "62503")
        nag.post(NAG, sent)
        nag.post(NAG, dict(sent, evidence="0" * 64))
    assert not any(q.strip().startswith("mutation") for q, _v in nag.w.queries[before:])
    text = caplog.text
    assert "endpoint=not-a-gap outcome=ok" in text and "endpoint=not-a-gap outcome=already_hidden" not in text
    for word in (sent["evidence"], "תלמיד-גימל", "62503", "4000"):
        assert word not in text, word


@needs_pg
def test_the_whole_path_on_postgres(pg, nag):
    """The routes on the real table: the press kept with the server's evidence, the desk after it hides the alert, and the
    undo ends the row - never deleting it."""
    main.app.state.dismissal_store = ds.DismissalStore(pg)
    main.app.state.desk = None
    first = nag.get(1)
    sent = body_of(first, "books_doc_unclear", "50501")
    got = nag.post(NAG, sent).json()
    assert got["view"]["books_unclear"]["documents"] == [] and got["view"]["books_unclear"]["hidden"][0]["subject"] == "50501"
    assert [(r[1], r[2], r[3], r[4], r[5]) for r in _rows(pg)] == [("books_doc_unclear", "50501", sent["evidence"], 1, False)]
    undone = nag.post(UNDO, {"kind": "books_doc_unclear", "subject": "50501", "id": got["dismissed"]["id"]}).json()
    assert [e["subject"] for e in undone["view"]["books_unclear"]["documents"]] == ["50501"]
    assert [(r[5], r[6]) for r in _rows(pg)] == [(True, 1)]
