"""The desk's page and its routes (Analiza-Monday backlog 0301 D1 and D2; the desk move plan §2-§3).

- GET /desk/ (and /desk): the page of the Board View "כספים", a second feature of the same monday app (the plan §2, option
  A; its External hosting URL is https://analiza-finance.replit.app/desk/). Served `no-cache` with an ETag of its bytes,
  its assets linked as `?v=<hash of their bytes>` and kept a year: monday reloads the iframe on every open, and a stale
  script against a new answer is a blank view (analiza-metrics app/main.py, the same rule).
- GET /desk/desk.js, /desk/desk.css, /desk/finance.js, /desk/finance.css, /desk/check.js, /desk/xlsx.js,
  /desk/vendor/monday-sdk.js: the assets. D2: finance.js and finance.css are the desk, ported from analiza-metrics; desk.js
  mounts it; check.js is D1's self-check, drawn on /desk/?check=1; D4: xlsx.js writes the students' file with the ID
  number in the browser. The SDK is analiza-metrics' copy, byte for byte (monday-sdk-js 0.4.12; a test holds
  its sha256).
- GET /api/desk: the gate (app/desk/gate.py), then who the reader is and the desk's reading (app/desk/reading.py) as
  counts, for the tabs the reader may see (D1), and the desk itself (D2): `view`, those tabs built from that reading
  (app/desk/view.py), a tab whose board the reader is not on removed, not hidden. It carries names and money, so only a
  tab's own members get it. Always 200 after the gate: no reading is `view: null`, and `last_attempt` says why. Past its
  span the reading held - or, on an instance that holds none, the stored one (app/desk/store.py) - is answered at once,
  `refreshing: true`, and the page's own next request, `?fresh=1`, reads the next: a refresh that reads only what changed
  (analiza-metrics `finance_view`, round 3 slice 2).
- POST /api/desk/refresh: the same answer from a reading that started after the press, at most one forced reading per
  REFRESH_GAP_S per instance (429 `rate_limited`).
- GET /api/desk/totals: a list's totals exactly as the page filters it, from the reading the page shows; never a read.
- POST /api/desk/export-link, GET /api/desk/export.xlsx: a list as an Excel file (app/desk/export.py) behind a link signed
  with this service's own key, EXPORT_LINK_S long. D4: the students by cohort too (`b2c_cohorts`, app/desk/cohorts.py and
  cohorts_export.py), its totals and its file gated as the students' list is.
- POST /api/desk/export.json (D4, 26.11): the students' file as data, the same rows, sheets and "פרטים" as its Excel file,
  with an empty "ת.ז" column and each row's leads: the page reads the ID numbers from monday as the person and builds the
  file itself (frontend/xlsx.js). Behind the sessionToken and Student Charge's members; it never holds an ID number.
- POST /api/usage: what the reader did on the page, as closed words and counts (app/desk/usage.py), kept in `desk_events`
  (app/desk/usage_store.py, migrations/005). Behind the sessionToken only, with a limit of its own.
- POST /api/alerts/not-a-gap, POST /api/alerts/not-a-gap/undo (R5, the spec §2.4 and §6.1 item 4): "לא פער" on one alert
  of the books, and its undo by its own dismisser, kept in `alert_dismissals` (app/desk/dismissals_store.py, migrations/
  008) - the service's own database, never monday or the books. The whole gate, then a member of the alert's own boards
  (app/desk/dismissals.py BOARDS), never a viewer; the evidence is the server's (app/desk/books.py DeskBooks.dismiss).
  Answered with the desk as it is after the press, as /api/desk answers it.

D2's routes are analiza-metrics app/finance_api.py at `da671b2` (`finance_view`, `finance_refresh`, `finance_totals`,
`finance_export_link`, `finance_export_file`, `usage`) on this service's gate and reading, with what changed said at each.
Not ported: POST /api/finance/account (ADR-0188: a customer's billing details are the person's write, in the browser).

Nothing the page or its assets carry is data: everything comes from /api/desk, behind the sessionToken, and what the server
never reads (an ID number, a refund's bank account, a company's ח.פ) the page reads from monday as the person. The service
writes nothing to monday (ADR-0188): `MONDAY_TOKEN` reads only (app/monday/client.py refuses a mutation); the desk's writes
go out from the browser, as the person (finance.js mondayWrite). No ID number, bank account or business number is ever in
an answer or a file the server writes: the reading never holds one (NEVER_READ). The students' file with the ID number
(D4) is written in the person's browser, never here.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict
from starlette.concurrency import run_in_threadpool

from .. import config
from ..monday.client import MondayReader
from .auth import AuthError, verify_session_token
from . import dismissals as dz
from .books import BOOKS_KEY_MAX, DeskBooks, merge as books_merge, receipts_split
from .cohorts import VIEW_WORD as COHORTS_WORD, cohort_totals, filter_cohorts, filter_unpriced
from .gate import DESK_PER_DAY, DESK_PER_USER_HOUR, Members, RateLimiter, Visitor, audit, bearer, err, gate
from .reading import B2B_CHARGE, STUDENT_CHARGE, TAB_BOARDS, HeldReading
from .usage_store import UsageStoreError, append_events
from .usage import Refused, UsageStats, clean_batch
from .view import (B2B_STATES, B2C_STATES, DESK_TZ, build_view, filter_b2b, filter_b2c, filter_customers, json_safe,
                   list_totals, query_exportable, today_in, visible_part)

logger = logging.getLogger("analiza_finance.desk")
router = APIRouter()

ROOT = Path(__file__).resolve().parent.parent.parent
FRONTEND_DIR = ROOT / "frontend"
STATIC_FILES = {
    "/desk/desk.js": ("desk.js", "application/javascript; charset=utf-8"),
    "/desk/desk.css": ("desk.css", "text/css; charset=utf-8"),
    "/desk/finance.js": ("finance.js", "application/javascript; charset=utf-8"),
    "/desk/finance.css": ("finance.css", "text/css; charset=utf-8"),
    "/desk/check.js": ("check.js", "application/javascript; charset=utf-8"),
    "/desk/xlsx.js": ("xlsx.js", "application/javascript; charset=utf-8"),
    "/desk/vendor/monday-sdk.js": ("vendor/monday-sdk.js", "application/javascript; charset=utf-8"),
}
PAGE = "index.html"
# the page's links to its assets, rewritten to `?v=<hash>` when the page is served
ASSET_LINKS = (("/desk/desk.js", 'src="/desk/desk.js"'), ("/desk/desk.css", 'href="/desk/desk.css"'),
               ("/desk/finance.js", 'src="/desk/finance.js"'), ("/desk/finance.css", 'href="/desk/finance.css"'),
               ("/desk/check.js", 'src="/desk/check.js"'), ("/desk/xlsx.js", 'src="/desk/xlsx.js"'),
               ("/desk/vendor/monday-sdk.js", 'src="/desk/vendor/monday-sdk.js"'))
ASSET_VERSION_CHARS = 12
IMMUTABLE = "public, max-age=31536000, immutable"
MEMBERS_READ_DEADLINE_S = 15

# D2, analiza-metrics' limits for the same routes (app/finance_api.py at da671b2):
# the refresh press, at most one forced reading per instance in this many seconds (FINANCE_REFRESH_GAP_SECONDS)
REFRESH_GAP_S = 5
# a reading shown while every reading after it failed is `stale` after this many seconds (FINANCE_STALE_SECONDS)
STALE_S = 600
# the Excel file's link lives this long: no session header needed, so it opens in a new tab (FINANCE_EXPORT_LINK_SECONDS)
EXPORT_LINK_S = 120
# the context of the link's key: this service's own, so analiza-metrics' links (`b"finance-export|"`) are not valid here
EXPORT_KEY_CONTEXT = b"analiza-finance-desk-export|"
# the lists a total or a file is of: the students', the companies', the aging by customer, and since D4 the students by
# cohort - a list of the students' tab (`b2c_` opens it for Student Charge's members, `_opens`)
TABS = ("b2c", "b2b", "b2b_customers", "b2c_cohorts")
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
# How the desk is used (analiza-metrics' limits, app/finance_api.py at da671b2): the page sends its events in batches -
# when 20 wait, 30 seconds after the first, and when it goes out of sight - so an hour of steady work is some 120 batches
# at most. They have their own limit and never take from the reader's desk budget (DESK_PER_USER_HOUR), which the desk's
# own reads need.
USAGE_BATCHES_PER_USER_HOUR = 240
USAGE_BATCHES_PER_DAY = 20000

_hashes: dict = {}
_hash_lock = threading.Lock()
_state_lock = threading.Lock()
_usage_lock = threading.Lock()


# ---- the state of one instance: the members, the reading, the budget ----------------------------------------------
def _reader(state: Any, token: str) -> MondayReader:
    if getattr(state, "monday_transport", None) is not None:  # tests
        return MondayReader(token, client=httpx.Client(transport=state.monday_transport), sleep=lambda s: None)
    return MondayReader(token)


def _store(state: Any, cfg: config.Config) -> Any:
    """Where the desk keeps its last reading (D2): the service's own Postgres, or None without DATABASE_URL (the desk then
    holds its reading in memory only, as D1 did). Tests put a store of their own on `state.desk_store`."""
    given = getattr(state, "desk_store", None)
    if given is not None:
        return given
    if cfg.database_url:
        from .store import DeskStore

        return DeskStore(cfg.database_url)
    return None


def _dismissal_store(state: Any) -> Any:
    """How the desk reaches "לא פער" (R5): `DismissalStore` on the URL given, or the tests' own on `state.dismissal_store`."""
    given = getattr(state, "dismissal_store", None)
    if given is not None:
        return lambda _url: given
    from .dismissals_store import DismissalStore

    return DismissalStore


