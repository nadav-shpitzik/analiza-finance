"""The finance desk's skeleton (Analiza-Monday backlog 0301 D1): the monday sessionToken, the gate by board membership, the
desk's reading of monday, and the page on the private board.

The sessionToken cases are analiza-metrics' tests/test_auth.py (at da671b2), ported. The gate's outcomes are the move
plan's list (§3.2): member, not a member, view-only, guest, the other board, an expired token - and the two the old gate
had besides: members that could not be read, and no secret."""

import base64
import hashlib
import hmac
import json
import logging
import re
import threading
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app import main
from app.desk import auth, gate as dg, reading as rd, routes as dr
from app.desk.auth import EXP_LEEWAY_SECONDS, AuthError, verify_session_token
from app.monday import client as monday
from app.monday.client import NEVER_READ, MondayError, MondayReader

SECRET = "CLIENTSECRET-0123456789abcdef"  # distinctive, so a leak is easy to grep
TOKEN = "MONDAYTOKEN-0123456789abcdef"
ACCOUNT = auth.ANALIZA_ACCOUNT_ID
NOW = 1_800_000_000.0
SC, B2 = rd.STUDENT_CHARGE, rd.B2B_CHARGE
OTHER_BOARD = "5000000001"
ROOT = Path(__file__).resolve().parent.parent


# ---- tokens ---------------------------------------------------------------------------------------------------------
def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def forge(payload, secret: str = SECRET, header=None, signature=None) -> str:
    header = header if header is not None else {"alg": "HS256", "typ": "JWT"}
    h = b64url(json.dumps(header).encode())
    p = b64url(json.dumps(payload).encode())
    sig = signature if signature is not None else hmac.new(secret.encode(), f"{h}.{p}".encode(), hashlib.sha256).digest()
    return f"{h}.{p}.{b64url(sig)}"


def good_payload(**over):
    payload = {"exp": NOW + 300, "dat": {"user_id": 7, "account_id": ACCOUNT}}
    payload.update(over)
    return payload


def session_token(user_id: int, secret: str = SECRET, account_id: int = ACCOUNT, exp=None, **dat) -> str:
    """A sessionToken as monday signs one, live by the real clock unless `exp` says otherwise."""
    return forge({"exp": time.time() + 3600 if exp is None else exp,
                  "dat": {"user_id": user_id, "account_id": account_id, **dat}}, secret=secret)


class TestToken:
    def test_a_valid_token(self):
        ident = verify_session_token(forge(good_payload()), SECRET, now=NOW)
        assert (ident.user_id, ident.account_id) == (7, ACCOUNT)
        assert ident.view_only is None and ident.guest is None  # the token did not say
        assert ident.claim_names == ("account_id", "user_id")

    def test_the_viewer_and_guest_claims_are_read_only_as_booleans(self):
        dat = {"user_id": 7, "account_id": ACCOUNT, "is_view_only": True, "is_guest": "true"}
        ident = verify_session_token(forge(good_payload(dat=dat)), SECRET, now=NOW)
        assert ident.view_only is True and ident.guest is None
        assert ident.claim_names == ("account_id", "is_guest", "is_view_only", "user_id")

    def test_another_secret_alg_none_and_rs256_are_refused(self):
        for tok in (forge(good_payload(), secret="other"),
                    forge(good_payload(), header={"alg": "none", "typ": "JWT"}, signature=b""),
                    forge(good_payload(), header={"alg": "RS256", "typ": "JWT"}),
                    forge(good_payload(), header=["HS256"])):
            with pytest.raises(AuthError) as e:
                verify_session_token(tok, SECRET, now=NOW)
            assert e.value.error_key == "unauthorized"

    def test_exp_is_required_and_a_number(self):
        payload = good_payload()
        del payload["exp"]
        for p in (payload, good_payload(exp="soon"), good_payload(exp=True)):
            with pytest.raises(AuthError) as e:
                verify_session_token(forge(p), SECRET, now=NOW)
            assert e.value.error_key == "unauthorized"

    def test_expired_beyond_the_leeway_and_not_within_it(self):
        with pytest.raises(AuthError) as e:
            verify_session_token(forge(good_payload(exp=NOW - EXP_LEEWAY_SECONDS - 60)), SECRET, now=NOW)
        assert e.value.error_key == "token_expired"
        assert verify_session_token(forge(good_payload(exp=NOW - 30)), SECRET, now=NOW).user_id == 7

    def test_the_account_is_pinned_and_nothing_unpins_it(self):
        tok = forge(good_payload(dat={"user_id": 7, "account_id": 999}))
        for pin in (ACCOUNT, 0, None):
            with pytest.raises(AuthError):
                verify_session_token(tok, SECRET, pin, now=NOW)

    def test_who_it_is_must_be_a_real_user_of_the_payload(self):
        for dat in ({"account_id": ACCOUNT}, {"user_id": 0, "account_id": ACCOUNT}, {"user_id": -3, "account_id": ACCOUNT},
                    {"user_id": True, "account_id": ACCOUNT}, {"user_id": 7, "account_id": True}, "7", None):
            with pytest.raises(AuthError):
                verify_session_token(forge(good_payload(dat=dat)), SECRET, now=NOW)

    def test_garbage_empty_and_oversized(self):
        for bad in ("", "a.b", "a.b.c", "x" * 500, "..", None, "a.b.c.d", forge(good_payload()) + "x" * 5000):
            with pytest.raises(AuthError):
                verify_session_token(bad, SECRET, now=NOW)

    def test_no_secret_never_verifies(self):
        with pytest.raises(AuthError):
            verify_session_token(forge(good_payload(), secret=""), "", now=NOW)

    def test_a_tampered_payload_is_refused(self):
        h, _p, s = forge(good_payload()).split(".")
        p2 = b64url(json.dumps(good_payload(dat={"user_id": 8, "account_id": ACCOUNT})).encode())
        with pytest.raises(AuthError):
            verify_session_token(f"{h}.{p2}.{s}", SECRET, now=NOW)


