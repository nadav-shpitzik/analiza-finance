"""The adversarial review of D2 at a32395f (24.11): each finding's own failing test, ported here and passing, with the
tests that hold its fix, and the tests that kill the mutants the review's pass left alive (its runner: one exact text
replacement at a time, run against tests/test_desk*.py).

1. A charge that left its board unseen hid behind one that came unseen: the refresh trusted `items_count == len(rows)`.
   Now every refresh pages the two charge boards' ids (app/desk/reading.py `read_delta`).
2. The usage events' alphabets let an ID number through: a session id of digits, a word with a digit run behind a letter,
   a period of any digits. Now the session is letters only (the page makes sixteen), a word carries no number, and a
   period is one the page can name (app/desk/usage.py).
3. Half of an emoji in a name (a lone surrogate) stopped every save of the stored reading and made /api/desk a 500. Now
   the reader's `_text` replaces it, as it removes a NUL (app/monday/client.py), and the store's guard says an encoding
   error as its own failure (app/desk/store.py).
4. A list or an object as an event's kind raised TypeError out of clean_batch: a 500, the batch's good events lost. Now
   it is refused and counted like any kind outside the list (app/desk/usage.py).
5. A request behind a reading in progress waited on its lock with no bound of its own, and the save after a reading is not
   bounded by the reading's deadline. Now it waits at most the deadline and WAIT_MARGIN_S, then is answered with what is
   held (or stored) marked refreshing, or with no reading (app/desk/reading.py `HeldReading.answer`).
6. An ID number typed into a name or a free-text cell (a lead named with hers, a credit reference) was kept as typed in
   desk_readings. Decided by Nadav (24.11, "Sure go"): the desk masks every ID-like number as it reads - eight digits or
   more, judged whole by the books' cleaning - so memory, the stored copy, the page and the Excel file agree
   (app/desk/reading.py `mask_ids`, `masked`).
7. POST /api/usage parsed and validated its body before the sessionToken was checked: 400 before 401, so neither the
   token nor the usage limit bounded what an anonymous client sent. Now the secret, the token and the limit come first,
   and the body after them, with the same closed codes; POST /api/desk/export-link reads its body after its gate too
   (app/desk/routes.py `_body_of`).
And the mutants the review's pass left alive, each killed by a test at the end: R7, R22, S3, S4, S5, C2, C5, C6 (U5, a
period of any digits, by tests/test_desk_usage.py's refusals).
"""
from __future__ import annotations

import json
import re
import threading
import time
import types

import httpx
import psycopg
import pytest

from app import main, migrate
from app.desk import reading as rd
from app.desk import store as ds
from app.desk import usage
from app.desk import usage_store
from app.monday import client as monday
from tests.conftest import TEST_DATABASE_URL, drop_all, needs_pg
from app.monday.client import MondayReader
from tests.test_desk import B2, OTHER_BOARD, SC, TOKEN, desk, reader_of, rel, session_token, status, world  # noqa: F401
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


def test_a_charge_that_came_between_the_windows_and_the_ids_is_counted_with_the_ids_it_came_with():
    """A refresh's items_count is the one its board's ids were paged with, never the windows' (one request earlier): a
    charge created between the two is read by id as one that came, and the board - and the page's own check of it, rows
    read against items_count - is whole by the count that holds it."""
    from app.desk import view as fd
    from tests.test_desk_mirror import TODAY

    w = world()
    h = holder(w)
    h.get()
    windows = w.answer_windows

    def then_a_new_charge(query, var):
        answered = windows(query, var)
        if "104" not in w.items:
            w.add(SC, 104, "תלמיד-דלת", status=status("שולם", 1), paid="700", enrollment=rel(), lead=rel(303))
        return answered

    w.answer_windows = then_a_new_charge
    kept, _ = h.get()
    assert kept["kind"] == "delta" and [r["id"] for r in kept["student_charge"]["rows"]] == ["101", "102", "103", "104"]
    assert kept["student_charge"]["items_count"] == 4 and kept["complete"] is True
    assert fd.build_view(kept, TODAY)["checks"]["b2c_ok"] is True


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


# --- 3. a lone surrogate in monday's text -------------------------------------------------------------------------------

MARK = "@@LONE@@"


def lone_transport(w) -> httpx.MockTransport:
    """The World's monday, whose JSON carries `\\ud800` where a name holds MARK: half of a surrogate pair, as monday's
    JSON may carry an emoji cut in two (json.loads makes it a lone surrogate)."""
    def handler(request):
        resp = w.handler(request)
        body = resp.read().replace(MARK.encode(), b"\\ud800")
        return httpx.Response(resp.status_code, content=body, headers={"content-type": "application/json"})
    return httpx.MockTransport(handler)


