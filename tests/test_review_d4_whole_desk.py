"""Hostile review of D4 (analiza-finance, merge of d4-fill and d4-cohort): a failure that belongs to the new cohort view
("לפי מחזור", backlog 0313) takes the WHOLE desk down for Chava - the payments list, the actions, D3's marks and D4's
proposals - where it should degrade only the cohort view.

Three doors, each new in D4 and none verified against a live answer (hard rule 5):
1. the voucher-track status filter (`t0`, app/monday/client.py `status_rule`) rides INSIDE the columns check of
   `read_desk` (app/desk/reading.py `require_columns(..., track=TRACK)`): monday refuses a GraphQL request whole when one
   field of it is invalid, so a filter monday does not accept, or answers in another shape, fails the columns check, and
   with it the reading. After the deploy there is no reading to fall back on: BODY_SCHEMA moved to "4", so the stored
   reading of schema 3 is discarded (`store_other_schema`) and the first full reading must succeed - filter included.
2. step 5 (the enrollments of the priced cohorts, `roster_ids`) is inside the same try: a request of it that fails ends
   the reading.
3. `build_cohorts` runs inside `build_view` (app/desk/view.py `_cohorts_of`), with no guard: an exception in the new
   module is `view_error: "crashed"` for both tabs (routes.py `_view_of`).

Each test states what a degraded-only design gives; each fails on HEAD.
Copy into tests/ and run from the worktree: python -m pytest -q tests/test_review_d4_whole_desk.py
"""
from __future__ import annotations

import httpx
import pytest

from app.desk import cohorts as ch
from app.desk import reading as rd
from app.desk import view as fd
from tests.desk_fixtures import TODAY
from tests.test_desk import reader_of
from tests.test_desk_cohorts import cohort_snapshot, cohort_world, track_world


def _refusing_the_filter(w):
    """monday refusing the status filter the way it refuses an invalid field: the whole request answered with `errors`
    and no data (a GraphQL validation error is never partial)."""
    plain = w.handler

    def handler(request: httpx.Request) -> httpx.Response:
        if b"any_of" in request.content:
            w.queries.append(("refused", {}))
            return httpx.Response(200, json={"errors": [{"message": "Invalid value for compare_value",
                                                         "extensions": {"code": "INVALID_ARGUMENT"}}]})
        return plain(request)

    w.handler = handler
    return w


def test_a_status_filter_monday_refuses_must_not_end_the_whole_reading():
    """HIGH. The filter's answer was never seen live. If monday refuses it, the payments - D2's whole desk - are not read
    at all: `read_desk` returns error "graphql" and no rows, so a cold instance (and every instance right after the deploy,
    BODY_SCHEMA 4) answers 503 `finance_unavailable` for both tabs. Expected: the charges are read, the desk stands, and
    only the cohort view says it is short."""
    w = _refusing_the_filter(cohort_world())
    r = rd.read_desk(reader_of(w))
    assert r["error"] is None, "the voucher-track filter failing ended the whole reading: %s %s" % (r["error"], r["detail"])
    assert {x["id"] for x in r["student_charge"]["rows"]} == {"101", "102", "103"}


def test_a_refresh_whose_filter_is_refused_falls_back_to_a_reading_that_fails_the_same_way():
    """The refresh carries the same filter beside its windows (`changed_items(..., track=)`); refused, the refresh raises,
    HeldReading falls back to a full reading - which carries it again, and fails. So once monday stops accepting the
    filter, no new reading lands at all: the desk shows the held one until it is `stale`, then nothing on a cold start."""
    w = track_world()
    held = rd.read_desk(reader_of(w))
    assert held["error"] is None
    _refusing_the_filter(w)
    reader = reader_of(w)                          # a reader binds the handler it is made with: made after the refusal
    try:
        got = rd.read_delta(reader, held)
    except Exception as exc:  # noqa: BLE001 - what HeldReading would do next: read whole
        full = rd.read_desk(reader_of(w))
        assert full["error"] is None, ("refresh failed (%s) and the full reading it falls back to failed too (%s): no "
                                       "reading can land while the filter is refused" % (getattr(exc, "code", exc),
                                                                                          full["error"]))
    else:
        assert got["student_charge"]["rows"]
