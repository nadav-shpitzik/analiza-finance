"""The adversarial review of D2 at a32395f (24.11): each finding's own failing test, ported here and passing, with the
tests that hold its fix, and the tests that kill the mutants the review's pass left alive (its runner: one exact text
replacement at a time, run against tests/test_desk*.py).

1. A charge that left its board unseen hid behind one that came unseen: the refresh trusted `items_count == len(rows)`.
   Now every refresh pages the two charge boards' ids (app/desk/reading.py `read_delta`).
2. The usage events' alphabets let an ID number through: a session id of digits, a word with a digit run behind a letter,
   a period of any digits. Now the session is letters only (the page makes sixteen), a word carries no number, and a
   period is one the page can name (app/desk/usage.py).
"""
from __future__ import annotations

import re

import psycopg
import pytest

from app import migrate
from app.desk import reading as rd
from app.desk import usage
from app.desk import usage_store
from app.monday import client as monday
from tests.conftest import TEST_DATABASE_URL, drop_all, needs_pg
from tests.test_desk import OTHER_BOARD, SC, reader_of, rel, session_token, status, world
from tests.test_desk_mirror import MemoryStore, desk_of, holder
from tests.test_desk_usage import batch, ev, use  # noqa: F401 - `use` is the fixture

ID_NUMBER = "039482716"      # nine digits, as an Israeli ID number is written
ITEM_ID = "5093448677"       # a monday item id (ten digits)
DIGIT_RUN = re.compile(r"\d{7,}")


# --- 1. the charge boards' ids, paged in every refresh ------------------------------------------------------------------

def restore(w, item_id):
    """The mirror image of World.leave: an archived (or trashed) charge comes back to its board's items_page and
    items_count, its updated_at untouched - as World.leave models archiving."""
    item = w.items[str(item_id)]
    item["state"] = "active"
    if str(item_id) not in w.order[item["board"]]:
        w.order[item["board"]].append(str(item_id))


def test_a_charge_that_left_unseen_and_one_that_came_back_unseen_are_not_hidden_by_an_equal_count():
    """read_delta trusted `items_count == len(rows)` to catch a charge that left its board unseen (DeltaMismatch). One
    that left and one that came back in the same interval kept the count equal: the refresh kept the departed row, missed
    the returned one, and called itself complete; it is also what the store keeps for every cold instance."""
    w = world()
    w.add(SC, 104, "תלמיד-דלת", status=status("שולם", 1), paid="9900", enrollment=rel(), lead=rel(303))
    w.leave(104, "archived")                     # archived long before the held reading
    store = MemoryStore()
    h = holder(w, store)
    first, _ = h.get()
    assert first["kind"] == "full" and [r["id"] for r in first["student_charge"]["rows"]] == ["101", "102", "103"]

    w.leave(103, "archived")                     # she archives one payment ...
    restore(w, 104)                              # ... and restores another (neither moves updated_at, as World.leave)

    kept, last = h.get()
    full = rd.read_desk(reader_of(w))
    assert [r["id"] for r in full["student_charge"]["rows"]] == ["101", "102", "104"] and full["complete"] is True
    assert kept["complete"] is False or desk_of(kept) == desk_of(full), (
        kept["kind"], [r["id"] for r in kept["student_charge"]["rows"]], h.health()["last_codes"])
    # and by the ids, not by a full reading: a refresh, whose rows are the full reading's, kept for every cold instance
    assert kept["kind"] == "delta" and kept["complete"] is True and h.health()["last_codes"] == ["delta", "store_saved"]
    assert [r["id"] for r in kept["student_charge"]["rows"]] == ["101", "102", "104"]
    assert desk_of(store.load()) == desk_of(full)


def test_a_charge_moved_out_and_one_moved_in_are_not_hidden_by_an_equal_count():
    """The same hole without a restore: one charge moved to another board, one moved in from another (neither moving
    updated_at). The refresh kept the one that left, never read the one that came, and called itself complete."""
    w = world()
    w.add(SC, 109, "תלמיד-טית", status=status("שולם", 1), paid="7000", enrollment=rel(), lead=rel(303))
    w.items["109"]["board"] = OTHER_BOARD                          # on another board when the desk first reads
    w.order[SC].remove("109")
    h = holder(w, MemoryStore())
    h.get()
    w.items["103"]["board"] = OTHER_BOARD
    w.order[SC].remove("103")
    w.items["109"]["board"] = SC
    w.order[SC].append("109")
    reads = len(w.by_id_reads())
    kept, _ = h.get()
    full = rd.read_desk(reader_of(w))
    assert [r["id"] for r in full["student_charge"]["rows"]] == ["101", "102", "109"] and full["complete"] is True
    assert kept["complete"] is False or desk_of(kept) == desk_of(full), [r["id"] for r in kept["student_charge"]["rows"]]
    assert kept["kind"] == "delta" and desk_of(kept) == desk_of(full)
    # the one that came is read by id, alone: the one that left is never read
    assert (SC, ["109"]) in w.by_id_reads()[reads:] and "103" not in [i for _b, ids in w.by_id_reads()[reads:] for i in ids]