# ---- a monday for the desk --------------------------------------------------------------------------------------------
def status(label: str, index: int) -> dict:
    return {"text": label, "index": index, "label": label}


def rel(*ids) -> dict:
    return {"text": "", "linked_item_ids": [str(i) for i in ids]}


def board_key(board_id: str):
    return next((k for k, b in rd.BOARDS.items() if b == board_id), None)


class World:
    """monday's GraphQL for the desk's seven boards: the members, the columns check, the charge boards' ids paged, items
    by id (monday answers an id of any board, with only the columns its own board has). Every query is recorded, and a
    mutation fails the test."""

    def __init__(self):
        self.items: dict = {}
        self.order = {SC: [], B2: []}
        self.count_off: dict = {}
        self.members = {SC: {"1", "2"}, B2: {"1", "3"}}
        self.members_fail: set = set()
        self.columns_missing: dict = {}
        self.hidden: set = set()
        self.fail: list = []
        self.queries: list = []
        self.delay = 0.0
        self.drop_cells: set = set()  # columns left out of by-id answers only (a column gone after the columns check)
        self.subscribers_of: dict = {}  # board -> the board id monday's members answer names instead
        self.extra_cells: dict = {}  # item id -> cells monday answers unasked
        self._lock = threading.Lock()

    def add(self, board: str, item_id: int, name: str, state: str = "active", **cells) -> None:
        cols = rd.COLUMNS.get(board_key(board) or "", {})
        self.items[str(item_id)] = {"board": board, "name": name, "state": state,
                                    "cells": {cols.get(k, k): v for k, v in cells.items()}}
        if board in self.order:
            self.order[board].append(str(item_id))

    def own_columns(self, board: str) -> set:
        key = board_key(board)
        return set(rd.CHECKED_COLUMNS[key]) - set(self.columns_missing.get(board, ())) if key else set()

    def cell(self, item: dict, col: str) -> dict:
        value = item["cells"].get(col)
        if isinstance(value, dict):
            return {"id": col, **value}
        if col.startswith("board_relation"):
            return {"id": col, "text": "", "linked_item_ids": []}
        if col.startswith("color_") or col == "deal_stage":
            return {"id": col, "text": "", "index": None, "label": None}
        return {"id": col, "text": "" if value is None else str(value)}

    def queries_of(self, kind: str) -> list:
        return [(q, v) for q, v in self.queries if kind in q]

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == TOKEN
        assert request.headers["API-Version"] == monday.MONDAY_API_VERSION
        body = json.loads(request.content)
        query, var = body["query"], body["variables"]
        assert query.lstrip().startswith("query"), "the desk sent something that is not a read"
        with self._lock:
            self.queries.append((query, var))
            failing = self.fail.pop(0) if self.fail else None
        if self.delay:
            time.sleep(self.delay)
        if failing is not None:
            return failing
        if "subscribers" in query:
            board = var["b"][0]
            if board in self.members_fail:
                return httpx.Response(500, text="no")
            return httpx.Response(200, json={"data": {"boards": [
                {"id": self.subscribers_of.get(board, board),
                 "subscribers": [{"id": u} for u in sorted(self.members.get(board, ()))]}]}})
        if "columns(ids:" in query:
            data = {}
            for k in [k for k in var if k.startswith("b")]:
                board, cols = var[k][0], var["c" + k[1:]]
                data[k] = [] if board in self.hidden else [{"columns": [{"id": c} for c in cols if c in self.own_columns(board)]}]
            return httpx.Response(200, json={"data": data})
        if "next_items_page" in query:
            board, start = var["c"].split(":")
            ids = self.order[board][int(start):int(start) + var["limit"]]
            nxt = int(start) + var["limit"]
            cursor = f"{board}:{nxt}" if nxt < len(self.order[board]) else None
            return httpx.Response(200, json={"data": {"next_items_page": {"cursor": cursor, "items": [{"id": i} for i in ids]}}})
        if "items_page" in query:
            board = var["b"][0]
            if board in self.hidden:
                return httpx.Response(200, json={"data": {"boards": []}})
            ids = self.order[board]
            cursor = f"{board}:{var['limit']}" if len(ids) > var["limit"] else None
            return httpx.Response(200, json={"data": {"boards": [{
                "items_count": len(ids) + self.count_off.get(board, 0),
                "items_page": {"cursor": cursor, "items": [{"id": i} for i in ids[:var["limit"]]]}}]}})
        if "items(ids:" in query:
            out = []
            for i in var["ids"]:
                item = self.items.get(str(i))
                if item is None:
                    continue  # monday leaves out an id it does not know
                own = self.own_columns(item["board"])
                cells = [self.cell(item, c) for c in var["cols"] if c in own and c not in self.drop_cells]
                cells += [{"id": c, **v} for c, v in self.extra_cells.get(str(i), {}).items()]
                out.append({"id": str(i), "name": item["name"], "state": item["state"], "created_at": "2026-11-01T00:00:00Z",
                            "board": {"id": item["board"]}, "column_values": cells})
            return httpx.Response(200, json={"data": {"items": out}})
        raise AssertionError("an unexpected query: " + query[:80])

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


