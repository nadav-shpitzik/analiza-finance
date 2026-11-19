"""analiza-finance: the home of finance at Analiza (ADR-0185 in the docs repo; was finance-sync).

Today (slice S0 of backlog 0301) it proves the connection and keeps it:
- GET /health - no call to WizCloud. The build (GitHub main's commit, scripts/deploy_source.py), the
                config (never a secret), the startup migrations, the store and the saved session's age,
                and the last check.
- GET /check  - one live, read-only round: the session (reused from memory or from the store, opened
                only when neither holds one), the companies the token reaches, the webhooks already
                registered. Cached 60 s, so a public link cannot hammer the vendor. 200 when everything
                answered, 503 otherwise, with the reason in a closed vocabulary.
Neither endpoint returns a value from the books: names of companies, and for webhooks only the table
and the receiving host. Neither returns the key, a session string or the database URL.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import logging
import re
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import config
from .wizcloud import READ_PATHS, Session, WizClient, WizError

SERVICE = "analiza-finance"
CHECK_TTL_S = 60
STARTED = time.time()
ROOT = Path(__file__).resolve().parent.parent
logger = logging.getLogger("analiza_finance")


def deploy_build(root: Path = ROOT) -> dict:
    """What the deployment build recorded in build_info.json, through scripts/deploy_source.py's own
    reader, as analiza-metrics does. Never raises: an absent file is source None with the reason."""
    try:
        spec = importlib.util.spec_from_file_location("deploy_source", root / "scripts" / "deploy_source.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.load_build_info(root / module.BUILD_INFO_NAME)
    except Exception as exc:  # noqa: BLE001 - a broken reader must not stop the service
        return {"source": None, "sha": None, "reason": f"scripts/deploy_source.py unreadable: {type(exc).__name__}"}


def startup_migrations(cfg: config.Config) -> dict:
    """migrations/ on the database this instance uses (production, in the deployment). Best effort
    with a trace: a failure is its class name in /health, never the URL, and the service still starts."""
    if not cfg.database_url:
        return {"ran": False, "reason": "DATABASE_URL is not set"}
    try:
        from .migrate import apply_migrations

        return {"ran": True, **apply_migrations(cfg.database_url), "error": None}
    except Exception as exc:  # noqa: BLE001 - class name only, never the URL
        logger.error("startup migration failed (%s)", type(exc).__name__)
        return {"ran": True, "error": type(exc).__name__}


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.build = deploy_build()
    app.state.migrations = startup_migrations(config.load())
    yield


app = FastAPI(title=SERVICE, docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
app.state.transport = None  # tests put an httpx.MockTransport here
app.state.clock = time.time
app.state.store_factory = None  # tests put a fake here; None is Postgres when DATABASE_URL is set
app.state.client = None
app.state.client_ident = None
app.state.build = None
app.state.migrations = None

_lock = threading.Lock()
_client_lock = threading.Lock()
_cache: dict = {"at": 0.0, "result": None}


def _now_iso(ts: Optional[float] = None) -> str:
    return datetime.fromtimestamp(ts if ts is not None else time.time(), tz=timezone.utc).isoformat(timespec="seconds")


def _store_for(cfg: config.Config, state: Any) -> Any:
    if state.store_factory is not None:
        return state.store_factory(cfg)
    if cfg.database_url and cfg.private_key:
        from .store import PgSessionStore

        return PgSessionStore(cfg.database_url, cfg.private_key)
    return None


def get_client(state: Any, cfg: config.Config) -> WizClient:
    """One client per process, so its session outlives a request. A new one when the config changes."""
    secrets = hashlib.sha256(f"{cfg.private_key}\0{cfg.database_url}".encode("utf-8")).hexdigest()
    ident = (cfg.server, cfg.db, secrets, id(state.transport), id(state.clock), id(state.store_factory))
    with _client_lock:
        if state.client is None or state.client_ident != ident:
            state.client = WizClient(
                cfg.server, cfg.db or "", cfg.private_key, transport=state.transport, clock=state.clock,
                store=_store_for(cfg, state),
            )
            state.client_ident = ident
        return state.client


@app.get("/")
def root() -> dict:
    return {"service": SERVICE, "endpoints": ["/health", "/check"]}


@app.get("/health")
def health(request: Request) -> dict:
    cfg = config.load()
    state = request.app.state
    last = _cache["result"]
    return {
        "service": SERVICE,
        "ok": True,
        "uptime_s": int(time.time() - STARTED),
        "build": state.build if state.build is not None else deploy_build(),
        "read_only": True,
        "read_paths": sorted(READ_PATHS),
        "config": {
            "private_key_set": bool(cfg.private_key),
            "server": cfg.server,
            "company": cfg.company,
            "db": cfg.db,
            "database_url_set": bool(cfg.database_url),
            "problems": list(cfg.problems),
        },
        "migrations": state.migrations if state.migrations is not None else {"ran": False, "reason": "no startup in this process"},
        "store": store_health(cfg, state.clock()),
        "last_check": None
        if last is None
        else {"ok": last["ok"], "checked_at": last["checked_at"], "error": last.get("error")},
    }


def store_health(cfg: config.Config, now: float) -> dict:
    """The tables, what the writes need and the database lacks, and the saved session's age. Never
    the session, never the URL; a failure is a kind or a class name."""
    if not cfg.database_url:
        return {"configured": False, "reason": "DATABASE_URL is not set: the session is kept in memory only"}
    try:
        from .migrate import schema_state
        from .store import TABLE, describe_saved

        state = schema_state(cfg.database_url)
        session = None
        if state["tables"].get(TABLE) == "present" and cfg.db:
            session = describe_saved(cfg.database_url, cfg.server, cfg.db, now)
    except Exception as exc:  # noqa: BLE001 - said, never raised past /health
        return {"configured": True, "ok": False, "error": getattr(exc, "kind", None) or type(exc).__name__}
    return {"configured": True, "ok": not state["missing_columns"], **state, "session": session}


@app.get("/check")
def check(request: Request) -> JSONResponse:
    state = request.app.state
    clock = state.clock
    with _lock:
        cached = _cache["result"]
        if cached is not None and clock() - _cache["at"] < CHECK_TTL_S:
            result = dict(cached, cached=True)
        else:
            cfg = config.load()
            client = get_client(state, cfg) if cfg.ok else None
            result = run_check(cfg, transport=state.transport, clock=clock, client=client)
            _cache.update(at=clock(), result=result)
            result = dict(result, cached=False)
    return JSONResponse(result, status_code=200 if result["ok"] else 503)


def session_report(sess: Session, held: bool, store_kind: Optional[str], now: float) -> dict:
    """How this round got its session, never the session: `source` is memory (this process held
    it), saved (read from the store) or new (opened now); `saved` is whether the store holds it."""
    out = {
        "ok": True,
        "shape": sess.shape,
        "length": len(sess.token),
        "source": "memory" if held else sess.origin,
        "issued_at": _now_iso(sess.issued_at),
        "age_s": int(now - sess.issued_at),
        "store": store_kind or "none",
        "saved": bool(sess.saved),
    }
    if sess.store_error:
        out["store_error"] = sess.store_error
    if sess.note:
        out["note"] = sess.note
    return out


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
        held = client.holds_session()
        sess = client.session()
        out["session"] = session_report(sess, held, getattr(client.store, "kind", None), clock())
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


def _maybe_json(value: Any) -> Any:
    """WizCloud sometimes nests JSON inside a string field; open it if so."""
    if isinstance(value, str) and value.strip()[:1] in ("[", "{"):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def _items(data: Any, depth: int = 3) -> Optional[list]:
    """The first list inside an answer, searched breadth-first a few levels down."""
    data = _maybe_json(data)
    if isinstance(data, list):
        return data
    if not isinstance(data, dict) or depth == 0:
        return None
    for key in ("repdata", "data", "companies", "rows", "result", "items", "status"):
        inner = _maybe_json(data.get(key))
        if isinstance(inner, list):
            return inner
    for value in data.values():
        value = _maybe_json(value)
        if isinstance(value, list):
            return value
    for value in data.values():
        found = _items(value, depth - 1)
        if found is not None:
            return found
    return None


def shape_tree(data: Any, depth: int = 3) -> Any:
    """Keys and types only, for an answer whose layout is not known yet. No values."""
    data = _maybe_json(data)
    if isinstance(data, dict):
        if depth == 0:
            return f"dict({len(data)})"
        return {str(k): shape_tree(v, depth - 1) for k, v in list(data.items())[:30]}
    if isinstance(data, list):
        first = shape_tree(data[0], depth - 1) if data and depth else None
        return {"list": len(data), "first": first}
    if isinstance(data, str):
        return f"str({len(data)})"
    return type(data).__name__


def _fields(items: list) -> list:
    keys: set = set()
    for item in items[:5]:
        if isinstance(item, dict):
            keys.update(map(str, item.keys()))
    return sorted(keys)


def summarize_companies(data: Any, our_db: Optional[str]) -> dict:
    items = _items(data)
    if items is None:
        return {"ok": True, "shape": type(data).__name__, "tree": shape_tree(data)}
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
        return {"ok": True, "shape": type(data).__name__, "tree": shape_tree(data)}
    found = []
    for item in items:
        if not isinstance(item, dict):
            continue
        url = next((v for k, v in item.items() if str(k).lower() == "url" and isinstance(v, str)), None)
        table = next((v for k, v in item.items() if str(k).lower() == "table" and isinstance(v, str)), None)
        found.append({"table": table, "host": urlparse(url).hostname if url else None})
    return {"ok": True, "count": len(items), "fields": _fields(items), "items": found}