@pytest.fixture
def pg():
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL not set")
    drop_all(TEST_DATABASE_URL)
    migrate.apply_migrations(TEST_DATABASE_URL)
    yield TEST_DATABASE_URL
    drop_all(TEST_DATABASE_URL)


def test_a_name_with_half_an_emoji_does_not_stop_every_save_of_the_stored_reading(pg):
    """flatten's `_text` drops NUL because jsonb refuses it and 'one such cell would fail every save while it stays'
    (review 22.11). A lone surrogate did the same: the text could not even be encoded to send, DeskStore.save raised
    UnicodeEncodeError (not even its own DeskStoreError), and the stored copy froze while the cell stayed. Ported with
    one change: the review's first assert held the surrogate in the reading (true before the fix); the reader replaces it
    now, so the reading holds the replacement character and nothing that cannot be encoded."""
    w = world()
    w.items["301"]["name"] = "ליד-אלף " + MARK
    make = lambda: MondayReader(TOKEN, client=httpx.Client(transport=lone_transport(w)), sleep=lambda s: None)  # noqa
    h = rd.HeldReading(make, store=ds.DeskStore(pg), ttl_s=0)
    kept, last = h.get()
    assert last["error"] is None and kept["lead_names"]["301"] == "ליד-אלף \ufffd"
    assert "store_saved" in h.health()["last_codes"], h.health()["last_codes"]
    assert ds.DeskStore(pg).load()["lead_names"]["301"] == "ליד-אלף \ufffd"


def test_the_desk_answer_survives_a_lone_surrogate_in_a_name(desk):  # noqa: F811
    """The same cell reached /api/desk: JSONResponse encodes with ensure_ascii=False, so every open of the desk was a 500
    while the cell stayed (the root cause was the reader's, app/monday/client.py `_text`)."""
    desk.w.items["101"]["name"] = "תלמידה-אלף " + MARK
    main.app.state.monday_transport = lone_transport(desk.w)
    client = type(desk.client)(main.app, raise_server_exceptions=False)
    r = client.get("/api/desk", headers={"Authorization": "Bearer " + session_token(1)})
    assert r.status_code == 200, r.status_code
    assert next(x for x in r.json()["view"]["b2c"]["rows"] if x["id"] == "101")["name"] == "תלמידה-אלף \ufffd"


def test_every_text_the_reader_gives_is_encodable_a_status_label_a_cell_and_monday_s_own_error():
    """`_text` is every text of a row (a name, a cell, a status label, a mirror's display); monday's error message reaches
    an answer as `last_attempt.detail`, so the reader's redaction replaces a lone surrogate too."""
    from app.monday.client import flatten

    row = flatten({"id": 1, "name": "a\ud800", "board": {"id": "9"}, "column_values": [
        {"id": "text_x", "text": "b\udfff"}, {"id": "color_x", "index": 1, "label": "c\ud83d", "text": ""},
        {"id": "lookup_x", "display_value": "\x00d\udc00"}]})
    assert [row[k] for k in ("name", "text_x", "color_x", "lookup_x")] == ["a\ufffd", "b\ufffd", "c\ufffd", "d\ufffd"]
    assert flatten({"id": 2, "name": "😀 שלם", "column_values": []})["name"] == "😀 שלם"   # a whole pair is kept
    reader = MondayReader(TOKEN, client=httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=b'{"errors": [{"message": "bad \\ud800 value"}]}'))))
    with pytest.raises(monday.MondayError) as err:
        reader.post("query { me { id } }", {})
    assert "\ufffd" in err.value.detail and "\ud800" not in err.value.detail
    err.value.detail.encode("utf-8")


@needs_pg
def test_a_text_the_store_cannot_encode_is_its_own_failure_never_a_crash(pg):
    """Were a lone surrogate to reach the store another way, its save is a DeskStoreError by its class, said in /health as
    store_save_failed_UnicodeEncodeError, and the desk goes on with its reading."""
    w = world()
    store = ds.DeskStore(pg)
    kept, _ = holder(w, store).get()
    bad = dict(kept, read_at=rd._now_iso(), lead_names=dict(kept["lead_names"], **{"301": "x\ud800"}))
    with pytest.raises(ds.DeskStoreError) as err:
        store.save(bad)
    assert err.value.detail == "UnicodeEncodeError"
    assert store.load()["read_at"] == kept["read_at"]                   # the stored reading stays as it was


# --- 4. a kind that is not a string -------------------------------------------------------------------------------------

