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
import re
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
# createSession is made while holding the store's renewal lock (app/store.LOCK_TIMEOUT is longer), so
# it gets a tighter budget than a report or a document. A live round of session, companies, webhooks
# and ten documents took about five seconds (18.11).
SESSION_TIMEOUT = httpx.Timeout(10.0, connect=5.0)

# The only hosts a request may go to, matched whole, and the only shape of a company database. Checked
# here as well as in app/config.py: a server like "lb19.wizcloud.co.il/invApi/delDocument#" would turn a
# read into a write (review 19.11), so the client never trusts its caller for this.
SERVER_RE = re.compile(r"^lb\d+\.wizcloud\.co\.il$")
DB_RE = re.compile(r"^wizdb[0-9a-z]+$")
# Any stretch of a message this long that also occurs inside a secret is masked: a partial echo too.
_PARTIAL_MIN = 12
# A run this long of characters a token is made of is masked in every message, whatever it is: no vendor
# error text needs one, and it catches a secret the client has not seen yet (a session in an error body).
_TOKEN_RUN = re.compile(r"[A-Za-z0-9+/=_.~-]{32,}")


def key_problem(key: str) -> Optional[str]:
    """Why the private key cannot travel as is in the createSession URL path, or None. One rule for
    app/config.py and the client (review 19.11): printable ASCII with no space, no character that
    changes the URL (/ ? #), and nothing httpx would rewrite as %XX - so a key pasted with quote
    marks, a BOM or a zero-width space is a config problem, not a forbidden path. A % is refused too:
    httpx sends it as is, and the vendor's server may decode %2F into a /. Says which kind of character,
    never which character."""
    if not key:
        return "is empty"
    if any(not 0x21 <= ord(ch) <= 0x7E for ch in key):
        return "has a space or a character that is not plain ASCII (a pasted quote mark, a BOM, a zero-width space?)"
    if re.search(r"[/?#%]", key):
        return "has a character that changes a URL (/, ?, # or %, which the server may decode as an escape)"
    try:
        same = httpx.URL(f"https://x/{key}").raw_path == f"/{key}".encode("ascii")
    except (httpx.InvalidURL, UnicodeError):
        same = False
    if not same:
        return 'has a character a URL path cannot carry as is (such as " < > ` { } | \\ ^)'
    return None

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
    def _secrets(self, *also: Optional[str]) -> list:
        return [x for x in (self.private_key, self._session.token if self._session else None, *also) if x and len(x) >= 4]

    def redact(self, text: str, *also: Optional[str]) -> str:
        """Mask the key and the session strings: whole, any run of _PARTIAL_MIN or more characters that
        occurs inside one of them (a partial echo), and any token-like run of 32 or more characters (a
        secret the client has not seen)."""
        if not text:
            return text
        secrets = self._secrets(*also)
        for secret in secrets:
            text = text.replace(secret, "***")
        return _TOKEN_RUN.sub("***", mask_overlaps(text, secrets))

    def _short(self, text: str, limit: int = 200, also: Optional[str] = None) -> str:
        """Redact first, then cut: a cut made first could leave a piece of a secret no longer whole. The
        text is first capped at limit plus twice the longest secret, so a large body costs nothing: a
        secret that starts before the limit is still whole inside the cap, and what lies past the limit
        is cut anyway."""
        text = (text or "").strip()
        longest = max((len(x) for x in self._secrets(also)), default=0)
        text = self.redact(text[: limit + 2 * longest + _PARTIAL_MIN], also)
        return text if len(text) <= limit else text[:limit] + "..."

    # ---- http ------------------------------------------------------------
    def _client(self, timeout: Any = TIMEOUT_S) -> httpx.Client:
        return httpx.Client(transport=self.transport, timeout=timeout, follow_redirects=False)

    def _url(self, path: str) -> str:
        """https://<server>/<path>, refused unless it lands on the configured host at exactly that path.
        The message never carries the URL: for createSession it holds the key."""
        if not SERVER_RE.match(self.server or ""):
            raise WizError("config_missing", "the server is not an lbN.wizcloud.co.il host")
        url = f"https://{self.server}/{path}"
        try:
            parsed = httpx.URL(url)
        except (httpx.InvalidURL, UnicodeError):
            raise WizError("config_missing", "the request URL is not valid (the key or the company has a stray character)") from None
        if parsed.host != self.server or parsed.raw_path.decode("ascii", "replace") != f"/{path}" or parsed.fragment:
            raise WizError("forbidden_path", "the request would not land on the configured server and path")
        return url

    def _send(self, method: str, path: str, *, headers: Optional[dict] = None, body: Any = None, timeout: Any = TIMEOUT_S) -> httpx.Response:
        url = self._url(path)
        try:
            with self._client(timeout) as client:
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
        if not DB_RE.match(self.db):
            raise WizError("config_missing", "the company database is not a wizdb name")
        problem = key_problem(self.private_key)
        if problem:
            raise WizError("config_missing", f"WIZ_PRIVATE_KEY {problem}")
        resp = self._send("GET", f"createSession/{self.private_key}/{self.db}", timeout=SESSION_TIMEOUT)
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

    def current(self) -> Optional[Session]:
        """The session this process holds now, or None (never held, or a renewal failed)."""
        return self._session

    def holds_session(self) -> bool:
        """Whether this process already holds a session it would use without asking anyone."""
        current = self._session
        return current is not None and self._fresh_enough(current.issued_at)

    def session(self, *, fresh: bool = False, rejected: Optional[Session] = None) -> Session:
        """The session to call with. `rejected` is the session WizCloud just refused: a replacement
        another thread or instance already made is taken as is, but only one issued after it - an
        older session in the row is not a renewal, it is a session that may be just as dead (review 19.11)."""
        with self._lock:
            current = self._session
            if fresh and rejected is None:
                rejected = current
            if current is not None and self._fresh_enough(current.issued_at) and _replaces(current, rejected):
                return current
            self._session = None
            self._session = self._acquire(rejected)
            return self._session

    def _usable(self, saved: Any, rejected: Optional[Session]) -> Optional[Session]:
        if saved is None or not saved.token or not self._fresh_enough(saved.issued_at) or not _replaces(saved, rejected):
            return None
        return Session(token=saved.token, issued_at=saved.issued_at, shape=saved.shape, origin="saved", saved=True)

    def _acquire(self, rejected: Optional[Session]) -> Session:
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
        rejected: Optional[Session] = None
        for attempt in (1, 2, 3):
            sess = self.session(rejected=rejected)
            resp = self._send("POST", path, headers={"Authorization": f"Bearer {sess.token}"}, body=body)
            data, err = read_envelope(resp)
            if err is None:
                return data
            # One fresh try after a refusal. A replacement taken from the store was never tried by this
            # call, and can be just as dead (the vendor dropped sessions twice): then one more, which
            # opens a session of its own, since the row now holds a refused one (review 19.11).
            fresh_try_left = attempt == 1 or (attempt == 2 and sess.origin == "saved")
            if looks_like_rejected_session(err) and retry and fresh_try_left:
                rejected = sess
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


