"""analiza-finance: the home of finance at Analiza (ADR-0185 in the docs repo; was finance-sync).

Today (slice S0 of backlog 0301) it proves the connection and keeps it:
- GET /health - no call to WizCloud. The build (GitHub main's commit, scripts/deploy_source.py), the
                config (never a secret), the store (its schema as the publish left it, and the saved
                session's age; read at most every STORE_TTL_S), and the last check.
- GET /check  - one live, read-only round: the session (reused from memory or from the store, opened
                only when neither holds one), the companies the token reaches, the webhooks already
                registered. Cached 60 s, so a public link cannot hammer the vendor. 200 when everything
                answered, 503 otherwise, with the reason in a closed vocabulary.
Neither endpoint returns a value from the books: names of companies, and for webhooks only the table
and the receiving host. Neither returns the key, a session string or the database URL.
"""

from __future__ import annotations

import hashlib
import hmac
import importlib.util
import json
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    # No migration here, by design: production's schema comes only from a Republish (app/migrate.py).
    app.state.build = deploy_build()
    yield


app = FastAPI(title=SERVICE, docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
app.state.transport = None  # tests put an httpx.MockTransport here
app.state.clock = time.time
app.state.store_factory = None  # tests put a fake here; None is Postgres when DATABASE_URL is set
app.state.client = None
app.state.client_ident = None
app.state.build = None

STORE_TTL_S = 15  # /health is open: at most one database connection per this many seconds, however often it is hit
# One live round at a time, and one store read at a time: a request that finds one running gets the
# last answer marked in_progress (or stale) instead of waiting in the threadpool (review 19.11).
_lock = threading.Lock()
_client_lock = threading.Lock()
_store_lock = threading.Lock()  # guards _store_cache only, never held across the read
_store_read = threading.Lock()  # held by the one request that reads the store
_cache: dict = {"at": 0.0, "result": None}
_store_cache: dict = {"at": 0.0, "key": None, "value": None}


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
    store = store_health(cfg, state.clock())
    books = store.pop("books_glance", None)
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
        "store": store,
        "books": {"sync_key": sync_key_state(cfg), **(books or {})},
        "last_check": None
        if last is None
        else {"ok": last["ok"], "checked_at": last["checked_at"], "error": last.get("error")},
    }


def _read_store(cfg: config.Config) -> dict:
    """One connection: the schema as it stands and the saved session's times. Raises on failure."""
    import psycopg

    from .migrate import CONNECT_TIMEOUT_S, schema_state
    from .store import READ_LOCK_TIMEOUT, READ_STATEMENT_TIMEOUT, TABLE, saved_times

    with psycopg.connect(cfg.database_url, connect_timeout=CONNECT_TIMEOUT_S) as conn, conn.cursor() as cur:
        # A table locked by someone else ends this read instead of hanging it (transaction-scoped, so a
        # pooled connection keeps nothing).
        cur.execute(f"SET LOCAL lock_timeout = '{READ_LOCK_TIMEOUT}'")
        cur.execute(f"SET LOCAL statement_timeout = '{READ_STATEMENT_TIMEOUT}'")
        state = schema_state(cur)
        times = saved_times(cur, cfg.server, cfg.db) if state["tables"].get(TABLE) == "present" and cfg.db else None
        books = None
        if state["tables"].get("books_sync_runs") == "present" and cfg.db:
            try:  # its own try: a books problem never blanks the store block (review 22.11)
                books = _books_glance(cur, cfg.db)
            except Exception as exc:  # noqa: BLE001 - the class only
                books = {"error": type(exc).__name__}
    return {**state, "times": times, "books": books}


