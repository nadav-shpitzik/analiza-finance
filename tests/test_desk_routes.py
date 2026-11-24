"""The finance desk's routes (app/desk/routes.py, Analiza-Monday backlog 0301 D2): GET /api/desk with the desk itself,
POST /api/desk/refresh, GET /api/desk/totals - analiza-metrics tests/test_finance.py at `da671b2`, its route tests ported
onto the new gate and reading - and the held reading's forced path that the refresh press reads with.

The monday is tests/test_desk.py's: user 1 on both charge boards, 2 on Student Charge only (Inna's case, ACC-34), 3 on
B2B Charge only, 9 on neither."""
from __future__ import annotations

import logging
import threading
from urllib.parse import quote

import httpx
import pytest

from app import config, main
from app.desk import reading as rd
from app.desk import routes as dr
from app.desk import view as fd
from tests.desk_fixtures import TODAY, b2, sc, served, snap
from tests.test_desk import SC, B2, Clock, desk, reader_of, session_token, world  # noqa: F401 - `desk` is the fixture


def auth(user: int = 1, **dat) -> dict:
    return {"Authorization": "Bearer " + session_token(user, **dat)}


def readings(w) -> int:
    """How many readings the desk made: each starts with its one columns check."""
    return len(w.queries_of("columns(ids:"))


# --- the desk itself -------------------------------------------------------------------------------------------------

def test_a_member_of_both_boards_reads_both_tabs_and_amounts_are_strings(desk):  # noqa: F811
    r1, r2 = desk.get(session_token(1)), desk.get(session_token(1))
    assert r1.status_code == 200 and r2.status_code == 200 and readings(desk.w) == 1
    body = r1.json()
    v = body["view"]
    assert v["schema"] == fd.VIEW_SCHEMA and v["tabs"] == {"b2c": True, "b2b": True}
    rows = {x["id"]: x for x in v["b2c"]["rows"]}
    assert rows["101"]["money_in"] == "4000" and rows["101"]["state"] == "receipt_pending"
    assert v["b2b"]["rows"][0]["amount"] == "1000" and v["b2b"]["rows"][0]["state"] == "pending"
    assert v["b2c"]["money_in"] == "7950" and v["read_at"] == body["taken_at"] == body["reading"]["read_at"]
    assert (body["refreshing"], body["stale"], body["view_error"]) == (False, False, None)
    assert isinstance(body["age_seconds"], int) and body["age_seconds"] == body["reading"]["age_seconds"]
    assert r1.headers["cache-control"] == "no-store"


def test_a_member_of_student_charge_only_never_receives_company_money(desk):  # noqa: F811
    body = desk.get(session_token(2)).json()
    assert body["view"]["b2b"] is None and body["view"]["tabs"] == {"b2c": True, "b2b": False}
    assert "cards" not in body["view"] and not any(k.startswith(("b2b", "deals", "accounts")) for k in body["view"]["checks"])
    companies = desk.get(session_token(3)).json()
    assert companies["view"]["b2c"] is None and companies["view"]["tabs"] == {"b2c": False, "b2b": True}


def test_one_unreadable_member_list_is_try_again_not_no_access(desk):  # noqa: F811
    # one list says yes and the other cannot be read: the yes tab only (fail closed for the other)
    desk.w.members_fail = {B2}
    body = desk.get(session_token(1)).json()
    assert body["tabs"] == {"b2c": True, "b2b": None}
    assert body["view"]["tabs"] == {"b2c": True, "b2b": False} and body["view"]["b2b"] is None
    # and the tab whose members are unknown gives no total and no file either
    taken = quote(body["taken_at"])
    assert desk.client.get("/api/desk/totals?tab=b2b&taken=" + taken, headers=auth()).status_code == 403
    assert desk.client.get("/api/desk/totals?tab=b2b_customers&taken=" + taken, headers=auth()).status_code == 403
    assert desk.client.post("/api/desk/export-link", headers=auth(), json={"tab": "b2b"}).status_code == 403
    assert desk.client.get("/api/desk/totals?tab=b2c&taken=" + taken, headers=auth()).status_code == 200


def test_the_view_is_built_once_per_reading_whoever_asks(desk, monkeypatch):  # noqa: F811
    built = []
    real = dr.build_view
    monkeypatch.setattr(dr, "build_view", lambda s, today: built.append(today) or real(s, today))
    for user in (1, 2, 3, 1):
        assert desk.get(session_token(user)).status_code == 200
    assert len(built) == 1 and readings(desk.w) == 1