def test_a_kind_that_is_not_a_word_is_counted_refused_not_a_500(use):  # noqa: F811
    """`kind not in KINDS` on a dict: a list or an object as `k` raised TypeError (unhashable) out of clean_batch, which
    the route does not catch - a 500, the batch's good events lost, nothing counted in /health."""
    use.client = type(use.client)(use.client.app, raise_server_exceptions=False)
    for bad in ([], {"a": 1}):
        r = use.client.post("/api/usage", json=batch(ev("hide"), {"k": bad}),
                            headers={"Authorization": "Bearer " + session_token(7301)})
        assert r.status_code == 200 and r.json() == {"stored": 1, "refused": 1}, (r.status_code, r.text[:80])
    assert use.health()["refused"] == 2 and use.health()["failed"] == 0


@pytest.mark.parametrize("bad", [[], {"a": 1}, ["hide"], 7, None, True, 1.5])
def test_an_event_whose_kind_or_tab_is_not_a_string_is_refused_alone(bad):
    rows, refused = usage.clean_batch(batch(ev("hide"), {"k": bad}, dict(ev("hide"), tab=bad), ev("state")), 7)
    assert refused == 2 and [r["kind"] for r in rows] == ["hide", "state"]


# --- 5. a request behind a reading in progress waits a bounded time -----------------------------------------------------

def _stuck_save(store: MemoryStore) -> threading.Event:
    """The store's save waits for the returned event (a connection that dropped silently: the server's statement_timeout
    never reaches a client that waits on a dead socket)."""
    release = threading.Event()
    real = store.save
    store.save = lambda reading: (release.wait(10), real(reading))[1]
    return release


def test_a_request_behind_a_reading_waits_no_longer_than_the_readings_deadline():
    """HeldReading.answer waited on `_lock` with no bound of its own ("a request that finds one running waits for it
    (bounded by its deadline)"); analiza-metrics' SharedReading waited `acquire(timeout=wait())` and answered with what it
    held. The store's save runs after the reading's deadline, so a save that does not come back held every fresh=1, press
    and export of the instance behind it."""
    w = world()
    store = MemoryStore()
    h = rd.HeldReading(lambda: reader_of(w), store=store, ttl_s=0, deadline_s=1, delta_deadline_s=1)
    h.get()
    release = _stuck_save(store)
    w.set(102, paid="1200")                                         # a change: the next refresh saves
    first = threading.Thread(target=h.get)
    first.start()
    time.sleep(0.3)                                                 # the refresh is in its save now
    t0 = time.monotonic()
    second = threading.Thread(target=h.get)
    second.start()
    second.join(4)
    waited = time.monotonic() - t0
    release.set()
    first.join(10)
    second.join(10)
    assert waited < 2, "a request waited %.1fs behind a reading whose deadline was 1s" % waited


def test_a_request_that_waited_out_the_reading_gets_the_held_one_marked_refreshing_or_none():
    w = world()
    store = MemoryStore()
    h = rd.HeldReading(lambda: reader_of(w), store=store, ttl_s=0, deadline_s=0.5, delta_deadline_s=0.5)
    held, _ = h.get()
    release = _stuck_save(store)
    w.set(102, paid="1200")
    first = threading.Thread(target=h.get)
    first.start()
    time.sleep(0.2)
    for force in (False, True):                                     # a fresh=1, and a press
        t0 = time.monotonic()
        shown, last, refreshing = h.answer(force=force)
        assert shown is held and refreshing is True and time.monotonic() - t0 < 1.5
    assert "wait_timeout" in [c["code"] for c in h.health()["recent_codes"]]
    release.set()
    first.join(10)
    assert h.reading is not held and h.reading["kind"] == "delta"   # the reading in progress landed after all
    # an instance that holds nothing, with nothing stored, has nothing to answer with: no reading, never a hung worker
    cold_store = MemoryStore()
    cold = rd.HeldReading(lambda: reader_of(w), store=cold_store, ttl_s=0, deadline_s=0.5)
    release = _stuck_save(cold_store)
    first = threading.Thread(target=cold.get)
    first.start()
    time.sleep(0.2)
    shown, last, refreshing = cold.answer()
    assert shown is None and refreshing is False
    release.set()
    first.join(10)


def test_the_desks_own_request_behind_a_stuck_reading_is_answered_with_the_held_one(desk):  # noqa: F811
    """At the route: the page's `?fresh=1` behind a reading whose save does not come back is answered 200, the reading
    held marked refreshing, within the reading's deadline and the margin."""
    from fastapi.testclient import TestClient

    store = MemoryStore()
    main.app.state.desk_store = store
    try:
        first = desk.get(session_token(1)).json()
        held = main.app.state.desk.reading
        held.ttl_s, held.deadline_s, held.delta_deadline_s = 0, 1, 1
        release = _stuck_save(store)
        desk.w.set(102, paid="1200")
        hung = threading.Thread(target=lambda: TestClient(main.app).get(
            "/api/desk?fresh=1", headers={"Authorization": "Bearer " + session_token(1)}))
        hung.start()
        time.sleep(0.3)
        t0 = time.monotonic()
        r = TestClient(main.app).get("/api/desk?fresh=1", headers={"Authorization": "Bearer " + session_token(1)})
        waited = time.monotonic() - t0
        release.set()
        hung.join(10)
        assert r.status_code == 200 and waited < 3
        assert r.json()["refreshing"] is True and r.json()["taken_at"] == first["taken_at"]
    finally:
        main.app.state.desk_store = None


