"""The adversarial review of R0 (22.11, on 78f6315): each test asserts what the code claims, and failed there. Kept as the
regression tests of its eight findings."""

import gzip
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import psycopg
import pytest

from app import main
from app.monday.client import MondayReader
from app.recon import reading as rr
from app.recon import store as rs
from tests.conftest import needs_pg
from tests.test_recon_reading import SYNC_KEY, TOKEN, Monday, pg, reader, recon_app  # noqa: F401 - fixtures

AUTH = {"Authorization": f"Bearer {SYNC_KEY}"}


# ---- F1: the completeness check counts rows, never which rows --------------------------------------------------
class SwappedId(Monday):
    """items(ids:) answers one asked id with another item (an id that is not on the board)."""

    def handler(self, request):
        resp = super().handler(request)
        body = json.loads(request.content)
        if "items(ids:" in body["query"] and str(self.ids(rr.ACCOUNTS)[3]) in body["variables"]["ids"]:
            data = json.loads(resp.content)
            for it in data["data"]["items"]:
                if it["id"] == str(self.ids(rr.ACCOUNTS)[3]):
                    it["id"] = "999999999"  # not on the Accounts board at all
            return httpx.Response(200, json=data)
        return resp


def test_f1_a_row_that_is_not_one_of_the_boards_ids_is_not_complete():
    fake = SwappedId()
    got = rr.read_profile(reader(fake))
    rows = {r["id"] for r in got["boards"]["accounts"]}
    paged = {str(i) for i in fake.ids(rr.ACCOUNTS)}
    assert rows != paged  # the board's item 3 was never read, a stranger stands in its place
    assert got["complete"] is False, "complete although an id of the board was never read"


class MovedItem(Monday):
    """An item moved to another board between its id and its row: items(ids:) still answers it (it reads by id from any
    board), and monday drops the column ids that board lacks (backlog 0234), so the row comes back with no cells."""

    def handler(self, request):
        resp = super().handler(request)
        body = json.loads(request.content)
        moved = str(self.ids(rr.STUDENT_CHARGE)[7])
        if "items(ids:" in body["query"] and moved in body["variables"]["ids"]:
            data = json.loads(resp.content)
            for it in data["data"]["items"]:
                if it["id"] == moved:
                    it["column_values"] = []
            return httpx.Response(200, json=data)
        return resp


def test_f1b_an_item_read_by_id_from_another_board_is_not_a_whole_board():
    fake = MovedItem()
    got = rr.read_profile(reader(fake))
    row = next(r for r in got["boards"]["student_charge"] if r["id"] == str(fake.ids(rr.STUDENT_CHARGE)[7]))
    assert rr.SC["invoice"] not in row  # the row carries none of the board's columns
    assert got["complete"] is False, "complete with a row that has none of the board's columns"


# ---- F2: two boards under one key hide the first one's check -----------------------------------------------------
def test_f2_a_profile_key_used_twice_cannot_hide_a_short_board():
    fake = Monday()
    fake.count_off[rr.ACCOUNTS] = 5  # Accounts reads short
    profile = (rr.Board("accounts", rr.ACCOUNTS, rr.PROFILE[2].columns),
               rr.Board("accounts", rr.DEALS, rr.PROFILE[3].columns))
    got = rr.read_profile(reader(fake), profile=profile)
    assert got["complete"] is False, "the short Accounts check was overwritten by the Deals check"


def test_f2b_the_profile_keys_are_unique():
    keys = [b.key for b in rr.PROFILE]
    assert len(keys) == len(set(keys))  # holds today; nothing in the suite says so (F2 is latent)


# ---- F3: a reading that fails outside MondayError leaves the last complete row in place -------------------------
class CorruptGzip(Monday):
    """The by-id answers come back with a gzip Content-Encoding and a body that is not gzip (a proxy or a CDN
    hiccup): httpx raises DecodingError, which is not a TransportError."""

    def handler(self, request):
        resp = super().handler(request)
        if "items(ids:" in json.loads(request.content)["query"]:
            return httpx.Response(200, headers={"Content-Encoding": "gzip"}, content=b"this is not gzip")
        return resp


@needs_pg
def test_f3_a_crashed_reading_replaces_the_last_complete_one(recon_app):
    client, _fake = recon_app
    first = client.post("/api/recon/run", headers=AUTH).json()
    assert first["complete"] is True
    main.app.state.monday_transport = httpx.MockTransport(CorruptGzip().handler)
    second = client.post("/api/recon/run", headers=AUTH).json()
    print("second run answered:", second)
    kept = rs.load_reading(main.config.load().database_url)
    print("stored row: complete=%s error=%s read_at=%s" % (kept["complete"], kept["error"], kept["read_at"]))
    assert rr.why_not_ready("analiza", kept) is not None, (
        "the last reading crashed, yet the stored reading is the earlier complete one and the guard lets a rule run")


