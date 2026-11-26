"""The desk's reading of monday (Analiza-Monday backlog 0301 D1; the desk move plan §1 item 4).

Ported on 24.11 from analiza-metrics `FinanceReader.read` (app/finance.py at `da671b2`) onto this service's reader
(app/monday/client.py, R0's port of the same transport), read only. What it reads:
- Student Charge and B2B Charge whole: their ids paged, then their rows by id;
- by id, what their rows link to: the Enrollments and Leads behind each payment, the Cohorts those name, and the Deals
  and Accounts behind each company charge.

In five steps, each one pool of at most CHUNK_WORKERS requests. analiza-metrics ran the two chains side by side with one
shared pool for their chunks; here the chains are staged together, so the cap holds without a shared pool:
1. the columns of all seven boards, in one request: a missing one ends the reading (`unknown_column`);
2. the ids of the two charge boards, side by side, then their rows;
3. the enrollments and the leads of the payments, and the deals of the company charges;
4. the cohorts of the enrollments and of the deciding leads, and the accounts of the company charges and of the deals'
   payers;
5. (D4, backlog 0313: the students by cohort) the enrollments of those cohorts that have a price and are not cancelled,
   through each cohort's link to its enrollments (`roster_ids`), those step 3 did not read - none when no cohort has one.
   They join the enrollments: one list of the board's rows, asked and counted as one (`asked["enrollments"]` holds both),
   so a refresh lays them over the held reading like any other; a refresh reads the cohorts with a price again every
   time, and the enrollments newly linked to them by id.

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

WHAT IS MASKED (D2's review of a32395f; Nadav 24.11): an ID number typed where it does not belong - into a lead's name, a
payment's credit reference, the name on an account's invoice - is no column the reader can refuse. So every name and
every free-text cell a reading reads (TEXT_CELLS) passes `mask_ids` as it is read, in a full reading and in a refresh
alike: a number of ID_DIGITS digits or more, judged whole by the books' cleaning (app/books/clean.py), becomes ID_MARK,
and a shorter one (an invoice, the Hashavshevet key, a cohort code) stays. So the reading in memory, its stored copy, the
page and the Excel file all hold the same masked text, and none an ID number (CLAUDE.md rule 4).

THE SNAPSHOT has the shape of analiza-metrics' `read()`, so D2 ports `build_view` onto it: the two charge boards with their
items_count and rows, the linked rows, the lead names and owners, and what was asked. Beside it, `checks` (counts only)
and `complete`.

THE REFRESH AND THE STORED COPY (D2, 24.11; analiza-metrics round 3 slice 2, app/finance.py `read_delta` and
app/finance_mirror.py at da671b2). A full reading costs about 9 s and 28 requests (measured live 24.11), and D1 held it in
the memory of one Autoscale instance only, so a cold open waited for one. Now:
1. `read_delta` reads only what changed since the held reading started: each board with a window is asked which items
   were updated, only those are read again by id and laid over the held reading, and the deciding leads are read again
   every time (Leads has no window). The two charge boards' ids are paged too (ids only, the query a full reading starts
   with): a charge no longer there is dropped and one that came unseen is read by id, since no window shows either and a
   count stays equal when one leaves as another comes (review of a32395f). The same snapshot, `kind` "delta", with its
   counts.
2. `HeldReading` keeps the last reading in Postgres too (app/desk/store.py, `desk_readings`): an instance that holds none
   answers an open with it at once, marked `refreshing`, and the page's own next request (`?fresh=1`) reads the next.
   Never a thread after the answer (analiza-metrics ADR-0087): a request, every time.
3. A full reading instead when nothing is held or stored, when the last full one is older than FULL_AFTER, when a charge
   board cannot be reconciled with its ids (pages that disagree with their own count, or an id paged that no read gave as
   the board's active row), or when the refresh failed in a way monday or its answer's shape allowed - at most once per
   FALLBACK_GAP; an error a full reading would meet the same way (NO_FALLBACK) is the attempt's error as is. Each says why
   in /health (`HeldReading.health`).
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import threading
import time
import unicodedata
from collections import Counter, deque
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Optional

# the books' rule for what a number is (CLAUDE.md rule 4): where it begins and ends, and its shape - reused as is
from ..books.clean import _numbers, _process, normalize
from ..monday.client import NEVER_READ, MondayError, MondayReader
from ..recon.reading import Board, check_board
from ..recon.receipt_dates import parse_refs

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
      "dates": "timerange_mkzdtx6t", "external": "text_mm383gtw",
      # D4 (backlog 0313): "מחיר", the price the student pays, a TEXT column ("10,000", "12000"), and the cohort's link to
      # its enrollments, the reflection of each enrollment's own cohort link (exact: 1,700 of 1,700, knowledge 01.09)
      "price": "text_mm5fhydb", "roster": "board_relation_mm1hetrb"}
DL = {"stage": "deal_stage", "value": "deal_value", "payer": "board_relation_mm1fh059"}
# the billing details: `vat_id` is the business number, checked to exist and never read here (NEVER_READ)
AC = {"terms": "dropdown_mm3yd36d", "vat_id": "text_mm3y3gm7", "hash_key": "text_mm3yprqa", "invoice_name": "text_mm3yfrew"}
EN = {"cohort": "board_relation_mm1h915d", "voucher": "color_mm5fr6d6", "asked": "date_mm8csw5y",
      # D4: the enrollment's own Status (נרשם, פעיל ...) and its person (the Directory), who a cohort's students are
      "status": "color_mm0sxcvn", "person": "board_relation_mm0spa0g"}
# the lead's ID number: checked to exist, never read here (NEVER_READ); `rep` is its Owner, the payment's "נציג מכירות"
LD = {"cohort": "board_relation_mkzvra97", "id_number": "text_mkzvem4", "rep": "multiple_person_mm2wwjjn"}
COLUMNS = {"student_charge": SC, "b2b_charge": B2, "cohorts": CO, "deals": DL, "accounts": AC,
           "enrollments": EN, "leads": LD}
# what the columns check asks of each board (every column the desk uses), and what a reading holds (the same, less
# NEVER_READ)
CHECKED_COLUMNS = {key: list(cols.values()) for key, cols in COLUMNS.items()}
ROW_COLUMNS = {key: [c for c in cols if c not in NEVER_READ] for key, cols in CHECKED_COLUMNS.items()}
# the cells a person types free text into, masked like an item's name (`masked`): the text columns and the lead's Owner;
# never a number, date, status, relation, dropdown or timeline cell, whose text monday writes
TEXT_CELLS = {key: tuple(c for c in cols if c.startswith(("text_", "multiple_person_"))) for key, cols in ROW_COLUMNS.items()}
# an ID-like number in that text is replaced by ID_MARK (`mask_ids`)
ID_DIGITS = 8
ID_MARK = "#"
# the two charge boards' "אסמכתת חשבונית": the documents a payment cites. A cell A1 reads as a list of numbers
# (`parse_refs`) is kept as typed - "062894, 62914" is two documents, though the books' cleaning reads it as one number of
# eleven digits and `mask_ids` would mask it whole, so A1 and the desk's marks lost both (the review of dfa967b) - and
# only a number of REF_DIGITS digits or more in it is masked, as no document number is (`mask_refs`)
INVOICE_CELLS = {"student_charge": SC["invoice"], "b2b_charge": B2["invoice"]}
REF_DIGITS = 7

# The reading's own limits: monday answers the whole of it in some seconds (R0: 6.1 s for five boards whole, 23.11).
READ_DEADLINE_S = 50
HELD_TTL_S = 60  # an open within a minute of a reading is answered with it
FAIL_GAP_S = 20  # after a reading that failed, the next is tried this much later, not on every open
# a request that finds a reading in progress waits for it at most a whole reading's deadline and this margin (its save is
# one short statement after it), then is answered with what is held: a save whose connection dropped silently could hold
# the reading lock far longer - the store's statement_timeout is the server's (D2's review; analiza-metrics' SharedReading
# waited `acquire(timeout=wait())` the same way)
WAIT_MARGIN_S = 0.5

# ---- the refresh (D2): analiza-metrics app/finance.py at da671b2, its constants with their reasons
# the filter is day-grained: each item's `updated_at` makes the exact cut, at the held reading's start less this margin (a
# clock between here and monday, and an item written while the held reading was being read)
DELTA_MARGIN = timedelta(minutes=10)
# a refresh takes about a second; past this it gives way to a full reading inside the same READ_DEADLINE_S
DELTA_DEADLINE_S = 20
# the boards a refresh asks what was updated lately (each with its columns check and its items_count), and the one it never
# asks: Leads' columns are checked in the same request, and its deciding leads read there by id
DELTA_KEYS = ("student_charge", "b2b_charge", "enrollments", "cohorts", "deals", "accounts")
CHECK_ONLY_KEYS = ("leads",)
CHARGE_KEYS = ("student_charge", "b2b_charge")
LINKED_KEYS = ("enrollments", "leads", "cohorts", "deals", "accounts")
# what a reading holds of monday: laid under the next refresh, and kept in Postgres (app/desk/store.py keeps these only)
BODY_KEYS = CHARGE_KEYS + LINKED_KEYS + ("lead_names", "lead_reps", "asked")
# the body's shape: a version and the columns each board's rows hold, so a reading kept under another shape (an older
# deploy) is never laid under a refresh that reads this one - it is read whole instead, once (analiza-metrics BODY_SCHEMA).
# 2 since its text is masked (`masked`, D2's review): a reading kept before is never laid under a refresh, so no row it
# holds unmasked outlives the first full reading after the deploy. 3 since a whole date is kept (QA 24.11): a name the
# mask of 2 hid ("... 01/12/2026 | 12.26") is read again whole. 4 since D4 (backlog 0313): a cohort's price and its link to
# its enrollments, an enrollment's Status and person, and the enrollments of the cohorts with a price (`roster_ids`) - a
# reading kept before holds none of them, so it is read whole once rather than refreshed short of every student
BODY_SCHEMA = "4:" + hashlib.sha256(json.dumps(ROW_COLUMNS, sort_keys=True).encode()).hexdigest()[:12]
# a full reading at least this often: a change no refresh can see (a deleted enrollment, a renamed lead that decides
# nothing) lives at most this long
FULL_AFTER = timedelta(hours=36)
# a refresh error a full reading would meet the same way: the attempt's error as it is, never a full reading - a rate limit
# above all, since a full reading is some thirty requests sent into the very limit monday just named (hostile review 11.11)
NO_FALLBACK = ("auth", "rate_limited", "unknown_column")
# a refresh that keeps failing reads whole at most once per this span; in between its own error is the attempt's, so a
# press every 5 s never prices a full reading (hostile review 11.11)
FALLBACK_GAP = timedelta(seconds=30)
RECENT_CODES = 20


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


# ---------------------------------------------------------------- D4: the students of a cohort with a price (backlog 0313)
# The desk counts a cohort's money by its students, and its students come from Enrollments, not from the payments: a student
# who paid nothing is in no payment, and owes the whole price. So the reading reads the enrollments of every cohort it reads
# that has a price, through the cohort's own link to them (CO["roster"]); which of them study in the cohort is decided by
# each enrollment's own cohort link, the side that owns it (app/desk/cohorts.py).
COHORT_CANCELLED = "2"  # Cohorts' Status "בוטל" (color_mkzh250v, by index; verified live 2026-08-09)


def roster_cohort(row: dict) -> bool:
    """A cohort whose enrollments the reading reads: its "מחיר" holds text, and its Status is not "בוטל" (a cancelled
    cohort's students owe it nothing, app/desk/cohorts.py). Whether it is archived is not asked: a refresh cannot see an
    item archived (its time does not move), and the desk reads a linked item the same, archived or not."""
    return bool(str(row.get(CO["price"]) or "").strip()) and str(row.get(CO["status"] + "#") or "") != COHORT_CANCELLED


def roster_ids(cohorts: list) -> list:
    """The enrollments those cohorts link to, sorted, each once."""
    return sorted(_uniq(e for c in cohorts if roster_cohort(c) for e in c.get(CO["roster"]) or []))


# ---------------------------------------------------------------- no ID number in what a reading holds (D2's review, #6)
# A date or a month written whole, cut out before a text is judged (QA 24.11): the books' cleaning joins digits across a
# mark or two, so a charge's name "A669 | ... 01/12/2026 | 12.26 | ..." read as one number of twelve digits and was masked,
# and so did "1.12.26-31.12.26" or "מחזור 2610 01/12/2026". Each token must be valid (a day 1-31, a month 1-12, a year of
# 19xx or 20xx where it has four digits) and stand apart from any other digit, so no run of an ID number's digits is ever
# one (039482716, 039-482-716 and 03-9482716 are judged as before).
_DATE_TOKEN = re.compile(
    r"(?<![0-9])(?:"
    r"(?P<d>[0-9]{1,2})(?P<s>[./-])(?P<m>[0-9]{1,2})(?P=s)(?:(?:19|20)[0-9]{2}|[0-9]{2})"  # 01/12/2026, 1.12.26
    r"|(?:19|20)[0-9]{2}(?P<s2>[./-])(?P<m2>[0-9]{1,2})(?P=s2)(?P<d2>[0-9]{1,2})"  # 2026-12-01
    r"|(?P<m3>[0-9]{1,2})[./-](?:19|20)[0-9]{2}"  # 12/2026
    r"|(?:19|20)[0-9]{2}[./-](?P<m4>[0-9]{1,2})"  # 2026-12
    r"|(?P<m5>[0-9]{1,2})[./][0-9]{2}"  # 12.26
    r")(?![0-9])")


def _date_token(m: "re.Match") -> bool:
    """Whether a match of _DATE_TOKEN is a real date or month (its day and month in range)."""
    day = m.group("d") or m.group("d2")
    month = next(g for g in (m.group("m"), m.group("m2"), m.group("m3"), m.group("m4"), m.group("m5")) if g)
    return 1 <= int(month) <= 12 and (day is None or 1 <= int(day) <= 31)


def mask_ids(text: Any) -> Any:
    """A text with every ID-like number replaced by ID_MARK; any other value as it is. Every whole date and month in it
    (_DATE_TOKEN) is kept as typed and cut out first, so it is never joined to a number beside it; each piece between
    them is judged by `_mask_piece`. A text with nothing to mask comes back unchanged."""
    if not isinstance(text, str) or sum(map(str.isdigit, unicodedata.normalize("NFKC", text))) < ID_DIGITS:
        return text
    out, last, hit = [], 0, False
    for m in _DATE_TOKEN.finditer(text):
        if not _date_token(m):
            continue
        piece = _mask_piece(text[last:m.start()])
        hit = hit or piece != text[last:m.start()]
        out += [piece, m.group(0)]
        last = m.end()
    piece = _mask_piece(text[last:])
    hit = hit or piece != text[last:]
    return "".join(out) + piece if hit else text


def _mask_piece(text: str) -> str:
    """A piece of text with every ID-like number replaced by ID_MARK. A number is what the books' cleaning
    judges one (app/books/clean.py, reused as is): its digits joined by spaces, dashes and the like, read after NFKC (a
    digit of any script, invisible characters dropped), and kept when its shape says what it is - a date, a month, a span
    of years, a document number, a list or a range of them, an amount. The desk masks it when it has ID_DIGITS digits or
    more and that shape does not keep it: an ID number with or without its leading zero, a phone, an account. A shorter
    number is never masked (the books mask from six digits; here the references a payment carries - an invoice, a credit
    reference, the Hashavshevet key, a cohort code - are four to six). The cost, the books' own: two short references in
    one cell read as one number (1234, 5678 or 123456, 50348) are masked from eight digits, unless each is a document
    number (five digits, not 0-led: 50347, 50348 stays). A text with nothing to mask comes back unchanged; one with
    something to mask comes back normalised as the books read it, the mask in its place."""
    if sum(map(str.isdigit, unicodedata.normalize("NFKC", text))) < ID_DIGITS:
        return text
    norm = normalize(text)
    cleaned = _process(norm, None)[0]  # norm with every number the books would mask as "#", the rest as it is
    out, at, last, hit = [], 0, 0, False
    for start, end in _numbers(norm):
        at += start - last
        books_mask = cleaned[at:at + 1] == "#"  # where this number starts in the books' text: "#", or its first digit
        at += 1 if books_mask else end - start
        if books_mask and sum(1 for ch in norm[start:end] if "0" <= ch <= "9") >= ID_DIGITS:
            out += [norm[last:start], ID_MARK]
            hit = True
        else:
            out.append(norm[last:end])
        last = end
    return "".join(out) + norm[last:] if hit else text


def mask_refs(text: Any) -> Any:
    """An invoice cell (INVOICE_CELLS) as the desk keeps it: a cell A1 reads as a list of numbers (`parse_refs`) as typed,
    each number of REF_DIGITS digits or more in it replaced by ID_MARK; any other cell as `mask_ids` masks every text."""
    if not isinstance(text, str):
        return text
    if parse_refs(text) is None:
        return mask_ids(text)
    return re.sub(r"[0-9]{%d,}" % REF_DIGITS, ID_MARK, text)


def masked(key: str, rows: list) -> list:
    """The rows of board `key`, just read, with `mask_ids` applied to each item's name and its TEXT_CELLS (an invoice
    cell, `mask_refs`), in place: so the reading in memory, its stored copy (desk_readings), the page and the Excel file
    hold the same masked text, in a full reading and in a refresh alike (Nadav 24.11). Only here, never in the reader's
    `_text`: the reconciliation (R0, A1) matches short invoice numbers through the same reader."""
    cells = ("name",) + TEXT_CELLS[key]
    refs = INVOICE_CELLS.get(key)
    for row in rows:
        if isinstance(row, dict):
            for col in cells:
                if isinstance(row.get(col), str):
                    row[col] = mask_refs(row[col]) if col == refs else mask_ids(row[col])
    return rows


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
    """A reading's time, to the microsecond: since D2 it names the reading (the page's `taken_at`, by which the totals and
    the files are asked), and two readings started within one second must not share a name (analiza-metrics' `taken_at`
    had the same precision)."""
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def read_desk(reader: MondayReader, clock: Callable[[], float] = time.monotonic) -> dict:
    """The whole reading: the snapshot (analiza-metrics' shape), its counts, and whether it is complete. A reading that
    failed carries its closed code and no rows: monday's own code, or `crashed` with the class for anything else."""
    started = clock()
    calls0 = reader.calls  # its own requests: a reader may have sent a refresh's first (HeldReading's fallback)
    read_at = _now_iso()
    out: dict = {"read_at": read_at, "full_read_at": read_at, "kind": "full", "complete": False, "error": None,
                 "detail": None, "checks": {}, "calls": 0, "seconds": 0.0}
    try:
        reader.require_columns([(BOARDS[key], CHECKED_COLUMNS[key]) for key in BOARDS])
        (n_sc, sc_asked, sc_read), (n_b2, b2_asked, b2_read) = reader.read_boards(
            [(STUDENT_CHARGE, ROW_COLUMNS["student_charge"]), (B2B_CHARGE, ROW_COLUMNS["b2b_charge"])])
        checks: dict = {}
        sc_rows, checks["student_charge"] = whole_board("student_charge", n_sc, sc_asked, masked("student_charge", sc_read))
        b2_rows, checks["b2b_charge"] = whole_board("b2b_charge", n_b2, b2_asked, masked("b2b_charge", b2_read))

        enr_ids, ld_ids = enrollment_ids(sc_rows), lead_ids(sc_rows)
        deal_ids = sorted(_uniq(d for r in b2_rows for d in r.get(B2["deal"]) or []))
        enr_read, ld_read, deal_read = reader.read_items([(enr_ids, ROW_COLUMNS["enrollments"]),
                                                          (ld_ids, ROW_COLUMNS["leads"]),
                                                          (deal_ids, ROW_COLUMNS["deals"])])
        enr_read = masked("enrollments", enr_read)
        enrollments, checks["enrollments"] = by_id("enrollments", enr_ids, enr_read)
        leads, checks["leads"] = by_id("leads", ld_ids, masked("leads", ld_read))
        deals, checks["deals"] = by_id("deals", deal_ids, masked("deals", deal_read))

        deciding = leads_deciding(sc_rows, enrollments)
        kept_leads = keep_only(leads, deciding)
        coh_ids = cohort_ids(enrollments, kept_leads)
        acct_ids = sorted(_uniq([a for r in b2_rows for a in r.get(B2["account"]) or []]
                                + [a for d in deals for a in d.get(DL["payer"]) or []]))
        coh_read, acct_read = reader.read_items([(coh_ids, ROW_COLUMNS["cohorts"]), (acct_ids, ROW_COLUMNS["accounts"])])
        cohorts, checks["cohorts"] = by_id("cohorts", coh_ids, masked("cohorts", coh_read))
        accounts, checks["accounts"] = by_id("accounts", acct_ids, masked("accounts", acct_read))

        # 5. (D4) the enrollments of the cohorts with a price that step 3 did not read, by id: one list of Enrollments'
        # rows, counted as one board by what it asked
        roster = [e for e in roster_ids(cohorts) if e not in set(enr_ids)]
        if roster:
            (roster_read,) = reader.read_items([(roster, ROW_COLUMNS["enrollments"])])
            enr_ids = sorted(_uniq(enr_ids + roster))
            enrollments, checks["enrollments"] = by_id("enrollments", enr_ids,
                                                       enr_read + masked("enrollments", roster_read))
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
    out["calls"], out["seconds"] = reader.calls - calls0, round(clock() - started, 1)
    logger.info("desk reading in %.1fs, %d calls: complete=%s error=%s", out["seconds"], out["calls"], out["complete"],
                out["error"])
    return out


# ---------------------------------------------------------------- a refresh that reads only what changed (D2, 24.11)
# analiza-metrics round 3, slice 2 (app/finance.py `read_delta`, `_delta`, `_read_new` at da671b2; live there since 11.11),
# on this service's reader and its rules: no request after the deadline, NEVER_READ refused, and a row answered from another
# board a stranger, dropped and counted. A refresh asks the six boards with a window which items were updated since the
# held reading started (less DELTA_MARGIN), reads only those again by id, and lays them over the held reading. Leads has no
# window (analiza-metrics, measured live 11.11: 521 of its 4,567 items updated in two days, of which the desk needed one):
# the deciding leads are read again by id in the same request, every time, and a lead that newly decides, or that a payment
# newly links to, is read by id after it. A lead that decides nothing is kept by its name and Owner only: a change to one
# shows at the next full reading, within FULL_AFTER, as a deleted enrollment does.
def window_days(gap: timedelta) -> int:
    """How many days to ask for. Whether monday counts n calendar days (today among them) or n times 24 hours is not
    verified, so n is the smallest whose calendar reading still covers the gap - n calendar days hold at least n - 1 whole
    days, and a day of the clock change has 23 hours - and never less than 2, the shape verified live. A held reading is at
    most FULL_AFTER old, so this is 2, or 3 after a quiet day (analiza-metrics, the same rule)."""
    hours = max(gap.total_seconds(), 0.0) / 3600
    return max(2, math.ceil(hours / 23) + 1)


def instant(stamp: Any) -> Optional[datetime]:
    """An ISO time from monday or from a reading as an aware instant (UTC when it carries no zone); None when absent or
    unreadable."""
    if isinstance(stamp, datetime):
        return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)
    if not stamp or not isinstance(stamp, str):
        return None
    try:
        t = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