# --- 6. an ID number typed into a name or a free-text cell --------------------------------------------------------------

def test_an_id_number_typed_into_a_name_or_a_text_cell_is_not_kept_in_desk_readings():
    """Policy, decided (Nadav 24.11): NEVER_READ keeps the ID columns out, but desk_readings kept every name and text cell
    verbatim - a lead named with her ID number, a payment's credit reference, an account's invoice name. The books' text is
    cleaned of digit runs for exactly this reason (app/books/clean.py; CLAUDE.md rule 4 'An ID number is never stored
    server side'); the desk's reading masks them now, as it reads."""
    w = world()
    w.items["301"]["name"] = "דנה כהן 039482716"
    w.set(101, credit_ref="ת.ז 039482716", touch=False)
    store = MemoryStore()
    holder(w, store).get()
    assert "039482716" not in store.row["body"]
    body = json.loads(store.row["body"])
    assert body["lead_names"]["301"] == "דנה כהן " + rd.ID_MARK
    assert next(r for r in body["student_charge"]["rows"] if r["id"] == "101")[rd.SC["credit_ref"]] == "ת.ז " + rd.ID_MARK


def _ids_everywhere(w) -> None:
    """An ID number in every item's name and every free-text cell the desk reads, each written another way."""
    ways = ["039482716", "39482716", "039-482-716", "039 482 716", "050-1234567"]
    for n, (item_id, item) in enumerate(sorted(w.items.items())):
        key = next(k for k, b in rd.BOARDS.items() if b == item["board"])
        way = ways[n % len(ways)]
        item["name"] = item["name"] + " " + way
        for col in rd.TEXT_CELLS[key]:
            item["cells"][col] = "ref " + way


def _no_id_in(text: str) -> None:
    for way in ("039482716", "39482716", "039-482-716", "039 482 716", "050-1234567", "1234567"):
        assert way not in text, way


def test_the_same_rule_masks_a_full_reading_and_every_read_of_a_refresh():
    """A full reading, then a refresh that reads through every path it has - a changed charge, a charge that came unseen,
    the deciding leads read beside the windows, a newly linked enrollment, lead, cohort, deal and account read by id -
    holds no ID number anywhere, and the same desk as a full reading of the same monday."""
    w = world()
    _ids_everywhere(w)
    store = MemoryStore()
    h = holder(w, store)
    full, _ = h.get()
    _no_id_in(json.dumps(full, ensure_ascii=False) + store.row["body"])
    assert full["lead_names"]["301"].endswith(" " + rd.ID_MARK) and full["kind"] == "full"
    w.set(102, paid="1200", credit_ref="ת.ז 012345678")                 # a changed charge, its new text read by id
    w.add(SC, 104, "תלמיד-דלת 023456789", status=status("שולם", 1), paid="700", enrollment=rel(204), lead=rel(304),
          credit_ref="0234-56789")
    w.add(rd.ENROLLMENTS_BOARD, 204, "הרשמה-ד 034567891", cohort=rel())
    w.add(rd.LEADS_BOARD, 304, "ליד-דלת 045678912", cohort=rel(404), rep="נציגה 056789123")
    w.add(rd.COHORTS_BOARD, 404, "מחזור-ארבע 067891234", code="C4", external="ext 078912345")
    w.add(B2, 503, "חיוב-חברה-3 089123456", deal=rel(603), account=rel(703), amount="500", invoice="91234567")
    w.add(rd.DEALS_BOARD, 603, "עסקה-3 091234568", payer=rel(704))
    w.add(rd.ACCOUNTS_BOARD, 703, "חברה-שלישית", invoice_name="על שם 512345678", hash_key="77003")
    w.add(rd.ACCOUNTS_BOARD, 704, "חברה-רביעית 523456789")
    w.touch(104, 204, 304, 404, 503, 603, 703, 704)
    w.set(303, name="ליד-גימל 087654321", touch=False)                   # a deciding lead, read beside the windows
    kept, _ = h.get()
    assert kept["kind"] == "delta"
    text = json.dumps(kept, ensure_ascii=False) + store.row["body"]
    _no_id_in(text)
    for number in ("012345678", "023456789", "0234-56789", "034567891", "045678912", "056789123", "067891234", "078912345",
                   "089123456", "91234567", "091234568", "512345678", "523456789", "087654321"):
        assert number not in text, number
    assert "77003" in text and '"C4"' in text                          # the short references stay
    full_now = rd.read_desk(reader_of(w))
    assert desk_of(kept) == desk_of(full_now) and kept["lead_names"] == full_now["lead_names"]
    assert {k: kept[k] for k in rd.BODY_KEYS if k not in rd.CHARGE_KEYS} == {
        k: full_now[k] for k in rd.BODY_KEYS if k not in rd.CHARGE_KEYS}


