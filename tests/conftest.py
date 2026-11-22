import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.store import Saved  # noqa: E402

# A throwaway Postgres for the store's SQL (CI sets it; locally: any empty database). Without it
# those tests skip, and say so.
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")
needs_pg = pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL not set")


def all_tables() -> list:
    """Every table the migrations make, read from the code (app.migrate), plus the ledger."""
    from app import migrate

    return list(migrate.checked_tables()) + [migrate.LEDGER]


def drop_all(url: str) -> None:
    import psycopg

    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("DROP TABLE IF EXISTS " + ", ".join(all_tables()))


def migration_files() -> list:
    from app import migrate

    return sorted(p.name for p in migrate.MIGRATIONS_DIR.glob("*.sql"))

KEY = "PRIVATEKEY-0123456789abcdef"  # distinctive, so a leak is easy to grep
SESSION = "SESSIONTOKEN-fedcba9876543210"


class FakeWiz:
    """A stand-in for lb19.wizcloud.co.il that records every request.

    The error bodies are the ones seen live on 2026-11-18.
    """

    def __init__(self):
        self.requests = []
        self.session_body = SESSION  # what createSession answers on success
        self.session_status = 200
        self.valid_sessions = {SESSION}
        self.companies = [{"dbName": "wizdb2245n4", "description": "Demo"}, {"dbName": "wizdb2245n5", "description": "אנליזה מודיעין סייבר"}]
        self.triggers = []
        self.issued = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path.lstrip("/")
        if path.startswith("createSession/"):
            _, key, db = path.split("/", 2)
            if key != KEY:
                return httpx.Response(400, text="iligal token")
            self.issued += 1
            return httpx.Response(self.session_status, text=self.session_body)
        auth = request.headers.get("authorization")
        if path == "CompanyListToTokenApi/TokenCompanies":
            if not auth:
                return httpx.Response(500, json={"statusCode": 500, "status": {"errors": "Not connected"}})
            if auth.removeprefix("Bearer ") not in self.valid_sessions:
                return httpx.Response(500, json={"statusCode": 500, "status": {"errors": "Token is not valid"}})
            return httpx.Response(200, json=self.companies)
        if path == "TriggersApi/getURL":
            if not auth:
                return httpx.Response(400, json={"status": "error", "statusCode": 400, "message": "Invalid params, no Authorization key in headers "})
            if auth.removeprefix("Bearer ") not in self.valid_sessions:
                return httpx.Response(400, json={"status": "error", "statusCode": 400, "message": "Invalid token"})
            return httpx.Response(200, json=self.triggers)
        return httpx.Response(404, text="not found")

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class FakeStore:
    """app.store.PgSessionStore's contract, in memory: load(), renewal() with current() and save().
    `rows` is what the database would hold, by (server, db). Set `fail` to a StoreError to make every
    call raise it, or `fail_on_save` to fail only the save (after createSession answered)."""

    kind = "fake"

    def __init__(self):
        self.rows = {}
        self.fail = None
        self.fail_on_save = None
        self.loads = 0
        self.renewals = 0
        self.saves = 0
        self.budgets = []

    def load(self, server, db, budget_s=None):
        self.loads += 1
        self.budgets.append(budget_s)
        if self.fail:
            raise self.fail
        return self.rows.get((server, db))

    @contextmanager
    def renewal(self, server, db, budget_s=None):
        self.renewals += 1
        self.budgets.append(budget_s)
        if self.fail:
            raise self.fail
        store = self

        class R:
            def current(self):
                return store.rows.get((server, db))

            def save(self, token, shape, issued_at):
                if store.fail_on_save:
                    raise store.fail_on_save
                store.saves += 1
                store.rows[(server, db)] = Saved(token=token, shape=shape, issued_at=issued_at)

        yield R()


@pytest.fixture
def fake():
    return FakeWiz()


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def app_client(fake, clock, monkeypatch):
    from fastapi.testclient import TestClient

    from app import main

    monkeypatch.setenv("WIZ_PRIVATE_KEY", KEY)
    monkeypatch.delenv("WIZ_COMPANY", raising=False)
    monkeypatch.delenv("WIZ_SERVER", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    main._cache.update(at=0.0, result=None)
    main._store_cache.update(at=0.0, key=None, value=None)
    main.app.state.transport = fake.transport()
    main.app.state.clock = clock
    main.app.state.client = None
    main.app.state.store_factory = None
    yield TestClient(main.app)
    main.app.state.transport = None
    main.app.state.clock = __import__("time").time
    main.app.state.client = None
    main.app.state.store_factory = None
    main._cache.update(at=0.0, result=None)
    main._store_cache.update(at=0.0, key=None, value=None)


def dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)
