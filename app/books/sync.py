"""One run of the books copy (spec §4.2, backlog 0301 B1; reworked after the three reviews of 22.11).

A run, inside its time box, reads in this order:
1. The ids a far probe jumped over that wait in books_retry (never read yet).
2. Optionally every held document again (`reread_held`: books-check's second run).
3. The frontier: upward from the high-water mark (the largest stockID held) until MISSES_IN_A_ROW ids in a row
   hold no document, then far probes (+64 ... +1024) so a jump in ids is found. A company with nothing held and no
   run that ever reached the end starts at its known first id and tolerates FIRST_RUN_MISSES misses; after one
   complete run, an empty company (Demo) gets the plain frontier.
4. The ids whose read failed in an earlier run, oldest failure first, after the frontier: the frontier steps past
   them, so an id that fails on every read never stands in front of new documents (third review 22.11, finding 1).

No id is read twice in a run (finding 5): a second visit takes the first read's outcome.

A document found above the highest id held, with ids between that this run has not read - a far probe's hit, or
a retried id - puts those ids in books_retry in the same transaction that saves it (findings 3 and 4), and they
are read next, as time allows.

A read that fails - anything but "Stock not found", an answer that is not a document or names another id, an
answer the parser cannot read, a document the database refuses - is a failed read: counted by kind, put in
books_retry, never a miss and never a change to what the copy believes. FAILURES_IN_A_ROW failed reads in a row
of ids not known to fail end the run (ok false: WizCloud is failing). An id that failed before and fails again is
not that signal: it is counted as `stuck`, and /api/books/status names it. The time box is not a failure: a read
it cuts (DeadlineReached) stops the run for time, and what was not read is read next run. A run is `complete` only
when the frontier reached the end, every far probe answered, nothing failed and nothing waits in books_retry.

What `complete` cannot promise: a document after a gap of more than MISSES_IN_A_ROW empty ids, short of the
first far probe, is found only once a probe lands on its run of ids - or by the weekly sweep (B2), which reads
every id. books-check prints the largest gap in the real books: 7 empty ids in Analiza (22.11), so the
window of 50 has a wide margin.

Every document is saved as it is read, every read is bounded by what is left of the box, and the run's row
is finished whatever happens.
"""

from __future__ import annotations

import time
from typing import Callable, Iterable, Optional

from ..wizcloud import MIN_CALL_S, NOT_FOUND, DeadlineReached, WizError
from .parse import ParseError, document_rows
from .store import BooksStoreError

MISSES_IN_A_ROW = 50
FIRST_RUN_MISSES = 200
FAR_PROBES = (64, 128, 256, 512, 1024)
FAILURES_IN_A_ROW = 3
MARGIN_S = 1.0
# Where a company's ids begin, so the first run does not read a long empty stretch (spec §4.2.1).
# Analiza: the first document is at id 3 (the full copy, 22.11). Demo: no document at any id probed
# (1-100 on 18.11, up to 1,000,000 on 22.11).
FIRST_ID = {"wizdb2245n5": 1, "wizdb2245n4": 1}
OUT_OF_TIME = "out_of_time"
JUMPED = "jumped"  # the kind of a books_retry row for an id never read: a far probe or a retried id jumped over it


def _merge(into: dict, add: dict) -> None:
    for key, value in add.items():
        if isinstance(value, dict):
            _merge(into.setdefault(key, {}), value)
        else:
            into[key] = into.get(key, 0) + value


