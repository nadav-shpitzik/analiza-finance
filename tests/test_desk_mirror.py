"""D2's stored reading and its refresh that reads only what changed (Analiza-Monday backlog 0301 D2, 24.11): analiza-metrics
tests/test_finance_mirror.py and the delta tests of tests/test_finance.py at da671b2, ported onto this service's reader
(app/monday/client.py), its desk reading (app/desk/reading.py), its store (app/desk/store.py) and the World of
tests/test_desk.py, extended for the refresh.

Each rule, against a monday that can answer a never-read column unasked:
1. a reading is kept in Postgres and read back without a column of NEVER_READ - and a text that names one is never written;
2. an instance that woke from zero answers from the stored reading at once, marked refreshing, and the page's own request
   reads the next;
3. a refresh reads only what changed and lays it over the held reading by id (replaced, new, newly linked); a charge that
   left its board unseen (archived, deleted, moved) is dropped and one that came unseen is read by id, by the board's ids
   paged in every refresh (D2's review of a32395f: an equal count hid both), and a board whose ids cannot be reconciled
   makes the next reading whole;
4. after FULL_AFTER since the last full reading, a full reading;
5. a refresh monday refuses, or whose answer has another shape, falls back to a full reading and says so in /health - at
   most once per FALLBACK_GAP, and never for an error a full reading would meet the same way;
6. /health says how the desk read and kept its reading: never a row, a name, an amount or a count of rows;
7. the migration is re-runnable, CREATE only, and names what the store writes;
8. Leads has no window: the deciding leads are read again by id in the refresh's one request, and a lead that newly
   decides, or that a payment newly links to, is read by id;
9. through random edits, every refresh holds the desk, the deciding leads and the names a full reading holds;
and this service's own rules on the refresh (D1's reviews): a row answered from another board is a stranger, no request
after the deadline, a NEVER_READ column refused before anything is sent.

What analiza-metrics tested that has no port here: the bank accounts and ID numbers read live for a view or a file
(`attach_bank`, `read_id_numbers`) - this server never reads them (NEVER_READ; the page reads them as the person).
"""
from __future__ import annotations

import copy
import json
import random
import re
import threading
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest

from app import main
from app.desk import reading as rd
from app.desk import store as ds
from app.desk import view as fd
from app.monday import client as monday
from app.monday.client import NEVER_READ, MondayError
from tests.conftest import TEST_DATABASE_URL, needs_pg
from tests.test_desk import B2, OTHER_BOARD, SC, World, desk, reader_of, rel, session_token, status, world  # noqa: F401

TODAY = date(2026, 11, 24)
ID_NUMBER = "039482716"          # what monday answers for a never-read column when a test makes it: grep for a leak
BANK_ACCOUNT = "ACCOUNT-90817364"


class MemoryStore:
    """app/desk/store.py's DeskStore in memory: its contract - `load`, `save` that answers False under a newer row, the
    body and the counts through JSON as the database keeps them, `refuse_unsafe` on the very text - with `writes` (saves
    written), and `fail_load` / `fail_save` (an exception every call raises)."""

    def __init__(self):
        self.row = None
        self.writes = 0
        self.fail_load = None
        self.fail_save = None

    def save(self, reading: dict) -> bool:
        if self.fail_save:
            raise self.fail_save
        assert not reading.get("error") and reading.get("kind") in ds.KINDS
        body = json.dumps(rd.body_of(reading), ensure_ascii=False, separators=(",", ":"))
        checks = json.dumps(reading.get("checks") or {}, ensure_ascii=False, separators=(",", ":"))
        ds.refuse_unsafe(body)
        ds.refuse_unsafe(checks)
        if self.row is not None and rd.instant(self.row["read_at"]) > rd.instant(reading["read_at"]):
            return False
        self.row = {"read_at": reading["read_at"], "full_read_at": reading["full_read_at"], "kind": reading["kind"],
                    "complete": bool(reading.get("complete")), "body_schema": rd.BODY_SCHEMA, "checks": checks,
                    "body": body, "calls": reading.get("calls"), "seconds": reading.get("seconds")}
        self.writes += 1
        return True

    def load(self):
        if self.fail_load:
            raise self.fail_load
        if self.row is None:
            return None
        r, body = self.row, json.loads(self.row["body"])
        out = {k: body.get(k) for k in rd.BODY_KEYS}
        out.update({"read_at": r["read_at"], "full_read_at": r["full_read_at"], "kind": r["kind"],
                    "complete": r["complete"], "error": None, "detail": None, "checks": json.loads(r["checks"]),
                    "calls": r["calls"], "seconds": r["seconds"], "body_schema": r["body_schema"]})
        return out


def desk_of(snapshot: dict) -> tuple:
    """What the page is shown of a reading: both tabs' rows, the view's checks and its notes - not its time."""
    v = fd.build_view(snapshot, TODAY)
    return v["b2c"]["rows"], v["b2b"]["rows"], v["checks"], v["data_notes"]


def view_row(snapshot: dict, item_id) -> dict:
    return next(x for x in fd.build_view(snapshot, TODAY)["b2c"]["rows"] if x["id"] == str(item_id))


def held_full(w: World) -> tuple:
    """A reader and a full reading of `w`: the base a refresh is laid over."""
    reader = reader_of(w)
    return reader, rd.read_desk(reader)


def holder(w: World, store=None, **kw) -> rd.HeldReading:
    """A held reading that reads on every `get()` (no span), with a store when given."""
    return rd.HeldReading(lambda: reader_of(w), store=store, ttl_s=0, **kw)


def readings(w: World) -> int:
    """How many readings (full or refresh) monday saw: each starts with its one columns check."""
    return len(w.queries_of("columns(ids:"))


def cols_read(w: World) -> set:
    """Every column asked of monday in a read by id: plain, packed and beside the windows - never a columns check."""
    out = set()
    for q, v in w.queries:
        if "columns(ids:" not in q and "items(ids:" in q:
            out |= set(v.get("cols") or [])
            out |= {c for n in re.findall(r"j(\d+): items\(", q) for c in v["c" + n]}
        out |= set(v.get("lc") or [])
    return out


def no_secret_in(text: str) -> None:
    assert ID_NUMBER not in text and BANK_ACCOUNT not in text
    assert not any(col in text for col in NEVER_READ)


def leaky(w: World) -> None:
    """monday answers a lead's ID number and a payment's bank account unasked, wherever they are read by id."""
    w.extra_cells = {"301": {rd.LD["id_number"]: {"text": ID_NUMBER}}, "302": {"lookup_mm8b1jzy": {"text": ID_NUMBER}},
                     "101": {rd.SC["bank_account"]: {"text": BANK_ACCOUNT}}}


@pytest.fixture
def stored(desk):  # noqa: F811
    """The desk fixture's app with a stored reading of its own (`stored.store`), shared by every desk state the test makes:
    a new state is another instance on the same database."""
    store = MemoryStore()
    main.app.state.desk_store = store
    desk.store = store
    yield desk
    main.app.state.desk_store = None


def auth(user: int = 1) -> dict:
    return {"Authorization": "Bearer " + session_token(user)}


# --- 1. kept without a never-read column -------------------------------------------------------------------------------

def test_a_reading_is_kept_and_read_back_without_a_never_read_column():
    w = world()
    leaky(w)
    store = MemoryStore()
    kept, _ = holder(w, store).get()
    assert not cols_read(w) & NEVER_READ                               # never asked with the rows
    no_secret_in(store.row["body"] + store.row["checks"])
    no_secret_in(json.dumps(kept, ensure_ascii=False))
    # an instance that holds nothing answers with it at once, as it was kept, without a request to monday
    before = len(w.queries)
    cold = rd.HeldReading(lambda: reader_of(w), store=store)
    shown, _last, refreshing = cold.answer(stale_ok=True)
    assert refreshing is True and cold.seeded is True and len(w.queries) == before
    assert rd.body_of(shown) == rd.body_of(kept) and desk_of(shown) == desk_of(kept)
    assert shown["read_at"] == kept["read_at"] and shown["checks"] == kept["checks"] and shown["complete"] is True


def test_a_refresh_is_kept_without_them_too():
    w = world()
    store = MemoryStore()
    held = holder(w, store)
    held.get()
    leaky(w)
    w.set(102, status=status("שולם", 1), paid="1200")
    w.touch(101, 301)
    kept, _ = held.get()
    assert kept["kind"] == "delta" and "store_saved" in held.health()["last_codes"]
    body = store.row["body"]
    no_secret_in(body)
    assert '"1200"' in body and not cols_read(w) & NEVER_READ