def world() -> World:
    """Three payments, two company charges, and what they link to (the names are distinctive, to grep for a leak):
    p101 enrollment 201 (cohort 401) and lead 301; p102 enrollment 202 (no cohort) and lead 302 (cohort 402); p103 no
    enrollment and lead 303 (no cohort). b501 deal 601 (payer 702) and account 701; b502 deal 602 and account 701."""
    w = World()
    w.add(SC, 101, "תלמידה-אלף", status=status("שולם", 1), paid="4000", enrollment=rel(201), lead=rel(301),
          invoice="5001")
    w.add(SC, 102, "תלמיד-בית", status=status("לא שולם", 0), paid="", enrollment=rel(202), lead=rel(302))
    w.add(SC, 103, "תלמיד-גימל", status=status("שולם", 1), paid="3950", enrollment=rel(), lead=rel(303))
    w.add(rd.ENROLLMENTS_BOARD, 201, "הרשמה-א", cohort=rel(401))
    w.add(rd.ENROLLMENTS_BOARD, 202, "הרשמה-ב", cohort=rel())
    w.add(rd.LEADS_BOARD, 301, "ליד-אלף", cohort=rel(), rep="נציגה-אחת")
    w.add(rd.LEADS_BOARD, 302, "ליד-בית", cohort=rel(402), rep="")
    w.add(rd.LEADS_BOARD, 303, "ליד-גימל", cohort=rel(), rep="נציג-שני")
    w.add(rd.COHORTS_BOARD, 401, "מחזור-אחד", code="C1")
    w.add(rd.COHORTS_BOARD, 402, "מחזור-שתיים", code="C2")
    w.add(B2, 501, "חיוב-חברה-1", deal=rel(601), account=rel(701), amount="1000")
    w.add(B2, 502, "חיוב-חברה-2", deal=rel(602), account=rel(701), amount="2500")
    w.add(rd.DEALS_BOARD, 601, "עסקה-1", payer=rel(702))
    w.add(rd.DEALS_BOARD, 602, "עסקה-2", payer=rel())
    w.add(rd.ACCOUNTS_BOARD, 701, "חברה-משלמת", terms="שוטף+30", hash_key="77001", invoice_name="חשבונית-על-שם")
    w.add(rd.ACCOUNTS_BOARD, 702, "חברה-אם", terms="שוטף")
    return w


def reader_of(w: World) -> MondayReader:
    return MondayReader(TOKEN, client=httpx.Client(transport=w.transport()), sleep=lambda s: None)


SECRET_WORDS = ("תלמידה-אלף", "תלמיד-בית", "ליד-אלף", "נציגה-אחת", "מחזור-אחד", "חיוב-חברה-1", "עסקה-1", "חברה-משלמת",
                "77001", "4000", "3950", "5001")


# ---- the reading ------------------------------------------------------------------------------------------------------
def test_the_reading_reads_both_chains_and_counts_every_board():
    w = world()
    r = rd.read_desk(reader_of(w))
    assert r["error"] is None and r["complete"] is True
    assert [x["id"] for x in r["student_charge"]["rows"]] == ["101", "102", "103"]
    assert r["student_charge"]["items_count"] == 3 and r["b2b_charge"]["items_count"] == 2
    assert [x["id"] for x in r["enrollments"]] == ["201", "202"]
    # every lead's name and owner, but the rows only of the leads whose cohort decides (no cohort on the enrollment)
    assert r["lead_names"] == {"301": "ליד-אלף", "302": "ליד-בית", "303": "ליד-גימל"}
    assert r["lead_reps"] == {"301": "נציגה-אחת", "302": "", "303": "נציג-שני"}
    assert [x["id"] for x in r["leads"]] == ["302", "303"]
    assert [x["id"] for x in r["cohorts"]] == ["401", "402"]
    assert [x["id"] for x in r["deals"]] == ["601", "602"]
    assert [x["id"] for x in r["accounts"]] == ["701", "702"]  # the charges' account and the deal's payer
    assert r["asked"] == {"enrollments": ["201", "202"], "leads": ["302", "303"], "cohorts": ["401", "402"],
                          "deals": ["601", "602"], "accounts": ["701", "702"]}
    assert set(r["checks"]) == set(rd.BOARDS)
    assert r["checks"]["leads"] == {"board_id": rd.LEADS_BOARD, "asked": 3, "rows": 3, "missing": 0, "strangers": 0,
                                    "archived": 0, "short_rows": 0, "ok": True}
    # the cost: the columns, the two boards' ids and rows, then two rounds by id - and nothing else
    assert r["calls"] == 10 and len(w.queries) == 10


def test_the_snapshot_has_the_shape_the_old_desk_builds_from():
    r = rd.read_desk(reader_of(world()))
    old = {"read_at", "full_read_at", "kind", "student_charge", "b2b_charge", "enrollments", "leads", "lead_names",
           "lead_reps", "cohorts", "deals", "accounts", "asked"}  # analiza-metrics FinanceReader.read at da671b2
    assert old <= set(r) and r["kind"] == "full"
    row = r["student_charge"]["rows"][0]
    assert row[rd.SC["status"]] == "שולם" and row[rd.SC["status"] + "#"] == "1"  # a label by its index too
    assert row[rd.SC["enrollment"]] == ["201"]


def test_the_columns_that_are_never_read_are_checked_to_exist_and_never_read():
    w = world()
    r = rd.read_desk(reader_of(w))
    checked = {c for _q, v in w.queries_of("columns(ids:") for k, cols in v.items() if k.startswith("c") for c in cols}
    read = {c for _q, v in w.queries_of("items(ids:") for c in v["cols"]}
    assert {"text_mm8cxm9y", "text_mm8ckgh8", "text_mkzvem4", "text_mm3y3gm7"} <= checked
    assert not read & NEVER_READ
    every_row = [x for k in ("enrollments", "leads", "cohorts", "deals", "accounts") for x in r[k]]
    every_row += r["student_charge"]["rows"] + r["b2b_charge"]["rows"]
    assert not any(NEVER_READ & set(x) for x in every_row)
    assert rd.AC["vat_id"] in NEVER_READ and rd.LD["id_number"] in NEVER_READ  # analiza-metrics read both


def test_the_reader_refuses_a_never_read_column_before_sending_anything():
    w = world()
    reader = reader_of(w)
    for col in sorted(NEVER_READ):
        with pytest.raises(MondayError) as e:
            reader.read_items([(["301"], ["name_x", col])])
        assert e.value.code == "never_read"
    assert w.queries == []


def test_a_column_a_board_lacks_ends_the_reading_with_no_rows():
    w = world()
    w.columns_missing[rd.ACCOUNTS_BOARD] = {rd.AC["terms"]}
    r = rd.read_desk(reader_of(w))
    assert r["error"] == "unknown_column" and r["complete"] is False
    assert "student_charge" not in r and r["checks"] == {}
    assert len(w.queries) == 1  # nothing read after the check