def test_a_short_reference_a_date_and_every_cell_monday_writes_are_never_masked():
    """An invoice of four to six digits, a list of document numbers, a credit reference of seven, a date or a month typed
    as text, the Hashavshevet key and a cohort code stay as typed; so does every number, date, status, relation, dropdown
    and timeline cell, whatever its digits - only names and free text are masked."""
    w = world()
    w.set(101, invoice="50347", credit_ref="1234567", paid="123456789", paid_date="2026-11-24", touch=False)
    w.set(102, invoice="123456", credit_ref="16.08.2026", touch=False)
    w.set(103, invoice="50347, 50348", credit_ref="אישור 4580", touch=False)
    w.set(501, month="10/2026", invoice="50999", amount="123456789", course={"text": "קורס 123456789"}, touch=False)
    w.set(701, hash_key="770012", touch=False)
    w.set(401, code="DA-2610-01", dates={"text": "2026-01-01 - 2026-03-01"}, status=status("123456789", 3), touch=False)
    r = rd.read_desk(reader_of(w))
    sc_rows = {x["id"]: x for x in r["student_charge"]["rows"]}
    assert [sc_rows[i][rd.SC["invoice"]] for i in ("101", "102", "103")] == ["50347", "123456", "50347, 50348"]
    assert [sc_rows[i][rd.SC["credit_ref"]] for i in ("101", "102", "103")] == ["1234567", "16.08.2026", "אישור 4580"]
    assert sc_rows["101"][rd.SC["paid"]] == "123456789" and sc_rows["101"][rd.SC["paid_date"]] == "2026-11-24"
    b2 = r["b2b_charge"]["rows"][0]
    assert [b2[rd.B2[k]] for k in ("month", "invoice", "amount", "course")] == ["10/2026", "50999", "123456789",
                                                                               "קורס 123456789"]
    assert next(a for a in r["accounts"] if a["id"] == "701")[rd.AC["hash_key"]] == "770012"
    cohort = next(c for c in r["cohorts"] if c["id"] == "401")
    assert [cohort[rd.CO[k]] for k in ("code", "dates", "status")] == ["DA-2610-01", "2026-01-01 - 2026-03-01", "123456789"]
    assert set(rd.TEXT_CELLS["student_charge"]) == {rd.SC["credit_ref"], rd.SC["invoice"]}
    assert set(rd.TEXT_CELLS["leads"]) == {rd.LD["rep"]} and rd.TEXT_CELLS["enrollments"] == () == rd.TEXT_CELLS["deals"]


def test_the_page_and_the_excel_file_show_the_mark_never_the_number(desk):  # noqa: F811
    """End to end: monday's text with an ID number, the desk's answer, the page drawn from it in node, and the Excel
    file of the same list - each with the mark where the number was."""
    import io
    import shutil

    import openpyxl

    desk.w.items["101"]["name"] = "תלמידה-אלף 039482716"
    desk.w.items["301"]["name"] = "ליד-אלף 039-482-716"
    desk.w.set(101, credit_ref="ת.ז 039482716", invoice="חשבונית 039 482 716", touch=False)
    desk.w.set(301, rep="נציגה-אחת 039482716", touch=False)
    auth = {"Authorization": "Bearer " + session_token(1)}
    answer = desk.client.get("/api/desk", headers=auth)
    assert answer.status_code == 200 and "039482716" not in answer.text and "039-482-716" not in answer.text
    row = next(x for x in answer.json()["view"]["b2c"]["rows"] if x["id"] == "101")
    assert row["name"] == "תלמידה-אלף " + rd.ID_MARK and row["lead_names"] == ["ליד-אלף " + rd.ID_MARK]
    assert row["credit_ref"] == "ת.ז " + rd.ID_MARK and row["invoice"] == "חשבונית " + rd.ID_MARK
    assert row["rep"] == "נציגה-אחת " + rd.ID_MARK
    url = desk.client.post("/api/desk/export-link", headers=auth,
                           json={"tab": "b2c", "taken": answer.json()["taken_at"]}).json()["url"]
    wb = openpyxl.load_workbook(io.BytesIO(desk.client.get(url).content))
    cells = [str(c.value) for ws in wb.worksheets for line in ws.iter_rows() for c in line if c.value is not None]
    assert "תלמידה-אלף " + rd.ID_MARK in cells and not any("039482716" in c or "039-482-716" in c for c in cells)
    if shutil.which("node") is None:
        pytest.skip("node is not installed: the page is not drawn")
    from tests.test_desk_parity_js import LOAD, node

    html, = node(LOAD + "console.log(JSON.stringify(input.cases.map(c => F._render(input.body, c))));",
                 {"body": answer.json(), "cases": [{"tab": "b2c", "sc": {"state": "", "open": {"101": True}}}]})
    # the row's name, and in its details the invoice and the lead's Owner ("נציג מכירות")
    assert ">תלמידה-אלף %s</a>" % rd.ID_MARK in html and ">חשבונית %s</bdi>" % rd.ID_MARK in html
    assert "<dt>נציג מכירות</dt><dd>נציגה-אחת %s</dd>" % rd.ID_MARK in html
    assert "039482716" not in html and "039-482-716" not in html and "039 482 716" not in html


