"""One run of the books copy (spec §4.2, backlog 0301 B1; reworked after the two reviews of 22.11).

A run, inside its time box:
1. Reads every id waiting in books_retry: a read that failed before, or an id a far probe jumped over.
2. Optionally re-reads every held document (`reread_held`: the acceptance's second run, and B2's sweep).
3. Reads upward from the high-water mark (the largest stockID held, taken after steps 1-2) until
   MISSES_IN_A_ROW ids in a row hold no document, then probes far ahead (+64 ... +1024) so a jump in ids is
   found. A hit there is saved, every id it jumped over is put in books_retry and read now, as time allows,
   and the frontier goes on from the hit. A company with nothing held and no run that ever reached the end
   starts at its known first id and tolerates FIRST_RUN_MISSES misses (Analiza's ids start near 100); after
   one complete run, an empty company (Demo) gets the plain frontier.

A read that fails - anything but "Stock not found", an answer that is not a document or names another id,
an answer the parser cannot read, a document the database refuses - is a failed read: counted by kind, put
in books_retry, never a miss and never a change to what the copy believes. FAILURES_IN_A_ROW of them end the
run (ok false). The time box is not a failure: a read it cuts (DeadlineReached) stops the run for time, and
what was not read is read next run. A run is `complete` only when it reached the end, every far probe
answered, nothing failed and nothing waits in books_retry.

What `complete` cannot promise: a document after a gap of more than MISSES_IN_A_ROW empty ids, short of the
first far probe, is found only once a probe lands on its run of ids - or by the weekly sweep (B2), which reads
every id. books-check prints the largest gap seen in the real books, so the window can be judged.

Every document is saved as it is read, every read is bounded by what is left of the box, and the run's row
is finished whatever happens.

What B2 adds: drafts and the last 300 ids every tick, the weekly sweep, the canary, `gone`, and Make's
schedule.
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
# Analiza: ids 1-99 hold nothing and documents start near 100 (18.11; the first full copy, 22.11, read
# from 1 with the wide frontier). Demo: no document at any id probed (1-100 on 18.11, up to 1,000,000 on 22.11).
FIRST_ID = {"wizdb2245n5": 1, "wizdb2245n4": 1}
OUT_OF_TIME = "out_of_time"


def _merge(into: dict, add: dict) -> None:
    for key, value in add.items():
        if isinstance(value, dict):
            _merge(into.setdefault(key, {}), value)
        else:
            into[key] = into.get(key, 0) + value


def run_once(client, store, company: str, *, trigger: str, box_s: float,
             clock: Callable[[], float] = time.monotonic, reread_held: bool = False) -> dict:
    """One run inside `store.session(company)`. Returns its summary, also written to books_sync_runs."""
    started = clock()
    run_id = store.start_run(company, trigger, None)
    summary = {
        "run_id": run_id, "company": company, "trigger": trigger, "from_id": None, "to_id": None,
        "probed": 0, "found": 0, "added": 0, "changed": 0, "unchanged": 0, "absent": 0, "failed": {},
        "masked": 0, "far_hits": [], "retried": 0, "reread": 0, "held_absent": 0, "queued": 0,
        "complete": False, "ok": True, "note": None, "masked_fields": {}, "clean": {},
    }
    notes: list = []
    waiting: set = set()  # ids taken from books_retry or queued in this run: cleared when read

    def left() -> float:
        return box_s - (clock() - started)

    def out_of_time() -> bool:
        if left() <= MARGIN_S + MIN_CALL_S:
            notes.append("time box reached")
            return True
        return False

    def fail(sid: int, kind: str) -> str:
        summary["failed"][kind] = summary["failed"].get(kind, 0) + 1
        store.mark_failed(company, sid, kind)
        return "failed"

    def read(sid: int) -> str:
        """'doc', 'absent', 'failed' or OUT_OF_TIME. Saves a document read; records a failure for a retry."""
        error = None
        try:
            body = client.get_doc(sid, deadline=time.monotonic() + max(0.0, left() - MARGIN_S))
        except DeadlineReached:
            notes.append("time box reached")
            return OUT_OF_TIME
        except WizError as exc:
            body, error = None, exc.kind
        summary["probed"] += 1
        summary["to_id"] = sid if summary["to_id"] is None else max(summary["to_id"], sid)
        if error is not None:
            return fail(sid, error)
        if body is NOT_FOUND:
            summary["absent"] += 1
            if sid in waiting:
                store.clear_retry(company, sid)
            return "absent"
        try:
            doc, lines, stats = document_rows(body)
        except ParseError:
            return fail(sid, "bad_response")
        except Exception as exc:  # noqa: BLE001 - an answer the parser cannot read is a failed read, said by class
            notes.append(f"an answer the parser could not read ({type(exc).__name__})")
            return fail(sid, "bad_response")
        if doc["stock_id"] != sid:
            notes.append("a document answered with another stockID")
            return fail(sid, "bad_response")
        try:
            result = store.save(company, run_id, doc, lines)
        except BooksStoreError as exc:
            notes.append(f"the database refused a document ({exc.detail})")
            return fail(sid, "store_refused")
        if sid in waiting:
            store.clear_retry(company, sid)
        summary["found"] += 1
        summary[result] += 1
        summary["masked"] += stats["masked"]
        _merge(summary["masked_fields"], stats.get("masked_fields", {}))
        _merge(summary["clean"], stats.get("clean", {}))
        return "doc"

    def sweep(ids: Iterable[int], count: str, on_absent: Optional[Callable[[], None]] = None) -> bool:
        """Read each id. False when the box ran out or reads kept failing."""
        failures = 0
        for sid in ids:
            if out_of_time():
                return False
            outcome = read(sid)
            if outcome == OUT_OF_TIME:
                return False
            summary[count] += 1
            if outcome == "failed":
                failures += 1
                if failures >= FAILURES_IN_A_ROW:
                    summary["ok"] = False
                    notes.append(f"{FAILURES_IN_A_ROW} failed reads in a row: WizCloud is failing")
                    return False
                continue
            failures = 0
            if outcome == "absent" and on_absent is not None:
                on_absent()
        return True

    def frontier(sid: int, misses_allowed: int) -> tuple:
        """Read upward from sid. (last id that held a document or None, first id not read, reached the end)."""
        last_doc, misses, failures = None, 0, 0
        while misses < misses_allowed:
            if out_of_time():
                return last_doc, sid, False
            outcome = read(sid)
            if outcome == OUT_OF_TIME:
                return last_doc, sid, False
            if outcome == "doc":
                last_doc, misses, failures = sid, 0, 0
            elif outcome == "absent":
                misses += 1
                failures = 0
            else:
                failures += 1
                if failures >= FAILURES_IN_A_ROW:
                    summary["ok"] = False
                    notes.append(f"{FAILURES_IN_A_ROW} failed reads in a row: WizCloud is failing")
                    return last_doc, sid + 1, False
            sid += 1
        return last_doc, sid, True

    def held_gone() -> None:
        summary["held_absent"] += 1

    try:
        with client.connection():
            waiting.update(store.retry_ids(company))  # 1. what failed or was jumped over before
            reached = sweep(sorted(waiting), "retried")
            if reached and reread_held:  # 2. every held document again
                reached = sweep(store.held_ids(company), "reread", on_absent=held_gone)
                if summary["held_absent"]:
                    notes.append(f"{summary['held_absent']} held documents answered 'not found' (gone is B2's)")
            if reached:  # 3. the frontier and the far probes, from what is held now
                held = store.high_water(company)
                plain = held is not None or store.ever_completed(company)
                start = (held + 1) if held is not None else FIRST_ID.get(company, 1)
                summary["from_id"] = start
                last, next_id, reached = frontier(start, MISSES_IN_A_ROW if plain else FIRST_RUN_MISSES)
                top = last if last is not None else start - 1
                while reached and summary["ok"]:
                    hit, answered, probed = None, True, set()
                    for jump in FAR_PROBES:
                        target = top + jump
                        if target < next_id:  # the frontier read it already
                            continue
                        if out_of_time():
                            reached = False
                            break
                        outcome = read(target)
                        if outcome == OUT_OF_TIME:
                            reached = False
                            break
                        probed.add(target)
                        if outcome == "doc":
                            hit = target
                            break
                        if outcome == "failed":
                            answered = False  # a probe that failed is not a miss: it waits in books_retry
                    if not answered:
                        notes.append("a far probe failed: it is read again next run")
                    if hit is None or not reached:
                        reached = reached and answered
                        break
                    summary["far_hits"].append(hit)
                    jumped = [x for x in range(next_id, hit) if x not in probed]
                    if jumped:  # every id the probe jumped over waits in books_retry until it is read
                        store.queue_retry(company, jumped, "jumped")
                        waiting.update(jumped)
                        summary["queued"] += len(jumped)
                        notes.append(f"a far probe found ids beyond a gap: {len(jumped)} ids it jumped over are read")
                        if not sweep(jumped, "retried"):
                            reached = False
                            break
                    last, next_id, reached = frontier(hit + 1, MISSES_IN_A_ROW)
                    top = last if last is not None else hit
            pending = len(store.retry_ids(company))
            if pending:
                notes.append(f"{pending} ids wait in books_retry")
            summary["complete"] = bool(reached and summary["ok"] and not summary["failed"] and not pending)
    except Exception as exc:
        summary["ok"] = False
        summary["complete"] = False
        notes.append(f"the run stopped on {type(exc).__name__}")
        _finish(store, run_id, summary, notes, started, clock, quiet=True)
        raise
    _finish(store, run_id, summary, notes, started, clock)
    return summary


def _finish(store, run_id: int, summary: dict, notes: list, started: float, clock, quiet: bool = False) -> None:
    summary["note"] = "; ".join(dict.fromkeys(notes)) or None
    summary["elapsed_s"] = round(clock() - started, 1)
    try:
        store.finish_run(run_id, summary)
    except BooksStoreError:
        if not quiet:  # a run that worked but could not be logged is said; one that failed keeps its own error
            raise