def test_a_text_that_names_a_never_read_column_is_never_written(monkeypatch):
    """Belt and braces: were a later change to read one with the rows, the store refuses the text before the database is
    asked, and the stored reading stays as it was."""
    for bad in ('{"%s":"1"}' % rd.SC["bank_account"], '{"x":"%s"}' % rd.LD["id_number"], '{"a":{"lookup_mm3y7rwr#":"1"}}'):
        with pytest.raises(rd.UnsafeBody):
            ds.refuse_unsafe(bad)
    ds.refuse_unsafe(json.dumps({"name": 'a "text" mm8 student'}, ensure_ascii=False, separators=(",", ":")))
    w = world()
    store = MemoryStore()
    held = holder(w, store)
    held.get()
    kept_row = dict(store.row)
    real = rd.read_delta

    def reads_one(reader, base):
        out = real(reader, base)
        out["student_charge"]["rows"][0] = dict(out["student_charge"]["rows"][0], **{rd.SC["bank_account"]: "1"})
        return out

    monkeypatch.setattr(rd, "read_delta", reads_one)
    held.get()
    assert "store_refused" in held.health()["last_codes"] and store.row == kept_row
    # the real store refuses it before it connects: an address that answers nothing is never tried
    unsafe = dict(held.reading, read_at=datetime.now(timezone.utc).isoformat())
    with pytest.raises(rd.UnsafeBody):
        ds.DeskStore("postgresql://user:SECRETPW@127.0.0.1:1/nothing").save(unsafe)


def test_a_stored_reading_of_another_shape_is_never_laid_under_a_refresh():
    w = world()
    store = MemoryStore()
    rd.HeldReading(lambda: reader_of(w), store=store).get()
    store.row["body_schema"] = "0:an-older-deploy"
    held = rd.HeldReading(lambda: reader_of(w), store=store)
    kept, _ = held.get()
    assert kept["kind"] == "full" and held.health()["last_codes"][:2] == ["store_other_schema", "full_first"]
    assert store.row["body_schema"] == rd.BODY_SCHEMA                  # the full reading replaced it, once
    assert holder(w, store).get()[0]["kind"] == "delta"


def test_an_older_reading_never_replaces_a_newer_one_and_a_refresh_that_changed_nothing_writes_nothing():
    w = world()
    store = MemoryStore()
    held = holder(w, store)
    held.get()
    saved = dict(store.row)
    kept, _ = held.get()                                                # nothing changed on any board
    assert kept["kind"] == "delta" and held.health()["last_codes"] == ["delta", "store_unchanged"]
    assert store.row == saved
    # another instance with an older reading cannot write over the newer one (two instances, one database)
    older = dict(kept, read_at=(rd.instant(saved["read_at"]) - timedelta(minutes=5)).isoformat())
    assert store.save(older) is False and store.row == saved


def test_a_run_of_refreshes_that_changed_nothing_writes_nothing():
    """analiza-metrics' hostile review 11.11: every other quiet refresh rewrote the whole body, since the store's read_at
    was compared with the held reading's, which moves on every refresh."""
    w = world()
    store = MemoryStore()
    held = holder(w, store)
    held.get()
    assert store.writes == 1
    for _ in range(4):
        held.get()
        assert held.health()["last_codes"] == ["delta", "store_unchanged"]
    assert store.writes == 1
    w.set(102, paid="1200")
    held.get()
    assert store.writes == 2 and "store_saved" in held.health()["last_codes"]
    for _ in range(3):
        held.get()
    assert store.writes == 2
    # what the store holds is the desk the warm instance shows: an instance that woke from zero builds the same one
    assert desk_of(store.load()) == desk_of(held.reading)


def test_a_reading_the_database_could_not_keep_is_kept_by_the_next_refresh_even_when_nothing_changed():
    w = world()
    store = MemoryStore()
    store.fail_save = ConnectionError("down")
    held = holder(w, store)
    held.get()
    assert "store_save_failed_ConnectionError" in held.health()["last_codes"] and store.row is None
    store.fail_save = None
    held.get()                                                          # a refresh that changed nothing
    assert "store_saved" in held.health()["last_codes"] and store.row["kind"] == "delta"


# --- 2. an instance that woke from zero -------------------------------------------------------------------------------

def test_an_instance_that_woke_from_zero_answers_from_the_stored_reading_at_once_and_the_page_reads_the_next(stored):
    first = stored.get(session_token(1)).json()
    assert first["refreshing"] is False and stored.store.writes == 1
    main.app.state.desk = None                                          # another instance: the same database, no memory
    before = len(stored.w.queries)
    cold = stored.get(session_token(1)).json()
    # answered without reading a board (the gate read the members, as on any cold instance)
    assert [q for q, _v in stored.w.queries[before:] if "subscribers" not in q] == []
    assert cold["refreshing"] is True and cold["taken_at"] == first["taken_at"] and cold["stale"] is False
    assert cold["reading"]["complete"] is True and cold["age_seconds"] >= 0
    assert [r["id"] for r in cold["view"]["b2c"]["rows"]] == [r["id"] for r in first["view"]["b2c"]["rows"]]
    held = main.app.state.desk.reading
    assert "seeded" in [c["code"] for c in held.health()["recent_codes"]] and held.health()["held"]["seeded"] is True
    fresh = stored.get(session_token(1), path="/api/desk?fresh=1").json()  # the page's own request
    assert fresh["refreshing"] is False and fresh["taken_at"] != first["taken_at"] and readings(stored.w) == 2
    h = stored.client.get("/health").json()["desk"]
    assert h["last_reading"]["kind"] == "delta" and h["refresh"]["last_codes"] == ["delta", "store_unchanged"]
    assert "store_loaded" in [c["code"] for c in h["refresh"]["recent_codes"]]


def test_a_cold_instance_with_nothing_stored_reads_monday_as_before(stored):
    body = stored.get(session_token(1)).json()
    assert body["refreshing"] is False and body["view"] is not None and readings(stored.w) == 1
    codes = stored.client.get("/health").json()["desk"]["refresh"]["last_codes"]
    assert codes == ["store_empty", "full_first", "store_saved"]


def test_a_database_that_cannot_be_reached_is_said_and_the_desk_still_reads_monday(stored, monkeypatch):
    stored.store.fail_load = stored.store.fail_save = ConnectionError("down")
    assert stored.get(session_token(1)).status_code == 200 and readings(stored.w) == 1
    codes = stored.client.get("/health").json()["desk"]["refresh"]["last_codes"]
    assert "store_load_failed_ConnectionError" in codes and "store_save_failed_ConnectionError" in codes
    # the real store at an address that answers nothing: said by its class, never the address or the password
    main.app.state.desk_store = None
    main.app.state.desk = None
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:SECRETPW@127.0.0.1:1/nothing")
    assert stored.get(session_token(1)).status_code == 200
    h = stored.client.get("/health")
    assert "store_load_failed_OperationalError" in h.json()["desk"]["refresh"]["last_codes"]
    assert "SECRETPW" not in h.text and "127.0.0.1" not in h.text


def test_an_excel_link_that_lands_on_a_cold_instance_is_built_from_the_stored_reading(stored):
    """analiza-metrics `finance_export_file` (`stale_ok=True`): the file of the reading the page showed, from the stored
    one on an instance the link landed on cold - no board read - and the file says nothing of another reading."""
    import io

    import openpyxl

    page = stored.get(session_token(1)).json()
    url = stored.client.post("/api/desk/export-link", headers=auth(),
                             json={"tab": "b2c", "taken": page["taken_at"]}).json()["url"]
    main.app.state.desk = None                                          # the link opens on another instance, cold
    before = len(stored.w.queries)
    f = stored.client.get(url)
    assert f.status_code == 200
    assert [q for q, _v in stored.w.queries[before:] if "subscribers" not in q] == []
    wb = openpyxl.load_workbook(io.BytesIO(f.content))
    assert [r[1].value for r in wb["פרטים"].iter_rows() if r[0].value == "שימו לב"] == []
    assert main.app.state.desk.reading.seeded is True


def test_a_held_reading_of_any_age_is_answered_at_once_marked_refreshing(stored):
    stored.get(session_token(1))
    main.app.state.desk.reading.reading_at -= 3 * 86400                 # held for three days
    body = stored.get(session_token(1)).json()
    assert body["refreshing"] is True and readings(stored.w) == 1 and body["age_seconds"] >= 3 * 86400


def test_a_seed_never_replaces_a_reading_and_a_failed_seed_only_waits_for_one():
    w = world()
    store = MemoryStore()
    rd.HeldReading(lambda: reader_of(w), store=store).get()            # an instance that read and kept its reading
    kept_at = store.row["read_at"]
    held = rd.HeldReading(lambda: reader_of(w), store=store)
    shown, _last, refreshing = held.answer(stale_ok=True)
    assert refreshing is True and shown["read_at"] == kept_at and held.seeded is True
    # a stored reading however young is not this instance's reading: never fresh, so the next request reads
    fresh, _ = held.get()
    assert fresh["read_at"] != kept_at and fresh["kind"] == "delta" and held.seeded is False
    held._seed_tried_at = None
    held._seed()                                                        # a seed after a reading changes nothing
    assert held.reading is fresh and held.seeded is False
    down = MemoryStore()
    down.fail_load = RuntimeError("db")
    cold = rd.HeldReading(lambda: reader_of(w), store=down)
    shown, _last, refreshing = cold.answer(stale_ok=True)
    assert shown is not None and refreshing is False and shown["kind"] == "full"