def test_a_linked_item_on_another_board_is_dropped_and_counted_not_used():
    w = world()
    w.items["201"]["board"] = OTHER_BOARD  # the relation now leads to an item of another board
    r = rd.read_desk(reader_of(w))
    assert [x["id"] for x in r["enrollments"]] == ["202"]
    assert r["checks"]["enrollments"]["strangers"] == 1 and r["checks"]["enrollments"]["missing"] == 1
    assert r["complete"] is True  # a note on the page, as a deleted item is, not a failed reading
    assert "301" in r["asked"]["leads"]  # p101's enrollment names no cohort now, so its lead decides


def test_a_deleted_linked_item_is_missing_and_an_archived_one_kept():
    w = world()
    w.items["601"]["state"] = "deleted"
    w.items["401"]["state"] = "archived"
    r = rd.read_desk(reader_of(w))
    assert [x["id"] for x in r["deals"]] == ["602"] and r["checks"]["deals"]["missing"] == 1
    assert [x["id"] for x in r["cohorts"]] == ["401", "402"] and r["checks"]["cohorts"]["archived"] == 1
    assert "702" not in r["asked"]["accounts"]  # the deleted deal's payer is not asked for
    assert r["complete"] is True


def test_a_charge_board_read_short_of_its_items_count_is_not_complete():
    w = world()
    w.count_off[SC] = 1
    r = rd.read_desk(reader_of(w))
    assert r["error"] is None and r["complete"] is False
    assert r["checks"]["student_charge"]["ok"] is False and r["checks"]["b2b_charge"]["ok"] is True


def test_a_request_that_fails_fails_the_reading_with_its_code_and_no_rows():
    w = world()
    w.fail = [httpx.Response(401, text="no")]
    r = rd.read_desk(reader_of(w))
    assert r["error"] == "auth" and r["complete"] is False and "student_charge" not in r


def test_a_reading_that_crashes_is_a_failed_reading_by_class():
    class Broken(MondayReader):
        def read_boards(self, boards):
            raise KeyError("x")

    r = rd.read_desk(Broken(TOKEN, client=httpx.Client(transport=world().transport())))
    assert r["error"] == "crashed" and r["detail"] == "KeyError"


def test_the_reading_never_starts_a_request_after_its_deadline():
    w = world()
    reader = reader_of(w)
    t = [0.0]
    reader.clock = lambda: t[0]
    reader.deadline = 0.0
    r = rd.read_desk(reader)
    assert r["error"] == "deadline" and w.queries == []


# ---- the held reading -------------------------------------------------------------------------------------------------
class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_one_reading_serves_every_open_within_a_minute():
    w, clock = world(), Clock()
    held = rd.HeldReading(lambda: reader_of(w), clock=clock)
    first, _ = held.get()
    clock.t += 59
    again, _ = held.get()
    assert again is first and len(w.queries) == 10
    clock.t += 2
    newer, last = held.get()
    assert newer is not first and len(w.queries) == 20 and last["complete"] is True


def test_a_failed_reading_keeps_the_one_before_and_is_tried_again_only_after_the_gap():
    w, clock = world(), Clock()
    held = rd.HeldReading(lambda: reader_of(w), clock=clock)
    good, _ = held.get()
    clock.t += 61
    w.fail = [httpx.Response(401, text="no")]
    shown, last = held.get()
    assert shown is good and last["error"] == "auth"
    n = len(w.queries)
    clock.t += 10
    shown, last = held.get()  # within the gap: no new try
    assert shown is good and len(w.queries) == n
    clock.t += 11
    shown, last = held.get()
    assert shown is not good and last["error"] is None


def test_a_reading_that_came_short_replaces_the_one_before():
    w, clock = world(), Clock()
    held = rd.HeldReading(lambda: reader_of(w), clock=clock)
    held.get()
    clock.t += 61
    w.count_off[B2] = 2
    shown, last = held.get()
    assert shown["complete"] is False and last["complete"] is False