def _books_store(state: Any) -> Any:
    """How the desk reaches the books copy (D3): `BooksStore` on the URL given, or the tests' own on `state.books_store`
    (and the tests' clock of the copy's age on `state.books_now`)."""
    given = getattr(state, "books_store", None)
    if given is not None:
        return lambda _url: given
    from ..books.store import BooksStore

    return BooksStore


def desk_state(state: Any, cfg: config.Config) -> SimpleNamespace:
    """One per instance, made again when the token, the database or the test transport changes (the config is read per
    request)."""
    key = (hashlib.sha256(cfg.monday_token.encode("utf-8")).hexdigest(), id(getattr(state, "monday_transport", None)),
           hashlib.sha256(cfg.database_url.encode("utf-8")).hexdigest(), id(getattr(state, "desk_store", None)),
           id(getattr(state, "books_store", None)), id(getattr(state, "dismissal_store", None)))
    with _state_lock:
        current = getattr(state, "desk", None)
        if current is not None and current.key == key:
            return current

        def subscribers(board_id: str) -> set:
            reader = _reader(state, cfg.monday_token)
            try:
                reader.deadline = reader.clock() + MEMBERS_READ_DEADLINE_S
                return reader.board_subscribers(board_id)
            finally:
                reader.close()

        # D2: `refresh`, the press's own limit (one forced reading per REFRESH_GAP_S on this instance, whoever presses);
        # `view`, the view of the reading held, built once for it: (that reading, its view or None, None or "crashed").
        # D3: `books`, what the books say of that reading (app/desk/books.py), and `merged`, the view with it laid over
        # (that reading, the part's key, the merged view)
        made = SimpleNamespace(key=key, members=Members(subscribers),
                               reading=HeldReading(lambda: _reader(state, cfg.monday_token), store=_store(state, cfg)),
                               limiter=RateLimiter(DESK_PER_USER_HOUR, DESK_PER_DAY),
                               refresh=RateLimiter(1, 1, user_window=REFRESH_GAP_S, global_window=REFRESH_GAP_S),
                               view=None, view_lock=threading.Lock(),
                               books=DeskBooks(_books_store(state), lambda: _reader(state, cfg.monday_token),
                                               now=getattr(state, "books_now", None),
                                               make_dismissals=_dismissal_store(state)),
                               merged=None)
        state.desk = made
        return made


def usage_state(state: Any) -> SimpleNamespace:
    """One per instance: the usage batches' own limiter, and what /health says of them. Apart from desk_state, which is
    made again when the monday token changes: a batch needs no monday, and its counts outlive a new token."""
    with _usage_lock:
        current = getattr(state, "usage", None)
        if current is None:
            current = SimpleNamespace(limiter=RateLimiter(USAGE_BATCHES_PER_USER_HOUR, USAGE_BATCHES_PER_DAY),
                                      stats=UsageStats())
            state.usage = current
        return current


def stored_part(glance: Any, now: Optional[float] = None) -> Any:
    """The stored reading for /health (app/desk/store.py `glance`, read with the store's other glances at most every
    STORE_TTL_S): when it was taken and its age now, which kind it is, whether it was complete. None when the table holds
    none (or was not read); the class of an error as it came."""
    if not isinstance(glance, dict) or "error" in glance:
        return glance
    out = dict(glance)
    try:
        taken = datetime.fromisoformat(glance["read_at"]).timestamp()
        out["age_s"] = max(0, int((time.time() if now is None else now) - taken))
    except (KeyError, TypeError, ValueError):
        out["age_s"] = None
    return out


def health_block(state: Any, cfg: config.Config, stored: Any = None, now: Optional[float] = None) -> dict:
    """The desk in /health: whether its two secrets are set, this instance's last reading (its kind, time and cost, no
    count of rows), and whether each board's members were read. Since D2, with a database the stored reading's times and
    age (`stored`), once this instance has served the desk how it read and kept its reading (`refresh`: codes, times,
    seconds and requests), and the usage batches: written, events refused, writes failed (`usage`). Never a secret, a
    user, a row, a count of rows or an event."""
    from .reading import health_part

    current = getattr(state, "desk", None)
    out = {"client_secret_set": bool(cfg.monday_client_secret), "monday_token_set": bool(cfg.monday_token),
           "page": "/desk/",
           "last_reading": None if current is None else health_part(current.reading.last),
           "members": {} if current is None else current.members.state(),
           "usage": usage_state(state).stats.health()}
    if cfg.database_url:
        out["stored"] = stored_part(stored, now)
    if current is not None:
        try:
            out["refresh"] = current.reading.health()
        except Exception as exc:  # noqa: BLE001 - /health never raises
            out["refresh"] = {"error": type(exc).__name__}
    # D3: the books' part - its state, its copy's time and its reasons, never a count (the deployment is public)
    try:
        out["books"] = None if current is None else current.books.health()
    except Exception as exc:  # noqa: BLE001 - /health never raises
        out["books"] = {"error": type(exc).__name__}
    return out