class DeltaMismatch(MondayError):
    """A charge board a refresh cannot reconcile with its ids: their pages disagree with their own `items_count` (the board
    changed while it was paged), or an id paged has no active row of the board after the reads (gone, archived or moved
    since its page). Only a full reading knows what the board holds, so HeldReading reads whole."""

    def __init__(self, key: str, count: Any, held: int):
        super().__init__("count_mismatch", "%s: items_count %s, rows %d" % (key, count, held))
        self.key = key


class UnsafeBody(Exception):
    """A reading whose text names a column of NEVER_READ: never written (app/desk/store.py `refuse_unsafe`). The name of
    what was found is the message."""


def own_rows(key: str, rows: list, asked: Iterable[Any], strangers: Counter) -> list:
    """The rows of this board among the ids asked, once each. A row answered from another board, or for an id not asked, is
    a stranger: dropped and counted under the board's key (by id monday answers an item of any board, review 22.11)."""
    board_id, want, seen, kept = BOARDS[key], {str(x) for x in asked}, set(), []
    for r in rows:
        if r.get("board_id") != board_id or r["id"] not in want:
            strangers[key] += 1
            continue
        if r["id"] not in seen:
            seen.add(r["id"])
            kept.append(r)
    return kept


def merge_rows(held: list, read: list, asked: Iterable[Any], charge: bool) -> list:
    """The rows a refresh read by id replace their held copies in place, and a new one joins at the end. An item monday no
    longer returns (deleted, gone, or a stranger now) leaves; so does an archived one on a charge board (a full reading reads
    a board's active items only) - but an archived enrollment, lead, cohort, deal or account stays, as a full reading keeps
    it (analiza-metrics `merge_rows`; here a row carries its own `state`)."""
    by_id = {str(r["id"]): r for r in held}
    order = [str(r["id"]) for r in held]
    got = {str(r["id"]): r for r in read}
    for i in (str(x) for x in asked):
        row = got.get(i)
        if row is None or (charge and row.get("state") == "archived"):
            by_id.pop(i, None)
            continue
        if i not in by_id:
            order.append(i)
        by_id[i] = row
    return [by_id[i] for i in dict.fromkeys(order) if i in by_id]