def test_two_opens_at_once_make_one_reading():
    w = world()
    w.delay = 0.05
    held = rd.HeldReading(lambda: reader_of(w))
    got = []
    threads = [threading.Thread(target=lambda: got.append(held.get()[0])) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(w.queries_of("columns(ids:")) == 1 and got[0] is got[1] is got[2]


def test_a_reader_that_cannot_be_made_is_a_failed_reading_not_a_crash():
    def broken():
        raise OSError("no")

    shown, last = rd.HeldReading(broken).get()
    assert shown is None and last["error"] == "crashed" and last["detail"] == "OSError"


# ---- the members ------------------------------------------------------------------------------------------------------
def test_members_are_kept_five_minutes_and_a_failed_read_thirty_seconds():
    clock, reads = Clock(), []

    def read(board):
        reads.append(board)
        if board == "bad":
            raise MondayError("http", "x")
        return {"1"}

    m = dg.Members(read, clock=clock)
    assert m.member(1, "b") is True and m.member(2, "b") is False and m.member(1, "bad") is None
    clock.t += 29
    m.member(1, "b"), m.member(1, "bad")
    assert reads == ["b", "bad"]
    clock.t += 2
    m.member(1, "bad")
    assert reads == ["b", "bad", "bad"]
    clock.t += 270
    m.member(1, "b")
    assert reads == ["b", "bad", "bad", "b"]
    assert m.state() == {"b": "read", "bad": "failed: http"}


def test_a_board_that_answers_nobody_is_a_failed_read_not_an_empty_list():
    w = world()
    w.members[SC] = set()
    with pytest.raises(MondayError):
        reader_of(w).board_subscribers(SC)


# ---- the route --------------------------------------------------------------------------------------------------------
@pytest.fixture
def desk(monkeypatch):
    """The app with the desk's two secrets and a monday of its own: `desk.w` is the World, `desk.client` the TestClient."""
    w = world()
    monkeypatch.setenv("MONDAY_CLIENT_SECRET", SECRET)
    monkeypatch.setenv("MONDAY_TOKEN", TOKEN)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    main.app.state.monday_transport = w.transport()
    main.app.state.desk = None
    main.app.state.frontend_dir = None

    class D:
        pass

    d = D()
    d.w, d.client = w, TestClient(main.app)
    d.get = lambda tok=None, path="/api/desk": d.client.get(path, headers={} if tok is None else {"Authorization": "Bearer " + tok})
    yield d
    main.app.state.monday_transport = None
    main.app.state.desk = None


def test_without_the_client_secret_the_desk_says_so_and_asks_monday_nothing(desk, monkeypatch):
    monkeypatch.delenv("MONDAY_CLIENT_SECRET")
    resp = desk.get(session_token(1))
    assert resp.status_code == 503 and resp.json() == {"error": "app_not_configured", "missing": ["MONDAY_CLIENT_SECRET"]}
    assert desk.w.queries == []


def test_no_token_a_forged_one_or_another_accounts_is_401_before_any_monday_read(desk):
    for tok in (None, "", "a.b.c", session_token(1, secret="other"), session_token(1, account_id=999)):
        resp = desk.get(tok)
        assert resp.status_code == 401 and resp.json() == {"error": "unauthorized"}
    assert desk.w.queries == []


def test_an_expired_token_says_so_so_the_page_asks_for_a_fresh_one(desk):
    resp = desk.get(session_token(1, exp=time.time() - 3600))
    assert resp.status_code == 401 and resp.json() == {"error": "token_expired"}


def test_a_member_of_both_boards_sees_every_board_counted(desk):
    resp = desk.get(session_token(1))
    assert resp.status_code == 200
    body = resp.json()
    assert body["user"] == {"id": 1, "view_only": None, "guest": None, "claims": ["account_id", "user_id"]}
    assert body["tabs"] == {"b2c": True, "b2b": True}
    assert set(body["reading"]["boards"]) == set(rd.BOARDS) and body["reading"]["complete"] is True
    assert body["reading"]["boards"]["student_charge"]["rows"] == 3 and body["last_attempt"]["error"] is None
    assert resp.headers["Cache-Control"] == "no-store"


def test_a_member_of_one_board_sees_only_its_tabs_boards(desk):
    b2c = desk.get(session_token(2)).json()  # Student Charge only (Inna's case, ACC-34)
    assert b2c["tabs"] == {"b2c": True, "b2b": False}
    assert set(b2c["reading"]["boards"]) == set(rd.TAB_BOARDS["b2c"])
    b2b = desk.get(session_token(3)).json()  # B2B Charge only
    assert b2b["tabs"] == {"b2c": False, "b2b": True}
    assert set(b2b["reading"]["boards"]) == set(rd.TAB_BOARDS["b2b"])


def test_not_a_member_of_either_is_403_and_the_boards_are_not_read(desk):
    resp = desk.get(session_token(9))
    assert resp.status_code == 403 and resp.json() == {"error": "not_a_board_member"}
    assert {q for q, _v in desk.w.queries} == {monday.SUBSCRIBERS}


def test_members_that_could_not_be_read_are_503_not_403(desk):
    desk.w.members_fail = {SC}
    resp = desk.get(session_token(9))  # not on B2B Charge, Student Charge unknown
    assert resp.status_code == 503 and resp.json() == {"error": "members_unavailable"}


def test_a_board_whose_members_could_not_be_read_opens_nothing_and_is_said_unknown(desk):
    desk.w.members_fail = {SC}
    body = desk.get(session_token(1)).json()  # on both, but Student Charge cannot be read
    assert body["tabs"] == {"b2c": None, "b2b": True}  # unknown, never "not a member" (review 24.11)
    assert set(body["reading"]["boards"]) == set(rd.TAB_BOARDS["b2b"])


def test_a_guest_is_refused_before_the_members_are_read(desk):
    resp = desk.get(session_token(1, is_guest=True))
    assert resp.status_code == 403 and resp.json() == {"error": "guest"}
    assert desk.w.queries == []


def test_a_viewer_reads_and_is_told_so(desk):
    body = desk.get(session_token(1, is_view_only=True, is_guest=False)).json()
    assert body["user"]["view_only"] is True and body["user"]["guest"] is False
    assert body["reading"]["complete"] is True


def test_the_answer_carries_counts_never_a_name_or_an_amount(desk):
    text = desk.get(session_token(1)).text
    for word in SECRET_WORDS:
        assert word not in text


def test_the_reading_is_shared_and_the_members_kept_across_opens(desk):
    desk.get(session_token(1))
    desk.get(session_token(2))
    desk.get(session_token(3))
    assert len(desk.w.queries_of("columns(ids:")) == 1
    assert len(desk.w.queries_of("subscribers")) == 2  # one per board


def test_the_readers_budget_is_429_with_retry_after(desk, monkeypatch):
    monkeypatch.setattr(dr, "DESK_PER_USER_HOUR", 2)
    assert desk.get(session_token(1)).status_code == 200
    assert desk.get(session_token(1)).status_code == 200
    resp = desk.get(session_token(1))
    assert resp.status_code == 429 and resp.json()["error"] == "rate_limited" and int(resp.headers["Retry-After"]) > 0
    assert desk.get(session_token(2)).status_code == 200  # per user


def test_a_failed_reading_is_said_with_its_code(desk):
    desk.w.columns_missing[rd.LEADS_BOARD] = {rd.LD["rep"]}
    body = desk.get(session_token(1)).json()
    assert body["reading"] is None
    assert body["last_attempt"]["error"] == "unknown_column" and rd.LD["rep"] in body["last_attempt"]["detail"]


def test_the_audit_line_names_the_user_and_never_the_token(desk, caplog):
    tok = session_token(1)
    with caplog.at_level(logging.INFO, logger="analiza_finance.desk"):
        desk.get(tok)
        desk.get(session_token(9))
    lines = [r.getMessage() for r in caplog.records if r.name == "analiza_finance.desk"]
    assert any("user=1 endpoint=desk outcome=ok" in x for x in lines)
    assert any("user=9 endpoint=desk outcome=not_a_member" in x for x in lines)
    assert not any(tok in x or TOKEN in x or SECRET in x for x in lines)


def test_health_says_the_desk_without_a_count_or_a_secret(desk):
    before = desk.client.get("/health").json()["desk"]
    assert before == {"client_secret_set": True, "monday_token_set": True, "page": "/desk/", "last_reading": None,
                      "members": {}}
    desk.get(session_token(1))
    text = desk.client.get("/health").text
    after = json.loads(text)["desk"]
    assert after["last_reading"]["complete"] is True and after["last_reading"]["error"] is None
    assert sorted(after["last_reading"]["boards_whole"]) == sorted(rd.BOARDS)
    assert after["members"] == {SC: "read", B2: "read"}
    assert "checks" not in after["last_reading"] and SECRET not in text and TOKEN not in text


# ---- the page ---------------------------------------------------------------------------------------------------------
def test_the_page_is_revalidated_on_every_open_and_links_its_assets_by_version(desk):
    resp = desk.client.get("/desk/")
    assert resp.status_code == 200 and resp.headers["Cache-Control"] == "no-cache"
    assert resp.headers["content-type"].startswith("text/html")
    links = re.findall(r'(?:src|href)="(/desk/[^"]+)"', resp.text)
    # D2: the desk (finance.js, finance.css), its glue (desk.js), the self-check (check.js), the frame and the SDK
    assert sorted(x.split("?")[0] for x in links) == sorted(dr.STATIC_FILES)
    # the glue runs last: it mounts what finance.js and check.js define, with the SDK they all share
    scripts = [x.split("?")[0] for x in re.findall(r'<script src="([^"]+)"', resp.text)]
    assert scripts == ["/desk/vendor/monday-sdk.js", "/desk/finance.js", "/desk/check.js", "/desk/desk.js"]
    assert all(re.search(r"\?v=[0-9a-f]{12}$", x) for x in links)
    again = desk.client.get("/desk/", headers={"If-None-Match": resp.headers["ETag"]})
    assert again.status_code == 304
    assert desk.client.get("/desk").text == resp.text  # without the slash, the same page


def test_an_asset_asked_by_its_version_is_kept_a_year_and_otherwise_revalidated(desk):
    page = desk.client.get("/desk/").text
    for path, (_rel, media) in dr.STATIC_FILES.items():
        versioned = re.search(r'(?:src|href)="(%s\?v=[0-9a-f]+)"' % re.escape(path), page).group(1)
        assert desk.client.get(versioned).headers["Cache-Control"] == dr.IMMUTABLE, path
        plain = desk.client.get(path)
        assert plain.headers["Cache-Control"] == "no-cache" and plain.headers["content-type"] == media, path
        assert desk.client.get(path, headers={"If-None-Match": plain.headers["ETag"]}).status_code == 304, path


def test_monday_may_embed_the_page_and_nothing_else_may(desk):
    for path in ("/desk/", "/desk/desk.js", "/api/desk", "/health"):
        csp = desk.client.get(path).headers["Content-Security-Policy"]
        assert "frame-ancestors 'self' https://*.monday.com" in csp and "object-src 'none'" in csp


def test_a_missing_frontend_file_is_a_404_not_a_500(desk, tmp_path):
    main.app.state.frontend_dir = str(tmp_path)
    assert desk.client.get("/desk/").status_code == 404
    assert desk.client.get("/desk/desk.js").status_code == 404


def test_the_sdk_is_analiza_metrics_copy_byte_for_byte():
    data = (ROOT / "frontend" / "vendor" / "monday-sdk.js").read_bytes()
    # analiza-metrics frontend/vendor/monday-sdk.js at da671b2: monday-sdk-js 0.4.12, which the old desk runs
    assert hashlib.sha256(data).hexdigest() == "70fa11f517b8d8aa319769b1f3187eb7722fd0894f9233c606c89702ccec9833"


def test_the_page_draws_text_only_and_writes_no_em_dash():
    """The glue and the self-check draw with textContent only. The desk (finance.js) builds HTML, as it did in
    analiza-metrics, and escapes every value of an answer into it: tests/test_desk_page.py holds that."""
    for name in ("desk.js", "check.js"):
        js = (ROOT / "frontend" / name).read_text(encoding="utf-8")
        assert not re.search(r"innerHTML|outerHTML|insertAdjacentHTML|document\.write|eval\(|new Function", js), name
    finance = (ROOT / "frontend" / "finance.js").read_text(encoding="utf-8")
    assert not re.search(r"outerHTML|insertAdjacentHTML|document\.write|eval\(|new Function", finance)
    for name in ("index.html", "desk.js", "desk.css", "check.js", "finance.js", "finance.css"):
        assert "—" not in (ROOT / "frontend" / name).read_text(encoding="utf-8"), name


def test_the_page_asks_monday_as_the_person_with_a_read_only():
    """The self-check's one read as the person (/desk/?check=1, check.js since D2)."""
    js = (ROOT / "frontend" / "check.js").read_text(encoding="utf-8")
    calls = re.findall(r"monday\.api\(([^,]+),", js)
    assert calls == ["query"]
    assert re.search(r'var query = "query \(\$b: \[ID!\]\) \{ boards\(ids: \$b\) \{ id name \} \}";', js)
    assert "mutation" not in js


def test_the_access_log_never_carries_a_session_token():
    """monday may load the page with the sessionToken in its URL, and uvicorn's access line prints the path with its
    query: the value is replaced before the line is written, wherever the parameter stands."""
    tok = session_token(1)
    for path in (f"/desk/?sessionToken={tok}", f"/desk/?locale=he&sessionToken={tok}&boardId=1", f"/desk?SESSIONTOKEN={tok}"):
        record = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
                                   ("1.2.3.4:5", "GET", path, "1.1", 200), None)
        assert all(f.filter(record) for f in logging.getLogger("uvicorn.access").filters)
        line = record.getMessage()
        assert tok not in line and "[redacted]" in line and ("boardId=1" in line or "boardId" not in path)



