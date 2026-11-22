"""One run of the books copy (spec §4.2, backlog 0301 B1; reworked after the review of 22.11).

A run, inside its time box:
1. Reads again every id whose read failed before (books_retry), so no id is skipped for good.
2. Optionally re-reads every held document (`reread_held`: the acceptance's second run, and B2's sweep).
3. Reads upward from the high-water mark (the largest stockID held) until MISSES_IN_A_ROW ids in a row hold
   no document, then probes far ahead (+64 ... +1024) so a jump in ids is found; a hit there is saved and
   the frontier goes on from it. A company with nothing held and no run that ever reached the end starts at
   its known first id and tolerates FIRST_RUN_MISSES misses (Analiza's ids start near 100); after one
   complete run, an empty company (Demo) gets the plain frontier.

A read that fails - anything but "Stock not found", an answer that is not a document or names another id,
a document the database refuses - is a failed read: counted by kind, put in books_retry, never a miss and
never a change to what the copy believes. FAILURES_IN_A_ROW of them end the run (ok false). A run is
`complete` only when it reached the end, every far probe answered, nothing failed and nothing waits for a
retry. Every document is saved as it is read, every read is bounded by what is left of the box, and the
run's row is finished whatever happens.

What B2 adds: drafts and the last 300 ids every tick, the weekly sweep (it also reads any gap a far probe
jumped over, which a run here only reports), the canary, `gone`, and Make's schedule.
"""

from __future__ import annotations

import time
from typing import Callable

from ..wizcloud import NOT_FOUND, WizError
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


def run_once(client, store, company: str, *, trigger: str, box_s: float,
             clock: Callable[[], float] = time.monotonic, reread_held: bool = False) -> dict:
    """One run inside `store.session(company)`. Returns its summary, also written to books_sync_runs."""
    started = clock()
    held = store.high_water(company)
    plain = held is not None or store.ever_completed(company)
    start = (held + 1) if held is not None else FIRST_ID.get(company, 1)
    run_id = store.start_run(company, trigger, start)
    summary = {
        "run_id": run_id, "company": company, "trigger": trigger, "from_id": start, "to_id": start - 1,
        "probed": 0, "found": 0, "added": 0, "changed": 0, "unchanged": 0, "absent": 0, "failed": {},
        "masked": 0, "far_hits": [], "retried": 0, "reread": 0, "held_absent": 0,
        "complete": False, "ok": True, "note": None,
    }
    notes: list = []
    retrying: set = set()

    def left() -> float:
        return box_s - (clock() - started)

    def out_of_time() -> bool:
        if left() <= MARGIN_S:
            notes.append("time box reached")
            return True
        return False

    def fail(sid: int, kind: str) -> str:
        summary["failed"][kind] = summary["failed"].get(kind, 0) + 1
        store.mark_failed(company, sid, kind)
        return "failed"

    def read(sid: int) -> str:
        """'doc', 'absent' or 'failed'. Saves a document read; records a failure for a retry."""
        summary["probed"] += 1
        summary["to_id"] = max(summary["to_id"], sid)
        try:
            body = client.get_doc(sid, deadline=time.monotonic() + max(0.0, left() - MARGIN_S))
        except WizError as exc:
            return fail(sid, exc.kind)
        if body is NOT_FOUND:
            summary["absent"] += 1
            if sid in retrying:
                store.clear_retry(company, sid)
            return "absent"
        try:
            doc, lines, stats = document_rows(body)
        except ParseError:
            return fail(sid, "bad_response")
        if doc["stock_id"] != sid:
            notes.append("a document answered with another stockID")
            return fail(sid, "bad_response")
        try:
            result = store.save(company, run_id, doc, lines)
        except BooksStoreError as exc:
            notes.append(f"the database refused a document ({exc.detail})")
            return fail(sid, "store_refused")
        if sid in retrying:
            store.clear_retry(company, sid)
        summary["found"] += 1
        summary[result] += 1
        summary["masked"] += stats["masked"]
        return "doc"

    def frontier(sid: int, misses_allowed: int):
        """Read upward from sid. Returns (last id that held a document or None, reached the end)."""
        last_doc, misses, failures = None, 0, 0
        while misses < misses_allowed:
            if out_of_time():
                return last_doc, False
            outcome = read(sid)
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
                    return last_doc, False
            sid += 1
        return last_doc, True

    try:
        with client.connection():
            reached = True
            for sid in store.retry_ids(company):  # 1. what failed before
                retrying.add(sid)
            for sid in sorted(retrying):
                if out_of_time():
                    reached = False
                    break
                summary["retried"] += 1
                read(sid)
            if reached and reread_held:  # 2. every held document again
                for sid in store.held_ids(company):
                    if out_of_time():
                        reached = False
                        break
                    summary["reread"] += 1
                    if read(sid) == "absent":
                        summary["held_absent"] += 1
                if summary["held_absent"]:
                    notes.append(f"{summary['held_absent']} held documents answered 'not found' (gone is B2's)")
            if reached:  # 3. the frontier and the far probes
                last, reached = frontier(start, MISSES_IN_A_ROW if plain else FIRST_RUN_MISSES)
                top = last if last is not None else (held if held is not None else start - 1)
                while reached and summary["ok"]:
                    hit, answered = None, True
                    for jump in FAR_PROBES:
                        if out_of_time():
                            reached = False
                            break
                        outcome = read(top + jump)
                        if outcome == "doc":
                            hit = top + jump
                            break
                        if outcome == "failed":
                            answered = False  # a probe that failed is not a miss: retried next run
                    if not answered:
                        notes.append("a far probe failed: it is read again next run")
                    if hit is None or not reached:
                        reached = reached and answered
                        break
                    summary["far_hits"].append(hit)
                    notes.append(f"ids {top + 1}-{hit - 1} were jumped over: the weekly sweep reads them (B2)")
                    last, reached = frontier(hit + 1, MISSES_IN_A_ROW)
                    top = last if last is not None else hit
            waiting = len(store.retry_ids(company))
            if waiting:
                notes.append(f"{waiting} ids wait for a retry")
            summary["complete"] = bool(reached and summary["ok"] and not summary["failed"] and not waiting)
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