def _books_glance(cur, company: str) -> dict:
    """For the public /health: whether the last run worked and why not (a closed vocabulary), and how many numbers
    are missing - no count of documents and no number (Nadav 19.11: the deployment is public; the detail is behind
    the sync key)."""
    from .books.store import summarize_series

    cur.execute(
        "SELECT finished_at, ok, complete, reasons FROM books_sync_runs WHERE company = %s ORDER BY run_id DESC LIMIT 1",
        (company,),
    )
    row = cur.fetchone()
    cur.execute("SELECT doc_type, doc_number, status FROM books_documents WHERE company = %s AND gone_at IS NULL", (company,))
    series = summarize_series(cur.fetchall())
    last = None if row is None else {
        "finished_at": row[0].isoformat(timespec="seconds") if row[0] else None, "ok": row[1], "complete": row[2],
        "reasons": list(row[3] or [])}
    return {"last_run": last, "holes": sum(x["holes"] for x in series), "series": len(series)}


def store_health(cfg: config.Config, now: float) -> dict:
    """The tables, what the writes need and the database lacks, and the saved session's age. Never
    the session, never the URL; a failure is a class name. Read at most once every STORE_TTL_S, by
    one request at a time: the others meanwhile get the last read marked stale."""
    if not cfg.database_url:
        return {"configured": False, "reason": "DATABASE_URL is not set: the session is kept in memory only"}
    key = (hashlib.sha256(cfg.database_url.encode("utf-8")).hexdigest(), cfg.server, cfg.db)
    stale = False
    with _store_lock:
        mine = _store_cache["key"] == key and _store_cache["value"] is not None
        fresh = mine and 0 <= now - _store_cache["at"] < STORE_TTL_S
        value, read_at = (_store_cache["value"], _store_cache["at"]) if mine else (None, now)
    if not fresh:
        if _store_read.acquire(blocking=False):
            try:
                try:
                    value = {"read": _read_store(cfg)}
                except Exception as exc:  # noqa: BLE001 - said, never raised past /health, never the text
                    value = {"error": type(exc).__name__}
                read_at = now
                with _store_lock:
                    _store_cache.update(at=now, key=key, value=value)
            finally:
                _store_read.release()
        elif value is None:
            return {"configured": True, "ok": None, "reason": "another request is reading the store"}
        else:
            stale = True
    try:
        from .store import describe

        if "error" in value:
            out = {"configured": True, "ok": False, "error": value["error"], "read_at": _now_iso(read_at)}
        else:
            read = value["read"]
            out = {
                "configured": True,
                "ok": not read["missing_columns"],
                "tables": read["tables"],
                "missing_columns": read["missing_columns"],
                "migrations": read["migrations"],
                "session": describe(read["times"], now),
                "read_at": _now_iso(read_at),
                "books_glance": read.get("books"),
            }
    except Exception as exc:  # noqa: BLE001 - /health never raises
        out = {"configured": True, "ok": False, "error": type(exc).__name__}
    if stale:
        out["stale"] = True
    return out


# ---- the books copy (backlog 0301 B1): behind the sync key ----------------------------------------

# The whole request is boxed at REQUEST_BOX_S, the session included (spec §2.3; Make's window is 300 s): the run gets
# what is left after the database's connection and lock, less FINISH_RESERVE_S for its log row, the verdict's
# queries and the answer.
REQUEST_BOX_S = 60
FINISH_RESERVE_S = 4
# What Make posts to Discord when `alert` is true: one line, counts only, never a number or a name from the books.
REASON_TEXT = {
    "wizcloud_failing": "WizCloud לא עונה: שלוש קריאות נכשלו ברצף",
    "canary": "מסמך הביקורת לא נקרא, אז שום 'לא נמצא' לא נרשם בסבב",
    "holes": "חסרים {holes} מספרים בסדרות המסמכים",
    "missing_valid": "{missing_valid} מסמכים תקפים לא נמצאים בספרים (נשארים בכל חישוב עד שאדם מאשר)",
    "stuck": "{stuck_day} מזהים נכשלים בקריאה כבר יותר מיממה",
    "crashed": "הסבב נפל באמצע",
}


SYNC_KEY_MIN = 24


def sync_key_state(cfg: config.Config) -> str:
    """'missing', 'too_short' or 'ok': what /health says, and what the routes act on (review 22.11: /health
    said 'set' for a key the routes refused)."""
    if not cfg.books_sync_key:
        return "missing"
    return "too_short" if len(cfg.books_sync_key) < SYNC_KEY_MIN else "ok"


