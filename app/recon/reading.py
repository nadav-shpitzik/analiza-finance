"""The reconciliation's reading of monday (Analiza-Monday backlog 0301 R0; spec §2.2; the rules' contract §0.6).

The desk's reading holds what its screen needs: the two charge boards and what their rows link to. The reconciliation
needs more, so it reads its own profile with the same reader (app/monday/client.py): the two charge boards, **every**
Account (the ones with a `מפתח חשבשבת` are the books' cards), every Deal, and the vouchers board (Defence's voucher card
10091 is tracked there). Every board is read whole and counted twice: the rows read against the board's `items_count`,
and the ids against themselves. A reading is complete only when every board agrees.

**A monday-side rule runs only on Analiza's books and a complete reading** (`monday_side`): a match between Analiza's
boards and the Demo company's books means nothing, and a short read would turn into alerts of things that are there.

**What is never read** (`NEVER_READ`): the ID numbers - Leads' `ת.ז`, the vouchers board's `ת.ז`, and Accounts' business
number, which for a sole proprietor is the person's ID number (ADR-0173 §1: an ID number is never stored server side) -
and a refund's bank account on Student Charge. None is in the profile, a test holds that, and the store refuses a body
that carries one.
"""

from __future__ import annotations

import functools
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

from ..monday.client import MondayError, MondayReader

PROFILE_NAME = "recon"
STUDENT_CHARGE = "5093448677"
B2B_CHARGE = "5097721228"
ACCOUNTS = "5089697343"
DEALS = "5089697338"
VOUCHERS = "5091817929"

# The column ids are analiza-metrics' (app/finance.py, read 22.11), the vouchers board's from Analiza-Monday
# docs/data-model/finance-processes.md (read live 2026-09-04). Every one is checked live before the rows are read.
SC = {
    "status": "color_mm1kxpvt", "paid": "numeric_mm1kgbnt", "refund": "numeric_mm62za1f",
    "refund_date": "date_mm8bvazs", "credit_approved": "numeric_mm8cvnsf", "planned": "date_mm8cp8gj",
    "refund_method": "color_mm8fm1sz", "credit_ref": "text_mm8fjzfj", "receipt": "color_mm4cq1hw",
    "channel": "color_mm1kxfgw", "ctype": "color_mm5fy93e", "invoice": "text_mm6d6s97",
    "enrollment": "board_relation_mm1kmggn", "lead": "board_relation_mm2whb5s", "paid_date": "date_mm8yh4bn",
}
B2 = {
    "deal": "board_relation_mm3yrbj", "account": "board_relation_mm3y27xa", "amount": "numeric_mm3ygk0b",
    "billing": "color_mm3zx2n7", "payment": "color_mm4bhvta", "component": "color_mm4arn0a",
    "month": "text_mm3ykpka", "course": "dropdown_mm4bcgdm", "due": "date_mm4a6y8y", "invoice": "text_mm3y6snz",
    "paid_so_far": "numeric_mm8zqhag",
}
AC = {"hash_key": "text_mm3yprqa", "invoice_name": "text_mm3yfrew", "terms": "dropdown_mm3yd36d"}
DL = {"stage": "deal_stage", "value": "deal_value", "payer": "board_relation_mm1fh059"}
VC = {"status": "status", "number": "text_mm0k9kmh", "value": "numeric_mm0mehjw", "invoice": "text_mm0mgcj5",
      "submitted": "date4"}

NEVER_READ = frozenset({
    "text_mm8cxm9y", "text_mm8cv3v3", "text_mm8cgc0d", "text_mm8ckgh8",  # Student Charge: a refund's bank account
    "text_mkzvem4",  # Leads: ת.ז
    "text_mm0kgsx9",  # the vouchers board: ת.ז
    "text_mm3y3gm7",  # Accounts: the business number, a sole proprietor's ID number
})


@dataclass(frozen=True)
class Board:
    key: str
    board_id: str
    columns: tuple


