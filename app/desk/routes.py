"""The desk's page and its routes (Analiza-Monday backlog 0301 D1 and D2; the desk move plan §2-§3).

- GET /desk/ (and /desk): the page of the Board View "כספים", a second feature of the same monday app (the plan §2, option
  A; its External hosting URL is https://analiza-finance.replit.app/desk/). Served `no-cache` with an ETag of its bytes,
  its assets linked as `?v=<hash of their bytes>` and kept a year: monday reloads the iframe on every open, and a stale
  script against a new answer is a blank view (analiza-metrics app/main.py, the same rule).
- GET /desk/desk.js, /desk/desk.css, /desk/finance.js, /desk/finance.css, /desk/check.js, /desk/vendor/monday-sdk.js: the
  assets. D2: finance.js and finance.css are the desk, ported from analiza-metrics; desk.js mounts it; check.js is D1's
  self-check, drawn on /desk/?check=1. The SDK is analiza-metrics' copy, byte for byte (monday-sdk-js 0.4.12; a test holds
  its sha256).
- GET /api/desk: the gate (app/desk/gate.py), then who the reader is and the desk's reading (app/desk/reading.py) as
  counts, for the tabs the reader may see (D1), and the desk itself (D2): `view`, those tabs built from that reading
  (app/desk/view.py), a tab whose board the reader is not on removed, not hidden. It carries names and money, so only a
  tab's own members get it. Always 200 after the gate: no reading is `view: null`, and `last_attempt` says why.
- POST /api/desk/refresh: the same answer from a reading that started after the press, at most one forced reading per
  REFRESH_GAP_S per instance (429 `rate_limited`).
- GET /api/desk/totals: a list's totals exactly as the page filters it, from the reading the page shows; never a read.
- POST /api/desk/export-link, GET /api/desk/export.xlsx: a list as an Excel file (app/desk/export.py) behind a link signed
  with this service's own key, EXPORT_LINK_S long.
- POST /api/usage: what the reader did on the page, as closed words and counts (app/desk/usage.py), kept in `desk_events`
  (app/desk/store.py, migrations/005). Behind the sessionToken only, with a limit of its own.

D2's routes are analiza-metrics app/finance_api.py at `da671b2` (`finance_view`, `finance_refresh`, `finance_totals`,
`finance_export_link`, `finance_export_file`, `usage`) on this service's gate and reading, with what changed said at each.
Not ported: POST /api/finance/account (ADR-0188: a customer's billing details are the person's write, in the browser).

Nothing the page or its assets carry is data: everything comes from /api/desk, behind the sessionToken, and what the server
never reads (an ID number, a refund's bank account, a company's ח.פ) the page reads from monday as the person. The service
writes nothing to monday (ADR-0188): `MONDAY_TOKEN` reads only (app/monday/client.py refuses a mutation); the desk's writes
go out from the browser, as the person (finance.js mondayWrite). No ID number, bank account or business number is ever in
an answer or a file: the reading never holds one (NEVER_READ).
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

from .. import config
from ..monday.client import MondayReader
from .auth import AuthError, verify_session_token
from .gate import DESK_PER_DAY, DESK_PER_USER_HOUR, Members, RateLimiter, Visitor, audit, bearer, err, gate
from .reading import B2B_CHARGE, STUDENT_CHARGE, TAB_BOARDS, HeldReading
from .store import UsageStoreError, append_events
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
    "/desk/vendor/monday-sdk.js": ("vendor/monday-sdk.js", "application/javascript; charset=utf-8"),
}
PAGE = "index.html"
# the page's links to its assets, rewritten to `?v=<hash>` when the page is served
ASSET_LINKS = (("/desk/desk.js", 'src="/desk/desk.js"'), ("/desk/desk.css", 'href="/desk/desk.css"'),
               ("/desk/finance.js", 'src="/desk/finance.js"'), ("/desk/finance.css", 'href="/desk/finance.css"'),
               ("/desk/check.js", 'src="/desk/check.js"'),
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
# the lists a total or a file is of: the students', the companies', the aging by customer
TABS = ("b2c", "b2b", "b2b_customers")
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


def desk_state(state: Any, cfg: config.Config) -> SimpleNamespace:
    """One per instance, made again when the token or the test transport changes (the config is read per request)."""
    key = (hashlib.sha256(cfg.monday_token.encode("utf-8")).hexdigest(), id(getattr(state, "monday_transport", None)))
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
        # `view`, the view of the reading held, built once for it: (that reading, its view or None, None or "crashed")
        made = SimpleNamespace(key=key, members=Members(subscribers),
                               reading=HeldReading(lambda: _reader(state, cfg.monday_token)),
                               limiter=RateLimiter(DESK_PER_USER_HOUR, DESK_PER_DAY),
                               refresh=RateLimiter(1, 1, user_window=REFRESH_GAP_S, global_window=REFRESH_GAP_S),
                               view=None, view_lock=threading.Lock())
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


def health_block(state: Any, cfg: config.Config) -> dict:
    """The desk in /health: whether its two secrets are set, this instance's last reading (no count of rows), whether
    each board's members were read, and the usage batches: written, events refused, writes failed (`usage`). Never a
    secret, a user, a row or an event."""
    from .reading import health_part

    current = getattr(state, "desk", None)
    return {"client_secret_set": bool(cfg.monday_client_secret), "monday_token_set": bool(cfg.monday_token),
            "page": "/desk/",
            "last_reading": None if current is None else health_part(current.reading.last),
            "members": {} if current is None else current.members.state(),
            "usage": usage_state(state).stats.health()}


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
    return (got.b2c if tab == "b2c" else got.b2b) is True


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


def _instant(text: Any) -> Optional[datetime]:
    """A reading's time as an instant, or None for one that is not a time: two readings are compared as instants, never as
    printed text (analiza-metrics, review 10.11)."""
    try:
        return datetime.fromisoformat(text) if text else None
    except (TypeError, ValueError):
        return None


def _unavailable(st: Any, reason: Optional[str] = None) -> JSONResponse:
    """No reading to answer from: 503 `finance_unavailable` with the last attempt's code, or `not_read` (analiza-metrics'
    word, which the page ported from it reads)."""
    last = st.reading.last or {}
    return err(503, "finance_unavailable", reason=reason or last.get("error") or "not_read")


def _answer(st: Any, got: Visitor, endpoint: str, force: bool) -> JSONResponse:
    """The desk for this reader. D1's counts (`user`, `tabs`, `reading`, `last_attempt`) and since D2 the desk itself:
    `view`, the tabs the reader may see (`visible_part`), its money as strings; `view_error`, "crashed" when the reading
    could not be built into one; `taken_at`, the reading's time, which the totals and the files are asked by; its
    `age_seconds`; `refreshing`, always false (a reading is made inside the request, never after it); and `stale`, when
    the reading shown is older than STALE_S because every reading since has failed. No reading: `view` is null and
    `last_attempt` says why."""
    # True, False, or None when the board's members could not be read: only True opens the tab
    tabs = {"b2c": got.b2c, "b2b": got.b2b}
    keys = [key for tab, seen in tabs.items() if seen is True for key in TAB_BOARDS[tab]]
    reading, last = st.reading.get(force=force)
    age = st.reading.age_s() if reading is not None else None
    shown, view, view_error = None, None, None
    if reading is not None:
        checks = reading.get("checks") or {}
        shown = {"read_at": reading.get("read_at"), "age_seconds": age,
                 "complete": _visible_complete(checks, keys), "seconds": reading.get("seconds"),
                 "calls": reading.get("calls"), "boards": {k: checks[k] for k in keys if k in checks}}
        whole, view_error = _view_of(st, reading)
        if whole is not None:
            view = visible_part(whole, got.b2c is True, got.b2b is True)
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
        "refreshing": False,
        "stale": bool(reading is not None and failing and age is not None and age > STALE_S),
    }))


@router.get("/api/desk")
def desk_api(request: Request) -> JSONResponse:
    """The desk (D2) over the skeleton's answer (D1): who the reader is, which tabs they may see, the desk's reading as
    counts for those tabs' boards, and the view of those tabs. sync def on purpose: a reading is seconds of blocking I/O,
    run in the threadpool."""
    got = _gated(request, "desk")
    if isinstance(got, JSONResponse):
        return got
    _cfg, st, visitor = got
    return _answer(st, visitor, "desk", force=False)


@router.post("/api/desk/refresh")
def desk_refresh(request: Request) -> JSONResponse:
    """Read the boards again now: the "רענון" press, and the page's own after it writes. The same answer as /api/desk,
    from a reading that started after the press; at most one press per REFRESH_GAP_S per instance (analiza-metrics
    `finance_refresh`, the same limit)."""
    got = _gated(request, "desk-refresh")
    if isinstance(got, JSONResponse):
        return got
    _cfg, st, visitor = got
    wait = st.refresh.check(visitor.identity.user_id)
    if wait is not None:
        audit(visitor.identity, "desk-refresh", "rate_limited")
        return err(429, "rate_limited", headers={"Retry-After": str(wait)}, retry_after_seconds=wait)
    return _answer(st, visitor, "desk-refresh", force=True)


def _filtered(whole: dict, tab: str, state: str, period: str, customer: str, q: str) -> list:
    """A list exactly as the page filters it; ValueError for a filter the desk does not know."""
    if tab == "b2c":
        return filter_b2c(whole["b2c"], state, period, q)
    if tab == "b2b":
        return filter_b2b(whole["b2b"], state, customer, q)
    return filter_customers(whole["b2b"]["customers"], q)


@router.get("/api/desk/totals")
def desk_totals(request: Request, tab: str = "b2c", state: str = "", period: str = "", customer: str = "", q: str = "",
                taken: str = "") -> JSONResponse:
    """The totals of a list exactly as the page filters it (Nadav 10.11 "תעשה את 6"): summed here, never in the page. The
    same gate and the same filters as the Excel file. Only from the reading the page shows (`taken`): this route never
    reads monday, and when this instance holds another reading it says so (`other_reading`) instead of a total that does
    not belong to the rows on the screen (analiza-metrics `finance_totals`, review 10.11)."""
    got = _gated(request, "desk-totals")
    if isinstance(got, JSONResponse):
        return got
    _cfg, st, visitor = got
    if tab not in TABS:
        return err(400, "unknown_tab")
    if not _opens(visitor, tab):
        audit(visitor.identity, "desk-totals", "not_a_member")
        return err(403, "not_a_board_member")
    if not query_exportable(q) or len(period) > 32 or len(customer) > 32 or len(taken) > 40:
        return err(400, "bad_filter")
    reading = st.reading.reading  # as held: a totals request reads nothing
    if reading is None:
        return _unavailable(st)
    taken_at = reading.get("read_at")
    shown = _instant(taken)
    if shown is None or _instant(taken_at) is None or shown != _instant(taken_at):
        audit(visitor.identity, "desk-totals", "other_reading")
        return JSONResponse(json_safe({"other_reading": True, "taken_at": taken_at}))
    whole, error = _view_of(st, reading)
    if whole is None:
        return _unavailable(st, error)
    try:
        rows = _filtered(whole, tab, state, period, customer, q)
    except ValueError:
        return err(400, "bad_filter")
    audit(visitor.identity, "desk-totals", "ok", len(rows))
    return JSONResponse(json_safe({"count": len(rows), "sums": list_totals(tab, rows), "taken_at": taken_at}))


class ExportBody(BaseModel):
    """What a list shows: its tab, a state ('' all, 'tasks', or a state key), a period key, the search box, the reading the
    page shows (its taken_at: the file says when it is built from another) and a customer (an account id, '' for all).
    Anything else in the body is 400 `invalid_filter` (app/main.py)."""
    model_config = ConfigDict(extra="forbid")
    tab: str = "b2c"
    state: str = ""
    period: str = ""
    q: str = ""
    taken: str = ""
    customer: str = ""


def export_sig(client_secret: str, fields: list) -> str:
    """The link's signature: an HMAC of its fields under a key of this service's own, so a link signed by
    analiza-metrics (whose key is derived from the same Client Secret, under another context) is not valid here. JSON,
    not a joined string: no text in one field can pass for the boundary of the next (analiza-metrics, review 10.11)."""
    key = hashlib.sha256(EXPORT_KEY_CONTEXT + client_secret.encode("utf-8")).digest()
    return hmac.new(key, json.dumps(fields, ensure_ascii=True).encode(), hashlib.sha256).hexdigest()


@router.post("/api/desk/export-link")
def desk_export_link(request: Request, body: ExportBody) -> JSONResponse:
    """A two-minute link to a list as an Excel file - the students', the companies' or the aging by customer - exactly as
    the page filters it. The link carries the reader and the filter, signed; the file route checks the reader's board
    again (Student Charge for the students, B2B Charge for the companies). It opens in a new tab, which cannot send the
    sessionToken: monday may sandbox the view's iframe against downloads (analiza-metrics `finance_export_link`)."""
    got = _gated(request, "desk-export")
    if isinstance(got, JSONResponse):
        return got
    cfg, _st, visitor = got
    if body.tab not in TABS:
        return err(400, "unknown_tab")
    if not _opens(visitor, body.tab):
        audit(visitor.identity, "desk-export", "not_a_member")
        return err(403, "not_a_board_member")
    states = B2C_STATES if body.tab == "b2c" else B2B_STATES
    if (body.state and body.state != "tasks" and body.state not in states) or len(body.period) > 32 \
            or len(body.q) > 100 or len(body.taken) > 40 \
            or not (body.customer == "" or (body.customer.isascii() and body.customer.isalnum() and len(body.customer) <= 32)) \
            or not query_exportable(body.q):
        return err(400, "bad_filter")
    user = visitor.identity.user_id
    exp = str(int(time.time()) + EXPORT_LINK_S)
    fields = [str(user), exp, body.tab, body.state, body.period, body.q, body.taken, body.customer]
    query = urlencode({"tab": body.tab, "state": body.state, "period": body.period, "q": body.q, "taken": body.taken,
                       "customer": body.customer, "u": user, "exp": exp,
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
                     taken: str = "", customer: str = "", u: int = 0, exp: str = "", sig: str = "") -> Response:
    """The file behind a link from /api/desk/export-link. No session header (a new tab cannot send one): the signature
    and the two minutes stand in for it, and the board's members are asked again, through the same cache as the gate.
    The reading is the one this instance holds, as the page got it - read now only when it holds none (an instance the
    link landed on cold) - and the file says when the page showed another (analiza-metrics `finance_export_file`)."""
    cfg = config.load()
    missing = [name for name, value in (("MONDAY_CLIENT_SECRET", cfg.monday_client_secret),
                                        ("MONDAY_TOKEN", cfg.monday_token)) if not value]
    if missing:
        return err(503, "app_not_configured", missing=missing)
    # bytes on both sides: a str with a non-ASCII character makes compare_digest raise (a 500, not a 403)
    good = hmac.compare_digest(export_sig(cfg.monday_client_secret, [str(u), exp, tab, state, period, q, taken, customer])
                               .encode(), (sig or "").encode("utf-8", "replace"))
    if not good or not exp.isdigit() or int(exp) < time.time() or tab not in TABS:
        _audit_link(u, "link_expired")
        return err(403, "link_expired")
    st = desk_state(request.app.state, cfg)
    member = st.members.member(u, STUDENT_CHARGE if tab == "b2c" else B2B_CHARGE)
    if member is None:
        _audit_link(u, "members_unknown")
        return err(503, "members_unavailable")
    if not member:
        _audit_link(u, "not_a_member")
        return err(403, "not_a_board_member")
    reading = st.reading.reading
    if reading is None:
        reading, _last = st.reading.get()
    if reading is None:
        return _unavailable(st)
    whole, error = _view_of(st, reading)
    if whole is None:
        return _unavailable(st, error)
    try:
        rows = _filtered(whole, tab, state, period, customer, q)
    except ValueError:
        return err(400, "bad_filter")
    # openpyxl only here: a file is the one thing that needs it, and nothing else of the service waits for it
    from .export import (b2b_file_names, b2b_workbook, b2c_file_names, b2c_workbook, customers_file_names,
                         customers_workbook, download_headers)

    held, shown = _instant(reading.get("read_at")), _instant(taken)
    # compared as instants, not as printed minutes: two readings a second apart are two readings
    other = shown is not None and held is not None and shown != held
    states = B2C_STATES if tab == "b2c" else B2B_STATES
    # "לטיפול": the tile's own word for every state that waits for her (one name for one thing, round 3)
    state_label = "לטיפול" if state == "tasks" else (states[state]["label"] if state in states else "הכל")
    side = "b2c" if tab == "b2c" else "b2b"
    # whole when the view's own checks of the tab hold and the reading read every board of the tab whole (stricter than
    # analiza-metrics, whose reading could not come short and still be held)
    complete = bool(whole["checks"].get(side + "_ok")) and _visible_complete(reading.get("checks") or {}, TAB_BOARDS[side])
    about = {"taken_at": _local(held), "screen_taken_at": _local(shown) if other else "", "complete": complete,
             "state_label": state_label, "q": q}
    today = today_in(DESK_TZ)
    # the file's name names its list, and its period or its company (QA 11.11, 2.12)
    if tab == "b2c":
        p = next((x for x in whole["b2c"]["periods"] if x["key"] == period), None)
        period_label = p["label"] if p else ("כל התקופה" if not period else period)
        data = b2c_workbook(rows, dict(about, period_label=period_label))
        name, ascii_name = b2c_file_names(state_label, state, period_label, period, today)
    elif tab == "b2b_customers":
        # a company is monday's "חברה" (round 3); only the view keeps its name "לפי לקוח"
        data = customers_workbook(rows, dict(about, state_label="לפי לקוח", period_label="כל החברות עם משהו פתוח"))
        name, ascii_name = customers_file_names(today)
    else:
        who = next((x["account"] for x in whole["b2b"]["rows"] if x["account_id"] == customer), "") if customer else ""
        data = b2b_workbook(rows, dict(about, period_label=("חברה: " + who) if who else "כל החברות"))
        name, ascii_name = b2b_file_names(state_label, state, who, customer, today)
    _audit_link(u, "file", len(rows))
    return Response(content=data, headers=download_headers(name, ascii_name), media_type=XLSX)


# ---- how the desk is used -------------------------------------------------------------------------------------------
class UsageBody(BaseModel):
    """A batch of usage events from the desk's page: the desk, the visit's random id, the events. Every part is checked by
    app/desk/usage.py `clean_batch`; a field outside these three (a user id above all: it is the session's) is refused like
    every body of the desk's that does not fit, 400 `invalid_filter` (app/main.py; analiza-metrics `UsageBody`)."""
    model_config = ConfigDict(extra="forbid")
    desk: Any = None
    session: Any = None
    events: Any = None


@router.post("/api/usage")
def desk_usage(request: Request, body: UsageBody) -> JSONResponse:
    """A batch of what the reader did on the desk's page (Nadav 16.11: her usage, to improve the desk over time;
    analiza-metrics `usage`, the same contract). Any signed-in reader of Analiza's account, for their own user id only:
    the sessionToken and nothing else of the gate - no board's membership (the desk refusing someone is itself an event,
    `load_failed`), no guest refusal, and never the reader's desk budget. Each event is checked against the closed
    vocabulary, where a name, an amount, a search's text or an ID number cannot pass. Its own limit (USAGE_BATCHES_*).

    `{stored, refused}`; 400 `bad_usage` for a batch of another shape; 503 `usage_unavailable` when nothing can be kept -
    no DATABASE_URL, no table (`store_schema`), or a write that failed - said in /health's `desk.usage` by its word. The
    page stops sending for the visit at its first failure, quietly. sync def on purpose: one short database write.

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
