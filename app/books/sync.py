"""One run of the books copy (spec §4.2, backlog 0301 B1): the frontier and the far probes.

From the high-water mark (the largest stockID held) the run reads upward until MISSES_IN_A_ROW ids in a
row hold no document, then probes far ahead (+64 ... +1024) so a jump in ids is found; a hit there is
saved and the frontier goes on from it. The first run of a company, with nothing held, starts at its known
first id and tolerates FIRST_RUN_MISSES misses (Demo has no ids 1-100; Analiza's start near 100).

What B2 adds: drafts and the last 300 ids re-read every tick, a weekly sweep (it also reads any gap a far
probe jumped over, which this run only reports), the canary, `gone`, and Make's schedule.

A read that fails (anything but "Stock not found") is a failed read: counted by kind, never a miss, never
a change to what the copy believes. FAILURES_IN_A_ROW of them end the run, which then reports ok false.
Every document is saved as it is read, so a run cut by its time box loses nothing.
"""

from __future__ import annotations

import time
from typing import Callable, Optional

from ..wizcloud import NOT_FOUND, WizError
from .parse import ParseError, document_rows

MISSES_IN_A_ROW = 50
FIRST_RUN_MISSES = 200
FAR_PROBES = (64, 128, 256, 512, 1024)
FAILURES_IN_A_ROW = 3
# Where a company's ids begin, so the first run does not read from 1 (spec §4.2.1). Analiza: ids 100 to
# 2800 held documents and 1 did not (18.11); Demo: no document at any id probed (1-100 on 18.11, and 101
# to 1,000,000 sampled on 22.11), so it starts at 1 and stays empty.
FIRST_ID = {"wizdb2245n5": 1, "wizdb2245n4": 1}


def run_once(client, store, company: str, *, trigger: str, box_s: float,
             clock: Callable[[], float] = time.monotonic) -> dict:
    """One run inside `store.session(company)`. Returns its summary, also written to books_sync_runs."""
    started = clock()
    held = store.high_water(company)
    first_run = held is None
    start = (held + 1) if held is not None else FIRST_ID.get(company, 1)
    run_id = store.start_run(company, trigger, start)
    summary = {
        "run_id": run_id, "company": company, "trigger": trigger, "from_id": start, "to_id": start - 1,
        "probed": 0, "found": 0, "added": 0, "changed": 0, "unchanged": 0, "absent": 0, "failed": {},
        "masked": 0, "far_hits": [], "complete": False, "ok": True, "note": None,
    }
    notes = []
    limit = FIRST_RUN_MISSES if first_run else MISSES_IN_A_ROW

    def out_of_time() -> bool:
        return clock() - started >= box_s

    def read(sid: int):
        """'doc', 'absent' or 'failed', and saves a document read."""
        summary["probed"] += 1
        summary["to_id"] = max(summary["to_id"], sid)
        try:
            body = client.get_doc(sid)
        except WizError as exc:
            summary["failed"][exc.kind] = summary["failed"].get(exc.kind, 0) + 1
            return "failed"
        if body is NOT_FOUND:
            summary["absent"] += 1
            return "absent"
        try:
            doc, lines, stats = document_rows(body)
        except ParseError:
            summary["failed"]["bad_response"] = summary["failed"].get("bad_response", 0) + 1
            return "failed"
        if doc["stock_id"] != sid:
            summary["failed"]["bad_response"] = summary["failed"].get("bad_response", 0) + 1
            notes.append("a document answered with another stockID")
            return "failed"
        result = store.save(company, run_id, doc, lines)
        summary["found"] += 1
        summary[result] += 1
        summary["masked"] += stats["masked"]
        return "doc"

    def frontier(sid: int, misses_allowed: int) -> Optional[int]:
        """Read upward from sid. Returns the last id that held a document, or None."""
        last_doc, misses, failures = None, 0, 0
        while misses < misses_allowed:
            if out_of_time():
                notes.append("time box reached")
                return last_doc
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
                    return last_doc
            sid += 1
        return last_doc

    with client.connection():
        last = frontier(start, limit)
        top = last if last is not None else (held if held is not None else start - 1)
        reached_end = summary["ok"] and not out_of_time()
        while reached_end:
            hit = None
            for jump in FAR_PROBES:
                if out_of_time():
                    reached_end = False
                    notes.append("time box reached")
                    break
                if read(top + jump) == "doc":
                    hit = top + jump
                    break
            if hit is None or not reached_end:
                break
            summary["far_hits"].append(hit)
            notes.append(f"ids {top + 1}-{hit - 1} were jumped over: the weekly sweep reads them (B2)")
            last = frontier(hit + 1, MISSES_IN_A_ROW)
            top = last if last is not None else hit
            reached_end = summary["ok"] and not out_of_time()
        summary["complete"] = bool(reached_end and summary["ok"])
    summary["note"] = "; ".join(dict.fromkeys(notes)) or None
    summary["elapsed_s"] = round(clock() - started, 1)
    store.finish_run(run_id, summary)
    return summary