def names_read(rows: list, asked: Iterable[Any]) -> dict:
    """The names a by-id read of leads gives, per id asked: None for one monday did not return (deleted, gone, a
    stranger) - the leads `merge_rows` drops, and a full reading does not return."""
    got = {str(r["id"]): r.get("name") or "" for r in rows}
    return {str(i): got.get(str(i)) for i in asked}


def reps_read(rows: list, asked: Iterable[Any]) -> dict:
    """The Owners a by-id read of leads gives, per id asked, as `names_read`: None for one not returned."""
    got = {str(r["id"]): lead_rep(r) for r in rows}
    return {str(i): got.get(str(i)) for i in asked}


def usable(snapshot: Any) -> bool:
    """A held or stored reading a refresh can lay itself over: both times, both charge boards and the five lists, every
    row with its id, what each list was asked for, and the linked leads' names and Owners."""
    try:
        return (instant(snapshot["read_at"]) is not None and instant(snapshot["full_read_at"]) is not None
                and all(isinstance(r, dict) and "id" in r for k in CHARGE_KEYS for r in snapshot[k]["rows"])
                and all(isinstance(r, dict) and "id" in r for k in LINKED_KEYS for r in snapshot[k])
                and all(isinstance(snapshot["asked"][k], list) for k in LINKED_KEYS)
                and isinstance(snapshot["lead_names"], dict)
                and all(n is None or isinstance(n, str) for n in snapshot["lead_names"].values())
                and isinstance(snapshot["lead_reps"], dict)
                and all(n is None or isinstance(n, str) for n in snapshot["lead_reps"].values()))
    except (KeyError, TypeError, AttributeError):
        return False


