"""Who may see the desk (Analiza-Monday backlog 0301 D1; ADR-0167 in the docs repo, the gate by board).

Ported on 24.11 from analiza-metrics at `da671b2` (`finance_gate` in app/finance_api.py, `board_member` and `RateLimiter`
in app/api.py), with two changes, both said below: a guest is refused, and a board's members are read once at a time.

THE GATE, in order:
1. no Client Secret here: 503 `app_not_configured` (a missing secret must not pass for "every token is forged");
2. the sessionToken (app/desk/auth.py): 401 `unauthorized`, or 401 `token_expired`, which the page answers by asking
   monday for a fresh token once;
3. a guest (`dat.is_guest` true): 403 `guest`. New here: the desk joins names and money from boards a guest is not on
   (the leads, the cohorts, the deals, the accounts). In monday a guest reaches only shareable boards, so today's member
   check already keeps one out; this keeps it so if a charge board is ever shared. The claim's name is not verified live
   (auth.py rule 5): until it is, the membership check is what holds;
4. membership of each charge board: the students' tab for Student Charge's members, the companies' tab for B2B
   Charge's (ACC-34: Inna is on the first and not the second), so nobody is shown a row their own board would not show
   them. Neither: 403 `not_a_board_member`. A board whose members could not be read counts as not a member (fail
   closed), and when neither answered yes and one could not be read the answer is 503 `members_unavailable`, not 403:
   whether this reader may see the desk is not known yet, and a 403 would tell Inna she has no access at all. The tab
   of a board that could not be read is said as unknown (None), never as "not a member" (D1's review);
5. the reader's budget, a member's only: 429 `rate_limited` with Retry-After. After the membership, so a request refused
   at the gate costs nothing (D1's review: before it, anyone in the account could spend the day's budget and lock the
   finance team out; analiza-metrics had the same order).

A viewer (`dat.is_view_only`) passes: a viewer of Student Charge sees its rows in monday too. What a viewer may not do is
write, and every write of the new desk is the person's own, in the browser (ADR-0188), where monday refuses it itself.

The members are read with the service's monday token, a read only (app/monday/client.py `board_subscribers`), kept
MEMBERS_TTL_S, a failed read MEMBERS_FAIL_TTL_S so an outage is not asked again on every open. One read per board at a
time: a request that finds one running waits for it (analiza-metrics let two run side by side).
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Optional, Union

from fastapi import Request
from fastapi.responses import JSONResponse

from .auth import ANALIZA_ACCOUNT_ID, AuthError, SessionIdentity, verify_session_token

logger = logging.getLogger("analiza_finance.desk")

MEMBERS_TTL_S = 300
MEMBERS_FAIL_TTL_S = 30
# The reader's budget for the desk's routes: an open is one request, a working hour some dozens.
DESK_PER_USER_HOUR = 600
DESK_PER_DAY = 20000


def err(status: int, key: str, headers: Optional[dict] = None, **extra) -> JSONResponse:
    return JSONResponse({"error": key, **extra}, status_code=status, headers=headers)


def _one_line(text: str) -> str:
    """The audit line is one line: a control character (a newline above all) becomes `?`, so nothing that came with a
    request can open a second line attributed to someone else (analiza-metrics `_one_line`)."""
    return "".join("?" if (ch < " " or ch in "\x7f\u0085  ") else ch for ch in str(text))[:200]


def audit(identity: Optional[SessionIdentity], endpoint: str, outcome: str, count: int = 0) -> None:
    """One line per request, ids and closed words only: never a name, an amount or a token."""
    logger.info("desk user=%s endpoint=%s outcome=%s results=%d",
                identity.user_id if identity is not None else "-", endpoint, _one_line(outcome), count)


def bearer(request: Request) -> str:
    value = request.headers.get("authorization") or ""
    return value[7:].strip() if value[:7].lower() == "bearer " else ""


class RateLimiter:
    """A sliding window per user and one for the instance (analiza-metrics `RateLimiter`, the same rule). In memory, per
    instance: under Autoscale each instance counts alone, which is enough for a dampener."""

    def __init__(self, per_user_hour: int, global_day: int, user_window: int = 3600, global_window: int = 86400):
        self.per_user_hour, self.global_day = per_user_hour, global_day
        self.user_window, self.global_window = user_window, global_window
        self._users: dict = {}
        self._global: deque = deque()
        self._lock = threading.Lock()

    def check(self, user_id: int, now: Optional[float] = None) -> Optional[int]:
        """None: allowed. Otherwise the seconds to wait (Retry-After)."""
        now = time.time() if now is None else now
        with self._lock:
            global_ago, user_ago = now - self.global_window, now - self.user_window
            while self._global and self._global[0] < global_ago:
                self._global.popleft()
            bucket = self._users.setdefault(user_id, deque())
            while bucket and bucket[0] < user_ago:
                bucket.popleft()
            if len(self._global) >= self.global_day:
                return max(1, int(self._global[0] + self.global_window - now))
            if len(bucket) >= self.per_user_hour:
                return max(1, int(bucket[0] + self.user_window - now))
            bucket.append(now)
            self._global.append(now)
            return None


class Members:
    """Who is on each board, by board id: {board: (when read, the ids or None for a failed read)}. `read(board_id)`
    returns the set of user ids as strings, or raises."""

    def __init__(self, read: Callable[[str], set], clock: Callable[[], float] = time.monotonic):
        self._read, self._clock = read, clock
        self._held: dict = {}
        self._locks: dict = {}
        self._guard = threading.Lock()
        self.last_error: dict = {}  # board -> the class or code of its last failed read, for /health

    def _lock_for(self, board_id: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(board_id, threading.Lock())

    def _fresh(self, board_id: str) -> tuple:
        at, ids = self._held.get(board_id, (None, None))
        if at is None:
            return False, None
        ttl = MEMBERS_TTL_S if ids is not None else MEMBERS_FAIL_TTL_S
        return 0 <= self._clock() - at <= ttl, ids

    def ids(self, board_id: str) -> Optional[set]:
        board_id = str(board_id)
        fresh, ids = self._fresh(board_id)
        if fresh:
            return ids
        with self._lock_for(board_id):
            fresh, ids = self._fresh(board_id)  # another request may have read it while this one waited
            if fresh:
                return ids
            try:
                got = self._read(board_id)
                ids = {str(x) for x in got} if got else None
                if ids is None:
                    raise ValueError("no members")
                self.last_error.pop(board_id, None)
            except Exception as exc:  # noqa: BLE001 - the class or the code is the trace, never the message
                ids = None
                self.last_error[board_id] = getattr(exc, "code", None) or type(exc).__name__
                logger.warning("board members not read: board=%s (%s)", board_id, self.last_error[board_id])
            self._held[board_id] = (self._clock(), ids)
            return ids

    def member(self, user_id: int, board_id: str) -> Optional[bool]:
        """True or False, or None when the board's members could not be read."""
        ids = self.ids(board_id)
        return None if ids is None else str(user_id) in ids

    def state(self) -> dict:
        """For /health: per board whether its members were read, failed, or not asked yet. Never an id."""
        out = {}
        for board_id, (at, ids) in sorted(self._held.items()):
            out[board_id] = "read" if ids is not None else ("failed: " + str(self.last_error.get(board_id, "")))[:60]
        return out