def test_the_ids_cost_a_page_per_board_and_a_long_board_costs_a_page_more(monkeypatch):
    """Keeping the refresh cheap: the ids are the full reading's cheap query (ids only), the two boards side by side - one
    request each, and one more per PAGE_LIMIT ids - counted in the reading's `id_pages` and in /health's `last_delta`."""
    monkeypatch.setattr(monday, "PAGE_LIMIT", 2)                        # Student Charge's three ids: two pages
    w = world()
    h = holder(w)
    h.get()
    before = len(w.queries)
    kept, _ = h.get()
    assert kept["kind"] == "delta" and kept["id_pages"] == 3 and len(w.queries) - before == 4
    assert h.health()["last_delta"]["id_pages"] == 3 and h.health()["last_delta"]["calls"] == 4
    assert [r["id"] for r in kept["student_charge"]["rows"]] == ["101", "102", "103"]


def test_a_row_monday_adds_beside_a_charges_read_from_another_board_fails_the_boards_check():
    """A stranger beside the rows asked (by id monday answers an item of any board): the board's rows still reconcile with
    its ids, but its check is not whole, as a full reading's would not be."""
    w = world()
    reader = reader_of(w)
    held = rd.read_desk(reader)
    w.set(102, paid="1200")
    real = reader.read_items

    def with_a_stranger(jobs, pack=False):
        out = real(jobs, pack=pack)
        if out and out[0]:                                              # the first job is Student Charge's
            out[0] = out[0] + [dict(out[0][0], id="999", board_id=OTHER_BOARD)]
        return out

    reader.read_items = with_a_stranger
    got = rd.read_delta(reader, held)
    c = got["checks"]["student_charge"]
    assert c["strangers"] == 1 and c["ok"] is False and got["complete"] is False
    assert [r["id"] for r in got["student_charge"]["rows"]] == ["101", "102", "103"]


# --- 2. the usage events: no number in a session, a word or a period ----------------------------------------------------

def no_identifier(rows: list) -> None:
    """Nothing a row would write carries a run of seven digits or more (an ID number, an item id, a phone, an account)
    or a Latin name: the vocabulary's own words are closed lists of short words."""
    for r in rows:
        text = repr({k: v for k, v in r.items() if k != "user_id"})   # the user id is the token's, a monday id by design
        assert not DIGIT_RUN.search(text), text


def test_a_session_id_that_is_an_id_number_is_refused():
    """SESSION was [a-z0-9]{8,40}: nine bare digits passed, and were written into `session` of every row of the batch."""
    for session_id in (ID_NUMBER, ITEM_ID, "0521234567", "id" + ID_NUMBER, "visit" + ITEM_ID):
        try:
            rows, _refused = usage.clean_batch(batch(ev("hide"), session_id=session_id), 7)
        except usage.Refused:
            continue
        no_identifier(rows)
    with pytest.raises(usage.Refused):
        usage.clean_batch(batch(ev("hide"), session_id=ID_NUMBER), 7)


@pytest.mark.parametrize("event", [
    ev("load_failed", code="id" + ID_NUMBER),                   # an ID number behind two letters
    ev("row", state="item" + ITEM_ID, open=True),               # an item id
    ev("open_item", where="x" + ITEM_ID),
    ev("state", st="t" + ID_NUMBER),                            # the event's own state
    ev("act", action="refund_done", phase="done", code="a4000x2500"),   # amounts
    ev("load_failed", code="id_039_482_716"),                   # an ID number cut by "_", as useWord makes a message
    ev("period", period="0394-82716"),                          # an ID number as a period
])
def test_a_word_that_carries_an_identifier_is_refused(event):
    rows, refused = usage.clean_batch(batch(event), 7)
    assert refused == 1 or not rows, rows
    no_identifier(rows)
    assert refused == 1 and rows == []


@needs_pg
def test_an_id_number_sent_as_the_session_or_in_a_word_never_reaches_desk_events(use, monkeypatch):  # noqa: F811
    """The review's end-to-end case, through the route and its own writer into Postgres. Ported with one change: the review
    called clean_batch outside a guard, and a session of another shape is refused whole (400 `bad_usage`, as a short one
    always was), so nothing of the batch is written; an ID number behind a letter in a word is refused alone."""
    drop_all(TEST_DATABASE_URL)
    try:
        migrate.apply_migrations(TEST_DATABASE_URL)
        monkeypatch.setattr("app.desk.routes.append_events", usage_store.append_events)
        monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
        r = use.post(batch(ev("load_failed", code="id" + ID_NUMBER), session_id=ID_NUMBER))
        assert r.status_code == 400 and r.json() == {"error": "bad_usage"}
        r = use.post(batch(ev("load_failed", code="id" + ID_NUMBER), ev("hide")))
        assert r.status_code == 200 and r.json() == {"stored": 1, "refused": 1}
        with psycopg.connect(TEST_DATABASE_URL) as conn:
            got = conn.execute("SELECT session, kind, detail::text FROM desk_events").fetchall()
        assert [k for _s, k, _d in got] == ["hide"]
        assert not any(ID_NUMBER in s or ID_NUMBER in d for s, _k, d in got), got
    finally:
        drop_all(TEST_DATABASE_URL)