def _sync_key_refusal(request: Request, cfg: config.Config) -> Optional[JSONResponse]:
    state = sync_key_state(cfg)
    if state == "missing":
        return JSONResponse({"ok": False, "error": "BOOKS_SYNC_KEY is not set"}, status_code=503)
    if state == "too_short":
        return JSONResponse({"ok": False, "error": f"BOOKS_SYNC_KEY is too short (at least {SYNC_KEY_MIN} characters)"}, status_code=503)
    sent = request.headers.get("authorization", "")
    expected = f"Bearer {cfg.books_sync_key}"
    if not hmac.compare_digest(sent.encode("utf-8"), expected.encode("utf-8")):
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
    if not cfg.database_url:
        return JSONResponse({"ok": False, "error": "DATABASE_URL is not set"}, status_code=503)
    if not cfg.ok:
        return JSONResponse({"ok": False, "error": "config: " + "; ".join(cfg.problems)}, status_code=503)
    return None


def _books_store(cfg: config.Config):
    from .books.store import BooksStore

    return BooksStore(cfg.database_url)


def tick_message(company: str, summary: dict) -> str:
    """The line Make posts to Discord: the reasons in Hebrew, or the way back to ok (only when it is one: an alert)."""
    head = f"ספרי חשבשבת ({company})"
    if not summary.get("reasons"):
        return f"{head}: חזר לתקין." if summary.get("alert") else f"{head}: תקין."
    parts = [REASON_TEXT.get(r, r).format(**{k: summary.get(k) for k in ("holes", "missing_valid", "stuck_day")})
             for r in summary["reasons"]]
    return f"{head}: " + "; ".join(parts) + "."


@app.post("/api/books/tick")
def books_tick(request: Request) -> JSONResponse:
    """One tick of the books copy for the configured company, boxed at REQUEST_BOX_S from the request's arrival. Always
    200, as Make needs (spec §2.3): ok false with `reasons` when WizCloud fails or the copy has a problem, `skipped`
    when another tick of the company holds the lock, and `alert` (with `message`, one Hebrew line) when the reasons
    changed since the last tick - what Make posts to Discord. Counts only, no values."""
    started = time.monotonic()
    cfg = config.load()
    refusal = _sync_key_refusal(request, cfg)
    if refusal is not None:
        return refusal
    from .books.sync import run_once

    state = request.app.state
    try:
        with _books_store(cfg).session(cfg.db) as store:
            if store is None:
                return JSONResponse({"ok": True, "skipped": True, "alert": False,
                                     "reason": "another run of this company is in progress"})
            box = REQUEST_BOX_S - FINISH_RESERVE_S - (time.monotonic() - started)
            summary = run_once(get_client(state, cfg), store, cfg.db, trigger="http", box_s=box)
    except Exception as exc:  # noqa: BLE001 - a 200 with the class, never the text; Make reruns a 5xx
        alert = getattr(exc, "books_alert", None)
        if alert is None:  # before the run's own row: say it in a row of its own, so the way back is said too
            from .books.store import record_crash

            alert = record_crash(cfg.database_url, cfg.db, "http", f"the tick stopped on {type(exc).__name__}")
            if alert is None:  # the database is not there either: remembered by this process
                state.books_unrecorded_crash = True
                alert = True
        return JSONResponse({"ok": False, "skipped": False, "alert": alert, "reasons": ["crashed"],
                             "error": type(exc).__name__, "detail": getattr(exc, "detail", None),
                             "message": f"ספרי חשבשבת ({cfg.company}): הסבב נפל ({type(exc).__name__})."})
    if getattr(state, "books_unrecorded_crash", False):
        state.books_unrecorded_crash = False
        summary["alert"] = True  # the crash was said and no row remembers it: say what came after
    return JSONResponse({"skipped": False, **summary, "message": tick_message(cfg.company, summary),
                         "request_s": round(time.monotonic() - started, 1)})