def body_of(snapshot: dict) -> dict:
    """What a reading holds of monday (BODY_KEYS): what is kept, and what two readings are compared by."""
    return {k: snapshot.get(k) for k in BODY_KEYS}


def _read_new(reader: MondayReader, lists: dict, ids_read: dict, names: dict, reps: dict, strangers: Counter,
              new: dict) -> None:
    """What the held reading did not hold, by id, packed into as few requests as their ids allow: merged into its list and
    counted as read; a lead's name - None for one monday did not return - goes into `names`, its Owner into `reps`."""
    keys = [k for k in new if new[k]]
    if not keys:
        return
    for k, rows in zip(keys, reader.read_items([(new[k], ROW_COLUMNS[k]) for k in keys], pack=True)):
        mine = own_rows(k, masked(k, rows), new[k], strangers)
        lists[k] = merge_rows(lists[k], mine, new[k], charge=False)
        ids_read[k] = ids_read[k] + new[k]
        if k == "leads":
            names.update(names_read(mine, new[k]))
            reps.update(reps_read(mine, new[k]))


def _delta_checks(out: dict, paged: dict, strangers: Counter, linked: list, names: dict) -> dict:
    """The counts of a refresh, in the shapes a full reading gives (`check_board`, `by_id`), over what it holds now: a
    charge board whole when its items_count, the ids its pages gave, its rows and its distinct ids agree and no row of it
    was answered from another board; a linked board by what it was asked for. The leads as a full reading counts them,
    every linked lead by its name; which of them are archived a refresh does not know (the leads that decide nothing are
    not read again): None, never a guess."""
    checks: dict = {}
    for k in CHARGE_KEYS:
        rows = out[k]["rows"]
        n, ids = paged[k]
        c = check_board(Board(k, BOARDS[k], tuple(ROW_COLUMNS[k])), n, ids, rows)
        if strangers[k]:
            c.update(strangers=c["strangers"] + strangers[k], ok=False)
        checks[k] = c
    for k in ("enrollments", "cohorts", "deals", "accounts"):
        _kept, c = by_id(k, out["asked"][k], out[k])
        c["strangers"] += strangers[k]
        checks[k] = c
    found = sum(1 for i in linked if names.get(i) is not None)
    short = sum(1 for r in out["leads"] if not set(ROW_COLUMNS["leads"]) <= set(r))
    checks["leads"] = {"board_id": LEADS_BOARD, "asked": len(linked), "rows": found, "missing": len(linked) - found,
                       "strangers": strangers["leads"], "archived": None, "short_rows": short, "ok": short == 0}
    return checks


