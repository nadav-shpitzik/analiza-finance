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
8. **Some columns are never read at all** (`NEVER_READ`, moved here from app/recon/reading.py on 24.11, D1): the ID
   numbers and a refund's bank account. `read_items` refuses a job that asks for one, before anything is sent, so no
   caller (the reconciliation's profile, the desk's reading, a later one) can read one by mistake. A column's existence
   is still checked (`require_columns` asks for its id, never its value). The desk shows the ID number read in the
   browser, as the person (ADR-0173).

Since D2 (24.11) it also asks what changed (`changed_items`, the desk's refresh): analiza-metrics' one request of round 3,
slice 2 (`FinanceReader._delta` and `delta_query` at da671b2, live there since 11.11) - every board's columns, and for the
boards with a window their items_count and the ids and update times of their items updated lately, with some items by
id beside them - under the same eight rules; and `read_items(..., pack=True)` sends the short tails of several jobs as one
aliased request (analiza-metrics `items_query`), so a refresh that changed two payments and a deal costs one request.
`board_ids` pages the two charge boards' ids for the refresh too, the query a whole reading starts with (D2's review).
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
CODES = ("auth", "rate_limited", "http", "graphql", "not_visible", "unknown_column", "deadline", "too_many_pages",
         "never_read",
         # D2's refresh (app/desk/reading.py): an answer of another shape than the one its request asked for, and a charge
         # board whose items_count disagrees with the rows a refresh holds (`DeltaMismatch`)
         "delta_shape", "count_mismatch")
DETAIL_MAX = 200

# What is never read (rule 8): the ID numbers - Leads' `ת.ז`, the Directory's, the vouchers board's, and Accounts' business
# number, which for a sole proprietor is the person's ID number (ADR-0173 §1: an ID number is never stored server side) -
# every mirror of one of them on a board this service reads, and a refund's bank account on Student Charge. The mirrors
# were read live on 24.11 (D1's review: a mirror carries its source's value, so refusing the source alone was not enough):
# every mirror column of the seven boards the desk reads and of the vouchers board, with the column each one displays.
NEVER_READ = frozenset({
    "text_mm8cxm9y", "text_mm8cv3v3", "text_mm8cgc0d", "text_mm8ckgh8",  # Student Charge: a refund's bank account
    "text_mkzvem4",  # Leads: ת.ז
    "text_mm0skfxr",  # the Directory (5090589061): ID Number
    "text_mm0kgsx9",  # the vouchers board: ת.ז
    "text_mm3y3gm7",  # Accounts: the business number, a sole proprietor's ID number
    "lookup_mm8b1jzy",  # Student Charge: "ת.ז", the mirror of Leads' text_mkzvem4
    "lookup_mm2kg3gw",  # Enrollments: "ID", the mirror of the Directory's text_mm0skfxr
    "lookup_mm3y7rwr",  # B2B Charge: "ח.פ", the mirror of Accounts' text_mm3y3gm7
})
# What a row is besides its cells: never dropped by the answer's filter (`flatten`'s `keep`)
ROW_KEYS = ("id", "name", "created_at", "state", "board_id")

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
# Who is on a board: the desk's gate (D1). analiza-metrics reads the same field for the same gate (`board_subscribers`).
SUBSCRIBERS = "query ($b: [ID!]) { boards(ids: $b) { id subscribers { id } } }"

# D2's refresh: the items of a board updated within the last n days (analiza-metrics `DELTA_RULE`, verified live there on
# B2B Charge 11.11 with `within_the_last ["DAYS", 1]`, and read by every refresh since). The filter is day-grained: the
# caller makes the exact cut by each item's `updated_at`.
DELTA_RULE = ('{rules: [{column_id: "__last_updated__", compare_value: ["DAYS", %d], operator: within_the_last, '
              'compare_attribute: "UPDATED_AT"}]}')
NEXT_WINDOW = ("query ($c: String!, $limit: Int!) { next_items_page(cursor: $c, limit: $limit) "
               "{ cursor items { id updated_at } } }")


