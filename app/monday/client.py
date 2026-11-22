"""monday's GraphQL API, READ ONLY (Analiza-Monday backlog 0301 R0; spec §2.2).

Ported on 22.11 from analiza-metrics, where it has read the finance desk's boards since 10.11: the transport of
`app/adapters/monday_board.py` (`MondayBoardAdapter._post`: the read-only guard, retry once, the closed error
vocabulary) and the board reading of `app/finance.py` (`FinanceReader`: a board's ids, paged and cheap, then its rows by
id in parallel chunks - measured live there on Student Charge, 3.9 s against 5.5 s for the same 600 rows paged with
their columns). The desk's chains (enrollments, leads and cohorts by id) are not here: they come with the desk (D1).

The rules it keeps:
1. **No write, ever.** Every query must start with `query`; a mutation is refused before it is sent, whatever a future
   edit asks for.
2. **A missing column is an error, not an empty column.** monday drops an unknown id from `column_values(ids:)` with no
   error (Analiza-Monday backlog 0234), so a renamed or deleted column would read as empty on every row. The columns of
   every board are checked first, in one request, and a missing one ends the reading (`unknown_column`).
3. **A whole board is counted twice:** its `items_count` beside the rows read. The caller compares them (app/recon/reading.py).
4. **A deleted item is dropped, an archived one kept** (read by id, its `state` says which).
5. **The token never appears** in an error, a log line or an answer. Errors carry a closed code and a short detail, with
   the token taken out of whatever monday's message said (review 22.11: a message that echoes it reached the answer).
6. **The deadline bounds the answer too:** a body is read in pieces and the deadline is checked between them, so a slow
   answer ends at the deadline instead of whenever it finishes (review 22.11: httpx's timeout is per read, not per request).
7. **Every row says which board it came from** (`board { id }`): an id read by id answers from any board, so a row is
   counted as the board's only when it is (review 22.11).
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Iterable, Optional

import httpx

from ..tls import tls_context

logger = logging.getLogger("analiza_finance.monday")

MONDAY_API_URL = "https://api.monday.com/v2"
# The version analiza-metrics pins (2026-07): the same API, so a column reads the same in both services.
MONDAY_API_VERSION = "2026-07"
PAGE_LIMIT = 500
MAX_PAGES = 200  # 100,000 items: a cursor that never ends is an error, not a hang
MAX_RETRIES = 1
RETRY_BACKOFF_SECONDS = 2.0
RETRY_MAX_SECONDS = 30.0
READ_TIMEOUT_SECONDS = 20
ITEMS_BY_ID_CHUNK = 100
# requests in flight at once: far under monday's concurrency limits (analiza-metrics, measured live 2026-11-10: 476
# items in 5 chunks took 7.0 s one after another and 1.35 s side by side)
CHUNK_WORKERS = 6
CODES = ("auth", "rate_limited", "http", "graphql", "not_visible", "unknown_column", "deadline", "too_many_pages")
DETAIL_MAX = 200

_AUTH_RE = re.compile(r"unauthor|unauthenticated|not authenticated|invalid token", re.IGNORECASE)
_RATE_RE = re.compile(r"complexity|rate.?limit", re.IGNORECASE)
_RESET_IN_RE = re.compile(r"reset in (\d+(?:\.\d+)?) seconds", re.IGNORECASE)

CV_FRAGMENT = (
    "{ id text"
    " ... on StatusValue { index label }"
    " ... on BoardRelationValue { linked_item_ids }"
    " ... on MirrorValue { display_value } }"
)
ITEM_IDS_FIRST = ("query ($b: [ID!], $limit: Int!) { boards(ids: $b) { items_count "
                  "items_page(limit: $limit) { cursor items { id } } } }")
ITEM_IDS_NEXT = "query ($c: String!, $limit: Int!) { next_items_page(cursor: $c, limit: $limit) { cursor items { id } } }"
ITEMS_BY_ID = ("query ($ids: [ID!], $cols: [String!], $limit: Int!) {"
               " items(ids: $ids, limit: $limit) { id name state created_at board { id } column_values(ids: $cols) %s } }"
               % CV_FRAGMENT)


class MondayError(Exception):
    def __init__(self, code: str, detail: str = ""):
        self.code, self.detail = code, detail
        super().__init__(f"{code} {detail}".strip())


def classify_graphql_errors(errors: list) -> tuple:
    """-> (code, short detail, reset-in seconds when the API said so). monday answers HTTP 200 with `errors[]` for auth,
    complexity and query problems alike, so the code is decided from the extension code and the message."""
    code, messages, reset_in = "graphql", [], None
    for err in errors:
        if not isinstance(err, dict):
            continue
        msg = str(err.get("message") or "")
        ext = err.get("extensions") or {}
        ext_code = str(ext.get("code") or "") if isinstance(ext, dict) else ""
        blob = f"{ext_code} {msg}"
        messages.append(blob.strip())
        if _AUTH_RE.search(blob):
            code = "auth"
        elif _RATE_RE.search(blob) and code != "auth":
            code = "rate_limited"
            m = _RESET_IN_RE.search(msg)
            if m:
                reset_in = float(m.group(1))
    return code, "; ".join(messages)[:200], reset_in


def _text(value) -> str:
    """A cell's text, without NUL: Postgres jsonb refuses \\u0000, and one such cell would fail every save while it
    stays (review 22.11). It never means anything in monday's text."""
    return str(value or "").replace("\x00", "")