def test_a_reading_kept_before_the_mask_is_never_laid_under_a_refresh_or_shown():
    """BODY_SCHEMA's version moved with the mask: a stored reading of the shape before it is neither a refresh's base nor
    a cold instance's seed - the first reading after the deploy is whole, and replaces it."""
    w = world()
    store = MemoryStore()
    rd.HeldReading(lambda: reader_of(w), store=store).get()
    version, shape = rd.BODY_SCHEMA.split(":", 1)
    assert int(version) >= 3  # 2 with the mask, 3 with whole dates kept (QA 24.11)
    for older in range(1, int(version)):
        store.row["body_schema"] = "%d:%s" % (older, shape)
        cold = rd.HeldReading(lambda: reader_of(w), store=store)
        shown, _last, _refreshing = cold.answer(stale_ok=True)
        assert shown["kind"] == "full" and cold.seeded is False
        assert cold.health()["last_codes"][:2] == ["store_other_schema", "full_first"]
        assert store.row["body_schema"] == rd.BODY_SCHEMA


# --- QA 24.11: a whole date is never joined to the number beside it -----------------------------------------------------

DATED = [
    "A669 | קורס-אלף 01/12/2026 | 12.26 | חברה-בית - לפי מסיימי קורס",  # the shape of a B2B charge's name (read live)
    "קורס 1.12.26-31.12.26", "קורס 12.26 · 01/12/2026", "A669 קורס 01/12/2026 12.26", "קורס 01/12/2026, 12.26",
    "מחזור 2610 01/12/2026", "תשלום 3/12 01/12/2026", "A12 | 01.12.2026", "01.2026-06.2026", "11.26-01.27",
    "2026-12-01 2026-12-31",
]


def test_a_whole_date_or_month_beside_another_number_is_kept_as_typed():
    """The QA saw "...גברים חרדים # · משרד העבודה...": the books' cleaning joins digits across " | ", so a charge's date
    and month read as one number of twelve digits and were masked. Every whole date and month is now cut out first."""
    for text in DATED:
        assert rd.mask_ids(text) == text, text


def test_an_id_number_beside_a_date_is_still_masked():
    for text, want in {"01/12/2026 039482716": "01/12/2026 #", "ליד 039 482 716 01/12/2026": "ליד # 01/12/2026",
                       "01/12/2026-039482716": "01/12/2026-#", "ת.ז 03-9482716": "ת.ז #",
                       "ת.ז 039-482-716 | 12.26": "ת.ז # | 12.26", "שם 39482716 1.12.26": "שם # 1.12.26"}.items():
        assert rd.mask_ids(text) == want.replace("#", rd.ID_MARK), text
    # what only looks like a date (a day or a month out of range, or digits run on) is no date: judged with its neighbours
    for text in ("13/13/2026 12345678", "32.12.2026 1", "01/12/2026039482716"):
        masked = rd.mask_ids(text)
        assert rd.ID_MARK in masked and "039482716" not in masked and "12345678" not in masked, text
    # a date is cut out only where no digit touches it: one carved out of a longer run would leave the rest of an ID
    # number short enough to show ("01/12/2003" out of "01/12/2003948271" leaves 948271)
    for text, inner in (("01/12/2003948271", "948271"), ("39482701/12/2026", "394827")):
        masked = rd.mask_ids(text)
        assert rd.ID_MARK in masked and inner not in masked, (text, masked)


def test_a_charge_name_with_its_dates_reaches_the_desk_as_typed():
    w = world()
    w.items["501"]["name"] = DATED[0]
    r = rd.read_desk(reader_of(w))
    assert next(x for x in r["b2b_charge"]["rows"] if x["id"] == "501")["name"] == DATED[0]


# --- 7. the token before the body ---------------------------------------------------------------------------------------

