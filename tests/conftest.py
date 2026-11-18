import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

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
    main._cache.update(at=0.0, result=None)
    main.app.state.transport = fake.transport()
    main.app.state.clock = clock
    yield TestClient(main.app)
    main.app.state.transport = None
    main._cache.update(at=0.0, result=None)


def dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)
