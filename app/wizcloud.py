"""A read-only client for the Hashavshevet cloud API (WizCloud).

The API can also create, issue and delete documents, post journal batches, import
accounts and register webhooks. This client cannot: every call goes through
`READ_PATHS`, an exact-match allowlist, and anything else is refused before a
request is built. Writing to the books is out of scope for this service
(ADR-0176 in the docs repo); widening the list is a decision, not a code change.

Auth, from the vendor docs (docs.wizcloud.co.il, read 2026-11-18):
1. `GET https://<server>/createSession/<private key>/<wizdb...>` returns a session
   token that lives 24 hours. The private key travels in the URL path, so nothing
   here ever logs or echoes a URL.
2. Every other call is a POST with `Authorization: Bearer <session token>`.

Error shapes seen live on 2026-11-18 (lb1 and lb19, garbage tokens):
- createSession, bad key: 400, plain text `iligal token`.
- TokenCompanies, no header: 500 `{"statusCode":500,"status":{"errors":"Not connected"}}`.
- TokenCompanies, bad session: 500 `{"statusCode":500,"status":{"errors":"Token is not valid"}}`.
- Triggers getURL, no header: 400 `{"status":"error","statusCode":400,"message":"Invalid params, no Authorization key in headers "}`.
- Triggers getURL, bad session: 400 `{"status":"error","statusCode":400,"message":"Invalid token"}`.
Success seen live on 2026-11-18 (live-check, GitHub Actions): createSession answers raw
text (224 characters), TokenCompanies a list under `status`, getURL an empty list. Other
success shapes are still read defensively, and every check reports what it found.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import httpx

# httpx logs every request line at INFO, and the createSession line carries the
# private key. Keep that logger quiet whatever the app's logging level is.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

READ_PATHS = frozenset(
    {
        "CompanyListToTokenApi/TokenCompanies",  # companies this token reaches
        "TriggersApi/getURL",  # webhooks already registered on the company
        "ExportDataApi/exportData",  # run a report defined in the report designer
        "invApi/getDoc",  # read one document by its internal id
    }
)

SESSION_TTL_S = 23 * 3600  # the vendor says 24h; renew an hour early
TIMEOUT_S = 30.0

# Closed vocabulary for what went wrong. /check reports exactly one of these.
KINDS = (
    "config_missing",  # no private key, server or company in the environment
    "bad_key",  # createSession refused the private key
    "module_inactive",  # the paid API module is not active on the company
    "session_rejected",  # an API call refused a fresh session token
    "http_error",  # any other non-2xx answer
    "network",  # timeout, DNS, connection refused
    "bad_response",  # 2xx that we could not read
    "forbidden_path",  # a call outside READ_PATHS (a bug, never the vendor)
)


class WizError(Exception):
    def __init__(self, kind: str, message: str = "", status: Optional[int] = None):
        assert kind in KINDS, kind
        self.kind = kind
        self.status = status
        self.message = message
        super().__init__(f"{kind}" + (f" [{status}]" if status else "") + (f": {message}" if message else ""))

    def as_dict(self) -> dict:
        out: dict = {"ok": False, "kind": self.kind}
        if self.status is not None:
            out["status"] = self.status
        if self.message:
            out["message"] = self.message
        return out


class StoreErrorBase(Exception):
    """What a session store raises when it cannot help (app/store.StoreError is the one in use).
    The client then opens a session of its own and reports `as_dict()`; the read goes on."""

    def as_dict(self) -> dict:
        return {"ok": False, "kind": "store_error"}


@dataclass
class Session:
    token: str = field(repr=False)
    issued_at: float
    shape: str  # how createSession's body looked: text / json-string / json-object:<key>
    origin: str = "new"  # "new": opened by this process; "saved": read from the store
    saved: Optional[bool] = None  # whether the store holds it; None when there is no store
    store_error: Optional[dict] = None  # what the store said when it could not help (app/store.py)
    note: Optional[str] = None


# A saved session issued up to this many seconds "in the future" is another instance's clock.
CLOCK_SKEW_S = 300


@dataclass
class WizClient:
    server: str
    db: str
    private_key: str = field(repr=False)
    transport: Optional[httpx.BaseTransport] = None
    clock: Callable[[], float] = time.time
    # Where the session is kept between processes (app/store.PgSessionStore); None keeps it in memory.
    store: Optional[Any] = field(default=None, repr=False)
    _session: Optional[Session] = field(default=None, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    last_session_shape: Optional[str] = field(default=None, init=False)

    # ---- redaction -------------------------------------------------------
    def redact(self, text: str, *also: Optional[str]) -> str:
        if not text:
            return text
        for secret in (self.private_key, self._session.token if self._session else None, *also):
            if secret and len(secret) >= 4:
                text = text.replace(secret, "***")
        return text

    def _short(self, text: str, limit: int = 200, also: Optional[str] = None) -> str:
        text = self.redact((text or "").strip(), also)
        return text if len(text) <= limit else text[:limit] + "..."

    # ---- http ------------------------------------------------------------
    def _client(self) -> httpx.Client:
        return httpx.Client(transport=self.transport, timeout=TIMEOUT_S, follow_redirects=False)

    def _send(self, method: str, path: str, *, headers: Optional[dict] = None, body: Any = None) -> httpx.Response:
        url = f"https://{self.server}/{path}"
        try:
            with self._client() as client:
                if method == "GET":
                    return client.get(url, headers=headers)
                return client.post(url, headers=headers, json=body if body is not None else {})
        except httpx.TimeoutException:
            raise WizError("network", "timeout") from None
        except httpx.HTTPError as exc:
            # str(exc) can carry the URL, and the createSession URL carries the key.
            raise WizError("network", type(exc).__name__) from None

    # ---- session -----------------------------------------------------------
    def _new_session(self) -> Session:
        if not (self.server and self.db and self.private_key):
            raise WizError("config_missing", "WIZ_PRIVATE_KEY, server or company is not set")
        resp = self._send("GET", f"createSession/{self.private_key}/{self.db}")
        text = resp.text or ""
        if "module not active" in text.lower():
            raise WizError("module_inactive", self._short(text), resp.status_code)
        if resp.status_code >= 400:
            kind = "bad_key" if "token" in text.lower() else "http_error"
            raise WizError(kind, self._short(text), resp.status_code)
        token, shape = parse_session_body(text)
        if not token:
            raise WizError("bad_response", f"createSession answered 2xx with an unreadable body ({shape})", resp.status_code)
        self.last_session_shape = shape
        return Session(token=token, issued_at=self.clock(), shape=shape)

    def _fresh_enough(self, issued_at: float) -> bool:
        return -CLOCK_SKEW_S <= self.clock() - issued_at < SESSION_TTL_S

    def holds_session(self) -> bool:
        """Whether this process already holds a session it would use without asking anyone."""
        current = self._session
        return current is not None and self._fresh_enough(current.issued_at)

    def session(self, *, fresh: bool = False, rejected: Optional[str] = None) -> Session:
        """The session to call with. `rejected` is a session string WizCloud just refused: any session
        but that one will do, so a renewal another thread or instance already made is taken as is."""
        with self._lock:
            current = self._session
            if fresh and rejected is None and current is not None:
                rejected = current.token
            if current is not None and current.token != rejected and self._fresh_enough(current.issued_at):
                return current
            self._session = None
            self._session = self._acquire(rejected)
            return self._session

    def _usable(self, saved: Any, rejected: Optional[str]) -> Optional[Session]:
        if saved is None or not saved.token or saved.token == rejected or not self._fresh_enough(saved.issued_at):
            return None
        return Session(token=saved.token, issued_at=saved.issued_at, shape=saved.shape, origin="saved", saved=True)

    def _acquire(self, rejected: Optional[str]) -> Session:
        """A session from the store when it holds a usable one; otherwise a new one, saved under the
        store's renewal lock. A store that fails costs a new session, never the read, and is said."""
        if self.store is None:
            return self._new_session()
        opened: Optional[Session] = None
        unreadable = False
        try:
            if rejected is None:
                saved = self.store.load(self.server, self.db)
                usable = self._usable(saved, rejected)
                if usable is not None:
                    return usable
            with self.store.renewal(self.server, self.db) as renewal:
                saved = renewal.current()
                usable = self._usable(saved, rejected)
                if usable is not None:
                    return usable
                unreadable = saved is not None and not saved.token
                opened = self._new_session()
                renewal.save(opened.token, opened.shape, opened.issued_at)
            opened.saved = True
            if unreadable:
                opened.note = "the saved session was sealed under another key and was replaced"
            return opened
        except StoreErrorBase as exc:
            problem = exc.as_dict()
        if opened is None:
            opened = self._new_session()
        opened.saved = False
        opened.store_error = problem
        return opened

    # ---- calls -------------------------------------------------------------
    def call(self, path: str, body: Any = None, *, retry: bool = True) -> Any:
        """POST to one read path and return the parsed JSON body. A rejected session gets one fresh
        try, unless `retry` is False (scripts/live_check.py's session test needs to see the refusal)."""
        if path not in READ_PATHS:
            raise WizError("forbidden_path", f"{path!r} is not a read path")
        rejected: Optional[str] = None
        for attempt in (1, 2):
            sess = self.session(rejected=rejected)
            resp = self._send("POST", path, headers={"Authorization": f"Bearer {sess.token}"}, body=body)
            data, err = read_envelope(resp)
            if err is None:
                return data
            if looks_like_rejected_session(err) and attempt == 1 and retry:
                rejected = sess.token  # it may have expired early, or been replaced; one fresh try
                continue
            kind = "session_rejected" if looks_like_rejected_session(err) else "http_error"
            raise WizError(kind, self._short(err, also=sess.token), resp.status_code)
        raise AssertionError("unreachable")

    def companies(self) -> Any:
        return self.call("CompanyListToTokenApi/TokenCompanies")

    def triggers(self) -> Any:
        return self.call("TriggersApi/getURL")

    def export_report(self, datafile: str, parameters: Optional[list] = None) -> Any:
        return self.call("ExportDataApi/exportData", {"datafile": datafile, "parameters": parameters or []})

    def get_doc(self, stock_id: int) -> Any:
        return self.call("invApi/getDoc", {"stockID": int(stock_id)})