def mask_overlaps(text: str, secrets: list, k: int = _PARTIAL_MIN) -> str:
    """Replace every stretch of `text` that shares k or more consecutive characters with a secret by
    "***", whatever surrounds it. A secret shorter than k is left to the whole-string pass."""
    grams = {x[i:i + k] for x in secrets for i in range(len(x) - k + 1)}
    if not grams or len(text) < k:
        return text
    hit = [False] * len(text)
    for i in range(len(text) - k + 1):
        if text[i:i + k] in grams:
            hit[i:i + k] = [True] * k
    out, i = [], 0
    while i < len(text):
        if hit[i]:
            while i < len(text) and hit[i]:
                i += 1
            out.append("***")
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def _replaces(candidate: Any, rejected: Optional[Session]) -> bool:
    """Whether `candidate` can stand in for a session WizCloud refused: another string, issued after
    the refused one. Equal times are refused too (a frozen clock in the review's proof): the cost is
    one extra session, never a dead one."""
    if rejected is None:
        return True
    return candidate.token != rejected.token and candidate.issued_at > rejected.issued_at


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
            # Name only short identifier-like keys; anything else (a key that is itself a session?) is counted.
            names = sorted(k for k in map(str, parsed.keys()) if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,31}", k))
            others = len(parsed) - len(names)
            return None, "json-object:keys=" + ",".join(names)[:120] + (f" (+{others} other)" if others else "")
        return None, f"json-{type(parsed).__name__}"
    if any(ch.isspace() for ch in body):
        return None, "text-with-spaces"
    return body, "text"


def read_envelope(resp: httpx.Response) -> tuple[Any, Optional[str]]:
    """Return (data, None) on success or (None, error text).

    WizCloud answers errors in at least two envelopes, sometimes with 2xx-looking
    bodies inside a 4xx/5xx status. Both the HTTP status and the envelope's own
    `statusCode` are checked. A 2xx body that is not JSON is never quoted: it can be
    a document or a report row (names, ID numbers), so only its shape is said
    (review 19.11) - unless it is a short refusal of the session, which is retried.
    """
    text = resp.text or ""
    try:
        data = json.loads(text) if text.strip() else None
    except ValueError:
        if resp.status_code >= 400:
            return None, text or f"HTTP {resp.status_code}"
        if len(text) < 200 and looks_like_rejected_session(text):
            return None, text
        content_type = (resp.headers.get("content-type") or "none").split(";")[0].strip()[:40]
        raise WizError(
            "bad_response",
            f"a {resp.status_code} answer that is not JSON ({len(text)} characters, content-type {content_type})",
            resp.status_code,
        ) from None
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