# ---- the page and its assets ----------------------------------------------------------------------------------------
def _frontend(request: Request) -> Path:
    return Path(getattr(request.app.state, "frontend_dir", None) or FRONTEND_DIR)


def content_hash(path: Path) -> str:
    """sha256 of a file's bytes, kept by (path, mtime, size): a Republish that changes a file changes its hash."""
    st = path.stat()
    key = (str(path), st.st_mtime_ns, st.st_size)
    with _hash_lock:
        digest = _hashes.get(key)
    if digest is None:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()[:32]
        with _hash_lock:
            _hashes[key] = digest
    return digest


def weak_tag(digest: str) -> str:
    return 'W/"' + digest + '"'


def not_modified(request: Request, tag: str) -> bool:
    asked = {t.strip().removeprefix("W/") for t in request.headers.get("if-none-match", "").split(",")}
    return "*" in asked or tag.removeprefix("W/") in asked


def _static(request: Request, url_path: str):
    rel, media_type = STATIC_FILES[url_path]
    path = _frontend(request) / rel
    if not path.is_file():
        return JSONResponse({"error": "not_found"}, status_code=404)
    digest = content_hash(path)
    versioned = request.query_params.get("v") == digest[:ASSET_VERSION_CHARS]
    headers = {"Cache-Control": IMMUTABLE if versioned else "no-cache", "ETag": weak_tag(digest)}
    if not_modified(request, headers["ETag"]):
        return Response(status_code=304, headers=headers)
    return FileResponse(path, media_type=media_type, headers=headers)


def _page(request: Request):
    path = _frontend(request) / PAGE
    if not path.is_file():
        return JSONResponse({"error": "not_found"}, status_code=404)
    html = path.read_text(encoding="utf-8")
    for url_path, attr in ASSET_LINKS:
        asset = _frontend(request) / STATIC_FILES[url_path][0]
        if asset.is_file():
            html = html.replace(attr, attr[:-1] + "?v=" + content_hash(asset)[:ASSET_VERSION_CHARS] + '"')
    body = html.encode("utf-8")
    headers = {"Cache-Control": "no-cache", "ETag": weak_tag(hashlib.sha256(body).hexdigest()[:32])}
    if not_modified(request, headers["ETag"]):
        return Response(status_code=304, headers=headers)
    return Response(content=body, media_type="text/html; charset=utf-8", headers=headers)


@router.get("/desk/")
def desk_page(request: Request):
    return _page(request)


@router.get("/desk")
def desk_page_bare(request: Request):
    """The same page without the slash: the URL typed in the Developer Center may lack it. The assets are linked by
    absolute paths, so the page works from either."""
    return _page(request)


@router.get("/desk/desk.js")
def desk_js(request: Request):
    return _static(request, "/desk/desk.js")


@router.get("/desk/desk.css")
def desk_css(request: Request):
    return _static(request, "/desk/desk.css")


@router.get("/desk/finance.js")
def desk_finance_js(request: Request):
    return _static(request, "/desk/finance.js")


@router.get("/desk/finance.css")
def desk_finance_css(request: Request):
    return _static(request, "/desk/finance.css")


@router.get("/desk/check.js")
def desk_check_js(request: Request):
    return _static(request, "/desk/check.js")


@router.get("/desk/xlsx.js")
def desk_xlsx_js(request: Request):
    return _static(request, "/desk/xlsx.js")


@router.get("/desk/vendor/monday-sdk.js")
def desk_sdk(request: Request):
    return _static(request, "/desk/vendor/monday-sdk.js")


# ---- the routes ---------------------------------------------------------------------------------------------------
def _visible_complete(checks: dict, keys: list) -> bool:
    """Complete for these boards: each was read and is whole. No board to show is not complete."""
    return bool(keys) and all((checks.get(k) or {}).get("ok") is True for k in keys)


def _attempt(last: Any, keys: list, both: bool) -> Any:
    """The last attempt as this reader may see it: its time, its closed code, and whether the boards of their own tabs
    were whole. The detail (board and column ids) only to a reader of both tabs (D1's review: a one-tab reader was told
    of the other tab's boards). Since D2 also under the old desk's two names for the same facts, `taken_at` (its time)
    and `ok` (no error), which the page ported from analiza-metrics reads."""
    if last is None:
        return None
    out = {"read_at": last.get("read_at"), "error": last.get("error"),
           "complete": not last.get("error") and _visible_complete(last.get("checks") or {}, keys),
           "taken_at": last.get("read_at"), "ok": last.get("error") is None}
    if both:
        out["detail"] = last.get("detail")
    return out


def _gated(request: Request, endpoint: str) -> Any:
    """(the config, this instance's state, the Visitor), or the refusal to send: the two secrets, then the whole gate
    (app/desk/gate.py), the reader's budget included. Every route behind the sessionToken goes through it."""
    cfg = config.load()
    missing = [name for name, value in (("MONDAY_CLIENT_SECRET", cfg.monday_client_secret),
                                        ("MONDAY_TOKEN", cfg.monday_token)) if not value]
    if missing:
        audit(None, endpoint, "app_not_configured")
        return err(503, "app_not_configured", missing=missing)
    st = desk_state(request.app.state, cfg)
    got = gate(request, cfg.monday_client_secret, st.members, st.limiter, endpoint, STUDENT_CHARGE, B2B_CHARGE)
    if isinstance(got, JSONResponse):
        return got
    return cfg, st, got


def _opens(got: Visitor, tab: str) -> bool:
    """Whether this reader may see a list of this tab: the students' for Student Charge's members, the companies' and
    their aging for B2B Charge's. Only True opens one: None (the members could not be read) is no."""
    return (got.b2c if tab.startswith("b2c") else got.b2b) is True


def _view_of(st: Any, reading: dict) -> tuple:
    """(the whole view of this reading, None), or (None, "crashed") when it could not be built. Built once per reading, on
    the day it is first asked for, and kept beside it: every answer, total and file of one reading is of one view, as in
    analiza-metrics, which built the view once when it read. A view that cannot be built is said, never a 500."""
    with st.view_lock:
        if st.view is not None and st.view[0] is reading:
            return st.view[1], st.view[2]
        try:
            built, error = build_view(reading, today_in(DESK_TZ)), None
        except Exception as exc:  # noqa: BLE001 - said by class: no row, no name and no amount reaches the log
            logger.error("desk view not built (%s)", type(exc).__name__)
            built, error = None, "crashed"
        st.view = (reading, built, error)
        return built, error


def _merged(st: Any, reading: dict, whole: dict, part: Optional[dict]) -> dict:
    """The view of this reading with the books' part laid over it (D3, app/desk/books.py `merge`), built once per reading
    and part, and kept beside them: the answer, its totals and its files are of one merged view."""
    if part is None:
        return whole
    with st.view_lock:
        if st.merged is not None and st.merged[0] is reading and st.merged[1] == part.get("key"):
            return st.merged[2]
        try:
            got = books_merge(whole, part)
        except Exception as exc:  # noqa: BLE001 - said by class: the desk is shown without the books, never a 500
            logger.error("desk books not laid over the view (%s)", type(exc).__name__)
            return whole
        st.merged = (reading, part.get("key"), got)
        return got