def window_query(windowed: int, checked: int, chunks: int, days: int) -> str:
    """One request (analiza-metrics `delta_query`): each board's columns check (b0, b1 ...); for the first `windowed`, its
    `items_count` and the first page of the items updated within the last `days` days - their ids and `updated_at` only;
    and items by id in `chunks` aliases (l0, l1 ...) under the one column list `$lc`, with the board each one answered
    from (rule 7)."""
    rule = DELTA_RULE % int(days)
    n = windowed + checked
    params = ["$b%d: [ID!], $c%d: [String!]" % (i, i) for i in range(n)] + (["$limit: Int!"] if windowed else [])
    body = ["b%d: boards(ids: $b%d) { columns(ids: $c%d) { id } items_count items_page(limit: $limit, query_params: %s) "
            "{ cursor items { id updated_at } } }" % (i, i, i, rule) for i in range(windowed)]
    body += ["b%d: boards(ids: $b%d) { columns(ids: $c%d) { id } }" % (i, i, i) for i in range(windowed, n)]
    if chunks:
        params.append("$lc: [String!]")
        params += ["$li%d: [ID!], $ln%d: Int!" % (k, k) for k in range(chunks)]
        body += ["l%d: items(ids: $li%d, limit: $ln%d) { id name state created_at board { id } column_values(ids: $lc) %s }"
                 % (k, k, k, CV_FRAGMENT) for k in range(chunks)]
    return "query (%s) { %s }" % (", ".join(params), " ".join(body))


def items_query(n: int) -> str:
    """Several short by-id reads in one request (analiza-metrics `items_query`), one alias each (j0, j1 ...) with its own
    ids, columns and limit, and the board each item answered from. A request of one piece keeps the ITEMS_BY_ID shape."""
    params = ", ".join("$i%d: [ID!], $c%d: [String!], $n%d: Int!" % (k, k, k) for k in range(n))
    body = " ".join("j%d: items(ids: $i%d, limit: $n%d) { id name state created_at board { id } column_values(ids: $c%d) %s }"
                    % (k, k, k, k, CV_FRAGMENT) for k in range(n))
    return "query (%s) { %s }" % (params, body)


def _refuse_never_read(cols: Iterable[str]) -> None:
    asked = NEVER_READ.intersection(str(c) for c in cols)
    if asked:
        raise MondayError("never_read", f"columns that are never read were asked: {sorted(asked)}")


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