def test_a_seed_that_lands_after_a_reading_leaves_the_reading():
    """The seed and a reading write the state under one small lock: a seed whose database answered late finds the
    reading there and leaves it."""
    w = world()
    store = MemoryStore()
    rd.HeldReading(lambda: reader_of(w), store=store).get()
    release, entered, calls = threading.Event(), threading.Event(), []
    real_load = store.load

    def slow_once():
        calls.append(1)
        if len(calls) == 1:                                             # the seed's load: answered late
            entered.set()
            assert release.wait(5)
        return real_load()

    store.load = slow_once
    held = rd.HeldReading(lambda: reader_of(w), store=store)
    got = {}
    opener = threading.Thread(target=lambda: got.setdefault("answer", held.answer(stale_ok=True)))
    opener.start()
    assert entered.wait(5)
    read, _ = held.get()                                                # a reading lands while the seed waits
    release.set()
    opener.join(5)
    assert held.reading is read and held.seeded is False and read["kind"] == "delta"
    assert "seeded" not in [c["code"] for c in held.health()["recent_codes"]]


# --- 3. a refresh reads only what changed -------------------------------------------------------------------------------

def test_a_refresh_reads_only_what_changed_and_lays_it_over_the_held_reading_by_id():
    w = world()
    reader, held = held_full(w)
    w.set(102, status=status("שולם", 1), paid="1200")
    w.set(501, amount="1100")
    before, reads = len(w.queries), len(w.by_id_reads())
    got = rd.read_delta(reader, held)
    # the six boards' windows with the deciding leads (302: its enrollment names no cohort; 303: no enrollment) in one
    # request, the two charge boards' ids (one page each), and the two changed rows packed into one more
    assert len(w.queries) - before == 4 and got["calls"] == 4 and got["id_pages"] == 2
    assert sorted(ids for _b, ids in w.by_id_reads()[reads:]) == [["102"], ["302", "303"], ["501"]]
    assert got["kind"] == "delta" and got["full_read_at"] == held["full_read_at"] and got["window_days"] == 2
    assert [r["id"] for r in got["student_charge"]["rows"]] == ["101", "102", "103"]       # in place
    assert got["complete"] is True and got["error"] is None and set(got["checks"]) == set(rd.BOARDS)
    assert str(view_row(got, 102)["money_in"]) == "1200"
    assert desk_of(got) == desk_of(rd.read_desk(reader))                # the same desk as a full reading


def test_a_new_charge_and_what_it_newly_links_to_are_read_by_id():
    w = world()
    reader, held = held_full(w)
    w.add(SC, 104, "תלמיד-דלת", status=status("שולם", 1), paid="700", enrollment=rel(204), lead=rel(304))
    w.add(rd.ENROLLMENTS_BOARD, 204, "הרשמה-ד", cohort=rel())
    w.add(rd.LEADS_BOARD, 304, "ליד-דלת", cohort=rel(404), rep="")
    w.add(rd.COHORTS_BOARD, 404, "מחזור-ארבע", code="C4")
    w.add(B2, 503, "חיוב-חברה-3", deal=rel(603), account=rel(703), amount="500")
    w.add(rd.DEALS_BOARD, 603, "עסקה-3", payer=rel(704))
    w.add(rd.ACCOUNTS_BOARD, 703, "חברה-שלישית")
    w.add(rd.ACCOUNTS_BOARD, 704, "חברה-רביעית")
    w.touch(104, 204, 304, 404, 503, 603, 703, 704)
    before, reads = len(w.queries), len(w.by_id_reads())
    got = rd.read_delta(reader, held)
    # the windows with the deciding leads; the charge boards' ids; the two new charges (in their windows and new to their
    # boards' ids: read once); what they link to; the cohort and the accounts those name
    assert len(w.queries) - before == 6
    assert [ids for b, ids in w.by_id_reads()[reads:] if b in (SC, B2)] == [["104"], ["503"]]
    # 304 decides (204 names no cohort) and joins the deciding leads; 301 is kept by name only (201 names 401)
    assert got["asked"]["enrollments"] == ["201", "202", "204"] and got["asked"]["leads"] == ["302", "303", "304"]
    assert got["lead_names"] == {"301": "ליד-אלף", "302": "ליד-בית", "303": "ליד-גימל", "304": "ליד-דלת"}
    assert got["lead_reps"] == {"301": "נציגה-אחת", "302": "", "303": "נציג-שני", "304": ""}
    assert got["asked"]["cohorts"] == ["401", "402", "404"] and got["asked"]["accounts"] == ["701", "702", "703", "704"]
    assert view_row(got, 104)["cohort_code"] == "C4" and got["complete"] is True
    assert desk_of(got) == desk_of(rd.read_desk(reader))


def test_a_linked_item_that_changed_replaces_its_copy_and_one_no_payment_links_to_is_not_read():
    w = world()
    reader, held = held_full(w)
    w.set(201, voucher=status("אושר - ממתין לתשלום", 1))
    w.add(rd.LEADS_BOARD, 309, "ליד-לא-מקושר", cohort=rel())
    w.touch(309)
    reads = len(w.by_id_reads())
    got = rd.read_delta(reader, held)
    # the deciding leads with the windows, then 201; 309 is never read, and Leads has no window at all
    assert w.by_id_reads()[reads:] == [(rd.LEADS_BOARD, ["302", "303"]), (rd.ENROLLMENTS_BOARD, ["201"])]
    assert rd.LEADS_BOARD not in w.windowed
    assert view_row(got, 101)["voucher"] == "אושר - ממתין לתשלום"
    assert desk_of(got) == desk_of(rd.read_desk(reader))


def test_a_link_a_payment_no_longer_has_leaves_the_reading_as_a_full_reading_would_leave_it():
    w = world()
    reader, held = held_full(w)
    w.set(102, enrollment=rel())                                        # 202 unlinked
    got = rd.read_delta(reader, held)
    assert "202" not in got["asked"]["enrollments"] and [e["id"] for e in got["enrollments"]] == ["201"]
    assert desk_of(got) == desk_of(rd.read_desk(reader))


def test_a_charge_that_left_its_board_unseen_is_dropped_by_the_boards_ids_without_a_full_reading():
    """No window shows a charge archived or deleted (its updated_at does not move): the charge boards' ids, paged in every
    refresh, do (D2's review of a32395f: before, only the count did, and an equal count hid one that left as another
    came)."""
    w = world()
    reader, held = held_full(w)
    w.leave(103, "archived")                                            # archiving moved no updated_at a window sees
    got = rd.read_delta(reader, held)
    assert [r["id"] for r in got["student_charge"]["rows"]] == ["101", "102"] and got["complete"] is True
    assert got["student_charge"]["items_count"] == 2 and desk_of(got) == desk_of(rd.read_desk(reader))
    # through the held reading: a refresh, never a full reading for it
    h = holder(w, MemoryStore())
    h.get()
    w.leave(501, "deleted")
    kept, _ = h.get()
    assert kept["kind"] == "delta" and h.health()["last_codes"][0] == "delta"
    assert [r["id"] for r in kept["b2b_charge"]["rows"]] == ["502"]
    assert [r["id"] for r in kept["student_charge"]["rows"]] == ["101", "102"]


def test_a_board_whose_ids_disagree_with_its_own_count_makes_the_next_reading_whole():
    w = world()
    reader, held = held_full(w)
    w.count_off[SC] = 1                                                 # monday counts an item its pages never give
    with pytest.raises(rd.DeltaMismatch) as err:
        rd.read_delta(reader, held)
    assert err.value.key == "student_charge" and err.value.code == "count_mismatch"
    # through the held reading: a full reading, and the reason in the trace
    h = holder(w, MemoryStore())
    w.count_off[SC] = 0
    h.get()
    w.count_off[B2] = 1
    kept, _ = h.get()
    assert kept["kind"] == "full" and "full_count_b2b_charge" in h.health()["last_codes"]
    assert kept["checks"]["b2b_charge"]["ok"] is False                 # and the full reading says it came short


def test_a_charge_archived_between_its_boards_page_and_its_read_makes_the_next_reading_whole():
    """An id the board's pages gave that comes back archived when read (archived in between) is no row of the board, and
    a refresh cannot tell what else changed: it reads whole, never holding the archived charge as the board's."""
    w = world()
    w.window_states = ("active", "archived")
    reader, held = held_full(w)
    w.items["102"]["state"] = "archived"                               # still in the board's pages: archived after them
    w.touch(102)
    with pytest.raises(rd.DeltaMismatch) as err:
        rd.read_delta(reader, held)
    assert err.value.key == "student_charge"


def test_a_charge_archived_inside_the_window_leaves_without_a_full_reading():
    w = world()
    w.window_states = ("active", "archived")         # if monday's window answers archived items, they are read and leave
    reader, held = held_full(w)
    w.leave(103, "archived")
    w.touch(103)
    got = rd.read_delta(reader, held)
    assert [r["id"] for r in got["student_charge"]["rows"]] == ["101", "102"] and got["complete"] is True