def _with_books(cfg: config.Config, st: Any, reading: dict, whole: dict, may_read: bool = True) -> dict:
    """The whole view with what the books say of it: the part for this reading (asked of the copy when the reading or the
    copy changed) laid over it. `may_read` false for an answer at once (`refreshing`): monday is not read for it. A part
    that cannot be made is said in /health, and the desk is drawn without it."""
    try:
        part = st.books.answer(cfg, reading, whole, may_read=may_read)
    except Exception as exc:  # noqa: BLE001 - said by class, never a 500
        logger.error("desk books not answered (%s)", type(exc).__name__)
        return whole
    return _merged(st, reading, whole, part)


def _instant(text: Any) -> Optional[datetime]:
    """A reading's time as an instant, or None for one that is not a time: two readings are compared as instants, never as
    printed text (analiza-metrics, review 10.11)."""
    try:
        return datetime.fromisoformat(text) if text else None
    except (TypeError, ValueError):
        return None


async def _body_of(request: Request, model: type) -> Any:
    """The request's JSON body as `model`, or None when it is not one (not JSON, or a field of another shape or one the
    model does not know): the caller answers 400 `invalid_filter`, the word app/main.py gives a body FastAPI refuses. Read
    only after the sessionToken was verified, so a body is never parsed for a client the desk does not know (D2's review:
    FastAPI validated it first, 400 before 401)."""
    try:
        return model.model_validate(await request.json())
    except Exception:  # noqa: BLE001 - not JSON, not UTF-8, nested too deep, or not the model's shape: never a 500
        return None


def _unavailable(st: Any, reason: Optional[str] = None) -> JSONResponse:
    """No reading to answer from: 503 `finance_unavailable` with the last attempt's code, or `not_read` (analiza-metrics'
    word, which the page ported from it reads)."""
    last = st.reading.last or {}
    return err(503, "finance_unavailable", reason=reason or last.get("error") or "not_read")


def _answer(cfg: config.Config, st: Any, got: Visitor, endpoint: str, force: bool, stale_ok: bool = False,
            extra: Optional[dict] = None) -> JSONResponse:
    """The desk for this reader. D1's counts (`user`, `tabs`, `reading`, `last_attempt`) and since D2 the desk itself:
    `view`, the tabs the reader may see (`visible_part`), its money as strings; `view_error`, "crashed" when the reading
    could not be built into one; `taken_at`, the reading's time, which the totals and the files are asked by; its
    `age_seconds`; `refreshing`, when the reading is the held or the stored one answered at once past its span (`stale_ok`,
    a plain open): the page asks for the next itself, `?fresh=1` - a request, never a thread after the answer
    (analiza-metrics ADR-0087); and `stale`, when the reading shown is older than STALE_S because every reading since has
    failed. No reading: `view` is null and `last_attempt` says why."""
    # True, False, or None when the board's members could not be read: only True opens the tab
    tabs = {"b2c": got.b2c, "b2b": got.b2b}
    keys = [key for tab, seen in tabs.items() if seen is True for key in TAB_BOARDS[tab]]
    reading, last, refreshing = st.reading.answer(force=force, stale_ok=stale_ok)
    age = st.reading.age_s() if reading is not None else None
    shown, view, view_error = None, None, None
    if reading is not None:
        checks = reading.get("checks") or {}
        shown = {"read_at": reading.get("read_at"), "age_seconds": age,
                 "complete": _visible_complete(checks, keys), "seconds": reading.get("seconds"),
                 "calls": reading.get("calls"), "boards": {k: checks[k] for k in keys if k in checks}}
        whole, view_error = _view_of(st, reading)
        if whole is not None:
            # D3: what the books say, laid over the whole view before a reader's part is cut from it
            view = visible_part(_with_books(cfg, st, reading, whole, may_read=not refreshing), got.b2c is True,
                                got.b2b is True)
    ident = got.identity
    outcome = ("view_crashed" if view_error else "ok" if shown["complete"] else "incomplete") if shown else "no_reading"
    audit(ident, endpoint, outcome, sum(int(c.get("rows") or 0) for c in (shown or {}).get("boards", {}).values()))
    failing = last is not None and last.get("error") is not None
    return JSONResponse(json_safe({
        "user": {"id": ident.user_id, "view_only": ident.view_only, "guest": ident.guest,
                 "claims": list(ident.claim_names)},
        "tabs": tabs,
        "reading": shown,
        "last_attempt": _attempt(last, keys, got.b2c is True and got.b2b is True),
        "view": view,
        "view_error": view_error,
        "taken_at": None if reading is None else reading.get("read_at"),
        "age_seconds": age,
        "refreshing": bool(refreshing and reading is not None),
        "stale": bool(reading is not None and failing and age is not None and age > STALE_S),
        **(extra or {}),
    }))


@router.get("/api/desk")
def desk_api(request: Request, fresh: int = 0) -> JSONResponse:
    """The desk (D2) over the skeleton's answer (D1): who the reader is, which tabs they may see, the desk's reading as
    counts for those tabs' boards, and the view of those tabs. A first open may be answered with the last reading at once
    (`refreshing`); the page's own second request, `?fresh=1`, then waits for a reading inside the span - read in THAT
    request (analiza-metrics `finance_view`). sync def on purpose: a reading is seconds of blocking I/O, run in the
    threadpool."""
    got = _gated(request, "desk")
    if isinstance(got, JSONResponse):
        return got
    cfg, st, visitor = got
    return _answer(cfg, st, visitor, "desk", force=False, stale_ok=not fresh)


@router.post("/api/desk/refresh")
def desk_refresh(request: Request) -> JSONResponse:
    """Read the boards again now: the "רענון" press, and the page's own after it writes. The same answer as /api/desk,
    from a reading that started after the press; at most one press per REFRESH_GAP_S per instance (analiza-metrics
    `finance_refresh`, the same limit)."""
    got = _gated(request, "desk-refresh")
    if isinstance(got, JSONResponse):
        return got
    cfg, st, visitor = got
    wait = st.refresh.check(visitor.identity.user_id)
    if wait is not None:
        audit(visitor.identity, "desk-refresh", "rate_limited")
        return err(429, "rate_limited", headers={"Retry-After": str(wait)}, retry_after_seconds=wait)
    return _answer(cfg, st, visitor, "desk-refresh", force=True)


def _filtered(whole: dict, tab: str, state: str, period: str, customer: str, q: str) -> list:
    """A list exactly as the page filters it; ValueError for a filter the desk does not know."""
    if tab == "b2c":
        return filter_b2c(whole["b2c"], state, period, q)
    if tab == "b2c_cohorts":   # D4: the search box only, as the page filters the view
        if state or period or customer:
            raise ValueError("the cohorts take no state, period or customer")
        return filter_cohorts(whole["b2c"].get("cohorts"), q)
    if tab == "b2b":
        return filter_b2b(whole["b2b"], state, customer, q)
    return filter_customers(whole["b2b"]["customers"], q)