@needs_pg
def test_f3b_a_nul_in_a_cell_is_dropped_so_the_reading_is_kept(recon_app):
    """A NUL in one monday text cell: jsonb refuses \\u0000 (22P05), and the save failed every hour while it stayed.
    The reader drops NUL from text (it never means anything there), so the reading is kept."""
    client, fake = recon_app
    assert client.post("/api/recon/run", headers=AUTH).json()["complete"] is True

    class Nul(Monday):
        def handler(self, request):
            resp = super().handler(request)
            if "items(ids:" in json.loads(request.content)["query"]:
                data = json.loads(resp.content)
                data["data"]["items"][0]["name"] = "שם\u0000"
                return httpx.Response(200, json=data)
            return resp

    main.app.state.monday_transport = httpx.MockTransport(Nul().handler)
    second = client.post("/api/recon/run", headers=AUTH).json()
    kept = rs.load_reading(main.config.load().database_url)
    assert second["complete"] is True and kept["read_at"] == second["read_at"]
    assert rr.why_not_ready("analiza", kept) is None
    assert not any("\x00" in r["name"] for rows in kept["boards"].values() for r in rows)


@needs_pg
def test_f3c_a_reading_the_store_refuses_replaces_the_last_complete_one(recon_app, monkeypatch):
    client, _fake = recon_app
    assert client.post("/api/recon/run", headers=AUTH).json()["complete"] is True
    real, calls = rs.save_reading, []

    def refusing(url, reading, budget_s=None):
        calls.append(reading.get("error"))
        if len(calls) == 1:
            raise rs.ReconStoreError("22P05")  # what jsonb answers to a value it cannot hold
        return real(url, reading, budget_s=budget_s)

    monkeypatch.setattr(rs, "save_reading", refusing)
    second = client.post("/api/recon/run", headers=AUTH).json()
    kept = rs.load_reading(main.config.load().database_url)
    assert calls == [None, "store"] and second["error"] == "store" and second["ok"] is False
    assert kept["error"] == "store" and kept["boards"] == {} and rr.why_not_ready("analiza", kept) == "failed"


