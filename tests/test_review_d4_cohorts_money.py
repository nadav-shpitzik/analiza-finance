"""Hostile review of D4 "לפי מחזור" (app/desk/cohorts.py): where the cohort line's money is not the money of backlog 0313.

Fixtures in the reading's shape (tests/test_desk_cohorts.py builders), shaped like the live records the knowledge repo
describes: a 4,000 deposit and the helper's "<student> | השלמת תשלום" row; a deposit that reaches the desk by its lead only
(backlog 0105: 63 of 417 Student Charge rows had no enrollment link on 03.09; the helper links one only when the lead's
enrollment link is set, `/b2c/link-charge-from-lead`); a waived student's deposit; a price typed in a right-to-left editor.

Copy into tests/ and run from the worktree: python -m pytest -q tests/test_review_d4_cohorts_money.py
"""
from __future__ import annotations

from decimal import Decimal

from app.desk import cohorts as ch
from tests.desk_fixtures import lead, sc
from tests.test_desk_cohorts import coh, enr, line, part_of, student

D = Decimal


def test_money_the_desk_files_under_the_cohort_is_still_counted_as_balance_to_collect():
    """MEDIUM (wrong money on the line Chava reads first). A1 is the only student of A700 (12,000, voucher 1 "אושר -
    ממתין לתשלום"). Her 4,000 deposit links her lead, not her enrollment (born before conversion; backlog 0105 counted 63
    such rows of 417). The desk files the deposit under A700 by its lead (`cohort_from` "lead") and says it on the line
    ("תקבולים בלי הרשמה: 1, 4,000"), but the line's balance - and its "אושר - ממתין לתשלום", the figure first on the
    screen and in the file - still holds those 4,000 as money to collect: 12,000 to collect where at most 8,000 can be.
    The line's own numbers then say more is missing than the cohort's money allows."""
    deposit = sc(1, paid="4000", lead=["L1"], enr=[])
    # changed deliberately by the fix (decision 4): a deposit filed by its lead is the student's when the data links it to
    # exactly one student of the cohort - here A1's enrollment carries the link a converted lead's enrollment has, its
    # "קישור לליד" to L1. So the desk counts the money for her: in her paid, no longer said as "unlinked". Without such a
    # link it stays the cohort's and in no balance (tests/test_desk_cohorts.py, the cases of none and of two).
    part = part_of([deposit], [coh(price="12,000", roster=["E1"])], [enr("E1", voucher="1", person="P1", lead=["L1"])],
                   leads=[lead("L1", "C1")])
    ln = line(part)
    came_in = D("4000")
    assert ln["unlinked"] == {"count": 0, "money": D(0)} and student(ln, "E1")["payments"] == ["1"]   # A1's money
    assert ln["balance"] <= ln["expected"] - came_in, (
        "A700: expected %s, of which %s came in (filed by lead), and the line still says %s to collect, all of it in "
        "'אושר - ממתין לתשלום' (%s)" % (ln["expected"], came_in, ln["balance"], ln["part_approved"]))


def test_the_prompts_scenario_owes_nothing():
    """Not a finding - the reviewer's own fixture, held: a 4,000 deposit and the helper's "X | השלמת תשלום" of 8,000 on a
    12,000 cohort, voucher 4, owes 0 (kept here so the fixer sees it pass)."""
    bal = sc(2, paid="8000", enr=["E1"], invoice="", receipt="0")
    bal["name"] = "נועה ברק | השלמת תשלום"
    part = part_of([sc(1, paid="4000", enr=["E1"]), bal], [coh(price="12,000", roster=["E1"])],
                   [enr("E1", voucher="4", person="P1")])
    s = student(line(part), "E1")
    assert (s["paid"], s["balance"], s["overpaid"]) == (D("12000"), D(0), D(0))