def test_the_cut_is_the_held_readings_start_less_the_margin_and_the_window_covers_the_gap():
    w = world()
    reader, held = held_full(w)
    start = rd.instant(held["read_at"])
    w.touch(101, at=(start - rd.DELTA_MARGIN - timedelta(minutes=1)).isoformat())   # before the cut: not read
    w.touch(102, at=(start - timedelta(minutes=5)).isoformat())                    # within the margin: read
    reads = len(w.by_id_reads())
    rd.read_delta(reader, held)
    assert w.by_id_reads()[reads:] == [(rd.LEADS_BOARD, ["302", "303"]), (SC, ["102"])] and w.windows[-1] == 2
    # a held reading 30 hours old asks for three days: n calendar days hold at least n - 1 days of 23 hours
    rd.read_delta(reader, dict(held, read_at=(start - timedelta(hours=30)).isoformat()))
    assert w.windows[-1] == 3
    assert [rd.window_days(timedelta(hours=h)) for h in (0, 1, 22.9, 23.5, 36.2, -1)] == [2, 2, 2, 3, 3, 2]
    q = monday.window_query(6, 1, 0, 2)
    assert 'compare_value: ["DAYS", 2], operator: within_the_last, compare_attribute: "UPDATED_AT"' in q
    assert q.count("query_params") == 6 and "items(ids" not in q


def test_a_long_window_is_paged_to_its_end():
    w = world()
    w.window_page = 1
    reader, held = held_full(w)
    w.touch(101, 102, 103)
    reads = len(w.by_id_reads())
    rd.read_delta(reader, held)
    assert len(w.queries_of("next_items_page")) == 2
    assert w.by_id_reads()[reads:] == [(rd.LEADS_BOARD, ["302", "303"]), (SC, ["101", "102", "103"])]


def test_the_held_reading_is_never_changed_by_a_refresh_or_by_the_desk_built_from_it():
    """The held reading is the next refresh's base and the stored copy's content: a refresh laid over it and a view built
    from it leave it exactly as kept."""
    w = world()
    h = holder(w)
    kept, _ = h.get()
    before = copy.deepcopy(kept)
    fd.visible_part(fd.build_view(kept, TODAY), True, False)
    w.set(102, paid="1200")
    w.set(201, cohort=rel())
    w.add(SC, 104, "תלמיד-דלת", enrollment=rel(), lead=rel(301))
    w.touch(104)
    newer, _ = h.get()
    assert newer["kind"] == "delta" and kept == before and newer is not kept


# --- 4. a full reading at least every FULL_AFTER ------------------------------------------------------------------------

def test_a_held_reading_whose_last_full_reading_is_older_than_36_hours_is_read_whole():
    w = world()
    store = MemoryStore()
    clock = {"now": datetime.now(timezone.utc)}
    h = holder(w, store, now=lambda: clock["now"])
    assert h.get()[0]["kind"] == "full"
    full_at = h.reading["full_read_at"]
    clock["now"] += timedelta(hours=35)
    w.set(101, paid="4100")
    kept, _ = h.get()
    assert kept["kind"] == "delta" and kept["full_read_at"] == full_at  # a refresh keeps the last full one's time
    assert store.load()["full_read_at"] == full_at and store.load()["kind"] == "delta"
    clock["now"] += timedelta(hours=2)                                  # 37 hours after the full reading
    kept, _ = h.get()
    assert kept["kind"] == "full" and kept["full_read_at"] == kept["read_at"] != full_at
    assert "full_36h" in h.health()["last_codes"]
    # and an instance that woke from zero counts the 36 hours from the stored reading's full one
    clock["now"] += timedelta(hours=37)
    cold = rd.HeldReading(lambda: reader_of(w), store=store, now=lambda: clock["now"])
    cold.get()
    assert cold.health()["last_codes"][:2] == ["store_loaded", "full_36h"]


# --- 5. a refresh that fails falls back to a full reading, within limits ------------------------------------------------

API_VERSION = [{"message": 'Unknown argument "compare_attribute" on field "ItemsQueryRule".'}]
# a reset that fits in the refresh's own time: it waits, tries once more, and is refused again
RATE_LIMITED = [{"message": "Complexity budget exhausted, query cost 30001 budget remaining 2 out of 5000000 reset in 2 "
                            "seconds", "extensions": {"code": "ComplexityException"}}]


def test_a_refresh_monday_refuses_falls_back_to_a_full_reading_and_says_why_in_health(desk):  # noqa: F811
    desk.get(session_token(1))
    desk.w.window_error = API_VERSION
    desk.w.set(102, status=status("שולם", 1), paid="1200")
    r = desk.client.post("/api/desk/refresh", headers=auth())
    assert r.status_code == 200 and r.json()["reading"]["complete"] is True
    assert next(x for x in r.json()["view"]["b2c"]["rows"] if x["id"] == "102")["money_in"] == "1200"
    h = desk.client.get("/health")
    d = h.json()["desk"]
    assert d["last_reading"]["kind"] == "full" and d["last_reading"]["error"] is None
    assert d["refresh"]["last_codes"] == ["full_delta_error_graphql", "store_not_configured"]
    assert d["refresh"]["last_delta_error"]["code"] == "graphql"
    assert "compare_attribute" not in h.text                            # monday's words are the log's, never /health's


@pytest.mark.parametrize("shape, code", [
    (lambda x: {"id": x["id"]}, "full_delta_error_delta_shape"),                 # no updated_at served at all
    (lambda x: dict(x, updated_at=None), None),                                 # a null time is read, never skipped
])
def test_a_window_of_another_shape_falls_back_and_a_missing_time_is_read(shape, code):
    w = world()
    h = holder(w)
    h.get()
    w.window_item = shape
    w.touch(101)
    reads = len(w.by_id_reads())
    kept, _ = h.get()
    if code:
        assert kept["kind"] == "full" and code in h.health()["last_codes"]
    else:
        assert kept["kind"] == "delta" and (SC, ["101"]) in w.by_id_reads()[reads:]


def test_an_error_the_refresh_did_not_foresee_falls_back_too_and_one_a_full_reading_would_repeat_is_its_own(
        monkeypatch):
    w = world()
    h = holder(w)
    h.get()
    real = rd.read_delta
    monkeypatch.setattr(rd, "read_delta", lambda reader, base: (_ for _ in ()).throw(TypeError("surprise")))
    assert h.get()[0]["kind"] == "full" and "full_delta_error_internal" in h.health()["last_codes"]
    monkeypatch.setattr(rd, "read_delta", real)
    held = h.reading
    w.columns_missing[SC] = {rd.SC["paid"]}
    before = len(w.queries)
    shown, last = h.get()
    assert last["error"] == "unknown_column" and len(w.queries) - before == 1   # not read again in full
    assert h.health()["last_codes"] == ["delta_failed_unknown_column"] and shown is held


def test_a_refresh_monday_rate_limits_is_its_own_error_and_never_followed_by_a_full_reading():
    """analiza-metrics' hostile review 11.11: a refresh monday answered "rate limited" fell back to a full reading - some
    thirty requests sent into the very limit monday had just named."""
    w = world()
    h = holder(w)
    held, _ = h.get()
    w.window_error = RATE_LIMITED
    before = len(w.queries)
    shown, last = h.get()
    assert last["error"] == "rate_limited" and len(w.queries) - before == 2      # the refresh and its one retry
    assert all("query_params" in q for q, _v in w.queries[before:]) and shown is held   # not one item read after them
    assert h.health()["last_codes"] == ["delta_failed_rate_limited"]


def test_a_rate_limit_whose_wait_does_not_fit_the_refresh_is_still_the_limits():
    """New here: the refresh has DELTA_DEADLINE_S, and a reset longer than what is left of it makes the reader's own
    error `deadline` ("no time left to retry"), which alone would fall back to a full reading - into the limit."""
    w = world()
    h = holder(w)
    held, _ = h.get()
    w.fail = [httpx.Response(429, headers={"Retry-After": "30"})]
    before = len(w.queries)
    shown, last = h.get()
    assert last["error"] == "rate_limited" and len(w.queries) - before == 1 and shown is held
    assert h.health()["last_codes"] == ["delta_failed_rate_limited"]


def test_a_refresh_that_keeps_failing_reads_whole_at_most_once_per_gap():
    """analiza-metrics' hostile review 11.11: a refresh monday refuses at the pinned API version (the case the fallback
    exists for) must not make every press a full reading. One that falls back again within FALLBACK_GAP is its own error,
    so a minute of presses every 5 seconds costs at most two full readings. A press (`force`) is held back by no gap
    after a failure: the route's REFRESH_GAP_S is its only limit."""
    w = world()
    clock = {"now": datetime.now(timezone.utc)}
    h = holder(w, now=lambda: clock["now"])
    h.get()                                                             # the first reading, whole
    w.window_error = API_VERSION
    kinds, minute, whole = [], 0, []
    for _ in range(12):                                                 # a press every 5 seconds for a minute
        clock["now"] += timedelta(seconds=5)
        before = len(w.queries)
        _shown, last = h.get(force=True)
        if last["error"] is None:
            kinds.append(h.reading["kind"])
            whole.append(len(w.queries) - before)
        else:
            assert last["error"] == "graphql" and len(w.queries) - before == 1   # the refresh alone, never whole
            assert h.health()["last_codes"] == ["delta_failed_graphql", "full_deferred"]
            kinds.append("deferred")
        minute += len(w.queries) - before
    assert kinds == ["full"] + ["deferred"] * 5 + ["full"] + ["deferred"] * 5
    assert minute == sum(whole) + 10                                    # two full readings and ten requests
    assert "full_delta_error_graphql" in [c["code"] for c in h.health()["recent_codes"]]
    w.window_error = None                                               # monday answers the refresh again
    assert h.get(force=True)[0]["kind"] == "delta"
    # a board whose ids keep disagreeing with its count is held to the same gap
    w.count_off[SC] = 1
    clock["now"] += rd.FALLBACK_GAP
    assert h.get(force=True)[0]["kind"] == "full"
    clock["now"] += timedelta(seconds=5)
    _shown, last = h.get(force=True)
    assert last["error"] == "count_mismatch"
    clock["now"] += rd.FALLBACK_GAP
    assert h.get(force=True)[0]["kind"] == "full"
    # and once they agree again, a refresh: charges that left are dropped by the ids, never read whole for it
    w.count_off[SC] = 0
    w.leave(102)
    w.leave(103)
    clock["now"] += timedelta(seconds=5)
    kept = h.get(force=True)[0]
    assert kept["kind"] == "delta" and [r["id"] for r in kept["student_charge"]["rows"]] == ["101"]