@router.get("/api/desk/totals")
def desk_totals(request: Request, tab: str = "b2c", state: str = "", period: str = "", customer: str = "", q: str = "",
                taken: str = "", books: str = "") -> JSONResponse:
    """The totals of a list exactly as the page filters it (Nadav 10.11 "תעשה את 6"): summed here, never in the page. The
    same gate and the same filters as the Excel file. Only from the reading the page shows (`taken`): this route never
    reads monday, and when this instance holds another reading it says so (`other_reading`) instead of a total that does
    not belong to the rows on the screen (analiza-metrics `finance_totals`, review 10.11). D3: and of the books' part the
    page shows (`books`, its key), since "לטיפול" holds the rows the books mark: another part is `other_reading` too.
    Nadav 26.11: for the list "שולם, שליחת קבלה: טרם", also how many of its rows the books hold a receipt of, how many none
    was identified of, and how many they cannot say of now (`receipts`, app/desk/books.py `receipts_split`)."""
    got = _gated(request, "desk-totals")
    if isinstance(got, JSONResponse):
        return got
    _cfg, st, visitor = got
    if tab not in TABS:
        return err(400, "unknown_tab")
    if not _opens(visitor, tab):
        audit(visitor.identity, "desk-totals", "not_a_member")
        return err(403, "not_a_board_member")
    if not query_exportable(q) or len(period) > 32 or len(customer) > 32 or len(taken) > 40 or len(books) > BOOKS_KEY_MAX:
        return err(400, "bad_filter")
    reading = st.reading.reading  # as held: a totals request reads nothing
    if reading is None:
        return _unavailable(st)
    taken_at = reading.get("read_at")
    shown = _instant(taken)
    part = st.books.current(_cfg)  # the books' part as held: nothing of the books is read, the dismissals at most every 15 s
    if shown is None or _instant(taken_at) is None or shown != _instant(taken_at) \
            or (books and (part is None or books != part.get("key"))):
        audit(visitor.identity, "desk-totals", "other_reading")
        return JSONResponse(json_safe({"other_reading": True, "taken_at": taken_at}))
    whole, error = _view_of(st, reading)
    if whole is None:
        return _unavailable(st, error)
    whole = _merged(st, reading, whole, part)
    try:
        rows = _filtered(whole, tab, state, period, customer, q)
    except ValueError:
        return err(400, "bad_filter")
    audit(visitor.identity, "desk-totals", "ok", len(rows))
    sums = cohort_totals(rows) if tab == "b2c_cohorts" else list_totals(tab, rows)
    out = {"count": len(rows), "sums": sums, "taken_at": taken_at}
    if tab == "b2c" and state == "receipt_pending":   # Nadav 26.11: the queue's line, counted here only
        out["receipts"] = receipts_split(rows)
    return JSONResponse(json_safe(out))


class ExportBody(BaseModel):
    """What a list shows: its tab, a state ('' all, 'tasks', or a state key), a period key, the search box, the reading the
    page shows (its taken_at: the file says when it is built from another), a customer (an account id, '' for all), and
    D3's the books' part the page shows (its key: the file says when it is built from another, as the totals do).
    Anything else in the body is 400 `invalid_filter`, read after the gate (`_body_of`)."""
    model_config = ConfigDict(extra="forbid")
    tab: str = "b2c"
    state: str = ""
    period: str = ""
    q: str = ""
    taken: str = ""
    customer: str = ""
    books: str = ""


def link_fields(user: Any, exp: str, tab: str, state: str, period: str, q: str, taken: str, customer: str,
                books: str) -> list:
    """What a link's signature covers: every field of its filter, and the books' part's key when the page sent one (D3) -
    a link without it is signed as before D3, so the 8 fields it had stay what they were."""
    return [str(user), exp, tab, state, period, q, taken, customer] + ([books] if books else [])


def _bad_filter(body: ExportBody) -> bool:
    """A filter no list of the desk has: a state its tab does not know, a key or a search too long, a company that is not an
    id, a search outside the exportable alphabet, or the cohorts with anything but the search. The link's and (D4) the
    students' table's alike."""
    states = B2C_STATES if body.tab == "b2c" else B2B_STATES
    return bool((body.tab == "b2c_cohorts" and (body.state or body.period or body.customer))
                or (body.state and body.state != "tasks" and body.state not in states) or len(body.period) > 32
                or len(body.q) > 100 or len(body.taken) > 40 or len(body.books) > BOOKS_KEY_MAX
                or not (body.customer == "" or (body.customer.isascii() and body.customer.isalnum()
                                                 and len(body.customer) <= 32))
                or not query_exportable(body.q))


def export_sig(client_secret: str, fields: list) -> str:
    """The link's signature: an HMAC of its fields under a key of this service's own, so a link signed by
    analiza-metrics (whose key is derived from the same Client Secret, under another context) is not valid here. JSON,
    not a joined string: no text in one field can pass for the boundary of the next (analiza-metrics, review 10.11)."""
    key = hashlib.sha256(EXPORT_KEY_CONTEXT + client_secret.encode("utf-8")).digest()
    return hmac.new(key, json.dumps(fields, ensure_ascii=True).encode(), hashlib.sha256).hexdigest()


@router.post("/api/desk/export-link")
async def desk_export_link(request: Request) -> JSONResponse:
    """A two-minute link to a list as an Excel file - the students', the companies' or the aging by customer - exactly as
    the page filters it. The link carries the reader and the filter, signed; the file route checks the reader's board
    again (Student Charge for the students, B2B Charge for the companies). It opens in a new tab, which cannot send the
    sessionToken: monday may sandbox the view's iframe against downloads (analiza-metrics `finance_export_link`). The gate
    first, in the threadpool (it may read a board's members), and only then the body (D2's review)."""
    got = await run_in_threadpool(_gated, request, "desk-export")
    if isinstance(got, JSONResponse):
        return got
    cfg, _st, visitor = got
    body = await _body_of(request, ExportBody)
    if body is None:
        audit(visitor.identity, "desk-export", "invalid_filter")
        return err(400, "invalid_filter")
    if body.tab not in TABS:
        return err(400, "unknown_tab")
    if not _opens(visitor, body.tab):
        audit(visitor.identity, "desk-export", "not_a_member")
        return err(403, "not_a_board_member")
    if _bad_filter(body):
        return err(400, "bad_filter")
    user = visitor.identity.user_id
    exp = str(int(time.time()) + EXPORT_LINK_S)
    fields = link_fields(user, exp, body.tab, body.state, body.period, body.q, body.taken, body.customer, body.books)
    query = urlencode({"tab": body.tab, "state": body.state, "period": body.period, "q": body.q, "taken": body.taken,
                       "customer": body.customer, **({"books": body.books} if body.books else {}), "u": user, "exp": exp,
                       "sig": export_sig(cfg.monday_client_secret, fields)})
    audit(visitor.identity, "desk-export", "link")
    return JSONResponse({"url": "/api/desk/export.xlsx?" + query, "expires_in": EXPORT_LINK_S})


def _audit_link(user_id: int, outcome: str, count: int = 0) -> None:
    """gate.audit's line for the file of a signed link, which comes without a sessionToken: the user the link names."""
    logger.info("desk user=%s endpoint=desk-export-file outcome=%s results=%d", int(user_id), outcome, count)