# ---- the review of 24.11 (on d019e9e): its five findings, and the mutants the suite let through ------------------------
ID_MIRRORS = ("lookup_mm8b1jzy", "lookup_mm2kg3gw", "lookup_mm3y7rwr")  # ת.ז, ID, ח.פ: mirrors of an ID number (read 24.11)


def test_the_mirrors_of_an_id_number_are_never_read_either():
    w = world()
    reader = reader_of(w)
    for col in ID_MIRRORS + ("text_mm0skfxr",):
        assert col in NEVER_READ
        with pytest.raises(MondayError) as e:
            reader.read_items([(["101"], [rd.SC["paid"]]), (["101"], [col])])  # in the second job, not the first
        assert e.value.code == "never_read"
    assert w.queries == []


def test_a_read_by_id_must_name_its_columns():
    w = world()
    with pytest.raises(MondayError):
        reader_of(w).read_items([(["101"], [])])
    assert w.queries == []


def test_a_cell_monday_answers_unasked_is_not_kept_and_a_never_read_one_never():
    w = world()
    w.extra_cells["302"] = {"text_mkzvem4": {"text": "012345678"}, "text_unasked": {"text": "x"}}
    r = rd.read_desk(reader_of(w))
    assert r["error"] is None
    rows = [x for k in ("enrollments", "leads", "cohorts", "deals", "accounts") for x in r[k]]
    assert not any("text_mkzvem4" in x or "text_unasked" in x for x in rows)
    assert monday.flatten({"id": 1, "column_values": [{"id": "lookup_mm8b1jzy", "display_value": "012345678"}]}) == {
        "id": "1", "name": "", "created_at": "", "state": "active", "board_id": ""}