@dataclass(frozen=True)
class Visitor:
    """Who passed the gate. Each tab is True (a member), False (not), or None (the board's members could not be read):
    only True opens a tab."""
    identity: SessionIdentity
    b2c: Optional[bool]  # Student Charge: the students' tab
    b2b: Optional[bool]  # B2B Charge: the companies' tab


def gate(request: Request, client_secret: str, members: Members, limiter: RateLimiter, endpoint: str,
         student_board: str, company_board: str,
         account_id: int = ANALIZA_ACCOUNT_ID) -> Union[Visitor, JSONResponse]:
    """The whole gate, in the order of the module's docstring: a Visitor, or the refusal to send."""
    if not client_secret:
        audit(None, endpoint, "app_not_configured")
        return err(503, "app_not_configured")
    try:
        identity = verify_session_token(bearer(request), client_secret, account_id)
    except AuthError as exc:
        audit(None, endpoint, exc.error_key)
        return err(401, exc.error_key)
    if identity.guest is True:
        audit(identity, endpoint, "guest")
        return err(403, "guest")
    b2c = members.member(identity.user_id, student_board)
    b2b = members.member(identity.user_id, company_board)
    if not b2c and not b2b:
        if b2c is None or b2b is None:
            audit(identity, endpoint, "members_unknown")
            return err(503, "members_unavailable")
        audit(identity, endpoint, "not_a_member")
        return err(403, "not_a_board_member")
    wait = limiter.check(identity.user_id)
    if wait is not None:
        audit(identity, endpoint, "rate_limited")
        return err(429, "rate_limited", headers={"Retry-After": str(wait)}, retry_after_seconds=wait)
    return Visitor(identity=identity, b2c=b2c, b2b=b2b)