def _local(stamp: Optional[datetime]) -> str:
    return stamp.astimezone(ZoneInfo(DESK_TZ)).strftime("%d/%m/%Y %H:%M:%S") if stamp else ""


@router.get("/api/desk/export.xlsx")
def desk_export_file(request: Request, tab: str = "b2c", state: str = "", period: str = "", q: str = "",
                     taken: str = "", customer: str = "", books: str = "", u: int = 0, exp: str = "",
                     sig: str = "") -> Response:
    """The file behind a link from /api/desk/export-link. No session header (a new tab cannot send one): the signature
    and the two minutes stand in for it, and the board's members are asked again, through the same cache as the gate.
    The reading is the one this instance holds, as the page got it - on an instance the link landed on cold, the stored
    one, and read now only when there is neither - and the file says when the page showed another (analiza-metrics
    `finance_export_file`, `stale_ok=True`)."""
    cfg = config.load()
    missing = [name for name, value in (("MONDAY_CLIENT_SECRET", cfg.monday_client_secret),
                                        ("MONDAY_TOKEN", cfg.monday_token)) if not value]
    if missing:
        return err(503, "app_not_configured", missing=missing)
    # bytes on both sides: a str with a non-ASCII character makes compare_digest raise (a 500, not a 403)
    good = hmac.compare_digest(export_sig(cfg.monday_client_secret,
                                          link_fields(u, exp, tab, state, period, q, taken, customer, books)).encode(),
                               (sig or "").encode("utf-8", "replace"))
    if not good or not exp.isdigit() or int(exp) < time.time() or tab not in TABS:
        _audit_link(u, "link_expired")
        return err(403, "link_expired")
    st = desk_state(request.app.state, cfg)
    member = st.members.member(u, STUDENT_CHARGE if tab.startswith("b2c") else B2B_CHARGE)
    if member is None:
        _audit_link(u, "members_unknown")
        return err(503, "members_unavailable")
    if not member:
        _audit_link(u, "not_a_member")
        return err(403, "not_a_board_member")
    got = _export_parts(cfg, st, u, tab, state, period, q, taken, customer, books)
    if isinstance(got, JSONResponse):
        return got
    whole, rows, about, sheets, state_label, today = (got.whole, got.rows, got.about, got.sheets, got.state_label,
                                                      got.today)
    # openpyxl only here: a file is the one thing that needs it, and nothing else of the service waits for it
    from .export import (b2b_file_names, b2b_workbook, b2c_workbook, customers_file_names, customers_workbook,
                         download_headers)

    # the file's name names its list, and its period or its company (QA 11.11, 2.12)
    if tab == "b2c":
        about_b2c, name, ascii_name = _b2c_about(got, state, period)
        data = b2c_workbook(rows, about_b2c, sheets=sheets)
    elif tab == "b2c_cohorts":
        # D4: whole when the students' tab is, and the cohorts' lines tie to their students
        from .cohorts_export import cohorts_file_names, cohorts_workbook

        part = whole["b2c"].get("cohorts") or {}
        tied = bool((part.get("checks") or {}).get("ties"))
        data = cohorts_workbook(rows, filter_unpriced(part, q), dict(about, complete=about["complete"] and tied,
                                                                     state_label=COHORTS_WORD,
                                                                     period_label="כל המחזורים עם מחיר"))
        name, ascii_name = cohorts_file_names(q, today)
    elif tab == "b2b_customers":
        # a company is monday's "חברה" (round 3); only the view keeps its name "לפי לקוח"
        data = customers_workbook(rows, dict(about, state_label="לפי לקוח", period_label="כל החברות עם משהו פתוח"))
        name, ascii_name = customers_file_names(today)
    else:
        who = next((x["account"] for x in whole["b2b"]["rows"] if x["account_id"] == customer), "") if customer else ""
        data = b2b_workbook(rows, dict(about, period_label=("חברה: " + who) if who else "כל החברות"), sheets=sheets)
        name, ascii_name = b2b_file_names(state_label, state, who, customer, today)
    _audit_link(u, "file", len(rows))
    return Response(content=data, headers=download_headers(name, ascii_name), media_type=XLSX)


def _export_parts(cfg: config.Config, st: Any, u: int, tab: str, state: str, period: str, q: str, taken: str,
                  customer: str, books: str) -> Any:
    """What a list's file is built from - the Excel file's and (D4) the students' table the page builds its file with the
    ID number from, alike: the reading this instance holds (`stale_ok`), its view with the books' part, the list as the
    page filters it, "פרטים"'s `about`, the books' lists of "לטיפול" as sheets, the list's word and the day. Or the
    refusal to send: no reading, no view, or a filter the view does not know."""
    reading, _last, _refreshing = st.reading.answer(stale_ok=True)
    if reading is None:
        return _unavailable(st)
    whole, error = _view_of(st, reading)
    if whole is None:
        return _unavailable(st, error)
    # D3: the books' part the page shows, held; asked of the copy only on an instance that holds none for any reading
    part = st.books.current(cfg)   # held; the live dismissals read again past RUN_CHECK_S (a press on another instance)
    whole = _merged(st, reading, whole, part) if part is not None else _with_books(cfg, st, reading, whole)
    try:
        rows = _filtered(whole, tab, state, period, customer, q)
    except ValueError:
        return err(400, "bad_filter")
    from .export import books_lines, books_sheet

    held, shown = _instant(reading.get("read_at")), _instant(taken)
    # compared as instants, not as printed minutes: two readings a second apart are two readings
    other = shown is not None and held is not None and shown != held
    states = B2C_STATES if tab == "b2c" else B2B_STATES
    # "לטיפול": the tile's own word for every state that waits for her (one name for one thing, round 3)
    state_label = "לטיפול" if state == "tasks" else (states[state]["label"] if state in states else "הכל")
    side = "b2c" if tab.startswith("b2c") else "b2b"
    # whole when the view's own checks of the tab hold and the reading read every board of the tab whole (stricter than
    # analiza-metrics, whose reading could not come short and still be held)
    complete = bool(whole["checks"].get(side + "_ok")) and _visible_complete(reading.get("checks") or {}, TAB_BOARDS[side])
    # D3: and when the page showed another part of the books (its key), as the totals say `other_reading`
    books_other = bool(books) and (whole.get("books") or {}).get("key") != books
    about = {"taken_at": _local(held), "screen_taken_at": _local(shown) if other else "", "complete": complete,
             "state_label": state_label, "q": q, "books_lines": books_lines(whole.get("books"), _local),
             "books_other": books_other}
    today = today_in(DESK_TZ)
    # D3: "לטיפול" of a tab carries its list of documents with no charge as a sheet of its own (the students', the
    # companies' of every company), and the unclear documents only for a member of both charge boards (spec §6.4.1)
    sheets: list = []
    if state == "tasks" and not customer and tab in ("b2c", "b2b") and (whole.get(side) or {}).get("books"):
        sheets.append(books_sheet(side, whole[side]["books"]))
        other_board = B2B_CHARGE if side == "b2c" else STUDENT_CHARGE
        if (whole.get("books_unclear") or {}).get("documents") and st.members.member(u, other_board) is True:
            sheets.append(books_sheet("unclear", whole["books_unclear"]))
    return SimpleNamespace(whole=whole, rows=rows, about=about, sheets=sheets, state_label=state_label, today=today,
                           taken_at=reading.get("read_at"))