PROFILE = (
    Board("student_charge", STUDENT_CHARGE, tuple(SC.values())),
    Board("b2b_charge", B2B_CHARGE, tuple(B2.values())),
    Board("accounts", ACCOUNTS, tuple(AC.values())),
    Board("deals", DEALS, tuple(DL.values())),
    Board("vouchers", VOUCHERS, tuple(VC.values())),
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def check_board(board: Board, count: Optional[int], asked: list, rows: list) -> dict:
    """Counts only. A board is whole when its items_count, the rows read and the distinct ids agree, **and** the rows are
    exactly the ids its pages gave, each answered from this board with every column asked (review 22.11: by id, monday
    answers an item of any board, and drops the columns that board lacks)."""
    ids = [r["id"] for r in rows]
    strangers = sum(1 for r in rows if r["id"] not in set(asked) or r.get("board_id") != board.board_id)
    short = sum(1 for r in rows if not set(board.columns) <= set(r))
    unique = len(set(ids))
    ok = (count is not None and count == len(rows) == unique and set(ids) == set(asked) and not strangers
          and not short)
    return {"board_id": board.board_id, "items_count": count, "rows": len(rows), "unique": unique,
            "strangers": strangers, "short_rows": short, "ok": ok}


def read_profile(reader: MondayReader, profile: tuple = PROFILE, clock: Callable[[], float] = time.monotonic) -> dict:
    """The whole profile: every board's columns checked in one request, the boards' ids side by side, then every row by
    id. A reading that failed carries its closed code and no rows - `crashed` for anything outside monday's own errors,
    so it too replaces the stored reading (review 22.11) - and one that read every board is complete only when every
    board is whole."""
    started = clock()
    out = {"profile": PROFILE_NAME, "read_at": _now_iso(), "complete": False, "error": None, "detail": None,
           "checks": {}, "boards": {}, "calls": 0, "seconds": 0.0}
    keys, ids = [b.key for b in profile], [b.board_id for b in profile]
    if len(set(keys)) != len(keys) or len(set(ids)) != len(ids):  # one key over two boards hides the first's check
        out.update(error="profile", detail="a key or a board appears twice in the profile")
        return out
    try:
        reader.require_columns([(b.board_id, b.columns) for b in profile])
        results = reader.read_boards([(b.board_id, b.columns) for b in profile])
        for board, (count, asked, rows) in zip(profile, results):
            out["checks"][board.key] = check_board(board, count, asked, rows)
            out["boards"][board.key] = rows
    except MondayError as exc:
        out.update(error=exc.code, detail=exc.detail[:200], checks={}, boards={})
    except Exception as exc:  # noqa: BLE001 - said by class: a reading that crashed is a reading that failed
        out.update(error="crashed", detail=type(exc).__name__, checks={}, boards={})
    else:
        out["complete"] = all(c["ok"] for c in out["checks"].values())
    out["calls"], out["seconds"] = reader.calls, round(clock() - started, 1)
    return out


# ---- the guard -----------------------------------------------------------------------------------------------
class NotReady(Exception):
    """A monday-side rule asked to run where it may not. `reason` is one of NOT_READY."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


NOT_READY = ("demo", "company", "no_reading", "failed", "incomplete", "stale")
# The run reads monday fresh before its rules, every hour: a reading older than this means the last runs did not keep
# theirs, and nothing should be matched on it.
MAX_READING_AGE_S = 2 * 3600


def _age_s(read_at) -> Optional[float]:
    try:
        then = datetime.fromisoformat(str(read_at))
    except ValueError:
        return None
    if then.tzinfo is None:
        return None
    return (datetime.now(timezone.utc) - then).total_seconds()


def why_not_ready(company: str, reading: Optional[dict], profile: tuple = PROFILE,
                  max_age_s: float = MAX_READING_AGE_S) -> Optional[str]:
    """None when a monday-side rule may run; else why not. monday's boards are Analiza's: they are matched to Analiza's
    books only, and only on a recent reading of this profile in which every board was read whole - judged from the
    reading's own checks, not from a flag (review 22.11: a reading of an older profile, or a bare {"complete": True},
    was ready)."""
    if company != "analiza":
        return "demo" if company == "demo" else "company"
    if not reading:
        return "no_reading"
    if reading.get("error"):
        return "failed"
    checks = reading.get("checks") or {}
    whole = (reading.get("profile") == PROFILE_NAME and reading.get("complete") is True
             and set(checks) == {b.key for b in profile}
             and all(checks[b.key].get("ok") is True and checks[b.key].get("board_id") == b.board_id for b in profile))
    if not whole:
        return "incomplete"
    age = _age_s(reading.get("read_at"))
    if age is None or age > max_age_s:
        return "stale"
    return None


def monday_side(rule: Callable) -> Callable:
    """Marks a rule that matches the books to monday (`rule(company, reading, ...)`): it refuses, with NotReady, to run
    on Demo or on a reading that is missing, failed or incomplete (the rules' contract §0.6)."""

    @functools.wraps(rule)
    def guarded(company: str, reading: Optional[dict], *args, **kwargs):
        reason = why_not_ready(company, reading)
        if reason is not None:
            raise NotReady(reason)
        return rule(company, reading, *args, **kwargs)

    guarded.monday_side = True
    return guarded