# --- 6. /health ---------------------------------------------------------------------------------------------------------

def test_health_says_how_the_desk_read_and_kept_its_reading_and_never_a_row(stored, monkeypatch):
    leaky(stored.w)
    stored.get(session_token(1))
    stored.w.set(102, status=status("שולם", 1), paid="1200")
    stored.client.post("/api/desk/refresh", headers=auth())
    h = stored.client.get("/health").json()["desk"]
    last, r = h["last_reading"], h["refresh"]
    assert last["kind"] == "delta" and last["calls"] == 4 and last["seconds"] is not None and last["error"] is None
    assert r["last_full"]["calls"] == 10 and r["last_delta"]["calls"] == 4 and r["last_delta"]["days"] == 2
    assert r["last_delta"]["id_pages"] == 2                             # of them, the charge boards' ids
    assert r["held"]["read_at"] == r["stored_read_at"] == r["last_delta"]["at"]
    assert r["held"]["full_read_at"] == r["last_full"]["at"] and r["full_after_hours"] == 36
    assert r["last_codes"] == ["delta", "store_saved"] and "stored" not in h   # no DATABASE_URL: nothing to glance at
    text = json.dumps(h, ensure_ascii=False)
    no_secret_in(text)
    assert not any(word in text for word in ("תלמידה-אלף", "ליד-אלף", "1200", "4000", "חברה-משלמת"))
    assert not re.search(r'"(rows|asked|strangers|missing|delta_rows)"', text)  # counts of rows are the members' only


def test_health_says_the_stored_readings_age_from_the_database(stored, monkeypatch):
    """With a database, the desk block says when the stored reading was taken and how old it is (read with the store's
    other glances, at most every STORE_TTL_S): its times and kind, never its body."""
    from tests.conftest import Clock

    monkeypatch.setenv("DATABASE_URL", "postgresql://user:SECRETPW@127.0.0.1:1/nothing")
    clock = Clock(1_800_000_000.0)
    main.app.state.clock = clock
    taken = datetime.fromtimestamp(clock.t - 125, tz=timezone.utc).isoformat(timespec="seconds")
    monkeypatch.setattr(main, "_read_store", lambda cfg: {
        "tables": {"desk_readings": "present"}, "missing_columns": [], "migrations": [], "times": None,
        "desk": {"read_at": taken, "full_read_at": taken, "kind": "delta", "complete": True, "saved_at": taken}})
    main._store_cache.update(at=0.0, key=None, value=None)
    try:
        h = stored.client.get("/health").json()
        assert h["desk"]["stored"] == {"read_at": taken, "full_read_at": taken, "kind": "delta", "complete": True,
                                       "saved_at": taken, "age_s": 125}
        assert "desk_glance" not in h["store"]
    finally:
        main.app.state.clock = __import__("time").time
        main._store_cache.update(at=0.0, key=None, value=None)


# --- 7. the migration and the store in Postgres -------------------------------------------------------------------------

def _sql(url, statement, params=None):
    import psycopg

    with psycopg.connect(url, autocommit=True) as conn:
        cur = conn.execute(statement, params)
        return cur.fetchall() if cur.description else None


@pytest.fixture
def pg_url():
    from app.migrate import apply_migrations

    apply_migrations(TEST_DATABASE_URL)
    _sql(TEST_DATABASE_URL, "DELETE FROM desk_readings")
    yield TEST_DATABASE_URL
    _sql(TEST_DATABASE_URL, "DELETE FROM desk_readings")


@needs_pg
def test_the_stored_reading_round_trips_through_postgres_without_a_never_read_column(pg_url):
    w = world()
    leaky(w)
    w.set(102, invoice="INV\x00-7", touch=False)        # jsonb refuses a NUL: the reader drops it, the save never meets it
    store = ds.DeskStore(pg_url)
    held = holder(w, store)
    kept, _ = held.get()
    assert held.health()["last_codes"][-1] == "store_saved"
    assert [r[rd.SC["invoice"]] for r in kept["student_charge"]["rows"] if r["id"] == "102"] == ["INV-7"]
    body, checks = _sql(pg_url, "SELECT body::text, checks::text FROM desk_readings WHERE name = 'desk'")[0]
    no_secret_in(body + checks)
    back = store.load()
    assert back.pop("body_schema") == rd.BODY_SCHEMA
    assert rd.body_of(back) == rd.body_of(kept) and back["checks"] == kept["checks"] and desk_of(back) == desk_of(kept)
    assert back["read_at"] == kept["read_at"] and back["full_read_at"] == kept["full_read_at"]  # to the microsecond
    # a refresh laid over the stored reading by an instance that woke from zero, and kept in its place
    w.set(101, paid="4100")
    cold = rd.HeldReading(lambda: reader_of(w), store=store)
    shown, _last, refreshing = cold.answer(stale_ok=True)
    assert refreshing is True and shown["read_at"] == kept["read_at"]
    fresh, _ = cold.get()
    assert fresh["kind"] == "delta" and cold.health()["last_codes"] == ["delta", "store_saved"]
    assert store.load()["kind"] == "delta" and store.load()["read_at"] == fresh["read_at"]
    # never over a newer reading: another instance's older one is not written
    older = dict(fresh, read_at=(rd.instant(fresh["read_at"]) - timedelta(minutes=1)).isoformat())
    assert store.save(older) is False and store.load()["read_at"] == fresh["read_at"]
    with pytest.raises(rd.UnsafeBody):
        store.save(dict(fresh, checks={"x": {rd.LD["id_number"]: 1}}))
    with pytest.raises(ValueError):
        store.save(dict(fresh, error="auth"))                           # a failed reading is never kept


@needs_pg
def test_the_glance_says_times_only_and_the_table_is_checked_by_name(pg_url):
    from app.migrate import schema_state

    import psycopg

    w = world()
    store = ds.DeskStore(pg_url)
    kept, _ = holder(w, store).get()
    with psycopg.connect(pg_url) as conn, conn.cursor() as cur:
        g = ds.glance(cur)
        state = schema_state(cur)
    assert set(g) == {"read_at", "full_read_at", "kind", "complete", "saved_at"} and g["kind"] == "full"
    assert g["read_at"] == rd.instant(kept["read_at"]).isoformat(timespec="seconds") and g["complete"] is True
    assert state["tables"]["desk_readings"] == "present" and state["missing_columns"] == []


@needs_pg
def test_a_missing_table_is_said_and_the_desk_reads_monday(pg_url):
    """The deployment never creates its schema (CLAUDE.md rule 8): before the Shell step and the Republish, every load and
    save fails with the table's SQLSTATE, and the desk reads monday as D1 did."""
    _sql(pg_url, "DROP TABLE desk_readings")
    try:
        w = world()
        held = rd.HeldReading(lambda: reader_of(w), store=ds.DeskStore(pg_url))
        shown, last, _refreshing = held.answer(stale_ok=True)
        assert shown is not None and last["error"] is None
        assert held.health()["last_codes"] == ["store_load_failed_42P01", "full_first", "store_save_failed_42P01"]
    finally:
        from app.migrate import apply_migrations

        apply_migrations(pg_url)


@needs_pg
def test_the_migration_runs_twice_and_a_table_missing_behind_its_ledger_row_is_repaired(pg_url):
    from app.migrate import MIGRATIONS_DIR, apply_migrations, schema_state

    import psycopg

    sql = (MIGRATIONS_DIR / "006_desk_readings.sql").read_text(encoding="utf-8")
    _sql(pg_url, sql)
    _sql(pg_url, sql)                                                   # re-runnable
    _sql(pg_url, "DROP TABLE desk_readings")
    assert _sql(pg_url, "SELECT count(*) FROM schema_migrations WHERE filename = '006_desk_readings.sql'")[0][0] == 1
    result = apply_migrations(pg_url)
    assert "006_desk_readings.sql" in result["skipped"] and result["repaired"] and result["missing"] == []
    with psycopg.connect(pg_url) as conn, conn.cursor() as cur:
        assert schema_state(cur)["tables"]["desk_readings"] == "present"