def test_no_reading_is_a_200_with_no_view_and_the_reason(desk):  # noqa: F811
    desk.w.columns_missing[rd.LEADS_BOARD] = {rd.LD["rep"]}
    resp = desk.get(session_token(1))
    body = resp.json()
    assert resp.status_code == 200 and body["view"] is None and body["reading"] is None
    assert (body["taken_at"], body["age_seconds"], body["refreshing"], body["stale"]) == (None, None, False, False)
    assert body["last_attempt"]["error"] == "unknown_column" and body["last_attempt"]["ok"] is False


def test_a_view_that_cannot_be_built_is_said_never_a_500(desk, monkeypatch, caplog):  # noqa: F811
    def broken(_snapshot, _today):
        raise KeyError("4000")   # a message that could carry a value: the class only reaches the log
    monkeypatch.setattr(dr, "build_view", broken)
    with caplog.at_level(logging.INFO, logger="analiza_finance.desk"):
        resp = desk.get(session_token(1))
    body = resp.json()
    assert resp.status_code == 200 and body["view"] is None and body["view_error"] == "crashed"
    assert body["reading"]["complete"] is True                     # the reading itself was whole
    lines = [r.getMessage() for r in caplog.records if r.name == "analiza_finance.desk"]
    assert any("desk view not built (KeyError)" in x for x in lines) and any("outcome=view_crashed" in x for x in lines)
    assert not any("4000" in x for x in lines)
    t = desk.client.get("/api/desk/totals?tab=b2c&taken=" + quote(body["taken_at"]), headers=auth())
    assert t.status_code == 503 and t.json() == {"error": "finance_unavailable", "reason": "crashed"}


def test_an_old_reading_is_answered_at_once_refreshing_and_the_pages_own_request_reads_the_next(desk):  # noqa: F811
    """D2 (24.11) ports analiza-metrics' stale path (SharedReading part 6, `test_an_expired_desk_is_answered_at_once_and_
    the_pages_own_request_reads_the_next`), which D2's first routes had left out - this test said so then, and pinned the
    reading inside the request instead: a reading past HELD_TTL_S is answered at once, `refreshing`, nothing is read after
    the answer, and the page's own request (`?fresh=1`) reads the next - a refresh, one request."""
    first = desk.get(session_token(1)).json()
    st = main.app.state.desk
    st.reading.reading_at -= rd.HELD_TTL_S + 1
    second = desk.get(session_token(1)).json()
    assert first["refreshing"] is False and second["refreshing"] is True and readings(desk.w) == 1
    assert second["taken_at"] == first["taken_at"] and second["stale"] is False and second["view"] is not None
    third = desk.get(session_token(1), path="/api/desk?fresh=1").json()
    assert third["refreshing"] is False and readings(desk.w) == 2 and third["taken_at"] != first["taken_at"]
    assert third["age_seconds"] == 0 and st.reading.reading["kind"] == "delta"
    assert desk.get(session_token(1)).json()["refreshing"] is False      # and the next open, inside the span, is not


def test_a_reading_shown_while_every_reading_after_it_fails_is_stale_after_ten_minutes(desk):  # noqa: F811
    first = desk.get(session_token(1)).json()
    st = main.app.state.desk
    st.reading.reading_at -= dr.STALE_S - 100                     # old, and the next try fails: not stale yet
    desk.w.fail = [httpx.Response(401, text="no")]
    # since D2 a plain open answers an old reading at once: the page's own request (`?fresh=1`) is the one that reads
    body = desk.get(session_token(1), path="/api/desk?fresh=1").json()
    assert body["stale"] is False and body["last_attempt"]["error"] == "auth" and body["taken_at"] == first["taken_at"]
    st.reading.reading_at -= 200                                   # past ten minutes, and the last attempt failed
    body = desk.get(session_token(1)).json()                       # (within the gap after it: nothing is read)
    assert body["stale"] is True and body["view"] is not None and body["taken_at"] == first["taken_at"]
    assert body["age_seconds"] > dr.STALE_S and readings(desk.w) == 2


# --- the refresh press -----------------------------------------------------------------------------------------------

def test_the_refresh_press_reads_again_and_is_rate_limited(desk, caplog):  # noqa: F811
    first = desk.get(session_token(1)).json()
    with caplog.at_level(logging.INFO, logger="analiza_finance.desk"):
        resp = desk.client.post("/api/desk/refresh", headers=auth())
    assert resp.status_code == 200 and readings(desk.w) == 2
    body = resp.json()
    assert set(body) == set(first) and body["view"]["tabs"] == {"b2c": True, "b2b": True}
    # a reading of its own, named apart from the one before however soon after it came (to the microsecond), and
    # nothing left to come after the answer
    assert body["taken_at"] != first["taken_at"] and body["refreshing"] is False and body["age_seconds"] == 0
    assert any("user=1 endpoint=desk-refresh outcome=ok" in r.getMessage() for r in caplog.records)
    again = desk.client.post("/api/desk/refresh", headers=auth())
    assert again.status_code == 429 and again.json()["error"] == "rate_limited"
    assert 0 < int(again.headers["Retry-After"]) <= dr.REFRESH_GAP_S and again.json()["retry_after_seconds"] > 0
    # one forced reading per instance, whoever presses, and the plain open is not held back by it
    assert desk.client.post("/api/desk/refresh", headers=auth(2)).status_code == 429
    assert desk.get(session_token(2)).status_code == 200 and readings(desk.w) == 2