def read_delta(reader: MondayReader, base: dict, clock: Callable[[], float] = time.monotonic) -> dict:
    """The next reading from a held one: what changed on the six boards with a window since it started, and the deciding
    leads again, laid over it; the charge boards as their ids, paged, say they are now (a charge that left is dropped, one
    that came is read by id) - the same snapshot as `read_desk`, `kind` "delta", the held reading's `full_read_at`, its
    counts, and `id_pages`, the requests the ids took. Raises DeltaMismatch when a charge board cannot be reconciled with
    its ids, and MondayError for anything monday or the shape of its answer did not allow: HeldReading then reads whole, or
    says why not."""
    started = clock()
    calls0 = reader.calls
    now = datetime.now(timezone.utc)
    held_at = instant(base.get("read_at")) if isinstance(base, dict) else None
    if held_at is None or not usable(base):
        raise MondayError("delta_shape", "the held reading cannot be laid under a refresh")
    cut = held_at - DELTA_MARGIN
    days = window_days(now - cut)
    strangers: Counter = Counter()
    asked = {k: [str(x) for x in base["asked"][k]] for k in LINKED_KEYS}
    asked_set = {k: set(v) for k, v in asked.items()}
    names, reps = dict(base["lead_names"]), dict(base["lead_reps"])
    held_deciding = asked["leads"]
    got = reader.changed_items([(BOARDS[k], CHECKED_COLUMNS[k]) for k in DELTA_KEYS],
                               [(BOARDS[k], CHECKED_COLUMNS[k]) for k in CHECK_ONLY_KEYS], days,
                               (held_deciding, ROW_COLUMNS["leads"]))
    counts = dict(zip(DELTA_KEYS, got["counts"]))
    windows = dict(zip(DELTA_KEYS, got["windows"]))
    for k in CHARGE_KEYS:
        if not isinstance(counts[k], int) or isinstance(counts[k], bool):
            raise MondayError("delta_shape", f"{k}: no items_count")
    # what each charge board holds now: its ids, paged as a full reading pages them (ids only, the two boards side by side).
    # No window shows a charge that left its board (archived, deleted, moved away) or came to it (restored, moved in), and a
    # count stays equal when one leaves as another comes (review of a32395f): the ids show both. Their cost is the trace's
    # `id_pages` - two requests, and one more per PAGE_LIMIT ids beyond the first page of a board
    before_ids = reader.calls
    paged = dict(zip(CHARGE_KEYS, reader.board_ids([BOARDS[k] for k in CHARGE_KEYS])))
    id_pages = reader.calls - before_ids
    for k, (n, ids) in paged.items():
        if n is None or n != len(ids) or len(set(ids)) != len(ids):
            raise DeltaMismatch(k, n, len(ids))  # pages that disagree with their own count: only a full reading knows
    on_board = {k: set(ids) for k, (_n, ids) in paged.items()}
    # the exact cut: what was updated after the held reading started (less the margin); an item whose time is missing or
    # unreadable is read, never skipped
    after = {k: _uniq(x["id"] for x in items if instant(x["updated_at"]) is None or instant(x["updated_at"]) > cut)
             for k, items in windows.items()}
    leads_got = own_rows("leads", masked("leads", got["rows"]), held_deciding, strangers)

    # read by id what changed: a changed charge still on its board, a charge that came to its board unseen, and the changed
    # linked items the held reading asked for (the deciding leads were read above, whatever their time). A charge that left
    # its board is dropped, never read. D4: and the held cohorts with a price, every time, as the deciding leads are -
    # their link to their enrollments says who studies in them, and whether a cohort's `updated_at` moves when an enrollment
    # is linked to it is not verified
    priced = [str(c["id"]) for c in base["cohorts"] if roster_cohort(c) and str(c["id"]) in asked_set["cohorts"]]
    ids_read: dict = {}
    for k in DELTA_KEYS:
        if k in CHARGE_KEYS:
            changed = [i for i in after[k] if i in on_board[k]]
            known = {str(r["id"]) for r in base[k]["rows"]} | set(changed)
            came = [i for i in paged[k][1] if i not in known]
            ids_read[k] = changed + came
        else:
            ids_read[k] = _uniq([i for i in after[k] if i in asked_set[k]] + (priced if k == "cohorts" else []))
    read = reader.read_items([(ids_read[k], ROW_COLUMNS[k]) for k in DELTA_KEYS], pack=True)
    got_rows = {k: own_rows(k, masked(k, rows), ids_read[k], strangers) for k, rows in zip(DELTA_KEYS, read)}
    ids_read["leads"], got_rows["leads"] = list(held_deciding), leads_got
    names.update(names_read(leads_got, held_deciding))
    reps.update(reps_read(leads_got, held_deciding))
    rows = {k: [r for r in merge_rows(base[k]["rows"], got_rows[k], ids_read[k], charge=True)
                if str(r["id"]) in on_board[k]] for k in CHARGE_KEYS}
    for k in CHARGE_KEYS:
        if len(rows[k]) != len(on_board[k]):
            # an id paged that no read gave as this board's active row (gone, archived or moved since its page, or answered
            # from another board): only a full reading knows what the board holds now
            raise DeltaMismatch(k, paged[k][0], len(rows[k]))
    lists = {k: merge_rows(base[k], got_rows[k], ids_read[k], charge=False) for k in LINKED_KEYS}

    # what the charges link to now that the held reading did not ask for: by id, by the same definitions as a full reading
    # (an id asked before and missing then stays missing until the next full reading)
    sc_rows, b2_rows = rows["student_charge"], rows["b2b_charge"]
    need = {"enrollments": enrollment_ids(sc_rows),
            "deals": sorted(_uniq(d for r in b2_rows for d in r.get(B2["deal"]) or []))}
    linked = lead_ids(sc_rows)
    # and the leads: those that may decide now - of the payments whose enrollments read so far name no cohort (an
    # enrollment read below can only name one more, so they hold every lead that will decide) - that the held reading did
    # not keep as deciding, and every lead a payment newly links to, for its name
    maybe = leads_deciding(sc_rows, keep_only(lists["enrollments"], need["enrollments"]))
    held = set(held_deciding)
    _read_new(reader, lists, ids_read, names, reps, strangers, {
        "enrollments": [i for i in need["enrollments"] if i not in asked_set["enrollments"]],
        "leads": _uniq([i for i in linked if i not in names] + [i for i in maybe if i not in held]),
        "deals": [i for i in need["deals"] if i not in asked_set["deals"]]})
    enrollments, deals = keep_only(lists["enrollments"], need["enrollments"]), keep_only(lists["deals"], need["deals"])
    need["leads"] = leads_deciding(sc_rows, enrollments)
    need["cohorts"] = cohort_ids(enrollments, keep_only(lists["leads"], need["leads"]))
    need["accounts"] = sorted(_uniq([a for r in b2_rows for a in r.get(B2["account"]) or []]
                                    + [a for d in deals for a in d.get(DL["payer"]) or []]))
    _read_new(reader, lists, ids_read, names, reps, strangers,
              {k: [i for i in need[k] if i not in asked_set[k]] for k in ("cohorts", "accounts")})
    # D4: the enrollments of the cohorts with a price, as a full reading reads them (step 5): through each cohort's link to
    # them, by id those that neither the held reading held nor this refresh read
    roster = [e for e in roster_ids(keep_only(lists["cohorts"], need["cohorts"])) if e not in set(need["enrollments"])]
    seen = asked_set["enrollments"] | set(ids_read["enrollments"])
    _read_new(reader, lists, ids_read, names, reps, strangers, {"enrollments": [e for e in roster if e not in seen]})
    need["enrollments"] = sorted(_uniq(need["enrollments"] + roster))
    out = {"student_charge": {"items_count": paged["student_charge"][0], "rows": sc_rows},
           "b2b_charge": {"items_count": paged["b2b_charge"][0], "rows": b2_rows},
           **{k: keep_only(lists[k], need[k]) for k in LINKED_KEYS},
           # every linked lead's name: held, read again with the deciding leads, or read as newly linked; and its Owner
           "lead_names": {i: names[i] for i in linked},
           "lead_reps": {i: reps.get(i) for i in linked},
           "asked": {k: list(need[k]) for k in LINKED_KEYS}}
    checks = _delta_checks(out, paged, strangers, linked, names)
    out.update(read_at=now.isoformat(timespec="microseconds"), full_read_at=base["full_read_at"], kind="delta",
               complete=all(c["ok"] for c in checks.values()), error=None, detail=None, checks=checks,
               calls=reader.calls - calls0, seconds=round(clock() - started, 1), window_days=days, id_pages=id_pages)
    logger.info("desk refresh in %.1fs over %d days, %d calls (%d paging the charges' ids): complete=%s, read by id %s",
                out["seconds"], days, out["calls"], id_pages, out["complete"], {k: len(v) for k, v in ids_read.items()})
    return out