def _b2c_about(got: Any, state: str, period: str) -> tuple:
    """The students' file's `about` with its period's word, and its two names (QA 11.11, 2.12: a file names its list and
    its period) - for the Excel file and for the table the page builds its file with the ID number from."""
    from .export import b2c_file_names

    p = next((x for x in got.whole["b2c"]["periods"] if x["key"] == period), None)
    period_label = p["label"] if p else ("כל התקופה" if not period else period)
    name, ascii_name = b2c_file_names(got.state_label, state, period_label, period, got.today)
    return dict(got.about, period_label=period_label), name, ascii_name


@router.post("/api/desk/export.json")
async def desk_export_table(request: Request) -> JSONResponse:
    """D4 (26.11): the students' Excel file as data, for the page to build with the ID number ("ייצוא לאקסל עם ת.ז",
    frontend/finance.js; written by frontend/xlsx.js): the very rows, columns, sheets and "פרטים" GET /api/desk/export.xlsx
    gives for the same filter (`_export_parts`, `_b2c_about`; export.py `b2c_table` of the file's own `b2c_book`), with an
    empty "ת.ז" column where analiza-metrics' file had it, and each row's item id and leads, which the page reads the
    number by, from monday, as the person. Never an ID number: the reading never holds one (NEVER_READ).
    Asked by the page itself, so the sessionToken comes with it and no signed link is needed (a link stands in for it only
    where a new tab cannot carry one): the gate, then Student Charge's members, then the body, the link's own
    (`ExportBody`, `_bad_filter`), the students' list only. The answer: the table, the file's `name`, the reading's
    `taken_at` and the row `count`."""
    got = await run_in_threadpool(_gated, request, "desk-export-table")
    if isinstance(got, JSONResponse):
        return got
    cfg, st, visitor = got
    body = await _body_of(request, ExportBody)
    if body is None:
        audit(visitor.identity, "desk-export-table", "invalid_filter")
        return err(400, "invalid_filter")
    if body.tab != "b2c":
        return err(400, "unknown_tab")
    if not _opens(visitor, "b2c"):
        audit(visitor.identity, "desk-export-table", "not_a_member")
        return err(403, "not_a_board_member")
    if _bad_filter(body):
        return err(400, "bad_filter")
    parts = await run_in_threadpool(_export_parts, cfg, st, visitor.identity.user_id, "b2c", body.state, body.period,
                                    body.q, body.taken, body.customer, body.books)
    if isinstance(parts, JSONResponse):
        return parts
    from .export import b2c_table

    about, name, _ascii_name = _b2c_about(parts, body.state, body.period)
    table = b2c_table(parts.rows, about, sheets=parts.sheets)
    audit(visitor.identity, "desk-export-table", "table", len(parts.rows))
    return JSONResponse(json_safe(dict(table, name=name, taken_at=parts.taken_at, count=len(parts.rows))),
                        headers={"Cache-Control": "no-store"})


# ---- how the desk is used -------------------------------------------------------------------------------------------
class UsageBody(BaseModel):
    """A batch of usage events from the desk's page: the desk, the visit's random id, the events. Every part is checked by
    app/desk/usage.py `clean_batch`; a field outside these three (a user id above all: it is the session's) is refused like
    every body of the desk's that does not fit, 400 `invalid_filter` (analiza-metrics `UsageBody`) - read only after the
    sessionToken and the usage limit (`_body_of`)."""
    model_config = ConfigDict(extra="forbid")
    desk: Any = None
    session: Any = None
    events: Any = None


@router.post("/api/usage")
async def desk_usage(request: Request) -> JSONResponse:
    """A batch of what the reader did on the desk's page (Nadav 16.11: her usage, to improve the desk over time;
    analiza-metrics `usage`, the same contract). Any signed-in reader of Analiza's account, for their own user id only:
    the sessionToken and nothing else of the gate - no board's membership (the desk refusing someone is itself an event,
    `load_failed`), no guest refusal, and never the reader's desk budget. Each event is checked against the closed
    vocabulary, where a name, an amount, a search's text or an ID number cannot pass. Its own limit (USAGE_BATCHES_*).

    `{stored, refused}`; 400 `bad_usage` for a batch of another shape; 400 `invalid_filter` for a body that is not one (not
    JSON, or a field the batch does not have); 503 `usage_unavailable` when nothing can be kept - no DATABASE_URL, no
    table (`store_schema`), or a write that failed - said in /health's `desk.usage` by its word. The page stops sending for
    the visit at its first failure, quietly.

    In this order (D2's review: FastAPI parsed and validated the body before the route, so an anonymous client's body of
    another shape was 400, not 401, and neither the token nor the limit bounded what it sent): the secret, the
    sessionToken, this reader's usage limit, and only then the body. async for that: the token, the limit and the body
    take no I/O but the request's own; the batch's check and its one short database write run in the threadpool.

    What changed with the move: the token is checked by this service's auth (Analiza's account pinned, nothing unpins
    it); a batch that finds no database is counted in /health as `no_store` (analiza-metrics did not count it); a failed
    write is said by the store's kind, not only its class; and the audit line is the desk's, `outcome=ok` or
    `some_refused` with the events kept as `results` (the refused ones are counted in /health)."""
    cfg = config.load()
    if not cfg.monday_client_secret:
        audit(None, "usage", "app_not_configured")
        return err(503, "app_not_configured", missing=["MONDAY_CLIENT_SECRET"])
    try:
        identity = verify_session_token(bearer(request), cfg.monday_client_secret)
    except AuthError as exc:
        audit(None, "usage", exc.error_key)
        return err(401, exc.error_key)
    us = usage_state(request.app.state)
    wait = us.limiter.check(identity.user_id)
    if wait is not None:
        audit(identity, "usage", "rate_limited")
        return err(429, "rate_limited", headers={"Retry-After": str(wait)}, retry_after_seconds=wait)
    body = await _body_of(request, UsageBody)
    if body is None:
        audit(identity, "usage", "invalid_filter")  # not counted in /health: as before, such a body is no batch
        return err(400, "invalid_filter")
    return await run_in_threadpool(_keep_usage, cfg, us, identity, body)


def _keep_usage(cfg: config.Config, us: Any, identity: Any, body: UsageBody) -> JSONResponse:
    """A batch whose reader is known and within the limit: checked against the vocabulary, then written."""
    try:
        rows, refused = clean_batch(body.model_dump(), identity.user_id)
    except Refused as r:
        us.stats.note(error=r.code)
        audit(identity, "usage", r.code)
        return err(400, r.code)
    if not cfg.database_url:
        us.stats.note(refused=refused, error="no_store")
        audit(identity, "usage", "no_store")
        return err(503, "usage_unavailable")
    try:
        stored = append_events(cfg.database_url, rows)
    except Exception as exc:  # noqa: BLE001 - said by its kind or its class, never its text; the page drops the batch
        word = exc.kind if isinstance(exc, UsageStoreError) else type(exc).__name__
        detail = exc.detail if isinstance(exc, UsageStoreError) else ""
        us.stats.note(refused=refused, error=word)
        logger.warning("usage not written (%s%s)", word, " " + detail if detail else "")
        audit(identity, "usage", word)
        return err(503, "usage_unavailable")
    us.stats.note(stored=stored, refused=refused)
    # closed words and one count: the refused events are counted in /health, never named
    audit(identity, "usage", "some_refused" if refused else "ok", stored)
    return JSONResponse({"stored": stored, "refused": refused})