def test_requests_refused_at_the_gate_never_spend_a_members_budget(desk, monkeypatch):
    monkeypatch.setattr(dr, "DESK_PER_DAY", 3)
    for tok in (session_token(9), session_token(9), session_token(9), session_token(1, is_guest=True), "a.b.c"):
        assert desk.get(tok).status_code in (401, 403)
    assert desk.get(session_token(1)).status_code == 200
    assert desk.get(session_token(2)).status_code == 200
    assert desk.get(session_token(3)).status_code == 200
    resp = desk.get(session_token(1))  # the fourth member request of the day
    assert resp.status_code == 429 and int(resp.headers["Retry-After"]) > 0


@pytest.mark.parametrize("exp", [float("nan"), float("inf"), float("-inf")])
def test_an_exp_that_is_not_a_finite_number_is_refused(exp):
    with pytest.raises(AuthError):
        verify_session_token(forge(good_payload(exp=exp)), SECRET, now=NOW + 10 ** 9)


def test_an_id_is_an_integer_or_its_digits_never_a_float_rounded_into_one():
    for dat in ({"user_id": 7.9, "account_id": ACCOUNT}, {"user_id": 7, "account_id": ACCOUNT + 0.5},
                {"user_id": 7, "account_id": float(ACCOUNT)}, {"user_id": "7.0", "account_id": ACCOUNT},
                {"user_id": "٧", "account_id": ACCOUNT}):
        with pytest.raises(AuthError):
            verify_session_token(forge(good_payload(dat=dat)), SECRET, now=NOW)
    ident = verify_session_token(forge(good_payload(dat={"user_id": "7", "account_id": str(ACCOUNT)})), SECRET, now=NOW)
    assert (ident.user_id, ident.account_id) == (7, ACCOUNT)


def test_a_member_of_one_board_hears_only_of_their_own_tab(desk):
    desk.w.count_off[B2] = 1  # B2B Charge reads short; the students' tab is whole
    inna = desk.get(session_token(2)).json()  # Student Charge only
    assert inna["reading"]["complete"] is True and set(inna["reading"]["boards"]) == set(rd.TAB_BOARDS["b2c"])
    assert inna["last_attempt"] == {"read_at": inna["last_attempt"]["read_at"], "error": None, "complete": True}
    both = desk.get(session_token(1)).json()
    assert both["reading"]["complete"] is False and both["last_attempt"]["complete"] is False
    assert "detail" in both["last_attempt"]


def test_a_failed_reading_tells_its_detail_only_to_a_reader_of_both_tabs(desk):
    desk.w.columns_missing[rd.ACCOUNTS_BOARD] = {rd.AC["terms"]}
    inna = desk.get(session_token(2)).json()
    assert inna["last_attempt"] == {"read_at": inna["last_attempt"]["read_at"], "error": "unknown_column", "complete": False}
    assert rd.ACCOUNTS_BOARD in desk.get(session_token(1)).json()["last_attempt"]["detail"]


def test_a_member_removed_during_an_outage_does_not_keep_the_old_grant():
    clock, answers = Clock(), [{"1", "2"}, MondayError("http", "x")]

    def read(board):
        got = answers.pop(0)
        if isinstance(got, Exception):
            raise got
        return got

    m = dg.Members(read, clock=clock)
    assert m.member(2, "b") is True
    clock.t += dg.MEMBERS_TTL_S + 1
    assert m.member(2, "b") is None  # unknown now, not the list of five minutes ago
    # and the failure is what is kept: said as failed, and not asked again on every open for thirty seconds
    assert m.state() == {"b": "failed: http"}
    clock.t += dg.MEMBERS_FAIL_TTL_S - 1
    assert m.member(2, "b") is None and answers == []


def test_a_board_that_answers_no_member_is_a_failed_read_and_is_asked_again():
    clock, reads = Clock(), []
    m = dg.Members(lambda board: reads.append(board) or set(), clock=clock)
    assert m.member(1, "b") is None and m.state() == {"b": "failed: ValueError"}
    clock.t += dg.MEMBERS_FAIL_TTL_S + 1
    m.member(1, "b")
    assert len(reads) == 2