def summary(reading: dict) -> dict:
    """A reading without its rows: when, which kind, whether complete, why not, its cost and its counts. What a member's
    answer and the operator's views may carry; /health carries less (`health_part`)."""
    return {"read_at": reading.get("read_at"), "kind": reading.get("kind"), "complete": bool(reading.get("complete")),
            "error": reading.get("error"), "detail": reading.get("detail"), "seconds": reading.get("seconds"),
            "calls": reading.get("calls"), "checks": reading.get("checks") or {}}


def health_part(last: Optional[dict]) -> Optional[dict]:
    """For the public /health: whether the last reading was complete and why not, which kind it was (D2: "full" or
    "delta"), its time and cost. No count of rows (Nadav 19.11: the deployment is public)."""
    if last is None:
        return None
    return {"read_at": last.get("read_at"), "kind": last.get("kind"), "complete": last.get("complete"),
            "error": last.get("error"), "seconds": last.get("seconds"), "calls": last.get("calls"),
            "boards_whole": None if last.get("error") else
            sorted(k for k, c in (last.get("checks") or {}).items() if c.get("ok"))}


def _iso_s(t: datetime) -> str:
    return t.astimezone(timezone.utc).isoformat(timespec="seconds")


def _why(exc: BaseException) -> str:
    """A store's failure as a trace code's tail: its SQLSTATE or its class (app/desk/store.py `DeskStoreError`), never its
    text, which can carry the database's host and user."""
    detail = getattr(exc, "detail", None)
    if isinstance(detail, str) and re.fullmatch(r"[A-Za-z0-9_]{1,40}", detail):
        return detail
    return type(exc).__name__


def _failed(kind: Optional[str], code: str, detail: Any, calls: int = 0, seconds: float = 0.0) -> dict:
    """An attempt that read nothing to show: its closed code and no rows, as a failed `read_desk` answers."""
    return {"read_at": _now_iso(), "kind": kind, "complete": False, "error": code, "detail": str(detail)[:200],
            "checks": {}, "calls": calls, "seconds": seconds}