def test_the_store_and_the_migration_name_the_same_columns_and_the_migration_only_creates():
    from app.migrate import MIGRATIONS_DIR, checked_tables

    sql = (MIGRATIONS_DIR / "006_desk_readings.sql").read_text(encoding="utf-8")
    body = sql[sql.index("CREATE TABLE IF NOT EXISTS desk_readings"):]
    for col in ds.DESK_TABLES["desk_readings"]:
        assert "\n  %s " % col in body, col
    statements = [s for s in re.sub(r"--[^\n]*", "", sql).split(";") if s.strip()]
    assert len(statements) == 1 and statements[0].strip().startswith("CREATE TABLE IF NOT EXISTS desk_readings")
    assert not re.search(r"\b(DROP|ALTER|DELETE|UPDATE|INSERT|TRUNCATE)\b", re.sub(r"--[^\n]*", "", sql).upper())
    assert checked_tables()["desk_readings"] == ds.DESK_TABLES["desk_readings"]


# --- 8. the leads: the deciding ones by id, never a window ---------------------------------------------------------------
# world(): 101 -> 201 (cohort 401) and lead 301, so 301 decides nothing; 102 -> 202 (no cohort) and 302 (cohort 402);
# 103 -> no enrollment, 303 (no cohort). The deciding leads are 302 and 303.

def lead_ids_read(w: World, since: int) -> list:
    return sorted(i for b, ids in w.by_id_reads()[since:] if b == rd.LEADS_BOARD for i in ids)


def test_a_refresh_never_asks_leads_for_a_window_and_a_quiet_one_is_three_requests():
    w = world()
    h = holder(w)
    h.get()
    w.touch(301)                                                        # a lead edited lately, deciding nothing
    before, reads = len(w.queries), len(w.by_id_reads())
    kept, _ = h.get()
    # one request: the six boards' windows, Leads' columns, and the deciding leads by id - 301 is never read; and the two
    # charge boards' ids, a page each (D2's review)
    assert kept["kind"] == "delta" and len(w.queries) - before == 3 and h.health()["last_delta"]["calls"] == 3
    assert h.health()["last_delta"]["id_pages"] == 2 and len(w.queries_of("query_params")) == 1
    assert lead_ids_read(w, reads) == ["302", "303"] and rd.LEADS_BOARD not in w.windowed
    assert sorted(w.windowed[-6:]) == sorted(rd.BOARDS[k] for k in rd.DELTA_KEYS)
    q = monday.window_query(6, 1, 1, 2)
    assert q.count("query_params") == 6 and "l0: items(ids: $li0, limit: $ln0)" in q and "l1: items" not in q
    assert "board { id }" in q                                          # every row says which board answered it


def test_deciding_leads_beyond_a_chunk_are_read_in_the_same_request_an_alias_per_chunk(monkeypatch):
    monkeypatch.setattr(monday, "ITEMS_BY_ID_CHUNK", 1)
    w = world()
    h = holder(w)
    h.get()
    w.set(302, cohort=rel(401))                                         # a cohort the reading holds: nothing more to read
    before, reads = len(w.queries), len(w.by_id_reads())
    kept, _ = h.get()
    assert len(w.queries) - before == 3                                 # the windows and the deciding leads; the ids
    assert [ids for b, ids in w.by_id_reads()[reads:] if b == rd.LEADS_BOARD] == [["302"], ["303"]]
    assert view_row(kept, 102)["cohort_code"] == "C1" and desk_of(kept) == desk_of(rd.read_desk(reader_of(w)))


def test_a_reading_keeps_the_deciding_leads_rows_and_every_linked_leads_name():
    w = world()
    store = MemoryStore()
    h = holder(w, store)
    full, _ = h.get()
    body = json.loads(store.row["body"])
    for kept in (full, body):
        assert [x["id"] for x in kept["leads"]] == ["302", "303"] and kept["asked"]["leads"] == ["302", "303"]
        assert kept["lead_names"] == {"301": "ליד-אלף", "302": "ליד-בית", "303": "ליד-גימל"}
        assert kept["lead_reps"] == {"301": "נציגה-אחת", "302": "", "303": "נציג-שני"}
    v = fd.build_view(full, TODAY)
    assert [next(x for x in v["b2c"]["rows"] if x["id"] == i)["lead_names"] for i in ("101", "102", "103")] == [
        ["ליד-אלף"], ["ליד-בית"], ["ליד-גימל"]]
    assert (v["checks"]["leads_linked"], v["checks"]["leads_found"]) == (3, 3) and "leads_missing" not in v["data_notes"]
    w.set(102, paid="1200")
    delta, _ = h.get()
    assert delta["kind"] == "delta" and [x["id"] for x in delta["leads"]] == ["302", "303"]
    assert delta["lead_names"] == full["lead_names"] and delta["lead_reps"] == full["lead_reps"]
    assert desk_of(delta) == desk_of(rd.read_desk(reader_of(w)))
    # the leads' counts as a full reading gives them, every linked lead by its name; which are archived it cannot know
    assert delta["checks"]["leads"] == {"board_id": rd.LEADS_BOARD, "asked": 3, "rows": 3, "missing": 0, "strangers": 0,
                                        "archived": None, "short_rows": 0, "ok": True}


def test_a_lead_handed_to_another_rep_shows_at_the_next_refresh_when_it_decides_and_at_the_next_full_reading_else():
    w = world()
    h = holder(w)
    h.get()
    w.set(302, rep="נועה", touch=False)                                  # 302 decides 102's cohort
    w.set(301, rep="גל", touch=False)                                    # 301 decides nothing
    kept, _ = h.get()
    assert kept["kind"] == "delta" and view_row(kept, 102)["rep"] == "נועה" and view_row(kept, 101)["rep"] == "נציגה-אחת"
    assert view_row(rd.read_desk(reader_of(w)), 101)["rep"] == "גל"


def test_a_payment_newly_linked_to_a_lead_gets_its_name_and_rep_in_the_refresh_that_reads_it():
    w = world()
    h = holder(w)
    h.get()
    w.add(rd.LEADS_BOARD, 305, "ליד-חדש", cohort=rel(402), rep="סבטלנה")
    w.set(101, lead=rel(301, 305))                                       # 101's enrollment decides: 305 by name only
    reads = len(w.by_id_reads())
    kept, _ = h.get()
    assert lead_ids_read(w, reads) == ["302", "303", "305"] and kept["asked"]["leads"] == ["302", "303"]
    assert view_row(kept, 101)["lead_names"] == ["ליד-אלף", "ליד-חדש"] and kept["lead_reps"]["305"] == "סבטלנה"
    assert desk_of(kept) == desk_of(rd.read_desk(reader_of(w)))


def test_a_deciding_leads_own_cohort_change_and_rename_show_at_the_next_refresh_whatever_its_time():
    w = world()
    h = holder(w)
    h.get()
    w.set(303, cohort=rel(401), touch=False)                             # 303 decides 103's cohort: none, now 401
    w.set(302, name="שם חדש", touch=False)
    kept, _ = h.get()
    assert kept["kind"] == "delta" and view_row(kept, 103)["cohort_code"] == "C1"
    assert view_row(kept, 102)["lead_names"] == ["שם חדש"] and kept["asked"]["cohorts"] == ["401", "402"]
    assert desk_of(kept) == desk_of(rd.read_desk(reader_of(w)))


def test_a_deciding_lead_monday_no_longer_returns_is_missing_at_the_next_refresh():
    w = world()
    h = holder(w)
    h.get()
    w.items["303"]["state"] = "deleted"
    kept, _ = h.get()
    v = fd.build_view(kept, TODAY)
    assert kept["lead_names"]["303"] is None and v["data_notes"]["leads_missing"] == 1
    assert kept["checks"]["leads"]["missing"] == 1 and kept["complete"] is True
    assert desk_of(kept) == desk_of(rd.read_desk(reader_of(w)))


@pytest.mark.parametrize("how", ["unlinked from its enrollment", "its enrollment lost its cohort"])
def test_a_lead_that_becomes_deciding_is_read_by_id_and_places_its_payment(how):
    w = world()
    w.set(301, cohort=rel(402), touch=False)                             # so 301 has a cohort to place 101 by
    h = holder(w)
    h.get()
    if how == "unlinked from its enrollment":
        w.set(101, enrollment=rel())
    else:
        w.set(201, cohort=rel())
    before, reads = len(w.queries), len(w.by_id_reads())
    kept, _ = h.get()
    # the windows with 302 and 303; the charge boards' ids; the changed row; 301, which decides now (its cohort 402 is
    # held already)
    assert kept["kind"] == "delta" and len(w.queries) - before == 5
    assert lead_ids_read(w, reads) == ["301", "302", "303"] and kept["asked"]["leads"] == ["301", "302", "303"]
    assert view_row(kept, 101)["cohort_code"] == "C2" and desk_of(kept) == desk_of(rd.read_desk(reader_of(w)))


def test_a_lead_that_stops_deciding_leaves_the_rows_and_keeps_its_name():
    w = world()
    h = holder(w)
    h.get()
    w.set(202, cohort=rel(401))                                         # 102's enrollment names a cohort now
    kept, _ = h.get()
    assert [x["id"] for x in kept["leads"]] == ["303"] and kept["asked"]["leads"] == ["303"]
    assert kept["lead_names"]["302"] == "ליד-בית" and desk_of(kept) == desk_of(rd.read_desk(reader_of(w)))
    reads = len(w.by_id_reads())
    h.get()                                                             # and the next refresh reads 303 alone
    assert lead_ids_read(w, reads) == ["303"]