def flatten(item: dict, keep: Optional[Iterable[str]] = None) -> dict:
    """One item as a row: a status cell's label under the column id and its index under `id#` (a label is matched by its
    index, never its text), a relation's ids as a list, a mirror's display text, any other cell's text; and the board the
    item answered from (`board_id`). With `keep`, a cell monday answered without being asked for it is dropped; a cell in
    NEVER_READ is dropped always, asked or not (D1's review: the answer was kept whole)."""
    board = item.get("board") if isinstance(item.get("board"), dict) else {}
    row = {"id": str(item["id"]), "name": _text(item.get("name")), "created_at": _text(item.get("created_at")),
           "state": str(item.get("state") or "active"), "board_id": str(board.get("id") or "")}
    wanted = None if keep is None else {str(k) for k in keep}
    for c in item.get("column_values") or []:
        if not isinstance(c, dict):
            continue
        cid = str(c.get("id"))
        if cid in NEVER_READ or cid in ROW_KEYS or (wanted is not None and cid not in wanted):
            continue
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
        # rate limits monday answered (429, or a complexity budget): a refresh that ran out of time after one is the limit's
        # (app/desk/reading.py HeldReading: never a full reading into it)
        self.limited = 0
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
                if status == 429:
                    with self._lock:
                        self.limited += 1
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
                if code == "rate_limited":
                    with self._lock:
                        self.limited += 1
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

    def board_subscribers(self, board_id: str) -> set:
        """The ids of the users on a board (its `subscribers`), for the desk's gate. A board the token cannot see is
        `not_visible`; a board that answers nobody is an error, not "nobody may read": a board always has a member, and
        an empty list must never be kept as the answer (analiza-metrics `board_subscribers`, the same rule)."""
        data = self.post(SUBSCRIBERS, {"b": [str(board_id)]})
        boards = data.get("boards") or []
        if not boards or not isinstance(boards[0], dict) or str(boards[0].get("id")) != str(board_id):
            raise MondayError("not_visible", f"board {board_id}")
        ids = {str(u["id"]) for u in boards[0].get("subscribers") or [] if isinstance(u, dict) and u.get("id")}
        if not ids:
            raise MondayError("graphql", f"board {board_id} answered no subscribers")
        return ids

    def read_items(self, jobs: list, pack: bool = False) -> list:
        """Items by id for several (ids, columns) jobs at once: every chunk of every job in one pool, the rows per job in
        order. A deleted item is dropped; an archived one is kept, its `state` saying so. A job that asks for a column
        in NEVER_READ is refused before anything is sent (rule 8). `pack` (D2's refresh, analiza-metrics `_fetch`) sends
        the short pieces of several jobs as one aliased request of at most ITEMS_BY_ID_CHUNK ids; without it each chunk is
        a request of its own, the shape every full reading sends."""
        for _ids, cols in jobs:
            if not list(cols):
                # never an empty list: what monday answers to `column_values(ids: [])` is not verified, and may be everything
                raise MondayError("graphql", "a read by id names its columns")
            _refuse_never_read(cols)
        pieces = [(j, list(ids[i:i + ITEMS_BY_ID_CHUNK]), list(cols)) for j, (ids, cols) in enumerate(jobs)
                  for i in range(0, len(ids), ITEMS_BY_ID_CHUNK)]
        requests: list = []
        for piece in pieces:
            last = requests[-1] if requests else None
            if pack and last is not None and sum(len(p[1]) for p in last) + len(piece[1]) <= ITEMS_BY_ID_CHUNK:
                last.append(piece)
            else:
                requests.append([piece])

        def one(req: list) -> list:
            if len(req) == 1:
                _j, ids, cols = req[0]
                return [self.post(ITEMS_BY_ID, {"ids": ids, "cols": cols, "limit": len(ids)}).get("items") or []]
            variables: dict = {}
            for k, (_j, ids, cols) in enumerate(req):
                variables["i%d" % k], variables["c%d" % k], variables["n%d" % k] = ids, cols, len(ids)
            data = self.post(items_query(len(req)), variables)
            return [data.get("j%d" % k) or [] for k in range(len(req))]

        if len(requests) < 2:
            answers = [one(r) for r in requests]
        else:
            with ThreadPoolExecutor(max_workers=CHUNK_WORKERS, thread_name_prefix="monday-chunk") as pool:
                answers = list(pool.map(one, requests))
        out: list = [[] for _ in jobs]
        for req, got in zip(requests, answers):
            for (j, _ids, cols), items in zip(req, got):
                # what is not a list holds no item (as before D2): the caller's counts say what came back short
                out[j] += [flatten(x, cols) for x in (items if isinstance(items, list) else [])
                           if isinstance(x, dict) and x.get("state") != "deleted"]
        return out

    def changed_items(self, windowed: list, checked: list, days: int, by_id: tuple = ((), ())) -> dict:
        """What changed lately, in one request (D2's refresh; analiza-metrics `FinanceReader._delta`): every board's
        columns checked; for each of `windowed` [(board, columns)], its items_count and the ids and update times of its
        items updated within the last `days` days, each window paged to its end; and `by_id` (ids, columns) read by id
        beside them (the deciding leads: Leads has no window). A board not visible is `not_visible`, a column it lacks
        `unknown_column`, an answer of another shape `delta_shape`; a column of NEVER_READ asked by id is refused before
        anything is sent (rule 8), and a row keeps the board it answered from (rule 7): the caller tells a stranger.
        -> {"counts": [items_count per windowed board, as monday gave it], "windows": [[{"id", "updated_at"}] per
        windowed board], "rows": the rows read by id, flattened, a deleted one dropped}."""
        ids, cols = [str(x) for x in by_id[0]], [str(c) for c in by_id[1]]
        if ids:
            if not cols:
                raise MondayError("graphql", "a read by id names its columns")
            _refuse_never_read(cols)
        windowed = [(str(b), [str(c) for c in cs]) for b, cs in windowed]
        boards = windowed + [(str(b), [str(c) for c in cs]) for b, cs in checked]
        chunks = [ids[i:i + ITEMS_BY_ID_CHUNK] for i in range(0, len(ids), ITEMS_BY_ID_CHUNK)]
        variables: dict = {"limit": PAGE_LIMIT} if windowed else {}
        for i, (board, cs) in enumerate(boards):
            variables["b%d" % i], variables["c%d" % i] = [board], cs
        if chunks:
            variables["lc"] = cols
            for k, piece in enumerate(chunks):
                variables["li%d" % k], variables["ln%d" % k] = piece, len(piece)
        data = self.post(window_query(len(windowed), len(boards) - len(windowed), len(chunks), days), variables)
        counts, windows, cursors = [], [], []
        for i, (board, cs) in enumerate(boards):
            got = data.get("b%d" % i)
            if not isinstance(got, list) or not got or not isinstance(got[0], dict):
                raise MondayError("not_visible", f"board {board}")
            present = {str(c.get("id")) for c in got[0].get("columns") or [] if isinstance(c, dict)}
            missing = sorted(set(cs) - present)
            if missing:
                raise MondayError("unknown_column", f"board {board} lacks {missing}")
            if i >= len(windowed):
                continue  # its columns only
            page = got[0].get("items_page")
            if not isinstance(page, dict) or not isinstance(page.get("items"), list):
                raise MondayError("delta_shape", f"board {board}: no items_page")
            counts.append(got[0].get("items_count"))
            windows.append(list(page["items"]))
            cursors.append(page.get("cursor"))
        rows: list = []
        for k in range(len(chunks)):
            items = data.get("l%d" % k)
            if not isinstance(items, list):
                raise MondayError("delta_shape", "the items read by id came back without a list")
            rows += [flatten(x, cols) for x in items if isinstance(x, dict) and x.get("state") != "deleted"]
        rest = [i for i, cursor in enumerate(cursors) if cursor]
        if len(rest) == 1:
            windows[rest[0]] += self._window_rest(windowed[rest[0]][0], cursors[rest[0]])
        elif rest:
            with ThreadPoolExecutor(max_workers=min(len(rest), CHUNK_WORKERS), thread_name_prefix="monday-window") as pool:
                for i, more in zip(rest, pool.map(lambda i: self._window_rest(windowed[i][0], cursors[i]), rest)):
                    windows[i] += more
        out_windows = []
        for (board, _cs), items in zip(windowed, windows):
            for x in items:
                # an item whose time is null is the caller's to read, never to skip; a window that does not carry the
                # key at all is not the shape this request asked for
                if not isinstance(x, dict) or x.get("id") is None or "updated_at" not in x:
                    raise MondayError("delta_shape", f"board {board}: an item without its id or updated_at")
            out_windows.append([{"id": str(x["id"]), "updated_at": x.get("updated_at")} for x in items])
        return {"counts": counts, "windows": out_windows, "rows": rows}

    def _window_rest(self, board: str, cursor: str) -> list:
        """A window's next pages, to its end."""
        items: list = []
        pages = 1
        while cursor:
            pages += 1
            if pages > MAX_PAGES:
                raise MondayError("too_many_pages", f"board {board}: a window whose cursor never ends")
            page = self.post(NEXT_WINDOW, {"c": cursor, "limit": PAGE_LIMIT}).get("next_items_page")
            if not isinstance(page, dict) or not isinstance(page.get("items"), list):
                raise MondayError("delta_shape", f"board {board}: next_items_page without items")
            items += page["items"]
            cursor = page.get("cursor")
        return items

    def board_ids(self, boards: Iterable[str]) -> list:
        """[(items_count, the ids of the board's items, paged)] for each board, side by side: the cheap query (ids only)
        a whole reading starts with, which the desk's refresh pages too, to see which charges left a board or came to it
        unseen (D2's review of a32395f)."""
        boards = [str(b) for b in boards]
        if len(boards) < 2:
            return [self.item_ids(b) for b in boards]
        with ThreadPoolExecutor(max_workers=min(len(boards), CHUNK_WORKERS), thread_name_prefix="monday-ids") as pool:
            return list(pool.map(self.item_ids, boards))

    def read_boards(self, boards: list) -> list:
        """[(board_id, columns)] whole: the ids of every board side by side, then every row by id in one pool.
        -> [(items_count, the ids asked, rows)] in order."""
        counted = self.board_ids([b for b, _ in boards])
        rows = self.read_items([(ids, cols) for (_n, ids), (_b, cols) in zip(counted, boards)])
        return [(n, ids, r) for (n, ids), r in zip(counted, rows)]