class HeldReading:
    """The desk's reading, one per instance, shared by every opener, and since D2 its copy in Postgres (`store`,
    app/desk/store.py; None without a database) and its refresh that reads only what changed.

    D1's rules, kept: `get()` answers with the reading held when it is younger than HELD_TTL_S; otherwise it reads, one
    reading at a time: a request that finds one running waits for it and is answered with it - at most READ_DEADLINE_S and
    WAIT_MARGIN_S, after which it is answered with what is held (or stored) marked refreshing, or with no reading, never a
    hung worker (D2's review: the save after a reading is not bounded by the reading's deadline). A
    reading that failed is kept as the last attempt, not as the reading: the one before stays on screen with its age, and
    the next try comes FAIL_GAP_S later, not on every open. A reading that read every board but came short replaces the one
    before: it is the truth of now, shown as not complete. Every attempt's reader gets READ_DEADLINE_S, the refresh
    DELTA_DEADLINE_S of it.

    `get(force=True)` is the refresh press (D2, POST /api/desk/refresh): neither the age of the held reading nor the gap
    after a failure holds it back, and it is satisfied only by a reading that STARTED after it - one that was already
    running when it came may not hold what was just changed in monday, so it waits for that one and reads again
    (analiza-metrics SharedReading, part 5). How often it may come is the route's to bound.

    D2's (analiza-metrics SharedReading parts 6 and 7, and FinanceMirror, at da671b2):
    - `answer(stale_ok=True)` is a plain open (GET /api/desk without `fresh`): past its span the reading held is answered at
      once, whatever its age, marked `refreshing`, and nothing is read; an instance that holds none answers with the stored
      reading the same way (`seeded`: never fresh, however young, since this instance did not read it). The page asks for
      the next in a request of its own (`?fresh=1`, which is `get()`). A seed never replaces a reading that landed meanwhile.
    - a reading is a refresh over the reading held, or over the stored one when none is held (`read_delta`); a full reading
      when there is neither, when the last full one is older than FULL_AFTER, when a charge board cannot be reconciled with
      its ids (`full_count_<board>`), or when the refresh failed in a way a full reading may not (once per FALLBACK_GAP at
      most); an error a full reading would meet the same way (NO_FALLBACK) is the attempt's own.
    - a reading without an error is kept in the store unless it holds what the store already holds; a reading older than
      the stored one never replaces it (another instance's). The store is best effort, with traces: a store that fails is
      said in /health and the desk reads monday.
    - `health()`: the trace, in a closed vocabulary - full_first, full_base_unusable, full_36h, full_count_<board>,
      full_delta_error_<code>, delta, delta_failed_<code>, full_deferred, store_not_configured, store_loaded,
      store_empty, store_other_schema, store_unusable, store_load_failed_<SQLSTATE or class>, store_saved, store_unchanged,
      store_newer_held, store_refused, store_save_failed_<SQLSTATE or class>, seeded, wait_timeout. Never a row or a count
      of rows."""

    def __init__(self, make_reader: Callable[[], MondayReader], clock: Callable[[], float] = time.monotonic,
                 ttl_s: float = HELD_TTL_S, deadline_s: float = READ_DEADLINE_S, fail_gap_s: float = FAIL_GAP_S,
                 store: Any = None, now: Optional[Callable[[], datetime]] = None,
                 delta_deadline_s: float = DELTA_DEADLINE_S):
        self.make_reader, self.clock, self.store = make_reader, clock, store
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.ttl_s, self.deadline_s, self.fail_gap_s = ttl_s, deadline_s, fail_gap_s
        self.delta_deadline_s = delta_deadline_s
        self.reading: Optional[dict] = None
        self.reading_at: Optional[float] = None
        self.seeded = False  # the reading held came from the store, not from this instance: never fresh
        self.last: Optional[dict] = None
        self.last_at: Optional[float] = None
        self.started_at: Optional[float] = None  # when the last attempt started: what a forced get() is measured by
        self._lock = threading.Lock()  # one reading at a time
        self._state = threading.Lock()  # the state, written in one step by a reading and by a seed
        self._seed_lock = threading.Lock()  # one seed at a time
        self._seed_tried_at: Optional[float] = None
        # the read_at of the reading whose content this instance knows the store holds: a refresh that changed nothing is
        # not written, so the stored row's own read_at may be older
        self._stored_at: Optional[str] = None
        self._fallback_at: Optional[datetime] = None  # when the last full reading a failed refresh fell back to began
        self._recent: deque = deque(maxlen=RECENT_CODES)
        self.trace: dict = {"last_codes": [], "last_full": None, "last_delta": None, "last_delta_error": None,
                            "stored_read_at": None}

    def _young(self, at: Optional[float], limit: float) -> bool:
        return at is not None and 0 <= self.clock() - at < limit

    def _fresh(self) -> bool:
        return self.reading is not None and not self.seeded and self._young(self.reading_at, self.ttl_s)

    def _failed_just_now(self) -> bool:
        return self.last is not None and bool(self.last.get("error")) and self._young(self.last_at, self.fail_gap_s)

    def get(self, force: bool = False) -> tuple:
        """(the reading to show or None, the last attempt's summary or None): a reading past its span is read now."""
        reading, last, _refreshing = self.answer(force=force)
        return reading, last

    def answer(self, force: bool = False, stale_ok: bool = False) -> tuple:
        """(the reading to show or None, the last attempt's summary or None, refreshing). With `stale_ok` (a plain open),
        a reading past its span - held, or stored when none is - is answered at once, `refreshing` true."""
        asked = self.clock()
        at_once = stale_ok and not force
        if at_once and self.reading is None and self.store is not None:
            self._seed()
        with self._state:
            if not force and (self._fresh() or self._failed_just_now()):
                return self.reading, self.last, False
            if at_once and self.reading is not None:
                return self.reading, self.last, True
        if not self._lock.acquire(timeout=max(0.0, self.deadline_s + WAIT_MARGIN_S)):
            return self._waited_out()
        try:
            if force:
                # strictly after: on a clock too coarse to tell them apart, a press reads once more rather than take a
                # reading that may have started before it
                if self.started_at is not None and self.started_at > asked:
                    return self.reading, self.last, False
            elif self._fresh() or self._failed_just_now():
                return self.reading, self.last, False
            self._read()
            return self.reading, self.last, False
        finally:
            self._lock.release()

    def _waited_out(self) -> tuple:
        """A request whose wait for the reading in progress ran out: what is held - or, on an instance that holds none, the
        stored reading - marked refreshing, and nothing read; with neither, no reading (the route says it is unavailable).
        Said in the trace (`wait_timeout`)."""
        logger.warning("desk request waited out the reading in progress")
        self._note(["wait_timeout"])
        if self.reading is None and self.store is not None:
            self._seed()
        with self._state:
            return self.reading, self.last, self.reading is not None

    def age_s(self) -> Optional[int]:
        return None if self.reading_at is None else max(0, int(self.clock() - self.reading_at))

    # ---- the readings ----
    def _read(self) -> None:
        """One attempt, under the reading lock. A failed attempt is kept as the last attempt only; a reading without an error
        replaces the one held."""
        self.started_at = self.clock()
        codes: list = []
        reader = None
        try:
            reader = self.make_reader()
            reading = self._refresh(reader, codes)
        except Exception as exc:  # noqa: BLE001 - a reader that could not even be made is a failed reading
            reading = _failed(None, "crashed", type(exc).__name__)
        finally:
            if reader is not None:
                try:
                    reader.close()
                except Exception:  # noqa: BLE001 - closing a client never fails a reading
                    pass
        ended = self.clock()
        with self._state:
            self.last, self.last_at = summary(reading), ended
            if not reading.get("error"):
                self.reading, self.reading_at, self.seeded = reading, ended, False
        self._note(codes, last=True)

    def _full_reason(self, base: Optional[dict]) -> Optional[str]:
        if base is None:
            return "full_first"
        if not usable(base):
            return "full_base_unusable"
        if self.now() - instant(base["full_read_at"]) > FULL_AFTER:
            return "full_36h"
        return None

    def _refresh(self, reader: MondayReader, codes: list) -> dict:
        """The next reading: a refresh over the reading held (or the stored one), or a full reading and why; kept in the
        store when it holds something new. Returns a failed attempt rather than raise."""
        start = reader.clock()
        with self._state:
            base = self.reading
        if base is None:
            base = self._load(codes)
        why = self._full_reason(base)
        got = None
        if why is None:
            reader.deadline = start + min(self.delta_deadline_s, self.deadline_s)
            failed: Optional[Exception] = None
            try:
                got = read_delta(reader, base)
            except DeltaMismatch as exc:
                why, failed = "full_count_" + exc.key, exc
            except MondayError as exc:
                failed = exc
                if exc.code not in NO_FALLBACK:
                    why = "full_delta_error_" + exc.code
            except Exception as exc:  # noqa: BLE001 - a shape the refresh did not foresee: said, then read whole
                why, failed = "full_delta_error_internal", exc
            if failed is not None:
                code = failed.code if isinstance(failed, MondayError) else "internal"
                detail = failed.detail if isinstance(failed, MondayError) else type(failed).__name__
                if code == "deadline" and getattr(reader, "limited", 0):
                    # monday rate-limited this refresh and its wait did not fit the refresh's time: still the limit's, so
                    # never a full reading - some thirty requests into that very limit (hostile review 11.11)
                    why, code = None, "rate_limited"
                logger.warning("desk refresh not used (%s): %s", code, str(detail)[:200])
                with self._state:
                    self.trace["last_delta_error"] = {"at": _iso_s(self.now()), "code": code}
                lost = _failed("delta", code, detail, reader.calls, round(reader.clock() - start, 1))
                if why is None:  # NO_FALLBACK: a full reading would meet it the same way
                    codes.append("delta_failed_" + code)
                    return lost
                now = self.now()
                if self._fallback_at is not None and now - self._fallback_at < FALLBACK_GAP:
                    # it fell back a moment ago: not whole again within the gap, the refresh's own error instead
                    codes += ["delta_failed_" + code, "full_deferred"]
                    return lost
                self._fallback_at = now
        if got is None:
            codes.append(why)
            reader.deadline = start + self.deadline_s
            got = read_desk(reader)
            if got.get("error"):
                return got
            with self._state:
                self.trace["last_full"] = {"at": got["read_at"], "seconds": got.get("seconds"), "calls": got.get("calls")}
        else:
            codes.append("delta")
            with self._state:
                self.trace["last_delta"] = {"at": got["read_at"], "seconds": got.get("seconds"), "calls": got.get("calls"),
                                            "days": got.get("window_days"), "id_pages": got.get("id_pages")}
        self._keep(got, base, codes)
        return got

    # ---- the stored copy ----
    def _load(self, codes: list) -> Optional[dict]:
        """The stored reading when it can be laid under a refresh, else None and why (in `codes`). Reads the database only,
        never monday."""
        if self.store is None:
            codes.append("store_not_configured")
            return None
        try:
            loaded = self.store.load()
        except Exception as exc:  # noqa: BLE001 - a database that cannot be read is said, and the desk reads monday
            logger.warning("desk stored reading not read (%s)", _why(exc))
            codes.append("store_load_failed_" + _why(exc))
            return None
        if loaded is None:
            codes.append("store_empty")
            return None
        if loaded.pop("body_schema", None) != BODY_SCHEMA:
            codes.append("store_other_schema")
            return None
        if not usable(loaded):
            codes.append("store_unusable")
            return None
        codes.append("store_loaded")
        with self._state:
            self._stored_at = loaded["read_at"]
            self.trace["stored_read_at"] = loaded["read_at"]
        return loaded

    def _seed(self) -> None:
        """Part 7 of analiza-metrics' SharedReading: the stored reading for an instance that holds none, as old as it is and
        never fresh. A seed that finds nothing changes nothing - the caller then waits for a reading - and is not tried
        again for FAIL_GAP_S: a database that is down costs one wait, not one per open."""
        with self._seed_lock:
            if self.reading is not None or self._young(self._seed_tried_at, self.fail_gap_s):
                return
            self._seed_tried_at = self.clock()
            codes: list = []
            loaded = self._load(codes)
            if loaded is not None:
                age = max(0.0, (self.now() - instant(loaded["read_at"])).total_seconds())
                with self._state:
                    if self.reading is None:
                        self.reading, self.reading_at, self.seeded = loaded, self.clock() - age, True
                        codes.append("seeded")
            self._note(codes)

    def _keep(self, got: dict, base: Optional[dict], codes: list) -> None:
        """The reading into the store, unless the store already holds what it holds (a quiet refresh writes nothing)."""
        if self.store is None:
            codes.append("store_not_configured")
            return
        with self._state:
            stored_at = self._stored_at
        if not (got.get("kind") == "full" or base is None or stored_at != base.get("read_at")
                or body_of(got) != body_of(base)):
            codes.append("store_unchanged")
            with self._state:
                self._stored_at = got["read_at"]  # the store holds the held reading's content, and so this one's
            return
        try:
            wrote = self.store.save(got)
        except UnsafeBody as exc:
            # cannot happen while the reader refuses every NEVER_READ column: loud if it ever does
            logger.error("desk reading NOT stored: its text names %s", exc)
            codes.append("store_refused")
            return
        except Exception as exc:  # noqa: BLE001 - the desk works without its store; the trace says it did not keep it
            logger.warning("desk reading not stored (%s)", _why(exc))
            codes.append("store_save_failed_" + _why(exc))
            return
        if not wrote:
            codes.append("store_newer_held")  # another instance kept a newer reading: it stays
            return
        codes.append("store_saved")
        with self._state:
            self._stored_at = got["read_at"]
            self.trace["stored_read_at"] = got["read_at"]

    # ---- the trace ----
    def _note(self, codes: list, last: bool = False) -> None:
        at = _iso_s(self.now())
        codes = list(dict.fromkeys(codes))
        with self._state:
            self._recent.extend({"at": at, "code": c} for c in codes)
            if last:
                self.trace["last_codes"] = codes

    def health(self) -> dict:
        """For /health: how this instance read and kept its reading - the last attempt's codes and the recent ones, the last
        full reading and the last refresh (when, how long, how many requests to monday, and of a refresh's how many paged the
        charge boards' ids), the last refresh that failed (its code), and the times of the reading held and of the stored
        one it knows. Never a row, a name, an amount or a count of rows."""
        with self._state:
            r = self.reading
            return {"last_codes": list(self.trace["last_codes"]), "recent_codes": list(self._recent),
                    "last_full": dict(self.trace["last_full"]) if self.trace["last_full"] else None,
                    "last_delta": dict(self.trace["last_delta"]) if self.trace["last_delta"] else None,
                    "last_delta_error": dict(self.trace["last_delta_error"]) if self.trace["last_delta_error"] else None,
                    "held": None if r is None else {"read_at": r.get("read_at"), "full_read_at": r.get("full_read_at"),
                                                    "kind": r.get("kind"), "seeded": self.seeded},
                    "stored_read_at": self.trace["stored_read_at"],
                    "full_after_hours": FULL_AFTER.total_seconds() / 3600}