def test_a_rename_of_a_lead_that_decides_nothing_shows_at_the_next_full_reading():
    """The one thing a refresh does not see: a lead deciding nothing is kept by name, and its rename waits for the next
    full reading - at most FULL_AFTER, as a deleted enrollment does. Its cohort never mattered."""
    w = world()
    clock = {"now": datetime.now(timezone.utc)}
    h = holder(w, now=lambda: clock["now"])
    h.get()
    w.set(301, name="שם מתוקן", cohort=rel(402))
    kept, _ = h.get()
    assert kept["kind"] == "delta" and kept["lead_names"]["301"] == "ליד-אלף" and view_row(kept, 101)["cohort_code"] == "C1"
    clock["now"] += rd.FULL_AFTER + timedelta(minutes=1)
    kept, _ = h.get()
    assert kept["kind"] == "full" and view_row(kept, 101)["lead_names"] == ["שם מתוקן"]


def test_a_renamed_leads_column_still_ends_a_refresh_instead_of_reading_as_empty():
    w = world()
    h = holder(w)
    held, _ = h.get()
    w.columns_missing[rd.LEADS_BOARD] = {rd.LD["cohort"]}
    before = len(w.queries)
    shown, last = h.get()
    assert last["error"] == "unknown_column" and len(w.queries) - before == 1 and shown is held
    assert h.health()["last_codes"] == ["delta_failed_unknown_column"]


def test_a_reading_that_cannot_be_laid_under_a_refresh_is_read_whole_once():
    w = world()
    store = MemoryStore()
    rd.HeldReading(lambda: reader_of(w), store=store).get()
    kept = store.load()
    kept.pop("body_schema")
    assert rd.usable(kept) and not rd.usable({k: v for k, v in kept.items() if k != "lead_names"})
    assert not rd.usable(dict(kept, lead_names=None)) and not rd.usable(dict(kept, lead_names={"301": 7}))
    assert not rd.usable({k: v for k, v in kept.items() if k != "lead_reps"}) and not rd.usable(dict(kept, lead_reps=None))
    assert not rd.usable(dict(kept, asked={})) and not rd.usable(dict(kept, read_at="soon"))
    body = json.loads(store.row["body"])
    del body["lead_reps"]
    store.row["body"] = json.dumps(body)                                # as an older deploy of the same shape kept it
    h = rd.HeldReading(lambda: reader_of(w), store=store)
    h.get()
    assert h.health()["last_codes"][:2] == ["store_unusable", "full_first"]
    assert holder(w, store).get()[0]["kind"] == "delta"


def test_a_refresh_whose_only_change_is_a_leads_name_is_kept(monkeypatch):
    """Belt and braces: today a name never changes without a row or a link changing with it, but the store keeps the
    content, so a refresh that changed one name and nothing else is written too."""
    w = world()
    store = MemoryStore()
    h = holder(w, store)
    h.get()
    real = rd.read_delta

    def renamed(reader, base):
        out = real(reader, base)
        out["lead_names"] = dict(out["lead_names"], **{"301": "שם אחר"})
        return out

    monkeypatch.setattr(rd, "read_delta", renamed)
    h.get()
    assert store.writes == 2 and json.loads(store.row["body"])["lead_names"]["301"] == "שם אחר"


# --- this service's rules on the refresh (D1's reviews) -----------------------------------------------------------------

def test_a_row_a_refresh_reads_answered_from_another_board_is_a_stranger_dropped_and_counted():
    """By id monday answers an item of any board (review 22.11). What a refresh reads again - a deciding lead, a charge its
    window listed - is the board's only when it answers from the board; a linked item it does not read again stays as held,
    like a deleted one, until the next full reading."""
    w = world()
    reader, held = held_full(w)
    w.items["302"]["board"] = OTHER_BOARD                               # a deciding lead answered from another board now
    got = rd.read_delta(reader, held)
    assert got["checks"]["leads"]["strangers"] == 1 and got["lead_names"]["302"] is None
    assert "302" not in [x["id"] for x in got["leads"]] and desk_of(got) == desk_of(rd.read_desk(reader))
    # a charge the window listed that its board's ids no longer give (moved away): dropped, never read
    w2 = world()
    reader2, held2 = held_full(w2)
    w2.items["102"]["board"] = OTHER_BOARD                               # it moved after the window listed it
    w2.order[SC].remove("102")
    listed = w2.window
    w2.window = lambda board, days: listed(board, days) + (
        [{"id": "102", "updated_at": datetime.now(timezone.utc).isoformat()}] if board == SC else [])
    reads = len(w2.by_id_reads())
    got2 = rd.read_delta(reader2, held2)
    assert [r["id"] for r in got2["student_charge"]["rows"]] == ["101", "103"] and got2["complete"] is True
    assert "102" not in [i for _b, ids in w2.by_id_reads()[reads:] for i in ids]
    # one its board's ids still give, answered by id from another board (moved after its page): dropped, and the board
    # cannot be reconciled, so the next reading is whole
    w2.order[SC].insert(1, "102")
    with pytest.raises(rd.DeltaMismatch) as err:
        rd.read_delta(reader2, held2)
    assert err.value.key == "student_charge"


def test_a_refresh_never_sends_a_request_after_its_deadline():
    w = world()
    reader, held = held_full(w)
    t = [0.0]
    reader.clock = lambda: t[0]
    reader.deadline = 0.0
    before = len(w.queries)
    with pytest.raises(MondayError) as err:
        rd.read_delta(reader, held)
    assert err.value.code == "deadline" and len(w.queries) == before


def test_the_held_reading_gives_a_refresh_its_own_shorter_deadline_and_a_fallback_the_whole_one():
    w = world()
    made = []

    def make():
        reader = reader_of(w)
        made.append(reader)
        return reader

    h = rd.HeldReading(make, ttl_s=0, deadline_s=50, delta_deadline_s=7)
    h.get()
    assert 49 < made[-1].deadline - made[-1].clock() <= 50              # the first reading, whole
    seen = []
    real = rd.read_delta

    def spy(reader, base):
        seen.append(reader.deadline - reader.clock())
        return real(reader, base)

    import unittest.mock as mock

    with mock.patch.object(rd, "read_delta", spy):
        h.get()
    assert 6 < seen[0] <= 7


def test_a_never_read_column_is_refused_before_the_refresh_sends_anything():
    w = world()
    reader = reader_of(w)
    for col in sorted(NEVER_READ):
        with pytest.raises(MondayError) as err:
            reader.changed_items([(SC, [rd.SC["paid"]])], [], 2, (["302"], [rd.LD["cohort"], col]))
        assert err.value.code == "never_read"
        with pytest.raises(MondayError) as err:
            reader.read_items([(["101"], [rd.SC["paid"]]), (["302"], [col])], pack=True)
        assert err.value.code == "never_read"
    assert w.queries == []


def test_packed_reads_keep_the_plain_shape_for_one_piece_and_the_board_of_every_row():
    w = world()
    reader = reader_of(w)
    one = reader.read_items([(["101"], [rd.SC["paid"]])], pack=True)
    assert [r["id"] for r in one[0]] == ["101"] and w.queries[-1][0] == monday.ITEMS_BY_ID
    two = reader.read_items([(["101"], [rd.SC["paid"]]), (["601"], [rd.DL["payer"]]), ([], [rd.CO["code"]])], pack=True)
    assert [[r["id"] for r in rows] for rows in two] == [["101"], ["601"], []]
    assert len(w.queries) == 2 and "j1: items" in w.queries[-1][0] and "j2" not in w.queries[-1][0]
    assert two[1][0]["board_id"] == rd.DEALS_BOARD and rd.DL["payer"] in two[1][0]


# --- 9. a refresh's desk is a full reading's, through random edits ------------------------------------------------------

def random_world(rng: random.Random) -> World:
    """Payments whose cohort comes from the enrollment, from the lead, from neither; leads in no cohort, one or two."""
    w = World()
    for i in range(1, 6):
        w.add(rd.COHORTS_BOARD, 400 + i, "מחזור-%d" % i, code="C%d" % i)
    for i in range(1, 7):
        w.add(rd.ENROLLMENTS_BOARD, 200 + i, "הרשמה-%d" % i, cohort=rel(*rng.choice([[], [400 + rng.randint(1, 5)]])))
    for i in range(1, 9):
        w.add(rd.LEADS_BOARD, 300 + i, "ליד-%d" % i, cohort=rel(*rng.sample(range(401, 406), rng.choice([0, 1, 1, 2]))),
              rep=rng.choice(["", "נועה", "גל, נועה"]))
    for i in range(1, 9):
        idx = rng.choice(sorted(fd.PAY))
        w.add(SC, 100 + i, "תלמיד-%d" % i, status=status(fd.PAY[idx], int(idx)), paid=str(rng.randint(1, 40) * 100),
              enrollment=rel(*rng.sample(range(201, 207), rng.choice([0, 1, 1, 2]))),
              lead=rel(*rng.sample(range(301, 309), rng.choice([0, 1, 1, 2]))))
    for i in range(1, 4):
        w.add(B2, 500 + i, "חיוב-%d" % i, amount="1000", deal=rel(600 + i % 2 + 1), account=rel(701),
              billing=status("חויב", 6), payment=status("שולם", 1))
    w.add(rd.DEALS_BOARD, 601, "עסקה-1", payer=rel(702))
    w.add(rd.DEALS_BOARD, 602, "עסקה-2", payer=rel(703))
    for a in (701, 702, 703):
        w.add(rd.ACCOUNTS_BOARD, a, "חברה-%d" % a)
    return w