@app.get("/api/books/status")
def books_status(request: Request) -> JSONResponse:
    """The last run, the series - documents held, first and last number, holes - and books_retry - how many ids
    wait, by kind, and the ids that keep failing - behind the sync key: document numbers and counts are no one's
    business on a public URL."""
    cfg = config.load()
    refusal = _sync_key_refusal(request, cfg)
    if refusal is not None:
        return refusal
    try:
        store = _books_store(cfg)
        with store.session(cfg.db + ":status") as s:  # its own lock id: reading never waits for a run
            if s is None:
                return JSONResponse({"ok": False, "error": "busy"})
            from .books.sync import MISSING_CONFIRM_S

            return JSONResponse({"ok": True, "company": cfg.company, "last_run": s.last_run(cfg.db), "series": s.series(cfg.db),
                                 "retry": s.retry_summary(cfg.db), "missing": s.missing_summary(cfg.db, MISSING_CONFIRM_S),
                                 "sweep": s.sweep_summary(cfg.db)})
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": type(exc).__name__, "detail": getattr(exc, "detail", None)})


@app.get("/check")
def check(request: Request) -> JSONResponse:
    state = request.app.state
    clock = state.clock
    if not _lock.acquire(blocking=False):
        # A round is running: the last answer, marked, rather than a thread parked on the lock.
        last = _cache["result"]
        if last is None:
            return JSONResponse({"ok": False, "in_progress": True, "message": "a check is running; ask again in a minute"}, status_code=503)
        return JSONResponse(dict(last, cached=True, in_progress=True), status_code=200 if last["ok"] else 503)
    try:
        cached = _cache["result"]
        if cached is not None and clock() - _cache["at"] < CHECK_TTL_S:
            result = dict(cached, cached=True)
        else:
            cfg = config.load()
            client = get_client(state, cfg) if cfg.ok else None
            result = run_check(cfg, transport=state.transport, clock=clock, client=client)
            _cache.update(at=clock(), result=result)
            result = dict(result, cached=False)
    finally:
        _lock.release()
    return JSONResponse(result, status_code=200 if result["ok"] else 503)


def session_report(sess: Session, held: bool, store_kind: Optional[str], now: float) -> dict:
    """How the round's session came to be, never the session: `source` is memory (this process held
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


def run_check(cfg: config.Config, transport=None, clock=time.time, client: Optional[WizClient] = None,
              others_named: bool = False) -> dict:
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
        first = client.session()
    except WizError as exc:
        out["session"] = out["error"] = exc.as_dict()
        return out
    for name, fetch, summarize in (
        ("companies", client.companies, lambda d: summarize_companies(d, cfg.db, others_named)),
        ("triggers", client.triggers, summarize_triggers),
    ):
        try:
            out[name] = summarize(fetch())
        except WizError as exc:
            out[name] = exc.as_dict()
            out.setdefault("error", exc.as_dict())
    # The session as the round ended, not as it began: a read may have renewed it, and that renewal's
    # store_error or note belongs in the answer (review 19.11).
    last = client.current()
    store_kind = getattr(client.store, "kind", None)
    if last is None:  # the session was refused and the renewal failed: the reads above say why
        # ok False: the round ended with no session, and live-check reads documents only after an ok one.
        out["session"] = dict(session_report(first, held, store_kind, clock()), ok=False, renewal_failed=True)
    else:
        out["session"] = session_report(last, held and last is first, store_kind, clock())
        if last is not first:
            out["session"]["renewed"] = True
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


def summarize_companies(data: Any, our_db: Optional[str], others_named: bool = False) -> dict:
    """The companies the token reaches. The public /check names only the configured one and counts the
    rest (Nadav 19.11: the deployment is public, for monday and Make, and another company's name or a
    subscription number has no reason to be); live-check, whose log is the private repo's, names all."""
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
    shown = found if others_named else [f for f in found if f["db"] == our_db]
    out = {
        "ok": True,
        "count": len(items),
        "fields": _fields(items),
        "items": shown,
        "includes_configured_db": any(f["db"] == our_db for f in found) if found else None,
    }
    if len(shown) < len(found):
        out["others_not_named"] = len(found) - len(shown)
    return out


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
