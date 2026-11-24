"""The desk's page and its route (Analiza-Monday backlog 0301 D1; the desk move plan §2-§3).

- GET /desk/ (and /desk): the page of the Board View "כספים", a second feature of the same monday app (the plan §2, option
  A; its External hosting URL is https://analiza-finance.replit.app/desk/). Served `no-cache` with an ETag of its bytes,
  its assets linked as `?v=<hash of their bytes>` and kept a year: monday reloads the iframe on every open, and a stale
  script against a new answer is a blank view (analiza-metrics app/main.py, the same rule).
- GET /desk/desk.js, /desk/desk.css, /desk/finance.js, /desk/finance.css, /desk/check.js, /desk/vendor/monday-sdk.js: the
  assets. D2: finance.js and finance.css are the desk, ported from analiza-metrics; desk.js mounts it; check.js is D1's
  self-check, drawn on /desk/?check=1. The SDK is analiza-metrics' copy, byte for byte (monday-sdk-js 0.4.12; a test holds
  its sha256).
- GET /api/desk: the gate (app/desk/gate.py), then who the reader is and the desk's reading (app/desk/reading.py): its
  counts only, and only for the tabs the reader may see. The self-check shows these as its checklist; D2's server answers
  the desk's view here too.

Nothing the page or its assets carry is data: everything comes from /api/desk, behind the sessionToken, and what the server
never reads (an ID number, a refund's bank account, a company's ח.פ) the page reads from monday as the person. The service
writes nothing to monday (ADR-0188): `MONDAY_TOKEN` reads only (app/monday/client.py refuses a mutation); the desk's
writes go out from the browser, as the person (finance.js mondayWrite).
"""

from __future__ import annotations

import hashlib
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from .. import config
from ..monday.client import MondayReader
from .gate import DESK_PER_DAY, DESK_PER_USER_HOUR, Members, RateLimiter, audit, err, gate
from .reading import B2B_CHARGE, STUDENT_CHARGE, TAB_BOARDS, HeldReading

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

_hashes: dict = {}
_hash_lock = threading.Lock()
_state_lock = threading.Lock()


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

        made = SimpleNamespace(key=key, members=Members(subscribers),
                               reading=HeldReading(lambda: _reader(state, cfg.monday_token)),
                               limiter=RateLimiter(DESK_PER_USER_HOUR, DESK_PER_DAY))
        state.desk = made
        return made


def health_block(state: Any, cfg: config.Config) -> dict:
    """The desk in /health: whether its two secrets are set, this instance's last reading (no count of rows), and whether
    each board's members were read. Never a secret, a user or a row."""
    from .reading import health_part

    current = getattr(state, "desk", None)
    return {"client_secret_set": bool(cfg.monday_client_secret), "monday_token_set": bool(cfg.monday_token),
            "page": "/desk/",
            "last_reading": None if current is None else health_part(current.reading.last),
            "members": {} if current is None else current.members.state()}


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


# ---- the route ----------------------------------------------------------------------------------------------------
def _visible_complete(checks: dict, keys: list) -> bool:
    """Complete for these boards: each was read and is whole. No board to show is not complete."""
    return bool(keys) and all((checks.get(k) or {}).get("ok") is True for k in keys)


def _attempt(last: Any, keys: list, both: bool) -> Any:
    """The last attempt as this reader may see it: its time, its closed code, and whether the boards of their own tabs
    were whole. The detail (board and column ids) only to a reader of both tabs (D1's review: a one-tab reader was told
    of the other tab's boards)."""
    if last is None:
        return None
    out = {"read_at": last.get("read_at"), "error": last.get("error"),
           "complete": not last.get("error") and _visible_complete(last.get("checks") or {}, keys)}
    if both:
        out["detail"] = last.get("detail")
    return out


@router.get("/api/desk")
def desk_api(request: Request) -> JSONResponse:
    """The skeleton's answer (D1): who the reader is, which tabs they may see, and the desk's reading as counts, for those
    tabs' boards only. sync def on purpose: a reading is seconds of blocking I/O, run in the threadpool."""
    cfg = config.load()
    missing = [name for name, value in (("MONDAY_CLIENT_SECRET", cfg.monday_client_secret),
                                        ("MONDAY_TOKEN", cfg.monday_token)) if not value]
    if missing:
        audit(None, "desk", "app_not_configured")
        return err(503, "app_not_configured", missing=missing)
    st = desk_state(request.app.state, cfg)
    got = gate(request, cfg.monday_client_secret, st.members, st.limiter, "desk", STUDENT_CHARGE, B2B_CHARGE)
    if isinstance(got, JSONResponse):
        return got
    # True, False, or None when the board's members could not be read: only True opens the tab
    tabs = {"b2c": got.b2c, "b2b": got.b2b}
    keys = [key for tab, seen in tabs.items() if seen is True for key in TAB_BOARDS[tab]]
    reading, last = st.reading.get()
    shown = None
    if reading is not None:
        checks = reading.get("checks") or {}
        shown = {"read_at": reading.get("read_at"), "age_seconds": st.reading.age_s(),
                 "complete": _visible_complete(checks, keys), "seconds": reading.get("seconds"),
                 "calls": reading.get("calls"), "boards": {k: checks[k] for k in keys if k in checks}}
    ident = got.identity
    audit(ident, "desk", "ok" if shown and shown["complete"] else ("incomplete" if shown else "no_reading"),
          sum(int(c.get("rows") or 0) for c in (shown or {}).get("boards", {}).values()))
    return JSONResponse({
        "user": {"id": ident.user_id, "view_only": ident.view_only, "guest": ident.guest,
                 "claims": list(ident.claim_names)},
        "tabs": tabs,
        "reading": shown,
        "last_attempt": _attempt(last, keys, got.b2c is True and got.b2b is True),
    })
