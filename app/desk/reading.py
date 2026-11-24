"""The desk's reading of monday (Analiza-Monday backlog 0301 D1; the desk move plan §1 item 4).

Ported on 24.11 from analiza-metrics `FinanceReader.read` (app/finance.py at `da671b2`) onto this service's reader
(app/monday/client.py, R0's port of the same transport), read only. What it reads:
- Student Charge and B2B Charge whole: their ids paged, then their rows by id;
- by id, what their rows link to: the Enrollments and Leads behind each payment, the Cohorts those name, and the Deals
  and Accounts behind each company charge.

In four steps, each one pool of at most CHUNK_WORKERS requests. analiza-metrics ran the two chains side by side with one
shared pool for their chunks; here the chains are staged together, so the cap holds without a shared pool:
1. the columns of all seven boards, in one request: a missing one ends the reading (`unknown_column`);
2. the ids of the two charge boards, side by side, then their rows;
3. the enrollments and the leads of the payments, and the deals of the company charges;
4. the cohorts of the enrollments and of the deciding leads, and the accounts of the company charges and of the deals'
   payers.

WHAT IS COUNTED (analiza-metrics' truth rules, and R0's):
- a charge board is whole when its items_count, the rows read and the distinct ids agree, and every row is one of the
  ids its pages gave, answered from this board with every column (R0's `check_board`, the same rule);
- an item read by id: the ids asked, the rows got, the ones missing (deleted, or not answered), and `strangers`, rows
  answered from another board (by id monday answers an item of any board, review 22.11): dropped and counted, never used
  as the board's. A missing linked item is a note on the page, not a failed reading, as in analiza-metrics
  (`enrollments_missing` and the rest); a row of the right board that lacks a column it was asked for fails the reading.

WHAT IS NEVER READ: NEVER_READ (app/monday/client.py): a refund's bank account, a lead's ID number, and an account's
business number. analiza-metrics read and kept the business number (`AC["vat_id"]`, stored in `finance_readings`); here the
reader refuses it, and the new desk shows it as it shows the ID number, read in the browser as the person (D2). Their
columns are still checked to exist, so a renamed one ends the reading instead of reading as empty.

THE SNAPSHOT has the shape of analiza-metrics' `read()`, so D2 ports `build_view` onto it: the two charge boards with their
items_count and rows, the linked rows, the lead names and owners, and what was asked. Beside it, `checks` (counts only)
and `complete`.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Optional

from ..monday.client import NEVER_READ, MondayError, MondayReader
from ..recon.reading import Board, check_board

logger = logging.getLogger("analiza_finance.desk")

STUDENT_CHARGE = "5093448677"
B2B_CHARGE = "5097721228"
COHORTS_BOARD = "5089806632"
DEALS_BOARD = "5089697338"
ACCOUNTS_BOARD = "5089697343"
ENROLLMENTS_BOARD = "5092151346"
LEADS_BOARD = "5089697337"
BOARDS = {"student_charge": STUDENT_CHARGE, "b2b_charge": B2B_CHARGE, "cohorts": COHORTS_BOARD,
          "deals": DEALS_BOARD, "accounts": ACCOUNTS_BOARD, "enrollments": ENROLLMENTS_BOARD, "leads": LEADS_BOARD}
# The boards behind each tab: a reader sees the counts of a tab's boards only when they may see the tab.
TAB_BOARDS = {"b2c": ("student_charge", "enrollments", "leads", "cohorts"),
              "b2b": ("b2b_charge", "deals", "accounts")}

# ---------------------------------------------------------------- column ids, analiza-metrics app/finance.py at da671b2
SC = {
    "status": "color_mm1kxpvt", "paid": "numeric_mm1kgbnt", "refund": "numeric_mm62za1f",
    "refund_date": "date_mm8bvazs", "credit_approved": "numeric_mm8cvnsf", "planned": "date_mm8cp8gj",
    "refund_method": "color_mm8fm1sz", "credit_ref": "text_mm8fjzfj", "receipt": "color_mm4cq1hw",
    "channel": "color_mm1kxfgw", "ctype": "color_mm5fy93e", "invoice": "text_mm6d6s97",
    "enrollment": "board_relation_mm1kmggn", "lead": "board_relation_mm2whb5s",
    "paid_date": "date_mm8yh4bn",
    # a refund's bank account (ADR-0143): checked to exist, never read here (NEVER_READ)
    "bank_holder": "text_mm8cxm9y", "bank": "text_mm8cv3v3", "bank_branch": "text_mm8cgc0d", "bank_account": "text_mm8ckgh8",
}
B2 = {
    "deal": "board_relation_mm3yrbj", "account": "board_relation_mm3y27xa", "amount": "numeric_mm3ygk0b",
    "billing": "color_mm3zx2n7", "payment": "color_mm4bhvta", "component": "color_mm4arn0a",
    "month": "text_mm3ykpka", "course": "dropdown_mm4bcgdm", "due": "date_mm4a6y8y", "invoice": "text_mm3y6snz",
    "paid_so_far": "numeric_mm8zqhag",
}
CO = {"code": "text_mkzh1j69", "status": "color_mkzh250v", "flow": "color_mm31k62f",
      "dates": "timerange_mkzdtx6t", "external": "text_mm383gtw"}
DL = {"stage": "deal_stage", "value": "deal_value", "payer": "board_relation_mm1fh059"}
# the billing details: `vat_id` is the business number, checked to exist and never read here (NEVER_READ)
AC = {"terms": "dropdown_mm3yd36d", "vat_id": "text_mm3y3gm7", "hash_key": "text_mm3yprqa", "invoice_name": "text_mm3yfrew"}
EN = {"cohort": "board_relation_mm1h915d", "voucher": "color_mm5fr6d6", "asked": "date_mm8csw5y"}
# the lead's ID number: checked to exist, never read here (NEVER_READ); `rep` is its Owner, the payment's "נציג מכירות"
LD = {"cohort": "board_relation_mkzvra97", "id_number": "text_mkzvem4", "rep": "multiple_person_mm2wwjjn"}
COLUMNS = {"student_charge": SC, "b2b_charge": B2, "cohorts": CO, "deals": DL, "accounts": AC,
           "enrollments": EN, "leads": LD}
# what the columns check asks of each board (every column the desk uses), and what a reading holds (the same, less
# NEVER_READ)
CHECKED_COLUMNS = {key: list(cols.values()) for key, cols in COLUMNS.items()}
ROW_COLUMNS = {key: [c for c in cols if c not in NEVER_READ] for key, cols in CHECKED_COLUMNS.items()}

# The reading's own limits: monday answers the whole of it in some seconds (R0: 6.1 s for five boards whole, 23.11).
READ_DEADLINE_S = 50
HELD_TTL_S = 60  # an open within a minute of a reading is answered with it
FAIL_GAP_S = 20  # after a reading that failed, the next is tried this much later, not on every open


def _uniq(values: Iterable[Any]) -> list:
    out, seen = [], set()
    for v in values:
        v = str(v)
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out


# ---------------------------------------------------------------- what a payment links to (analiza-metrics, the same)
def enrollment_ids(sc_rows: list) -> list:
    return sorted(_uniq(e for r in sc_rows for e in r.get(SC["enrollment"]) or []))


def enrollment_cohorts(r: dict, by_enrollment: dict) -> list:
    return _uniq(c for e in r.get(SC["enrollment"]) or [] for c in by_enrollment.get(str(e), []))


def lead_ids(sc_rows: list) -> list:
    """Every payment's lead: its name is the payment's "ליד מקור", its Owner the payment's "נציג מכירות", and its cohort
    decides when the enrollment names none (`leads_deciding`)."""
    return sorted(_uniq(x for r in sc_rows for x in r.get(SC["lead"]) or []))


def leads_deciding(sc_rows: list, enrollments: list) -> list:
    """The leads whose cohort decides: those of the payments whose enrollment names no cohort. The only leads a reading
    keeps as rows (analiza-metrics, 12.11)."""
    by_enr = {str(e["id"]): _uniq(e.get(EN["cohort"]) or []) for e in enrollments}
    return sorted(_uniq(x for r in sc_rows if not enrollment_cohorts(r, by_enr) for x in r.get(SC["lead"]) or []))


def lead_names_of(ids: Iterable[Any], rows: list) -> dict:
    """{lead id: its name}, in the order asked; None for one monday did not return."""
    got = {str(r["id"]): r.get("name") or "" for r in rows}
    return {str(i): got.get(str(i)) for i in ids}


def lead_rep(row: dict) -> str:
    return (row.get(LD["rep"]) or "").strip()


def lead_reps_of(ids: Iterable[Any], rows: list) -> dict:
    """{lead id: its Owner}: "" for a lead with no owner, None for one monday did not return - never the same value."""
    got = {str(r["id"]): lead_rep(r) for r in rows}
    return {str(i): got.get(str(i)) for i in ids}


def cohort_ids(enrollments: list, leads: list) -> list:
    return sorted(_uniq([c for e in enrollments for c in e.get(EN["cohort"]) or []]
                        + [c for x in leads for c in x.get(LD["cohort"]) or []]))


def keep_only(rows: list, ids: list) -> list:
    want = {str(x) for x in ids}
    return [r for r in rows if str(r["id"]) in want]


# ---------------------------------------------------------------- the counts
def whole_board(key: str, count: Optional[int], asked: list, rows: list) -> tuple:
    """(the rows of this board among the ids its pages gave, once each; R0's check of the board)."""
    board = Board(key, BOARDS[key], tuple(ROW_COLUMNS[key]))
    check = check_board(board, count, asked, rows)
    want, seen, kept = set(asked), set(), []
    for r in rows:
        if r["id"] in want and r.get("board_id") == board.board_id and r["id"] not in seen:
            seen.add(r["id"])
            kept.append(r)
    return kept, check


def by_id(key: str, asked: list, rows: list) -> tuple:
    """(the rows of this board among the ids asked, once each; the counts). A row answered from another board is a
    stranger: dropped and counted. A deleted item never comes back (the reader drops it): it is missing."""
    board_id, cols = BOARDS[key], set(ROW_COLUMNS[key])
    want, seen, kept = set(asked), set(), []
    strangers = short = archived = 0
    for r in rows:
        if r.get("board_id") != board_id or r["id"] not in want:
            strangers += 1
            continue
        if r["id"] in seen:
            continue
        seen.add(r["id"])
        if not cols <= set(r):
            short += 1
        if r.get("state") == "archived":
            archived += 1
        kept.append(r)
    check = {"board_id": board_id, "asked": len(want), "rows": len(kept), "missing": len(want - seen),
             "strangers": strangers, "archived": archived, "short_rows": short, "ok": short == 0}
    return kept, check


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_desk(reader: MondayReader, clock: Callable[[], float] = time.monotonic) -> dict:
    """The whole reading: the snapshot (analiza-metrics' shape), its counts, and whether it is complete. A reading that
    failed carries its closed code and no rows: monday's own code, or `crashed` with the class for anything else."""
    started = clock()
    read_at = _now_iso()
    out: dict = {"read_at": read_at, "full_read_at": read_at, "kind": "full", "complete": False, "error": None,
                 "detail": None, "checks": {}, "calls": 0, "seconds": 0.0}
    try:
        reader.require_columns([(BOARDS[key], CHECKED_COLUMNS[key]) for key in BOARDS])
        (n_sc, sc_asked, sc_read), (n_b2, b2_asked, b2_read) = reader.read_boards(
            [(STUDENT_CHARGE, ROW_COLUMNS["student_charge"]), (B2B_CHARGE, ROW_COLUMNS["b2b_charge"])])
        checks: dict = {}
        sc_rows, checks["student_charge"] = whole_board("student_charge", n_sc, sc_asked, sc_read)
        b2_rows, checks["b2b_charge"] = whole_board("b2b_charge", n_b2, b2_asked, b2_read)

        enr_ids, ld_ids = enrollment_ids(sc_rows), lead_ids(sc_rows)
        deal_ids = sorted(_uniq(d for r in b2_rows for d in r.get(B2["deal"]) or []))
        enr_read, ld_read, deal_read = reader.read_items([(enr_ids, ROW_COLUMNS["enrollments"]),
                                                          (ld_ids, ROW_COLUMNS["leads"]),
                                                          (deal_ids, ROW_COLUMNS["deals"])])
        enrollments, checks["enrollments"] = by_id("enrollments", enr_ids, enr_read)
        leads, checks["leads"] = by_id("leads", ld_ids, ld_read)
        deals, checks["deals"] = by_id("deals", deal_ids, deal_read)

        deciding = leads_deciding(sc_rows, enrollments)
        kept_leads = keep_only(leads, deciding)
        coh_ids = cohort_ids(enrollments, kept_leads)
        acct_ids = sorted(_uniq([a for r in b2_rows for a in r.get(B2["account"]) or []]
                                + [a for d in deals for a in d.get(DL["payer"]) or []]))
        coh_read, acct_read = reader.read_items([(coh_ids, ROW_COLUMNS["cohorts"]), (acct_ids, ROW_COLUMNS["accounts"])])
        cohorts, checks["cohorts"] = by_id("cohorts", coh_ids, coh_read)
        accounts, checks["accounts"] = by_id("accounts", acct_ids, acct_read)
    except MondayError as exc:
        out.update(error=exc.code, detail=exc.detail[:200])
    except Exception as exc:  # noqa: BLE001 - said by class: a reading that crashed is a reading that failed
        out.update(error="crashed", detail=type(exc).__name__)
    else:
        out.update(
            student_charge={"items_count": n_sc, "rows": sc_rows}, b2b_charge={"items_count": n_b2, "rows": b2_rows},
            enrollments=enrollments, leads=kept_leads, lead_names=lead_names_of(ld_ids, leads),
            lead_reps=lead_reps_of(ld_ids, leads), cohorts=cohorts, deals=deals, accounts=accounts,
            asked={"enrollments": enr_ids, "leads": deciding, "cohorts": coh_ids, "deals": deal_ids,
                   "accounts": acct_ids},
            checks=checks, complete=all(c["ok"] for c in checks.values()))
    out["calls"], out["seconds"] = reader.calls, round(clock() - started, 1)
    logger.info("desk reading in %.1fs, %d calls: complete=%s error=%s", out["seconds"], out["calls"], out["complete"],
                out["error"])
    return out


def summary(reading: dict) -> dict:
    """A reading without its rows: when, whether complete, why not, its cost and its counts. What a member's answer
    and the operator's views may carry; /health carries less (`health_part`)."""
    return {"read_at": reading.get("read_at"), "complete": bool(reading.get("complete")), "error": reading.get("error"),
            "detail": reading.get("detail"), "seconds": reading.get("seconds"), "calls": reading.get("calls"),
            "checks": reading.get("checks") or {}}


def health_part(last: Optional[dict]) -> Optional[dict]:
    """For the public /health: whether the last reading was complete and why not, its time and cost. No count of rows
    (Nadav 19.11: the deployment is public)."""
    if last is None:
        return None
    return {"read_at": last.get("read_at"), "complete": last.get("complete"), "error": last.get("error"),
            "seconds": last.get("seconds"), "calls": last.get("calls"),
            "boards_whole": None if last.get("error") else
            sorted(k for k, c in (last.get("checks") or {}).items() if c.get("ok"))}


class HeldReading:
    """The desk's reading, one per instance, shared by every opener (in memory: D1 keeps no copy in Postgres).

    `get()` answers with the reading held when it is younger than HELD_TTL_S; otherwise it reads, one reading at a time:
    a request that finds one running waits for it (bounded by its deadline) and is answered with it. A reading that
    failed is kept as the last attempt, not as the reading: the one before stays on screen with its age, and the next
    try comes FAIL_GAP_S later, not on every open. A reading that read every board but came short replaces the one
    before: it is the truth of now, shown as not complete."""

    def __init__(self, make_reader: Callable[[], MondayReader], clock: Callable[[], float] = time.monotonic,
                 ttl_s: float = HELD_TTL_S, deadline_s: float = READ_DEADLINE_S, fail_gap_s: float = FAIL_GAP_S):
        self.make_reader, self.clock = make_reader, clock
        self.ttl_s, self.deadline_s, self.fail_gap_s = ttl_s, deadline_s, fail_gap_s
        self.reading: Optional[dict] = None
        self.reading_at: Optional[float] = None
        self.last: Optional[dict] = None
        self.last_at: Optional[float] = None
        self._lock = threading.Lock()

    def _young(self, at: Optional[float], limit: float) -> bool:
        return at is not None and 0 <= self.clock() - at < limit

    def get(self) -> tuple:
        """(the reading to show or None, the last attempt's summary or None)."""
        with self._lock:
            if self.reading is not None and self._young(self.reading_at, self.ttl_s):
                return self.reading, self.last
            if self.last is not None and self.last.get("error") and self._young(self.last_at, self.fail_gap_s):
                return self.reading, self.last
            reader = None
            try:
                reader = self.make_reader()
                reader.deadline = reader.clock() + self.deadline_s
                reading = read_desk(reader)
            except Exception as exc:  # noqa: BLE001 - a reader that could not even be made is a failed reading
                reading = {"read_at": _now_iso(), "complete": False, "error": "crashed", "detail": type(exc).__name__,
                           "checks": {}, "calls": 0, "seconds": 0.0}
            finally:
                if reader is not None:
                    try:
                        reader.close()
                    except Exception:  # noqa: BLE001 - closing a client never fails a reading
                        pass
            self.last, self.last_at = summary(reading), self.clock()
            if not reading.get("error"):
                self.reading, self.reading_at = reading, self.last_at
            return self.reading, self.last

    def age_s(self) -> Optional[int]:
        return None if self.reading_at is None else max(0, int(self.clock() - self.reading_at))