def test_the_refresh_answers_each_reader_with_their_own_tabs(desk):  # noqa: F811
    body = desk.client.post("/api/desk/refresh", headers=auth(3)).json()
    assert body["view"]["b2c"] is None and body["view"]["tabs"] == {"b2c": False, "b2b": True}


# --- the gate, on every route behind the sessionToken ------------------------------------------------------------------

ROUTES = [("get", "/api/desk/totals?tab=b2b"), ("post", "/api/desk/refresh"), ("post", "/api/desk/export-link")]


def call(desk, method: str, path: str, headers=None):  # noqa: F811
    if method == "get":
        return desk.client.get(path, headers=headers or {})
    return desk.client.post(path, headers=headers or {}, json={"tab": "b2b"} if path.endswith("export-link") else None)


@pytest.mark.parametrize("method, path", ROUTES)
def test_no_session_no_desk(desk, method, path):  # noqa: F811
    assert call(desk, method, path).status_code == 401
    assert call(desk, method, path, {"Authorization": "Bearer a.b.c"}).json() == {"error": "unauthorized"}
    assert desk.w.queries == []


@pytest.mark.parametrize("method, path", ROUTES)
def test_not_on_either_board_is_403_and_unknown_members_is_503(desk, method, path):  # noqa: F811
    resp = call(desk, method, path, auth(9))
    assert resp.status_code == 403 and resp.json() == {"error": "not_a_board_member"}
    main.app.state.desk = None
    desk.w.members_fail = {SC}
    resp = call(desk, method, path, auth(9))
    assert resp.status_code == 503 and resp.json() == {"error": "members_unavailable"}


@pytest.mark.parametrize("method, path", ROUTES)
def test_a_guest_is_refused_and_without_the_secret_the_desk_says_so(desk, monkeypatch, method, path):  # noqa: F811
    assert call(desk, method, path, auth(1, is_guest=True)).json() == {"error": "guest"}
    monkeypatch.delenv("MONDAY_CLIENT_SECRET")
    resp = call(desk, method, path, auth(1))
    assert resp.status_code == 503 and resp.json() == {"error": "app_not_configured", "missing": ["MONDAY_CLIENT_SECRET"]}


def test_only_the_desks_routes_answer_a_body_that_does_not_fit_in_their_own_words(desk):  # noqa: F811
    """app/main.py's handler speaks for /api/desk only: the service's other routes keep FastAPI's own answer. Since D2's
    review a desk route reads its body after the gate: a body that does not fit is the desk's word for a member, and a
    401 for anyone else, whatever the body."""
    bad = {"content": b"{", "headers": {"Content-Type": "application/json", **auth(1)}}
    assert desk.client.post("/api/desk/export-link", **bad).json() == {"error": "invalid_filter"}
    assert desk.client.post("/api/desk/export-link", content=b"{").json() == {"error": "unauthorized"}
    assert desk.client.post("/api/actions/plan", **bad).status_code == 422
    assert desk.client.get("/api/desk?fresh=x", headers=auth(1)).json() == {"error": "invalid_filter"}


@pytest.mark.parametrize("method, path", ROUTES)
def test_every_route_spends_the_readers_budget(desk, monkeypatch, method, path):  # noqa: F811
    monkeypatch.setattr(dr, "DESK_PER_USER_HOUR", 1)
    assert desk.get(session_token(1)).status_code == 200
    resp = call(desk, method, path, auth(1))
    assert resp.status_code == 429 and int(resp.headers["Retry-After"]) > 0


# --- the totals --------------------------------------------------------------------------------------------------------