def test_a_usage_body_is_not_read_before_the_token_is_checked(use):  # noqa: F811
    """POST /api/usage validated (and so parsed) the whole body before the route checked the sessionToken: with no token at
    all, a body of another shape was 400 invalid_filter, not 401 - so MAX_EVENTS and the usage limiter bounded nothing an
    anonymous client sent (the deployment is public)."""
    r = use.client.post("/api/usage", json={"desk": "finance", "session": "s" * 9, "events": [], "pad": "x" * 10_000})
    assert r.status_code == 401, (r.status_code, r.json())


def test_the_usage_route_answers_the_token_then_the_limit_then_the_body_with_the_same_codes(use, monkeypatch):  # noqa: F811
    from app.desk import routes as dr

    for body in ({"desk": "finance", "pad": 1}, b"{", b"not json", b""):
        kw = {"content": body, "headers": {"Content-Type": "application/json"}} if isinstance(body, bytes) else {"json": body}
        assert use.client.post("/api/usage", **kw).json() == {"error": "unauthorized"}
        signed = dict(kw, headers={**kw.get("headers", {}), "Authorization": "Bearer " + session_token(7301)})
        r = use.client.post("/api/usage", **signed)
        assert (r.status_code, r.json()) == (400, {"error": "invalid_filter"})
    assert use.writer.calls == 0 and use.health()["batches"] == 0        # such a body is no batch, as before
    # a reader past the usage limit is told so before the body is read at all
    monkeypatch.setattr(dr, "USAGE_BATCHES_PER_USER_HOUR", 1)
    from app import main

    main.app.state.usage = None
    auth = {"Authorization": "Bearer " + session_token(7302)}
    assert use.client.post("/api/usage", json=batch(ev("hide")), headers=auth).json() == {"stored": 1, "refused": 0}
    r = use.client.post("/api/usage", content=b"{", headers={**auth, "Content-Type": "application/json"})
    assert r.status_code == 429 and r.json()["error"] == "rate_limited"
    # and a well formed batch is kept as before, its check and its write as they were
    assert use.post(batch(ev("hide"), ev("teleport"))).json() == {"stored": 1, "refused": 1}


def test_the_export_links_body_is_read_after_its_gate(desk):  # noqa: F811
    auth = {"Authorization": "Bearer " + session_token(1)}
    for body in ({"tab": "b2c", "id_number": "1"}, {"tab": 5}, ["b2c"]):
        assert desk.client.post("/api/desk/export-link", json=body).json() == {"error": "unauthorized"}
        r = desk.client.post("/api/desk/export-link", json=body, headers=auth)
        assert (r.status_code, r.json()) == (400, {"error": "invalid_filter"})
    r = desk.client.post("/api/desk/export-link", json={"tab": "b2c"}, headers=auth)
    assert r.status_code == 200 and r.json()["url"].startswith("/api/desk/export.xlsx?")
    assert desk.client.get(r.json()["url"]).status_code == 200


# --- the mutants the review's pass left alive ---------------------------------------------------------------------------

def test_r7_a_refresh_whose_deciding_leads_come_back_short_says_the_leads_are_not_whole():
    """R7: `_delta_checks` marked the leads ok whatever the deciding leads' rows lacked. A column gone from the answers
    after the columns check (`drop_cells`) leaves the deciding leads short in the refresh as in a full reading."""
    w = world()
    h = holder(w)
    h.get()
    w.drop_cells = {rd.LD["cohort"]}
    kept, _ = h.get()
    assert kept["kind"] == "delta" and kept["checks"]["leads"]["short_rows"] == 2
    assert kept["checks"]["leads"]["ok"] is False and kept["complete"] is False
    full = rd.read_desk(reader_of(w))
    assert full["checks"]["leads"]["ok"] is False and full["complete"] is False


def test_r22_a_reading_whose_leads_names_or_owners_are_no_dict_is_never_laid_under_a_refresh():
    """R22: usable() without its `lead_reps` dict check. A reading's names and Owners are the dicts a reading and the
    store hold; a mapping of another kind (a read-only view here) is not that shape, so it is never a refresh's base."""
    kept = rd.read_desk(reader_of(world()))
    assert rd.usable(kept)
    for key in ("lead_names", "lead_reps"):
        assert not rd.usable(dict(kept, **{key: types.MappingProxyType(dict(kept[key]))})), key


def _sql(url, statement):
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(statement)


@needs_pg
def test_s3_a_save_behind_a_lock_gives_up_at_its_lock_timeout(pg, monkeypatch):
    """S3: DeskStore.save without its lock_timeout waited for a held table until the statement_timeout, and longer when
    that is off: a refresh's request held for a lock someone else holds."""
    monkeypatch.setattr(ds, "LOCK_TIMEOUT_MS", 300)
    monkeypatch.setattr(ds, "STATEMENT_TIMEOUT_MS", 5000)
    store = ds.DeskStore(pg)
    kept, _ = holder(world(), store).get()
    with psycopg.connect(pg) as other:
        other.execute("LOCK TABLE desk_readings IN ACCESS EXCLUSIVE MODE")
        t0 = time.monotonic()
        with pytest.raises(ds.DeskStoreError) as err:
            store.save(dict(kept, read_at=rd._now_iso()))
        took = time.monotonic() - t0
        other.rollback()
    assert err.value.detail == "55P03" and took < 3