class _Run:
    """The state of one run: what it read, what waits, and the highest id held."""

    def __init__(self, client, store, company: str, trigger: str, box_s: float, clock: Callable[[], float]):
        self.client, self.store, self.company = client, store, company
        self.box_s, self.clock = box_s, clock
        self.started = clock()
        self.run_id = store.start_run(company, trigger, None)
        self.summary = {
            "run_id": self.run_id, "company": company, "trigger": trigger, "from_id": None, "to_id": None,
            "probed": 0, "found": 0, "added": 0, "changed": 0, "unchanged": 0, "absent": 0, "failed": {},
            "masked": 0, "far_hits": [], "retried": 0, "reread": 0, "held_absent": 0, "queued": 0, "stuck": 0,
            "complete": False, "ok": True, "note": None, "masked_fields": {}, "clean": {},
        }
        self.notes: list = []
        self.seen: dict = {}  # id -> the outcome of its read in this run: no id is read twice
        self.waiting: set = set()  # ids in books_retry, as this run knows them
        self.known_failed: set = set()  # ids whose read failed in an earlier run
        self.retry_order: list = []  # the known-failed ids, oldest failure first
        self.pending_jumped: list = []  # ids this run queued as jumped and has not read yet
        self.high: Optional[int] = None  # the highest id held, kept as documents are saved
        self.base = FIRST_ID.get(company, 1)
        self.in_a_row = 0
        self.failing = False

    # ---- time ------------------------------------------------------------------------------------------
    def left(self) -> float:
        return self.box_s - (self.clock() - self.started)

    def out_of_time(self) -> bool:
        if self.left() <= MARGIN_S + MIN_CALL_S:
            self.notes.append("time box reached")
            return True
        return False

    def stopped(self) -> bool:
        return self.failing or self.out_of_time()

    # ---- one read --------------------------------------------------------------------------------------
    def read(self, sid: int) -> str:
        """'doc', 'absent', 'failed' or OUT_OF_TIME. Saves a document read; records a failure for a retry."""
        if sid in self.seen:
            return self.seen[sid]
        error = None
        try:
            body = self.client.get_doc(sid, deadline=time.monotonic() + max(0.0, self.left() - MARGIN_S))
        except DeadlineReached:
            self.notes.append("time box reached")
            return OUT_OF_TIME
        except WizError as exc:
            body, error = None, exc.kind
        s = self.summary
        s["probed"] += 1
        s["to_id"] = sid if s["to_id"] is None else max(s["to_id"], sid)
        if error is not None:
            outcome = self._fail(sid, error)
        elif body is NOT_FOUND:
            s["absent"] += 1
            if sid in self.waiting:
                self.store.clear_retry(self.company, sid)
                self.waiting.discard(sid)
            outcome = "absent"
        else:
            outcome = self._found(sid, body)
        self.seen[sid] = outcome
        if outcome != "failed":
            self.in_a_row = 0
        elif sid not in self.known_failed:  # an id that failed before says nothing new about WizCloud
            self.in_a_row += 1
            if self.in_a_row >= FAILURES_IN_A_ROW and not self.failing:
                self.failing = True
                s["ok"] = False
                self.notes.append(f"{FAILURES_IN_A_ROW} failed reads in a row: WizCloud is failing")
        return outcome

    def _fail(self, sid: int, kind: str) -> str:
        s = self.summary
        s["failed"][kind] = s["failed"].get(kind, 0) + 1
        if sid in self.known_failed:
            s["stuck"] += 1
        self.store.mark_failed(self.company, sid, kind)
        self.waiting.add(sid)
        return "failed"

    def _found(self, sid: int, body) -> str:
        try:
            doc, lines, stats = document_rows(body)
        except ParseError:
            return self._fail(sid, "bad_response")
        except Exception as exc:  # noqa: BLE001 - an answer the parser cannot read is a failed read, said by class
            self.notes.append(f"an answer the parser could not read ({type(exc).__name__})")
            return self._fail(sid, "bad_response")
        if doc["stock_id"] != sid:
            self.notes.append("a document answered with another stockID")
            return self._fail(sid, "bad_response")
        # The ids between the highest held and this one that this run has not read: a far probe's hit, or a retried
        # id above the high-water mark. Saved in the same transaction as the document (findings 3 and 4).
        floor = self.high if self.high is not None else self.base - 1
        gap = [x for x in range(floor + 1, sid) if x not in self.seen and x not in self.waiting]
        try:
            result = self.store.save(self.company, self.run_id, doc, lines, jumped=gap)
        except BooksStoreError as exc:
            self.notes.append(f"the database refused a document ({exc.detail})")
            return self._fail(sid, "store_refused")
        self.waiting.discard(sid)
        if gap:
            self.waiting.update(gap)
            self.pending_jumped.extend(gap)
            self.summary["queued"] += len(gap)
            self.notes.append(f"a document beyond a gap: {len(gap)} ids it jumped over are read")
        self.high = sid if self.high is None else max(self.high, sid)
        s = self.summary
        s["found"] += 1
        s[result] += 1
        s["masked"] += stats["masked"]
        _merge(s["masked_fields"], stats.get("masked_fields", {}))
        _merge(s["clean"], stats.get("clean", {}))
        return "doc"

    # ---- many reads ------------------------------------------------------------------------------------
    def sweep(self, ids: Iterable[int], count: str, on_absent: Optional[Callable[[], None]] = None) -> bool:
        """Read each id not read yet in this run. False when the box ran out or WizCloud is failing."""
        for sid in ids:
            if sid in self.seen:
                continue
            if self.stopped():
                return False
            outcome = self.read(sid)
            if outcome == OUT_OF_TIME:
                return False
            self.summary[count] += 1
            if outcome == "absent" and on_absent is not None:
                on_absent()
        return not self.failing

    def read_jumped(self) -> bool:
        """The ids this run queued as jumped, as time allows; what is left waits in books_retry."""
        while self.pending_jumped:
            batch, self.pending_jumped = self.pending_jumped, []
            if not self.sweep(batch, "retried"):
                return False
        return True

    def walk(self, sid: int, misses_allowed: int) -> tuple:
        """Read upward from sid. (last id that held a document or None, first id not read, reached the end)."""
        last_doc, misses = None, 0
        while misses < misses_allowed:
            if sid in self.known_failed and sid not in self.seen:
                sid += 1  # read after the frontier, with the other ids that failed before (finding 1)
                continue
            if self.stopped():
                return last_doc, sid, False
            outcome = self.read(sid)
            if outcome == OUT_OF_TIME:
                return last_doc, sid, False
            if outcome == "doc":
                last_doc, misses = sid, 0
            elif outcome == "absent":
                misses += 1
            sid += 1  # a failed read is neither a document nor a miss: it waits in books_retry
            if self.failing:
                return last_doc, sid, False
        return last_doc, sid, True

    def probe(self, top: int, next_id: int) -> tuple:
        """The far probes above top. (the id that held a document or None, every probe answered, still going)."""
        answered = True
        for jump in FAR_PROBES:
            target = top + jump
            if target < next_id:  # the walk read it already
                continue
            if self.stopped():
                return None, answered, False
            outcome = self.read(target)
            if outcome == OUT_OF_TIME:
                return None, answered, False
            if outcome == "doc":
                return target, answered, True
            if outcome == "failed":
                answered = False  # a probe that failed is not a miss: it waits in books_retry
        return None, answered, not self.failing

    def frontier(self) -> bool:
        """The walk and its far probes, from the high-water mark. True when it reached the end and every probe
        answered."""
        plain = self.high is not None or self.store.ever_completed(self.company)
        start = (self.high + 1) if self.high is not None else self.base
        self.summary["from_id"] = start
        last, next_id, going = self.walk(start, MISSES_IN_A_ROW if plain else FIRST_RUN_MISSES)
        top = last if last is not None else start - 1
        while going:
            hit, answered, going = self.probe(top, next_id)
            if not answered:
                self.notes.append("a far probe failed: it is read again next run")
            if hit is None:
                return going and answered
            self.summary["far_hits"].append(hit)
            if not self.read_jumped():
                return False
            last, next_id, going = self.walk(hit + 1, MISSES_IN_A_ROW)
            top = last if last is not None else hit
        return False

    def retry_failed(self) -> None:
        """The ids that failed in an earlier run and were not read yet in this one, oldest failure first."""
        ids = [sid for sid in self.retry_order if sid not in self.seen]
        if self.sweep(ids, "retried"):
            self.read_jumped()

    def held_gone(self) -> None:
        self.summary["held_absent"] += 1

    # ---- the run ---------------------------------------------------------------------------------------
    def execute(self, reread_held: bool) -> None:
        rows = self.store.retry_rows(self.company)
        self.waiting = {r[0] for r in rows}
        self.known_failed = {r[0] for r in rows if r[1] != JUMPED}
        self.retry_order = [r[0] for r in sorted((r for r in rows if r[1] != JUMPED), key=lambda r: (r[3], r[0]))]
        self.high = self.store.high_water(self.company)
        going = self.sweep(sorted(r[0] for r in rows if r[1] == JUMPED), "retried")  # 1. ids never read
        if going and reread_held:  # 2. every held document again
            going = self.sweep(self.store.held_ids(self.company), "reread", on_absent=self.held_gone)
            if self.summary["held_absent"]:
                self.notes.append(f"{self.summary['held_absent']} held documents answered 'not found' (gone is B2's)")
        reached = going and self.frontier()  # 3. the frontier and the far probes
        if not self.stopped():  # 4. what failed before, after the frontier
            self.retry_failed()
        pending = len(self.store.retry_ids(self.company))
        if pending:
            self.notes.append(f"{pending} ids wait in books_retry")
        if self.summary["stuck"]:
            self.notes.append(f"{self.summary['stuck']} ids failed again (/api/books/status names them)")
        s = self.summary
        s["complete"] = bool(reached and s["ok"] and not s["failed"] and not pending)

    def finish(self, quiet: bool = False) -> dict:
        s = self.summary
        s["note"] = "; ".join(dict.fromkeys(self.notes)) or None
        s["elapsed_s"] = round(self.clock() - self.started, 1)
        try:
            self.store.finish_run(self.run_id, s)
        except BooksStoreError:
            if not quiet:  # a run that worked but could not be logged is said; one that failed keeps its own error
                raise
        return s


def run_once(client, store, company: str, *, trigger: str, box_s: float,
             clock: Callable[[], float] = time.monotonic, reread_held: bool = False) -> dict:
    """One run inside `store.session(company)`. Returns its summary, also written to books_sync_runs."""
    run = _Run(client, store, company, trigger, box_s, clock)
    try:
        with client.connection():
            run.execute(reread_held)
    except Exception as exc:
        run.summary["ok"] = False
        run.summary["complete"] = False
        run.notes.append(f"the run stopped on {type(exc).__name__}")
        run.finish(quiet=True)
        raise
    return run.finish()