# ---- F4: the deadline does not bound a request ---------------------------------------------------------------------
class Trickle:
    """A real HTTP server on 127.0.0.1 that answers as the Monday fake does, but sends the body of the queries `slow`
    picks one small piece at a time, each gap under the client's timeout. httpx's timeout is per read, not per request."""

    def __init__(self, fake, slow, gap=0.25, pieces=20):
        self.fake, self.slow, self.gap, self.pieces = fake, slow, gap, pieces
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                raw = self.rfile.read(int(self.headers["Content-Length"]))
                req = httpx.Request("POST", "http://local/v2", headers=dict(self.headers), content=raw)
                resp = outer.fake.handler(req)
                content = resp.content
                self.send_response(resp.status_code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.flush()
                if outer.slow(json.loads(raw)["query"]):
                    step = max(1, len(content) // outer.pieces + 1)
                    for i in range(0, len(content), step):
                        self.wfile.write(content[i:i + step])
                        self.wfile.flush()
                        time.sleep(outer.gap)
                else:
                    self.wfile.write(content)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


class ToLocal(httpx.BaseTransport):
    """The route's reader always posts to api.monday.com: rewrite to the local server, through a REAL transport, so
    httpx's own timeouts apply (a MockTransport enforces none)."""

    def __init__(self, port):
        self.inner, self.port = httpx.HTTPTransport(), port

    def handle_request(self, request):
        request.url = request.url.copy_with(scheme="http", host="127.0.0.1", port=self.port)
        return self.inner.handle_request(request)


def test_f4_no_request_waits_past_the_deadline():
    server = Trickle(Monday(), slow=lambda q: "columns(ids:" in q)
    try:
        r = MondayReader(TOKEN, client=httpx.Client(trust_env=False), api_url=f"http://127.0.0.1:{server.port}/v2")
        r.deadline = time.monotonic() + 1.0
        t0 = time.monotonic()
        got = rr.read_profile(r)
        over = time.monotonic() - r.deadline
        print(f"reading took {time.monotonic() - t0:.1f}s, {over:.1f}s past its deadline; error={got['error']}")
        assert over < 0.6, f"a request waited {over:.1f}s past the deadline (the timeout was at most 1 s)"
    finally:
        server.close()


@needs_pg
def test_f4b_the_route_answers_inside_its_box(recon_app, monkeypatch):
    client, fake = recon_app
    monkeypatch.setattr(main, "REQUEST_BOX_S", 3)  # the 60 s box scaled down: the arithmetic is the same
    monkeypatch.setattr(main, "FINISH_RESERVE_S", 1)
    server = Trickle(fake, slow=lambda q: "items_page" in q and "next_items_page" not in q, gap=0.25, pieces=24)
    try:
        main.app.state.monday_transport = ToLocal(server.port)
        t0 = time.monotonic()
        body = client.post("/api/recon/run", headers=AUTH).json()
        took = time.monotonic() - t0
        print(f"the route answered after {took:.1f}s (box {main.REQUEST_BOX_S}s): error={body.get('error')} "
              f"request_s={body.get('request_s')}")
        assert took <= main.REQUEST_BOX_S, f"answered after {took:.1f}s, past its {main.REQUEST_BOX_S}s box"
    finally:
        server.close()


# ---- F5: the token is never in an error ----------------------------------------------------------------------------
@needs_pg
def test_f5_a_vendor_message_that_echoes_the_token_never_reaches_the_answer_or_the_row(recon_app):
    client, fake = recon_app
    fake.fail = [httpx.Response(200, json={"errors": [{"message": f"Invalid token {TOKEN} for this account"}]})]
    run = client.post("/api/recon/run", headers=AUTH).json()
    status = client.get("/api/recon/status", headers=AUTH).json()
    with psycopg.connect(main.config.load().database_url) as conn:
        stored = conn.execute("SELECT detail FROM recon_readings").fetchone()[0]
    print("run:", run["error"], run["detail"])
    leaks = [where for where, text in (("run", json.dumps(run)), ("status", json.dumps(status)), ("row", stored or ""))
             if TOKEN in text]
    assert not leaks, f"the token is in: {leaks}"


# ---- F6: the guard trusts a flag, not the reading ------------------------------------------------------------------
@needs_pg
def test_f6_the_guard_refuses_a_reading_that_lacks_a_board_of_the_profile(pg):
    """A reading kept under the name 'recon' by an older PROFILE (a board added in a later deploy), or any reading without
    its boards, is still `complete` to the guard."""
    older = rr.read_profile(reader(Monday()), profile=rr.PROFILE[:4])  # no vouchers board
    rs.save_reading(pg, older)
    kept = rs.load_reading(pg)
    assert "vouchers" not in kept["boards"]
    assert rr.why_not_ready("analiza", kept) is not None, "ready, with a board of the profile never read"


def test_f6b_the_guard_refuses_a_reading_without_rows_or_checks():
    assert rr.why_not_ready("analiza", {"complete": True}) is not None, "ready on a bare flag"


# ---- F7: the advisory lock lives on one connection; when it drops, the lock is gone and the saved reading says "store"
class KillLock(Monday):
    def __init__(self, url):
        super().__init__()
        self.url, self.second, self.killed = url, None, 0

    def handler(self, request):
        if "columns(ids:" in json.loads(request.content)["query"]:
            with psycopg.connect(self.url, autocommit=True) as c:  # a network blip, a pooler restart, an idle timeout
                self.killed = c.execute("SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity "
                                        "WHERE query LIKE 'SELECT pg_try_advisory_xact_lock%' AND pid <> pg_backend_pid()"
                                        ).fetchone()[0]
            with rs.reading_lock(self.url) as second:  # a second /api/recon/run arriving now
                self.second = second
        return super().handler(request)


@needs_pg
def test_f7_a_lock_connection_that_drops_is_no_error_once_the_reading_is_kept(recon_app):
    """The lock lives on a connection of its own; when that drops, the lock goes with it. A second reading may then start:
    both read every board whole and the later save wins - judged harmless, and said in reading_lock's docstring. What was
    wrong is the answer: the reading was kept, complete, and the route said `store`."""
    client, _fake = recon_app
    fake = KillLock(main.config.load().database_url)
    main.app.state.monday_transport = httpx.MockTransport(fake.handler)
    body = client.post("/api/recon/run", headers=AUTH).json()
    kept = rs.load_reading(main.config.load().database_url)
    assert fake.killed == 1 and fake.second is True  # the harmless half, pinned so a change to it is seen
    assert body.get("error") is None and body["complete"] is True and kept["complete"] is True


# ---- sanity (passes): an incomplete reading does replace a complete one today (M10 shows no test holds it)
@needs_pg
def test_sanity_an_incomplete_reading_replaces_a_complete_one(pg):
    rs.save_reading(pg, rr.read_profile(reader(Monday())))
    fake = Monday()
    fake.count_off[rr.DEALS] = 1
    rs.save_reading(pg, rr.read_profile(reader(fake)))
    assert rr.why_not_ready("analiza", rs.load_reading(pg)) == "incomplete"


# ---- F8: /health's recon block shares the books glance's transaction
@needs_pg
def test_f8_a_books_glance_that_fails_in_sql_does_not_blank_the_recon_glance(recon_app, monkeypatch):
    client, _fake = recon_app
    assert client.post("/api/recon/run", headers=AUTH).json()["complete"] is True
    monkeypatch.setenv("WIZ_COMPANY", "demo")

    def slow_books(cur, company):  # what a statement timeout on books_documents does to the transaction
        cur.execute("SELECT 1/0")

    monkeypatch.setattr(main, "_books_glance", slow_books)
    main._store_cache.update(at=0.0, key=None, value=None)
    health = client.get("/health").json()
    print("books:", health["books"], "| recon:", health["recon"])
    assert health["recon"]["last_reading"].get("complete") is True, health["recon"]