@needs_pg
def test_s4_a_load_behind_a_lock_gives_up_at_its_statement_timeout(pg, monkeypatch):
    """S4: DeskStore.load without its statement_timeout waited for a held table as long as it was held: a cold open's
    seed, in a request, behind someone else's lock."""
    monkeypatch.setattr(ds, "STATEMENT_TIMEOUT_MS", 300)
    store = ds.DeskStore(pg)
    holder(world(), store).get()
    with psycopg.connect(pg) as other:
        other.execute("LOCK TABLE desk_readings IN ACCESS EXCLUSIVE MODE")
        timer = threading.Timer(4, other.rollback)                    # never a hang, whatever the code does
        timer.start()
        try:
            t0 = time.monotonic()
            with pytest.raises(ds.DeskStoreError) as err:
                store.load()
            took = time.monotonic() - t0
        finally:
            timer.cancel()
            other.rollback()
    assert err.value.detail == "57014" and took < 3


@needs_pg
def test_s5_load_says_the_schema_its_row_was_kept_under(pg):
    """S5: load() said BODY_SCHEMA for any row, so a reading kept by an older deploy was laid under a refresh."""
    w = world()
    store = ds.DeskStore(pg)
    holder(w, store).get()
    assert store.load()["body_schema"] == rd.BODY_SCHEMA
    _sql(pg, "UPDATE desk_readings SET body_schema = '1:an-older-deploy'")
    assert store.load()["body_schema"] == "1:an-older-deploy"
    cold = rd.HeldReading(lambda: reader_of(w), store=store)
    shown, _last, _refreshing = cold.answer(stale_ok=True)
    assert shown["kind"] == "full" and cold.health()["last_codes"][:2] == ["store_other_schema", "full_first"]


def test_c2_a_refresh_keeps_only_the_cells_it_asked_for_of_the_leads_it_reads_beside_the_windows():
    """C2: changed_items flattened the deciding leads without `keep`, so a cell monday answered unasked was kept (and
    stored); a read by id keeps only the cells it asked for (flatten's `keep`), here as in read_items."""
    w = world()
    h = holder(w)
    h.get()
    w.extra_cells = {"302": {"text_unasked": {"text": "x"}}, "303": {"color_unasked": {"text": "y", "index": 1, "label": "y"}}}
    kept, _ = h.get()
    allowed = set(rd.ROW_COLUMNS["leads"]) | set(monday.ROW_KEYS)
    assert kept["kind"] == "delta" and [r["id"] for r in kept["leads"]] == ["302", "303"]
    for row in kept["leads"]:
        assert set(row) <= allowed, sorted(set(row) - allowed)


def test_c5_a_graphql_rate_limit_whose_wait_does_not_fit_the_refresh_is_still_the_limits():
    """C5: a complexity budget monday names in GraphQL (not a 429) was not counted in `limited`, so a refresh whose reset
    did not fit its time ended `deadline` and fell back to a full reading - some thirty requests into that very limit."""
    w = world()
    h = holder(w)
    held, _ = h.get()
    w.window_error = [{"message": "Complexity budget exhausted, query cost 30001 budget remaining 2 out of 5000000 reset in "
                                  "30 seconds", "extensions": {"code": "ComplexityException"}}]
    before = len(w.queries)
    shown, last = h.get()
    assert last["error"] == "rate_limited" and len(w.queries) - before == 1 and shown is held
    assert h.health()["last_codes"] == ["delta_failed_rate_limited"]


def test_c6_packed_reads_never_send_more_than_a_chunk_of_ids_in_one_request():
    """C6: packing ignored ITEMS_BY_ID_CHUNK, so a refresh with many changes sent one request of every id."""
    w = world()
    reader = reader_of(w)
    jobs = [([str(5000 + i) for i in range(60)], [rd.SC["paid"]]), ([str(6000 + i) for i in range(60)], [rd.DL["payer"]]),
            ([str(7000 + i) for i in range(30)], [rd.CO["code"]])]
    reader.read_items(jobs, pack=True)
    sizes = [len(var["ids"]) if "ids" in var else sum(len(v) for k, v in var.items() if re.fullmatch(r"i\d+", k))
             for _q, var in w.queries]
    assert sorted(sizes) == [60, 90] and max(sizes) <= monday.ITEMS_BY_ID_CHUNK      # side by side, in any order
