"""finance-sync: the home of the Hashavshevet (WizCloud) integration.

v0 proves the connection and nothing else:
- GET /health  - no call to WizCloud. Build, config (never a secret), last check.
- GET /check   - one live, read-only round: createSession, the companies the token
                 reaches, the webhooks already registered. Cached 60 s, so a
                 public link cannot hammer the vendor. 200 when everything
                 answered, 503 otherwise, with the reason in a closed vocabulary.
Neither endpoint returns a value from the books: names of companies, and for
webhooks only the table and the receiving host.
"""

from __future__ import annotations

import re
import threading
import time
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import config
from .wizcloud import READ_PATHS, WizClient, WizError

CHECK_TTL_S = 60
STARTED = time.time()

app = FastAPI(title="finance-sync", docs_url=None, redoc_url=None, openapi_url=None)
app.state.transport = None  # tests put an httpx.MockTransport here
app.state.clock = time.time

_lock = threading.Lock()
_cache: dict = {"at": 0.0, "result": None}


def _now_iso(ts: Optional[float] = None) -> str:
    return datetime.fromtimestamp(ts if ts is not None else time.time(), tz=timezone.utc).isoformat(timespec="seconds")


@app.get("/")
def root() -> dict:
    return {"service": "finance-sync", "endpoints": ["/health", "/check"]}


@app.get("/health")
def health() -> dict:
    cfg = config.load()
    last = _cache["result"]
    return {
        "service": "finance-sync",
        "ok": True,
        "uptime_s": int(time.time() - STARTED),
        "build": config.build_info(),
        "read_only": True,
        "read_paths": sorted(READ_PATHS),
        "config": {
            "private_key_set": bool(cfg.private_key),
            "server": cfg.server,
            "company": cfg.company,
            "db": cfg.db,
            "problems": list(cfg.problems),
        },
        "last_check": None
        if last is None
        else {"ok": last["ok"], "checked_at": last["checked_at"], "error": last.get("error")},
    }


@app.get("/check")
def check(request: Request) -> JSONResponse:
    clock = request.app.state.clock
    with _lock:
        cached = _cache["result"]
        if cached is not None and clock() - _cache["at"] < CHECK_TTL_S:
            result = dict(cached, cached=True)
        else:
            result = run_check(config.load(), transport=request.app.state.transport, clock=clock)
            _cache.update(at=clock(), result=result)
            result = dict(result, cached=False)
    return JSONResponse(result, status_code=200 if result["ok"] else 503)


def run_check(cfg: config.Config, transport=None, clock=time.time, client: Optional[WizClient] = None) -> dict:
    out: dict = {
        "ok": False,
        "checked_at": _now_iso(clock()),
        "server": cfg.server,
        "company": {"key": cfg.company, "db": cfg.db},
    }
    if not cfg.ok:
        out["error"] = {"ok": False, "kind": "config_missing", "message": "; ".join(cfg.problems)}
        return out
    client = client or WizClient(cfg.server, cfg.db, cfg.private_key, transport=transport, clock=clock)
    try:
        sess = client.session()
        out["session"] = {"ok": True, "shape": sess.shape, "length": len(sess.token)}
    except WizError as exc:
        out["session"] = out["error"] = exc.as_dict()
        return out
    for name, fetch, summarize in (
        ("companies", client.companies, lambda d: summarize_companies(d, cfg.db)),
        ("triggers", client.triggers, summarize_triggers),
    ):
        try:
            out[name] = summarize(fetch())
        except WizError as exc:
            out[name] = exc.as_dict()
            out.setdefault("error", exc.as_dict())
    out["ok"] = all(out[n].get("ok") is True for n in ("companies", "triggers"))
    return out


# ---- summaries: shapes and names only, never a value from the books --------------

_WIZDB = re.compile(r"^wizdb\w+$", re.IGNORECASE)


def _items(data: Any) -> Optional[list]:
    """The list inside an answer: the answer itself, or its first list value."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("repdata", "data", "companies", "rows", "result", "items"):
            if isinstance(data.get(key), list):
                return data[key]
        for value in data.values():
            if isinstance(value, list):
                return value
    return None


def _fields(items: list) -> list:
    keys: set = set()
    for item in items[:5]:
        if isinstance(item, dict):
            keys.update(map(str, item.keys()))
    return sorted(keys)


def summarize_companies(data: Any, our_db: Optional[str]) -> dict:
    items = _items(data)
    if items is None:
        return {"ok": True, "shape": type(data).__name__, "keys": sorted(map(str, data))[:30] if isinstance(data, dict) else None}
    found = []
    for item in items:
        if not isinstance(item, dict):
            continue
        db = next((str(v) for v in item.values() if isinstance(v, str) and _WIZDB.match(v.strip())), None)
        name = next(
            (str(v) for k, v in item.items() if isinstance(v, str) and v.strip() and re.search(r"name|desc|company", str(k), re.I)
             and not _WIZDB.match(v.strip())),
            None,
        )
        found.append({"db": db, "name": name})
    return {
        "ok": True,
        "count": len(items),
        "fields": _fields(items),
        "items": found,
        "includes_configured_db": any(f["db"] == our_db for f in found) if found else None,
    }


def summarize_triggers(data: Any) -> dict:
    items = _items(data)
    if items is None:
        if data in (None, "", {}):
            return {"ok": True, "count": 0, "items": []}
        return {"ok": True, "shape": type(data).__name__, "keys": sorted(map(str, data))[:30] if isinstance(data, dict) else None}
    found = []
    for item in items:
        if not isinstance(item, dict):
            continue
        url = next((v for k, v in item.items() if str(k).lower() == "url" and isinstance(v, str)), None)
        table = next((v for k, v in item.items() if str(k).lower() == "table" and isinstance(v, str)), None)
        found.append({"table": table, "host": urlparse(url).hostname if url else None})
    return {"ok": True, "count": len(items), "fields": _fields(items), "items": found}