def flatten(item: dict) -> dict:
    """One item as a row: a status cell's label under the column id and its index under `id#` (a label is matched by its
    index, never its text), a relation's ids as a list, a mirror's display text, any other cell's text; and the board the
    item answered from (`board_id`)."""
    board = item.get("board") if isinstance(item.get("board"), dict) else {}
    row = {"id": str(item["id"]), "name": _text(item.get("name")), "created_at": _text(item.get("created_at")),
           "state": str(item.get("state") or "active"), "board_id": str(board.get("id") or "")}
    for c in item.get("column_values") or []:
        if not isinstance(c, dict):
            continue
        cid = str(c.get("id"))
        if "linked_item_ids" in c:
            row[cid] = [str(x) for x in c.get("linked_item_ids") or []]
        elif "display_value" in c:
            row[cid] = _text(c.get("display_value"))
        elif "index" in c:
            row[cid] = _text(c.get("label") or c.get("text"))
            row[cid + "#"] = "" if c.get("index") is None else str(c.get("index"))
        else:
            row[cid] = _text(c.get("text"))
    return row


class MondayReader:
    """Reads boards. `deadline` (time.monotonic) bounds a reading: no request starts after it, and none waits past it."""

    def __init__(self, token: str, client: Optional[httpx.Client] = None, api_url: str = MONDAY_API_URL,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic):
        self._token = token
        self.client = client or httpx.Client(timeout=READ_TIMEOUT_SECONDS, verify=tls_context())
        self.api_url, self.sleep, self.clock = api_url, sleep, clock
        self.deadline: Optional[float] = None
        self.calls = 0  # requests sent, a retry included: what a reading costs against monday's budget
        self._lock = threading.Lock()

    def __repr__(self) -> str:
        return "MondayReader()"

    def close(self) -> None:
        self.client.close()

    def _redact(self, text: str) -> str:
        text = str(text or "")
        if self._token:
            text = text.replace(self._token, "[token]")
        return text[:DETAIL_MAX]

    def _error(self, code: str, detail: str) -> MondayError:
        return MondayError(code, self._redact(detail))

    # ---- transport -------------------------------------------------------
    def _headers(self) -> dict:
        return {"Authorization": self._token, "API-Version": MONDAY_API_VERSION, "Content-Type": "application/json"}

    def _left(self) -> Optional[float]:
        return None if self.deadline is None else self.deadline - self.clock()

    def _backoff(self, attempt: int, retry_after: Any) -> None:
        try:
            delay = float(retry_after) if retry_after is not None else RETRY_BACKOFF_SECONDS * attempt
        except (TypeError, ValueError):
            delay = RETRY_BACKOFF_SECONDS * attempt
        delay = max(0.0, min(delay, RETRY_MAX_SECONDS))
        left = self._left()
        if left is not None and delay >= left:
            raise MondayError("deadline", "no time left to retry")
        self.sleep(delay)

    def post(self, query: str, variables: dict) -> dict:
        """One read, retried once on 429, 5xx or a request error (a read is idempotent). Returns `data`."""
        if not query.lstrip().startswith("query"):
            raise MondayError("graphql", "only read queries are allowed")
        payload = {"query": query, "variables": variables}
        attempt = 0
        while True:
            attempt += 1
            left = self._left()
            if left is not None and left <= 0:
                raise MondayError("deadline", "the reading's time is up")
            timeout = READ_TIMEOUT_SECONDS if left is None else max(0.5, min(READ_TIMEOUT_SECONDS, left))
            with self._lock:
                self.calls += 1
            try:
                status, retry_after, raw = self._send(payload, timeout)
            except httpx.RequestError as exc:  # a transport error, a timeout, a body that does not decode
                after = self._left()
                if after is not None and after <= 0.5:
                    raise MondayError("deadline", f"transport {type(exc).__name__}") from None
                if attempt <= MAX_RETRIES:
                    logger.warning("monday request error %s, retrying", type(exc).__name__)
                    self._backoff(attempt, None)
                    continue
                raise MondayError("http", f"transport {type(exc).__name__}") from None
            if status in (401, 403):
                raise MondayError("auth", f"http {status}")
            if status == 429 or status >= 500:
                if attempt <= MAX_RETRIES:
                    logger.warning("monday http %s, retrying", status)
                    self._backoff(attempt, retry_after)
                    continue
                raise MondayError("rate_limited" if status == 429 else "http", f"http {status} after {attempt} attempts")
            if status != 200:
                raise MondayError("http", f"http {status}")
            try:
                body = json.loads(raw)
            except ValueError:
                raise MondayError("http", "non-json body") from None
            if not isinstance(body, dict):
                raise MondayError("http", "unexpected body shape")
            errors = body.get("errors")
            if errors:
                code, detail, reset_in = classify_graphql_errors(errors)
                if code == "rate_limited" and attempt <= MAX_RETRIES:
                    self._backoff(attempt, reset_in)
                    continue
                raise self._error(code, detail)
            if body.get("error_code") or body.get("error_message"):  # the legacy top-level shape
                blob = f"{body.get('error_code') or ''} {body.get('error_message') or ''}".strip()
                raise self._error("auth" if _AUTH_RE.search(blob) else "graphql", blob)
            data = body.get("data")
            if not isinstance(data, dict):
                raise MondayError("graphql", "no data in the answer")
            return data

    def _send(self, payload: dict, timeout: float) -> tuple:
        """(status, Retry-After, body) - the body read in pieces, the deadline checked between them."""
        with self.client.stream("POST", self.api_url, json=payload, headers=self._headers(), timeout=timeout) as resp:
            if resp.status_code != 200:
                return resp.status_code, resp.headers.get("Retry-After"), b""
            pieces = []
            for piece in resp.iter_bytes():
                pieces.append(piece)
                if self.deadline is not None and self.clock() > self.deadline:
                    raise MondayError("deadline", "the answer was still coming at the deadline")
            return resp.status_code, None, b"".join(pieces)

    # ---- boards ----------------------------------------------------------
    def require_columns(self, spec: Iterable[tuple]) -> None:
        """Every board's columns in one request (an alias per board). A board not visible to the token is
        `not_visible`; a column it lacks is `unknown_column`."""
        spec = [(str(b), list(cols)) for b, cols in spec]
        params = ", ".join("$b%d: [ID!], $c%d: [String!]" % (i, i) for i in range(len(spec)))
        body = " ".join("b%d: boards(ids: $b%d) { columns(ids: $c%d) { id } }" % (i, i, i) for i in range(len(spec)))
        variables: dict = {}
        for i, (board_id, cols) in enumerate(spec):
            variables["b%d" % i], variables["c%d" % i] = [board_id], cols
        data = self.post("query (%s) { %s }" % (params, body), variables)
        for i, (board_id, cols) in enumerate(spec):
            boards = data.get("b%d" % i) or []
            if not boards:
                raise MondayError("not_visible", f"board {board_id}")
            present = {str(c.get("id")) for c in boards[0].get("columns") or [] if isinstance(c, dict)}
            missing = sorted(set(cols) - present)
            if missing:
                raise MondayError("unknown_column", f"board {board_id} lacks {missing}")

    def item_ids(self, board_id: str) -> tuple:
        """(items_count, the ids of the board's items, paged)."""
        data = self.post(ITEM_IDS_FIRST, {"b": [str(board_id)], "limit": PAGE_LIMIT})
        boards = data.get("boards") or []
        if not boards:
            raise MondayError("not_visible", f"board {board_id}")
        page = boards[0].get("items_page") or {}
        ids, cursor, pages = [str(x["id"]) for x in page.get("items") or []], page.get("cursor"), 1
        while cursor:
            pages += 1
            if pages > MAX_PAGES:
                raise MondayError("too_many_pages", f"board {board_id}")
            page = self.post(ITEM_IDS_NEXT, {"c": cursor, "limit": PAGE_LIMIT}).get("next_items_page") or {}
            ids += [str(x["id"]) for x in page.get("items") or []]
            cursor = page.get("cursor")
        count = boards[0].get("items_count")
        return (int(count) if isinstance(count, int) or str(count).isdigit() else None), ids

    def read_items(self, jobs: list) -> list:
        """Items by id for several (ids, columns) jobs at once: every chunk of every job in one pool, the rows per job in
        order. A deleted item is dropped; an archived one is kept, its `state` saying so."""
        pieces = [(j, ids[i:i + ITEMS_BY_ID_CHUNK], cols) for j, (ids, cols) in enumerate(jobs)
                  for i in range(0, len(ids), ITEMS_BY_ID_CHUNK)]

        def one(piece: tuple) -> list:
            _j, ids, cols = piece
            return self.post(ITEMS_BY_ID, {"ids": ids, "cols": list(cols), "limit": len(ids)}).get("items") or []

        if len(pieces) < 2:
            answers = [one(p) for p in pieces]
        else:
            with ThreadPoolExecutor(max_workers=CHUNK_WORKERS, thread_name_prefix="monday-chunk") as pool:
                answers = list(pool.map(one, pieces))
        out: list = [[] for _ in jobs]
        for (j, _ids, _cols), items in zip(pieces, answers):
            out[j] += [flatten(x) for x in items if isinstance(x, dict) and x.get("state") != "deleted"]
        return out

    def read_boards(self, boards: list) -> list:
        """[(board_id, columns)] whole: the ids of every board side by side, then every row by id in one pool.
        -> [(items_count, the ids asked, rows)] in order."""
        if len(boards) < 2:
            counted = [self.item_ids(b) for b, _ in boards]
        else:
            with ThreadPoolExecutor(max_workers=min(len(boards), CHUNK_WORKERS), thread_name_prefix="monday-ids") as pool:
                counted = list(pool.map(lambda bc: self.item_ids(bc[0]), boards))
        rows = self.read_items([(ids, cols) for (_n, ids), (_b, cols) in zip(counted, boards)])
        return [(n, ids, r) for (n, ids), r in zip(counted, rows)]
