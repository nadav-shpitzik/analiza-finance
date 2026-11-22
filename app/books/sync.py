"""One run of the books copy - a tick (spec §4.2, backlog 0301 B1 and B2; reworked after the three reviews of 22.11).

A run, inside its time box, reads in this order:
0. The canary (spec §4.2.4): the oldest valid document held, and the next one if it does not answer. When neither
   answers as a document, no "not found" is trusted in this tick: no miss is recorded, nothing goes gone, no
   waiting id is cleared. When they answered "not found", the tick is not ok (the wrong company?); when their reads
   failed, the reads after them judge whether WizCloud is failing.
1. The ids a far probe jumped over that wait in books_retry (never read yet).
2. Optionally every held document again (`reread_held`: books-check's second run).
3. The frontier: upward from the high-water mark (the largest stockID held) until MISSES_IN_A_ROW ids in a row
   hold no document, then far probes (+64 ... +1024) so a jump in ids is found. A company with nothing held and no
   run that ever reached the end starts at its known first id and tolerates FIRST_RUN_MISSES misses; after one
   complete run, an empty company (Demo) gets the plain frontier.
4. The ids whose read failed in an earlier run, oldest failure first, after the frontier: the frontier steps past
   them, so an id that fails on every read never stands in front of new documents (third review 22.11, finding 1).
5. Every draft held (spec §4.2.2), the one read longest ago first.
6. Held documents that answered "not found" at least MISSING_CONFIRM_S ago, read again to confirm (spec §4.2.5).
7. Of the RECENT_HELD highest ids held, the RECENT_PER_TICK read longest ago: each is read again every few ticks.
   The spec's "the last 300 ids every tick" does not fit: a read takes about 0.19 s from Replit (22.11), so 300
   would take 57 of the 60 seconds.
8. The weekly sweep (spec §4.2.3), from its saved position, in what is left of the box: every id from the first to
   the high-water mark, held or not, so a document the frontier missed is found and an edit is seen. A pass that
   ends waits until a week from its start before the next one begins.

A held document that answers "not found" is missing: its first and latest miss are kept. Missed twice at least
MISSING_CONFIRM_S apart, a draft is gone at once (at most GONE_PER_TICK a tick), and a valid document stays in every
figure until a person confirms it gone - until then the tick is not ok. A document read again is no longer missing,
and one that was gone reappears.

The verdict (`ok`, with `reasons`): not ok when WizCloud is failing, the canary answered "not found", a series has a hole
(judged only once the frontier reached the end: a copy still catching up has holes that are not holes), a valid
document is missing, or an id has kept failing for a day. `alert` is whether the reasons changed since the last
finished run - a new problem, another one, or the way back to ok - which is when Make posts to Discord.

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
from .doctypes import DRAFT_STATUS
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
CANARIES = 5  # read in turn until one answers (fourth review 22.11: two deleted ones blinded every tick)
RECENT_HELD = 300
RECENT_PER_TICK = 100
MISSING_CONFIRM_S = 3600  # two misses at least an hour apart (spec §4.2.5)
MISSING_PER_TICK = 20
GONE_PER_TICK = 5
SWEEP_EVERY_S = 7 * 24 * 3600
STUCK_ALERT_S = 24 * 3600
# Every reason a run can give, in the order the verdict gives them: main says each one in Hebrew (a test holds the two
# together).
REASONS = ("wizcloud_failing", "canary", "holes", "missing_valid", "stuck", "sweep_late", "crashed")


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
            "canary": None, "drafts": 0, "rechecked": 0, "recent": 0, "swept": 0, "sweep": None, "missing": 0, "gone": 0,
            "complete": False, "failing": False, "catching_up": False, "holes": None, "missing_valid": 0, "stuck_day": 0,
            "ok": True, "reasons": [], "alert": False, "note": None, "masked_fields": {}, "clean": {},
        }
        self.notes: list = []
        self.seen: dict = {}  # id -> the outcome of its read in this run: no id is read twice
        self.waiting: set = set()  # ids in books_retry, as this run knows them
        self.sweep_late = False  # the weekly sweep's open pass began more than a week ago
        self.known_failed: set = set()  # ids whose read failed in an earlier run
        self.retry_order: list = []  # the known-failed ids, oldest failure first
        self.pending_jumped: list = []  # ids this run queued as jumped and has not read yet
        self.high: Optional[int] = None  # the highest id held, kept as documents are saved
        self.base = FIRST_ID.get(company, 1)
        self.in_a_row = 0
        self.failing = False
        self.held: dict = {}  # stock_id -> status of every document held and not gone
        self.trust_absence = False  # until the canary answered: a 'not found' changes nothing before that
        self.gone_now = 0
        self.prev_reasons: Optional[list] = None
        self.walk_end: Optional[int] = None  # the first id the frontier's last walk did not read

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
            if self.trust_absence:  # before the canary answered, or when it did not, a 'not found' changes nothing
                if sid in self.waiting:
                    self.store.clear_retry(self.company, sid)
                    self.waiting.discard(sid)
                if sid in self.held:
                    self._missed(sid)
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
                s["failing"] = True
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
            result = self.store.save(self.company, self.run_id, doc, lines, jumped=gap, held=sid in self.held)
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
        self.held[sid] = doc["status"]
        s = self.summary
        s["found"] += 1
        s[result] += 1
        s["masked"] += stats["masked"]
        _merge(s["masked_fields"], stats.get("masked_fields", {}))
        _merge(s["clean"], stats.get("clean", {}))
        return "doc"

    def _missed(self, sid: int) -> None:
        """A held document answered 'not found' (spec §4.2.5)."""
        marked = self.store.mark_missing(self.company, sid)
        if marked is None:
            return
        status, since_s = marked
        self.summary["missing"] += 1
        if since_s < MISSING_CONFIRM_S or status != DRAFT_STATUS:
            return  # a first miss, or a valid document: a person confirms it (the verdict says so)
        if self.gone_now >= GONE_PER_TICK:
            self.notes.append(f"more than {GONE_PER_TICK} drafts gone in one tick: the rest wait for the next")
            return
        self.store.mark_gone(self.company, sid, self.run_id, "a draft missed twice an hour apart")
        self.held.pop(sid, None)
        self.gone_now += 1
        self.summary["gone"] += 1

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
        self.walk_end = sid
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

    def canary(self) -> None:
        """Read the oldest valid documents held until one answers. None held: nothing to protect, absence trusted."""
        ids = self.store.canaries(self.company, CANARIES)
        if not ids:
            self.summary["canary"] = "none"
            self.trust_absence = True
            return
        answers = []
        for sid in ids:
            if self.stopped():
                break
            answers.append(self.read(sid))
            if answers[-1] == "doc":
                self.summary["canary"] = "ok"
                self.trust_absence = True
                for earlier, answer in zip(ids, answers):  # an older canary that said 'not found' is missing now
                    if answer == "absent" and earlier in self.held:
                        self._missed(earlier)
                return
        # 'absent': WizCloud answers, and says the oldest documents are not there (the wrong company?) - a reason.
        # 'failed': the reads failed - WizCloud failing, which the reads after it judge. Either way no absence is
        # trusted in this tick.
        self.summary["canary"] = "absent" if "absent" in answers else "failed"
        self.notes.append(f"the canary {'answered not found' if 'absent' in answers else 'was not read'}: "
                          "no 'not found' is trusted in this tick")

    def weekly_sweep(self) -> None:
        """Every id from the first to the high-water mark, from the saved position, in what is left of the box. A pass
        that ended waits a week from its start, whatever new documents came meanwhile (the frontier reads those). It
        runs even when the box is spent: a pass then begins with no read, so a sweep that never gets time grows late
        and says so (22.11 on the deployment: the box closed before this phase in every tick, no row was written, and
        nothing could tell that the sweep never ran)."""
        if self.high is None:
            return
        state = self.store.sweep_state(self.company)
        if state is not None and state["ended"] and state["pass_age_s"] < SWEEP_EVERY_S:
            return  # this week's pass is done
        new_pass = state is None or state["ended"]
        sid = self.base if new_pass else state["next_id"]
        top = self.high
        while sid <= top:
            if sid in self.seen:
                sid += 1
                continue
            if self.stopped():
                break
            if self.read(sid) == OUT_OF_TIME:
                break
            self.summary["swept"] += 1
            sid += 1
        ended = sid > top
        if new_pass or ended or sid != state["next_id"]:  # a pass that got no read keeps its row as it was
            self.store.save_sweep(self.company, sid, new_pass=new_pass, ended=ended)
        self.summary["sweep"] = {"next_id": sid, "ended": ended, "new_pass": new_pass}
        self.sweep_late = not new_pass and not ended and state["pass_age_s"] > SWEEP_EVERY_S

    def conclude(self, reached: bool) -> None:
        """complete, and the verdict: ok, reasons, alert."""
        s = self.summary
        pending = len(self.store.retry_ids(self.company))
        if pending:
            self.notes.append(f"{pending} ids wait in books_retry")
        if s["stuck"]:
            self.notes.append(f"{s['stuck']} ids failed again (/api/books/status names them)")
        s["complete"] = bool(reached and not self.failing and not s["failed"] and not pending)
        reasons = []
        if self.failing:
            reasons.append("wizcloud_failing")
        if s["canary"] == "absent":
            reasons.append("canary")
        s["holes"] = sum(x["holes"] for x in self.store.series(self.company))
        if reached:
            if s["holes"]:
                reasons.append("holes")
        else:
            s["catching_up"] = not self.failing
            # Not judged afresh, but what the last tick knew stands: a tick cut short is no news that a hole closed
            # (fourth review 22.11: one failed far probe posted 'back to ok' while the hole was there).
            if s["holes"] and "holes" in (self.prev_reasons or []):
                reasons.append("holes")
        s["missing_valid"] = self.store.missing_summary(self.company, MISSING_CONFIRM_S)["valid_confirmed"]
        if s["missing_valid"]:
            reasons.append("missing_valid")
        s["stuck_day"] = self.store.stuck_since(self.company, STUCK_ALERT_S)
        if s["stuck_day"]:
            reasons.append("stuck")
        if self.sweep_late:
            reasons.append("sweep_late")
        s["reasons"] = reasons
        s["ok"] = not reasons
        s["alert"] = set(reasons) != set(self.prev_reasons or [])

    # ---- the run ---------------------------------------------------------------------------------------
    def execute(self, reread_held: bool, tick: bool = True) -> None:
        rows = self.store.retry_rows(self.company)
        self.waiting = {r[0] for r in rows}
        self.known_failed = {r[0] for r in rows if r[1] != JUMPED}
        self.retry_order = [r[0] for r in sorted((r for r in rows if r[1] != JUMPED), key=lambda r: (r[3], r[0]))]
        self.high = self.store.high_water(self.company)
        self.held = self.store.held_status(self.company)
        if tick:
            self.canary()  # 0. does WizCloud answer for this company
        else:
            self.trust_absence = True  # the copy alone (B1): no canary, absence trusted
        going = self.sweep(sorted(r[0] for r in rows if r[1] == JUMPED), "retried")  # 1. ids never read
        if going and reread_held:  # 2. every held document again
            going = self.sweep(self.store.held_ids(self.company), "reread", on_absent=self.held_gone)
            if self.summary["held_absent"]:
                self.notes.append(f"{self.summary['held_absent']} held documents answered 'not found'")
        reached = going and self.frontier()  # 3. the frontier and the far probes
        if not self.stopped():  # 4. what failed before, after the frontier
            self.retry_failed()
        if reached and self.high is not None and self.walk_end is not None and self.high >= self.walk_end:
            # a retried id held a document past where the walk ended: documents may follow it, and the frontier walks
            # on from it next tick (fourth review 22.11: the run said complete)
            reached = False
            self.notes.append("a retried id held a document past the frontier: it walks on from there next tick")
        if tick and not self.stopped():  # 5. the drafts
            self.sweep(self.store.draft_ids(self.company), "drafts")
        if tick and not self.stopped():  # 6. what went missing, read again to confirm
            self.sweep(self.store.missing_due(self.company, MISSING_CONFIRM_S, MISSING_PER_TICK), "rechecked")
        if tick and not self.stopped():  # 7. the recent documents, a slice a tick
            self.sweep(self.store.recent_ids(self.company, RECENT_HELD, RECENT_PER_TICK), "recent")
        if tick:  # 8. the weekly sweep, in what is left - with nothing left its pass still begins, so it has an age
            self.weekly_sweep()
        self.conclude(reached)

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
             clock: Callable[[], float] = time.monotonic, reread_held: bool = False, tick: bool = True) -> dict:
    """One run inside `store.session(company)`. Returns its summary, also written to books_sync_runs. `tick` False
    is the copy alone (phases 1-4, absence trusted with no canary): what the B1 tests describe."""
    run = _Run(client, store, company, trigger, box_s, clock)
    try:
        run.prev_reasons = store.last_reasons(company)
        with client.connection():
            run.execute(reread_held, tick)
    except Exception as exc:
        run.summary["ok"] = False
        run.summary["complete"] = False
        run.summary["reasons"] = ["crashed"]
        run.notes.append(f"the run stopped on {type(exc).__name__}")
        run.finish(quiet=True)
        # A crash that repeats is said once, like any reason (fourth review 22.11).
        exc.books_alert = set(run.prev_reasons or []) != {"crashed"}
        raise
    return run.finish()