def test_the_totals_route_sums_the_list_as_filtered_for_its_members_only(desk, monkeypatch):  # noqa: F811
    """analiza-metrics read the bands by the real day; here the route's day is the fixtures' TODAY, so a band does not
    turn under the test as the calendar moves."""
    monkeypatch.setattr(dr, "today_in", lambda _tz: TODAY)
    served(monkeypatch, snap([sc(1, paid="4000.50", receipt="0"), sc(2, paid="100", receipt="0"), sc(3)],
                             [b2(1, payment="2", due="2026-09-01", amount="1000.25"), b2(2, billing="2", amount="50000")]))
    taken = desk.get(session_token(1)).json()["taken_at"]

    def get(qs, user=1):
        return desk.client.get("/api/desk/totals?" + qs + "&taken=" + quote(taken), headers=auth(user))
    t = get("tab=b2c&state=tasks").json()
    assert (t["count"], t["sums"]["money_in"], t["taken_at"]) == (2, "4100.50", taken)
    t = get("tab=b2b").json()
    assert (t["count"], t["sums"]["amount"], t["sums"]["open_money"]) == (2, "1000.25", "1000.25")   # cancelled is out
    t = get("tab=b2b_customers").json()
    assert t["sums"]["aging_d61_90"] == "1000.25"
    assert get("tab=b2c&q=12%EF%BB%BF000").status_code == 400
    assert get("tab=b2c&state=nope").json() == {"error": "bad_filter"}
    assert get("tab=both").json() == {"error": "unknown_tab"}
    assert get("tab=b2b", user=2).status_code == 403 and get("tab=b2c", user=3).status_code == 403
    assert get("tab=b2b_customers", user=2).json() == {"error": "not_a_board_member"}


def test_the_totals_belong_to_the_reading_on_the_screen_and_never_read_monday(desk):  # noqa: F811
    desk.get(session_token(1))
    before = len(desk.w.queries)
    t = desk.client.get("/api/desk/totals?tab=b2b&taken=2020-01-01T00:00:00%2B00:00", headers=auth()).json()
    assert t["other_reading"] is True and "sums" not in t and len(desk.w.queries) == before
    assert t["taken_at"] == desk.get(session_token(1)).json()["taken_at"]
    assert desk.client.get("/api/desk/totals?tab=b2b", headers=auth()).json()["other_reading"] is True   # no `taken`


def test_without_a_reading_the_totals_say_why_and_read_nothing(desk):  # noqa: F811
    resp = desk.client.get("/api/desk/totals?tab=b2c&taken=2026-11-24T10:00:00%2B00:00", headers=auth())
    assert resp.status_code == 503 and resp.json() == {"error": "finance_unavailable", "reason": "not_read"}
    assert readings(desk.w) == 0
    desk.w.fail = [httpx.Response(401, text="no")]
    desk.get(session_token(1))                                     # a reading that failed
    resp = desk.client.get("/api/desk/totals?tab=b2c", headers=auth())
    assert resp.json() == {"error": "finance_unavailable", "reason": "auth"}


# --- the held reading's forced path (app/desk/reading.py) --------------------------------------------------------------

def test_a_forced_reading_reads_however_young_the_held_one():
    w, clock = world(), Clock()
    held = rd.HeldReading(lambda: reader_of(w), clock=clock)
    first, _ = held.get()
    again, last = held.get(force=True)
    assert again is not first and readings(w) == 2 and last["error"] is None
    assert held.get()[0] is again                                  # and the plain open takes the forced one


def test_a_forced_reading_does_not_wait_out_the_gap_after_a_failure():
    w, clock = world(), Clock()
    held = rd.HeldReading(lambda: reader_of(w), clock=clock)
    w.fail = [httpx.Response(401, text="no")]
    shown, last = held.get()
    assert shown is None and last["error"] == "auth"
    clock.t += 1
    assert held.get()[0] is None and readings(w) == 1               # within the gap: no try
    shown, last = held.get(force=True)
    assert shown is not None and last["error"] is None and readings(w) == 2


def test_a_press_that_came_while_a_reading_ran_reads_again_after_it():
    """A reading that was already running may not hold what was just written in monday: the press waits for it, then
    reads again (analiza-metrics SharedReading, part 5)."""
    w = world()
    w.delay = 0.02
    running = threading.Event()

    def make():
        running.set()
        return reader_of(w)

    held = rd.HeldReading(make)
    got = {}
    opener = threading.Thread(target=lambda: got.setdefault("open", held.get()[0]))
    opener.start()
    assert running.wait(5)
    pressed, _ = held.get(force=True)
    opener.join()
    assert readings(w) == 2 and pressed is not got["open"] and held.get()[0] is pressed


def test_a_press_is_answered_by_a_reading_that_started_after_it():
    w, clock = world(), Clock()
    held = rd.HeldReading(lambda: reader_of(w), clock=clock)
    first, _ = held.get()                                          # started at 1000
    clock.t -= 1                                                   # a press that came before that reading started
    assert held.get(force=True)[0] is first and readings(w) == 1
    clock.t += 1                                                   # one that came as it started: read again
    assert held.get(force=True)[0] is not first and readings(w) == 2


def test_a_new_desk_state_has_its_own_press_limit(desk, monkeypatch):  # noqa: F811
    monkeypatch.setattr(dr, "REFRESH_GAP_S", 1)
    st = dr.desk_state(main.app.state, config.load())
    assert st.refresh.check(1, now=0) is None and st.refresh.check(2, now=0.5) == 1
    assert st.refresh.check(2, now=1.5) is None