def test_members_of_a_cold_board_are_read_once_however_many_ask_at_once():
    reads = []

    def slow(board):
        reads.append(board)
        time.sleep(0.05)
        return {"1"}

    m = dg.Members(slow)
    threads = [threading.Thread(target=m.member, args=(1, "b")) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert reads == ["b"]


def test_members_answered_for_another_board_are_not_this_boards():
    w = world()
    w.subscribers_of[SC] = B2
    with pytest.raises(MondayError) as e:
        reader_of(w).board_subscribers(SC)
    assert e.value.code == "not_visible"


def test_the_instances_budget_refuses_once_spent():
    limiter = dg.RateLimiter(per_user_hour=100, global_day=2)
    assert limiter.check(1, now=0) is None and limiter.check(2, now=1) is None
    assert limiter.check(3, now=2) == 86398
    assert limiter.check(3, now=86401) is None


def test_no_audit_line_carries_the_bearer(desk, caplog):
    forged, expired = session_token(1, secret="other"), session_token(1, exp=time.time() - 3600)
    with caplog.at_level(logging.INFO, logger="analiza_finance.desk"):
        for tok in (forged, expired, "Bearer-looking-garbage"):
            desk.get(tok)
    lines = [r.getMessage() for r in caplog.records if r.name == "analiza_finance.desk"]
    assert len(lines) == 3 and any("outcome=token_expired" in x for x in lines)
    for tok in (forged, expired):
        assert not any(part in x for part in tok.split(".") for x in lines)


def test_the_access_log_keeps_no_piece_of_the_token():
    tok = session_token(1)
    record = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
                               ("1.2.3.4:5", "GET", f"/desk/?sessionToken={tok}&x=1", "1.1", 200), None)
    for f in logging.getLogger("uvicorn.access").filters:
        f.filter(record)
    line = record.getMessage()
    assert not any(part in line for part in tok.split(".")) and "x=1" in line


def test_the_security_headers_are_exactly_these(desk):
    for path in ("/desk/", "/api/desk", "/health"):
        h = desk.client.get(path).headers
        assert h["Content-Security-Policy"] == "frame-ancestors 'self' https://*.monday.com; object-src 'none'; base-uri 'none'"
        assert h["X-Content-Type-Options"] == "nosniff" and h["Referrer-Policy"] == "no-referrer"


def test_a_validly_signed_token_past_the_size_cap_is_refused():
    big = forge(good_payload(dat={"user_id": 7, "account_id": ACCOUNT, "pad": "x" * auth.TOKEN_MAX_CHARS}))
    with pytest.raises(AuthError):
        verify_session_token(big, SECRET, now=NOW)


def test_the_leeway_is_sixty_seconds_to_the_second():
    assert verify_session_token(forge(good_payload(exp=NOW - 60)), SECRET, now=NOW).user_id == 7
    with pytest.raises(AuthError) as e:
        verify_session_token(forge(good_payload(exp=NOW - 61)), SECRET, now=NOW)
    assert e.value.error_key == "token_expired"


def test_only_the_bearer_scheme_carries_the_token(desk):
    tok = session_token(1)
    for header in (tok, "Basic " + tok, "Token " + tok, "Bearer"):
        assert desk.client.get("/api/desk", headers={"Authorization": header}).status_code == 401
    assert desk.client.get("/api/desk", headers={"Authorization": "bearer " + tok}).status_code == 200


def test_without_the_monday_token_the_desk_says_so(desk, monkeypatch):
    monkeypatch.delenv("MONDAY_TOKEN")
    resp = desk.get(session_token(1))
    assert resp.status_code == 503 and resp.json() == {"error": "app_not_configured", "missing": ["MONDAY_TOKEN"]}


def test_a_new_monday_token_makes_the_desks_state_again(desk, monkeypatch):
    from app import config

    first = dr.desk_state(main.app.state, config.load())
    assert dr.desk_state(main.app.state, config.load()) is first
    monkeypatch.setenv("MONDAY_TOKEN", TOKEN + "-new")
    assert dr.desk_state(main.app.state, config.load()) is not first


def test_a_charge_boards_row_answered_from_another_board_is_not_kept():
    w = world()
    w.items["102"]["board"] = OTHER_BOARD  # an id the board's pages gave, answered by id from another board
    r = rd.read_desk(reader_of(w))
    assert [x["id"] for x in r["student_charge"]["rows"]] == ["101", "103"]
    assert r["checks"]["student_charge"]["strangers"] == 1 and r["complete"] is False


def test_a_linked_row_of_the_right_board_without_a_column_fails_the_reading():
    w = world()
    w.drop_cells = {rd.CO["code"]}  # the cohorts' code, there at the check and gone by the read
    r = rd.read_desk(reader_of(w))
    assert r["checks"]["cohorts"]["short_rows"] == 2 and r["checks"]["cohorts"]["ok"] is False
    assert r["complete"] is False


def test_a_crashed_reading_is_never_complete():
    class Broken(MondayReader):
        def read_items(self, jobs):
            raise KeyError("x")

    r = rd.read_desk(Broken(TOKEN, client=httpx.Client(transport=world().transport()), sleep=lambda s: None))
    assert r["error"] == "crashed" and r["complete"] is False and r["checks"] == {}


def test_the_held_reading_gives_its_reader_a_deadline():
    made = []

    def make():
        reader = reader_of(world())
        made.append(reader)
        return reader

    rd.HeldReading(make, deadline_s=7).get()
    assert made[0].deadline is not None and 0 < made[0].deadline - made[0].clock() <= 7


def test_a_reading_exactly_a_minute_old_is_read_again():
    w, clock = world(), Clock()
    held = rd.HeldReading(lambda: reader_of(w), clock=clock)
    held.get()
    clock.t += rd.HELD_TTL_S
    held.get()
    assert len(w.queries_of("columns(ids:")) == 2


def test_an_asset_asked_by_another_version_is_revalidated(desk):
    assert desk.client.get("/desk/desk.js?v=000000000000").headers["Cache-Control"] == "no-cache"


def test_the_pages_etag_follows_its_bytes(desk, tmp_path):
    (tmp_path / "index.html").write_text("<p>a</p>", encoding="utf-8")
    main.app.state.frontend_dir = str(tmp_path)
    first = desk.client.get("/desk/").headers["ETag"]
    (tmp_path / "index.html").write_text("<p>b</p>", encoding="utf-8")
    assert desk.client.get("/desk/").headers["ETag"] != first