# ---- pure helpers (tested directly) ----------------------------------------


def parse_session_body(text: str) -> tuple[Optional[str], str]:
    """createSession's 2xx body: the vendor's own client uses it raw.

    Read it as raw text, a JSON string, or a JSON object with a token-like key,
    and say which one it was, so the first live answer settles it.
    """
    body = (text or "").strip()
    if not body:
        return None, "empty"
    if body[0] in "{[\"":
        try:
            parsed = json.loads(body)
        except ValueError:
            return None, "json-unparseable"
        if isinstance(parsed, str):
            return (parsed.strip() or None), "json-string"
        if isinstance(parsed, dict):
            for key in ("wizAuthToken", "token", "authToken", "access_token", "data"):
                value = parsed.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip(), f"json-object:{key}"
            return None, "json-object:keys=" + ",".join(sorted(map(str, parsed.keys())))[:120]
        return None, f"json-{type(parsed).__name__}"
    if any(ch.isspace() for ch in body):
        return None, "text-with-spaces"
    return body, "text"


def read_envelope(resp: httpx.Response) -> tuple[Any, Optional[str]]:
    """Return (data, None) on success or (None, error text).

    WizCloud answers errors in at least two envelopes, sometimes with 2xx-looking
    bodies inside a 4xx/5xx status. Both the HTTP status and the envelope's own
    `statusCode` are checked.
    """
    text = resp.text or ""
    try:
        data = json.loads(text) if text.strip() else None
    except ValueError:
        if resp.status_code >= 400:
            return None, text or f"HTTP {resp.status_code}"
        return None, "unparseable body: " + text[:80]
    envelope_status = None
    if isinstance(data, dict):
        raw = data.get("statusCode")
        if isinstance(raw, (int, str)) and str(raw).isdigit():
            envelope_status = int(raw)
    if resp.status_code >= 400 or (envelope_status is not None and envelope_status >= 400) or (
        isinstance(data, dict) and data.get("status") == "error"
    ):
        return None, error_text(data) or text or f"HTTP {resp.status_code}"
    return data, None


def error_text(data: Any) -> str:
    if not isinstance(data, dict):
        return ""
    status = data.get("status")
    if isinstance(status, dict) and status.get("errors"):
        return str(status["errors"])
    for key in ("message", "errors", "error"):
        if data.get(key):
            return str(data[key])
    return ""


def looks_like_rejected_session(err: str) -> bool:
    e = (err or "").lower()
    return ("token" in e and ("not valid" in e or "invalid" in e)) or "not connected" in e or "no authorization" in e