# ---- "לא פער" (R5) ------------------------------------------------------------------------------------------------------
# What a press is answered, past the gate: each word of DeskBooks' with its status, and the route's own (a body of another
# shape, a reader who may not press)
DISMISS_STATUS = {"dismissals_unavailable": 503, "busy": 503, "other_reading": 409, "not_shown": 409,
                  "already_hidden": 409, "evidence_changed": 409, "not_live": 409, "not_yours": 403}
EVIDENCE_CHARS = 64


class DismissBody(BaseModel):
    """A press of "לא פער" (or "לא להציע"): the alert - its kind (app/desk/dismissals.py KINDS) and its subject (an item
    id or a document's number) - the evidence the page was sent with it, and the reading and the books' part the page
    shows (its `taken_at` and its `books.key`). Anything else in the body is 400 `invalid_filter`, read after the gate."""
    model_config = ConfigDict(extra="forbid")
    kind: Any = None
    subject: Any = None
    evidence: Any = None
    taken: Any = None
    books: Any = None


class UndoBody(BaseModel):
    """A press of "החזרה" in the hidden list: the dismissal's kind, subject and id, as the list showed them."""
    model_config = ConfigDict(extra="forbid")
    kind: Any = None
    subject: Any = None
    id: Any = None


def _alert_ok(kind: Any, subject: Any) -> bool:
    return (isinstance(kind, str) and kind in dz.KINDS and isinstance(subject, str) and subject.isascii()
            and subject.isdigit() and 0 < len(subject) <= dz.SUBJECT_MAX)


def _may_press(visitor: Visitor, kind: str, endpoint: str) -> Optional[JSONResponse]:
    """A member of each of the alert's boards (dz.BOARDS: the students' marks, proposals and documents Student Charge's,
    the companies' documents B2B Charge's, the unclear ones both), and never a viewer: a dismissal hides the alert from
    everyone, and a viewer of a board writes nothing on it (app/desk/gate.py). Checked by the kind alone, before anything
    about the alert is looked up, so a reader of one board learns nothing of the other's documents."""
    tabs = {"b2c": visitor.b2c, "b2b": visitor.b2b}
    if not all(tabs[b] is True for b in dz.BOARDS[kind]):
        audit(visitor.identity, endpoint, "not_a_member")
        return err(403, "not_a_board_member")
    if visitor.identity.view_only is True:
        audit(visitor.identity, endpoint, "view_only")
        return err(403, "view_only")
    return None


def _refused(visitor: Visitor, endpoint: str, word: str) -> JSONResponse:
    audit(visitor.identity, endpoint, word)
    return err(DISMISS_STATUS[word], word)


@router.post("/api/alerts/not-a-gap")
async def alert_not_a_gap(request: Request) -> JSONResponse:
    """"לא פער" on one alert of the books (R5; the spec §2.4: the sessionToken, a member of the alert's board). The gate
    first, in the threadpool (it may read a board's members), and only then the body. The server keeps the evidence it
    computed itself for the alert the part shows now (app/desk/books.py DeskBooks.dismiss) and refuses a press whose
    evidence is not that (`evidence_changed`): a hash from the page is compared, never kept. It writes only its own
    database. Answered with the desk as it is after the press, and `dismissed`: {kind, subject, id}."""
    got = await run_in_threadpool(_gated, request, "not-a-gap")
    if isinstance(got, JSONResponse):
        return got
    cfg, st, visitor = got
    body = await _body_of(request, DismissBody)
    if body is None:
        audit(visitor.identity, "not-a-gap", "invalid_filter")
        return err(400, "invalid_filter")
    return await run_in_threadpool(_dismiss, cfg, st, visitor, body)


def _dismiss(cfg: config.Config, st: Any, visitor: Visitor, body: DismissBody) -> JSONResponse:
    ep = "not-a-gap"
    if not _alert_ok(body.kind, body.subject) or not isinstance(body.evidence, str) \
            or len(body.evidence) != EVIDENCE_CHARS or not all(c in "0123456789abcdef" for c in body.evidence) \
            or not isinstance(body.taken, str) or len(body.taken) > 40 \
            or not isinstance(body.books, str) or len(body.books) > BOOKS_KEY_MAX:
        audit(visitor.identity, ep, "bad_alert")
        return err(400, "bad_alert")
    refused = _may_press(visitor, body.kind, ep)
    if refused is not None:
        return refused
    reading = st.reading.reading   # as held: a press reads nothing of monday
    if reading is None or _instant(body.taken) is None or _instant(body.taken) != _instant(reading.get("read_at")):
        return _refused(visitor, ep, "other_reading")
    done = st.books.dismiss(cfg, body.kind, body.subject, body.evidence, body.books, reading.get("read_at"),
                            visitor.identity.user_id)
    if "error" in done:
        return _refused(visitor, ep, done["error"])
    audit(visitor.identity, ep, "replaced" if done["replaced"] else "ok")
    return _answer(cfg, st, visitor, ep, force=False, stale_ok=True,
                   extra={"dismissed": {"kind": body.kind, "subject": body.subject, "id": done["id"]}})


@router.post("/api/alerts/not-a-gap/undo")
async def alert_not_a_gap_undo(request: Request) -> JSONResponse:
    """"החזרה": the undo of one's own "לא פער" (R5): the same gate and boards, only a dismissal the part shown now holds
    hidden, and only by whoever dismissed it (ADR-0190 §6's norm; the repo has no admin), never deleted - its row keeps who
    undid it and when. Answered with the desk as it is after it, and `undone`: {kind, subject, id}."""
    got = await run_in_threadpool(_gated, request, "not-a-gap-undo")
    if isinstance(got, JSONResponse):
        return got
    cfg, st, visitor = got
    body = await _body_of(request, UndoBody)
    if body is None:
        audit(visitor.identity, "not-a-gap-undo", "invalid_filter")
        return err(400, "invalid_filter")
    return await run_in_threadpool(_undo, cfg, st, visitor, body)


def _undo(cfg: config.Config, st: Any, visitor: Visitor, body: UndoBody) -> JSONResponse:
    ep = "not-a-gap-undo"
    if not _alert_ok(body.kind, body.subject) or type(body.id) is not int or not 0 < body.id < 2 ** 63:
        audit(visitor.identity, ep, "bad_alert")
        return err(400, "bad_alert")
    refused = _may_press(visitor, body.kind, ep)
    if refused is not None:
        return refused
    reading = st.reading.reading
    done = st.books.undo(cfg, body.kind, body.subject, body.id, None if reading is None else reading.get("read_at"),
                         visitor.identity.user_id)
    if "error" in done:
        return _refused(visitor, ep, done["error"])
    audit(visitor.identity, ep, "ok")
    return _answer(cfg, st, visitor, ep, force=False, stale_ok=True,
                   extra={"undone": {"kind": body.kind, "subject": body.subject, "id": body.id}})