def random_edits(w: World, rng: random.Random, held: dict) -> list:
    """The edits a round may make, each as monday makes it (an edited item's time moves; a lead's too, though Leads has
    no window). Since D2's review a charge also leaves its board unseen (archived, deleted, moved to another board) and
    comes back unseen (restored, moved back), its time untouched: the boards' ids reconcile both. Never: a linked item
    deleted that no refresh reads, an archived item edited, or a rename of a lead deciding nothing - the things a refresh
    leaves to the next full reading."""

    def active(board):
        return sorted(int(i) for i, x in w.items.items() if x["board"] == board and x["state"] == "active")

    def fresh(board, **cells):
        # an id monday never held: ids only grow (an archived item is never written over)
        i = 1000 + len(w.items)
        assert str(i) not in w.items
        w.add(board, i, "חדש-%d" % i, **cells)
        w.touch(i)
        return i

    def one(board):
        ids = active(board)
        return rng.choice(ids) if ids else None

    def cohort_or_none():
        c = one(rd.COHORTS_BOARD)
        return rng.choice([[], [c]]) if c else []

    def some(board):
        r = rng.random()
        if r < 0.3:
            return []
        if r < 0.75:
            return rng.sample(active(board), min(len(active(board)), rng.choice([1, 1, 2])))
        return [fresh(board, cohort=rel(*cohort_or_none()))]

    def edit_link(key, board):
        s = one(SC)
        w.set(s, **{key: rel(*some(board))})

    def charge_amount():
        idx = rng.choice(sorted(fd.PAY))
        w.set(one(SC), status=status(fd.PAY[idx], int(idx)), paid=str(rng.randint(1, 40) * 100))

    def new_charge():
        fresh(SC, status=status("שולם", 1), paid="900", enrollment=rel(*some(rd.ENROLLMENTS_BOARD)),
              lead=rel(*some(rd.LEADS_BOARD)))

    def enrollment_cohort():
        e = one(rd.ENROLLMENTS_BOARD)
        if e:
            w.set(e, cohort=rel(*cohort_or_none()))

    def lead_cohort():
        x = one(rd.LEADS_BOARD)
        if x:
            cohorts = active(rd.COHORTS_BOARD)
            w.set(x, cohort=rel(*rng.sample(cohorts, min(len(cohorts), rng.choice([0, 1, 1, 2])))))

    def deciding_lead_renamed_or_deleted():
        deciding = [int(x) for x in held["asked"]["leads"] if w.items[str(x)]["state"] == "active"]
        if deciding:
            x = rng.choice(deciding)
            if rng.random() < 0.8:
                w.set(x, name="ליד %d" % rng.randint(1, 10 ** 6), touch=False)
            else:
                w.items[str(x)]["state"] = rng.choice(["deleted", "gone"])

    def cohort_code():
        c = one(rd.COHORTS_BOARD)
        if c:
            w.set(c, code="B%d" % rng.randint(100, 999))

    def new_cohort_named():
        board = rng.choice([rd.ENROLLMENTS_BOARD, rd.LEADS_BOARD])
        x = one(board)
        if x:
            w.set(x, cohort=rel(fresh(rd.COHORTS_BOARD, code="N%d" % rng.randint(100, 999))))

    def linked_item_archived():
        board = rng.choice([rd.ENROLLMENTS_BOARD, rd.LEADS_BOARD, rd.COHORTS_BOARD, rd.DEALS_BOARD, rd.ACCOUNTS_BOARD])
        if len(active(board)) > 1:
            w.items[str(rng.choice(active(board)))]["state"] = "archived"

    def company():
        b, d, a = one(B2), one(rd.DEALS_BOARD), one(rd.ACCOUNTS_BOARD)
        w.set(b, amount=str(rng.randint(1, 50) * 100))
        if d and a:
            w.set(d, payer=rel(a))

    def charge_left():
        board = rng.choice([SC, B2])
        if len(active(board)) > 1:
            i = str(rng.choice(active(board)))
            how = rng.choice(["archived", "deleted", "moved"])
            if how == "moved":                                          # monday's "move to board"
                w.items[i]["home"] = board
                w.items[i]["board"] = OTHER_BOARD
                w.order[board].remove(i)
            else:
                w.leave(i, how)

    def charge_came():
        back = sorted(i for i, x in w.items.items()
                      if x.get("home") or (x["state"] == "archived" and x["board"] in (SC, B2)))
        if back:
            i = rng.choice(back)
            x = w.items[i]
            x["board"], x["state"] = x.pop("home", None) or x["board"], "active"   # restored, or moved back
            w.order[x["board"]].append(i)

    return [lambda: edit_link("enrollment", rd.ENROLLMENTS_BOARD), lambda: edit_link("lead", rd.LEADS_BOARD),
            charge_amount, new_charge, enrollment_cohort, lead_cohort, deciding_lead_renamed_or_deleted, cohort_code,
            new_cohort_named, linked_item_archived, company, charge_left, charge_came]


def test_through_random_edits_every_refresh_holds_the_desk_and_the_leads_a_full_reading_holds(monkeypatch):
    """analiza-metrics' equivalence (160 rounds, 12.11), on this service's reader: after every round of edits the
    refresh's desk is a full reading's, its deciding leads and their rows are a full reading's, and so is every linked
    lead's name - while the refresh never asks Leads for a window and reads by id only the deciding leads, the leads that
    may decide, and the leads a payment newly links to. No margin, so each refresh reads its own round's edits only."""
    monkeypatch.setattr(rd, "DELTA_MARGIN", timedelta(0))
    rng = random.Random(20261124)
    w = random_world(rng)
    store = MemoryStore()
    h = holder(w, store)
    h.get()
    seen = {"added": 0, "removed": 0, "changed": 0, "named": 0, "rounds": 0, "charge_left": 0, "charge_back": 0}
    ever: set = set()
    for _ in range(160):
        held = h.reading
        for edit in rng.sample(random_edits(w, rng, held), rng.choice([1, 1, 2, 3])):
            edit()
        reads = len(w.by_id_reads())
        kept, last = h.get()
        assert last["error"] is None and kept["kind"] == "delta", h.health()["last_codes"]
        read_now = set(lead_ids_read(w, reads))
        full = rd.read_desk(reader_of(w))
        assert desk_of(kept) == desk_of(full)
        assert kept["asked"] == full["asked"]
        assert {x["id"]: x for x in kept["leads"]} == {x["id"]: x for x in full["leads"]}
        assert kept["lead_names"] == full["lead_names"] and kept["lead_reps"] == full["lead_reps"]
        assert kept["complete"] is True and {k: c["ok"] for k, c in kept["checks"].items()} == {
            k: c["ok"] for k, c in full["checks"].items()}
        # what the refresh read of Leads: the deciding leads it held and holds, the leads of a payment that links an
        # enrollment it never held (they may decide), and the leads newly linked - nothing else
        new_enr = {r["id"] for r in kept["student_charge"]["rows"]
                   if set(r.get(rd.SC["enrollment"]) or []) - set(held["asked"]["enrollments"])}
        allowed = (set(held["asked"]["leads"]) | set(kept["asked"]["leads"])
                   | (set(kept["lead_names"]) - set(held["lead_names"]))
                   | {x for r in kept["student_charge"]["rows"] if r["id"] in new_enr for x in r.get(rd.SC["lead"]) or []})
        assert read_now <= allowed, read_now - allowed
        before, after = {x["id"] for x in held["leads"]}, {x["id"] for x in kept["leads"]}
        seen["added"] += bool(after - before)
        seen["removed"] += bool(before - after)
        seen["changed"] += any(x != y for x in held["leads"] for y in kept["leads"] if x["id"] == y["id"])
        seen["named"] += bool(set(kept["lead_names"]) - set(held["lead_names"]))
        seen["rounds"] += 1
        # charges that left their board unseen, and charges that came back to it unseen (never by a full reading)
        held_ids = {r["id"] for k in rd.CHARGE_KEYS for r in held[k]["rows"]}
        kept_ids = {r["id"] for k in rd.CHARGE_KEYS for r in kept[k]["rows"]}
        seen["charge_left"] += bool(held_ids - kept_ids)
        seen["charge_back"] += bool((kept_ids - held_ids) & ever)
        ever |= held_ids
    assert rd.LEADS_BOARD not in w.windowed
    # the rounds moved the lead set every way, not only its rows' cells
    assert all(n >= 5 for n in seen.values()), seen
    # and the store holds the warm instance's desk: an instance that woke from zero builds the same one
    assert desk_of(store.load()) == desk_of(h.reading)
