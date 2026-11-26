"""D4: the students by cohort (Analiza-Monday backlog 0313; app/desk/cohorts.py) and the reading that feeds it
(app/desk/reading.py, its step 5 and the refresh).

Each rule the backlog and Chava's answer of 26.11 set, on hand-made readings in the reading's shape:
1. every voucher status, by index, lands in its part - or is waived, or out - and an empty or unknown one has its own line;
2. a waived student has no balance; a student out of the count (by the voucher, the enrollment's Status, or two cohorts)
   is listed and in no sum of the line;
3. a student who paid nothing owes the whole price; the helper's balance row "<student> | השלמת תשלום" is paid money; a
   refund is netted exactly as the desk's own rows net it;
4. a cohort without a clean price, or cancelled, is listed with why and no money;
5. the sums tie: the line is its students' sums, and expected = paid + balance - overpaid, through random cohorts;
and the reading: the enrollments of the cohorts with a price are read with the new columns, never a NEVER_READ one, and a
refresh holds the students a full reading holds.
"""
from __future__ import annotations

import io
import json
import random
from datetime import timedelta
from decimal import Decimal
from urllib.parse import quote

import openpyxl
import pytest

from app.desk import cohorts as ch
from app.desk import reading as rd
from app.desk import view as fd
from app.monday.client import NEVER_READ
from tests.desk_fixtures import TODAY, lead, sc, served, snap
from tests.test_desk import SC, World, desk, reader_of, rel, session_token, status  # noqa: F401 - `desk` is the fixture

D = Decimal
STATUS = {"0": "Registered", "1": "נרשם", "2": "פעיל", "3": "At Risk", "4": "סיים", "5": "עבר לקורס אחר",
          "6": "ביטל השתתפות", "7": "Frozen", "8": "ביקשה החזר"}
COHORT_STATUS = {"2": "בוטל", "3": "פעיל", "6": "סגור", "7": "בסגירה"}


def _st(row: dict, col: str, idx: str, labels: dict) -> None:
    row[col] = labels.get(idx, "תווית חדשה") if idx else ""
    row[col + "#"] = idx


def enr(id_, cohort="C1", voucher="", status_="1", person=None, name=None, cohorts=None) -> dict:
    """An Enrollments row as the reading holds it: its cohort link, voucher status and Status by index, its person."""
    r = {"id": str(id_), "name": name or "סטודנט/ית %s" % id_, rd.EN["asked"]: "",
         rd.EN["cohort"]: [str(c) for c in cohorts] if cohorts is not None else ([str(cohort)] if cohort else []),
         rd.EN["person"]: [str(person)] if person else []}
    _st(r, rd.EN["voucher"], voucher, fd.VOUCHER)
    _st(r, rd.EN["status"], status_, STATUS)
    return r


def coh(id_="C1", code="A700", price="10,000", roster=(), status_="3", start="2026-08-01") -> dict:
    """A Cohorts row: its price as typed, its link to its enrollments, its Status by index."""
    r = {"id": str(id_), "name": "%s | קורס | 01.08.26" % code, rd.CO["code"]: code, rd.CO["flow"]: "שוברים משרד העבודה",
         rd.CO["dates"]: "%s - 2026-12-01" % start, rd.CO["external"]: "קורס", rd.CO["price"]: price,
         rd.CO["roster"]: [str(x) for x in roster]}
    _st(r, rd.CO["status"], status_, COHORT_STATUS)
    return r


def view_of(payments=(), cohorts=(), enrollments=(), leads=()) -> dict:
    return fd.build_view(snap(list(payments), cohorts=list(cohorts), enrollments=list(enrollments), leads=list(leads)),
                         TODAY)


def part_of(payments=(), cohorts=(), enrollments=(), leads=()) -> dict:
    return view_of(payments, cohorts, enrollments, leads)["b2c"]["cohorts"]


def line(part: dict, code: str = "A700") -> dict:
    return next(x for x in part["cohorts"] if x["code"] == code)


def student(ln: dict, id_) -> dict:
    return next(s for s in ln["students_list"] if s["id"] == str(id_))


def one(voucher="", status_="1", paid=None, price="10,000", **kw) -> tuple:
    """A cohort with one student (enrollment E1) and, when given, one payment of `paid` on that enrollment."""
    payments = [sc(1, paid=paid, enr=["E1"], **kw)] if paid is not None else []
    part = part_of(payments, [coh(price=price, roster=["E1"])], [enr("E1", voucher=voucher, status_=status_)])
    return part, (line(part) if part["cohorts"] else None)


# ---- 1. every voucher status in its part ------------------------------------------------------------------------------
PLACE = {"17": ("counted", "not_submitted"), "0": ("counted", "submitted"), "1": ("counted", "approved"),
         "8": ("counted", "hq"), "9": ("counted", "hq"), "4": ("counted", "settled"), "2": ("waived", ""),
         "3": ("waived", ""), "6": ("out", ""), "7": ("out", ""), "10": ("out", ""), "5": ("counted", "no_status"),
         "": ("counted", "no_status"), "31": ("counted", "no_status")}


@pytest.mark.parametrize("voucher", sorted(PLACE))
def test_every_voucher_status_lands_in_its_part_by_index(voucher):
    _part, ln = one(voucher=voucher, paid="4000")
    s = student(ln, "E1")
    standing, part = PLACE[voucher]
    assert (s["standing"], s["part"]) == (standing, part)
    assert s["voucher_idx"] == voucher and s["paid"] == D("4000")
    if standing == "counted":
        # the balance in its part, and in no other
        assert s["balance"] == D("6000") and ln["part_" + part] == D("6000") and ln["balance"] == D("6000")
        assert all(ln["part_" + k] == 0 for k in ch.PARTS if k != part)
        assert ln["students"] == 1 and ln["expected"] == D("10000") and ln["paid"] == D("4000")
    else:
        assert s["balance"] == 0 and ln["balance"] == 0 and ln["students"] == 0 and ln["expected"] == 0
        assert ln["paid"] == 0 and ln[standing] == 1 and ln[standing + "_paid"] == D("4000")


def test_every_live_voucher_index_is_placed_and_the_parts_are_chavas_in_her_order():
    # the runbook's live set and 6-9 since: each index has its place, decided by index - never by its text
    live = {"17", "0", "1", "4", "2", "3", "10", "6", "7", "8", "9", "5"}
    assert live == set(fd.VOUCHER) and live <= set(PLACE)
    assert set(ch.PART_OF) | set(ch.WAIVED) | set(ch.OUT) | {"5"} == live
    assert list(ch.PARTS)[:4] == ["approved", "submitted", "hq", "not_submitted"]
    assert ch.PARTS["approved"]["label"] == "אושר - ממתין לתשלום" and ch.PARTS["not_submitted"]["label"] == "טרם הוגש"
    assert ch.PARTS["hq"]["label"] == "בטיפול מטה / ממתין לתקציב" and ch.PARTS["no_status"]["label"] == "בלי סטטוס שובר"
    assert fd.VOUCHER["8"] == "בטיפול מטה" and fd.VOUCHER["9"] == "ממתין לתקציב"
    # a label renamed in monday is still placed by its index
    part = part_of([], [coh(roster=["E1"])], [dict(enr("E1", voucher="1"), **{rd.EN["voucher"]: "שם חדש"})])
    assert student(line(part), "E1")["part"] == "approved"


def test_an_empty_grey_or_unknown_voucher_status_has_its_own_line_and_is_never_hidden():
    rows = [enr("E1", voucher=""), enr("E2", voucher="5"), enr("E3", voucher="31"), enr("E4", voucher="1")]
    ln = line(part_of([], [coh(roster=["E1", "E2", "E3", "E4"])], rows))
    assert ln["part_no_status"] == D("30000") and ln["part_approved"] == D("10000") and ln["owing"]["no_status"] == 3
    said = {s["id"]: s["voucher"] for s in ln["students_list"]}
    assert said == {"E1": "ריק", "E2": fd.UNNAMED_LABEL, "E3": "תווית חדשה", "E4": "אושר - ממתין לתשלום"}
    assert student(ln, "E3")["notes"] == [ch.NOTES["voucher_unknown"]] and student(ln, "E2")["notes"] == []


# ---- 2. waived, and out of the count -----------------------------------------------------------------------------------
@pytest.mark.parametrize("voucher", ["2", "3"])
def test_a_rejected_voucher_is_waived_no_balance_and_not_in_the_expected(voucher):
    rows = [enr("E1", voucher=voucher), enr("E2", voucher="1")]
    ln = line(part_of([sc(1, paid="4000", enr=["E1"])], [coh(roster=["E1", "E2"])], rows))
    s = student(ln, "E1")
    assert s["standing"] == "waived" and s["balance"] == 0 and s["price"] is None and s["paid"] == D("4000")
    assert s["said"] == 'ויתור: "%s"' % fd.VOUCHER[voucher]
    assert ln["students"] == 1 and ln["expected"] == D("10000") and ln["balance"] == D("10000") and ln["paid"] == 0
    assert ln["waived"] == 1 and ln["waived_paid"] == D("4000")


@pytest.mark.parametrize("how", ["voucher_6", "voucher_7", "voucher_10", "status_0", "status_5", "status_6", "status_7",
                                 "status_8", "status_empty", "status_unknown", "two_cohorts"])
def test_a_student_out_of_the_count_is_listed_with_why_and_in_no_sum(how):
    kind, _, value = how.partition("_")
    out = (enr("E1", voucher=value) if kind == "voucher"
           else enr("E1", voucher="1", cohorts=["C1", "C2"]) if kind == "two"
           else enr("E1", voucher="1", status_={"empty": "", "unknown": "31"}.get(value, value)))
    rows = [out, enr("E2", voucher="17")]
    part = part_of([sc(1, paid="3000", enr=["E1"])], [coh(roster=["E1", "E2"]), coh("C2", "A701", roster=["E1"])], rows)
    ln = line(part)
    s = student(ln, "E1")
    assert s["standing"] == "out" and s["balance"] == 0 and s["said"].startswith(ch.OUT_WORD + ": ")
    assert ln["students"] == 1 and ln["expected"] == D("10000") and ln["paid"] == 0 and ln["balance"] == D("10000")
    assert ln["out"] == 1 and ln["out_paid"] == (0 if kind == "two" else D("3000"))
    if kind == "two":
        # in both cohorts' lists, in neither's count; its payment names two cohorts, so it is counted for no one
        assert s["said"] == "%s: %s" % (ch.OUT_WORD, ch.OUT_WHY["cohorts"])
        assert student(line(part, "A701"), "E1")["standing"] == "out" and ln["shared"] == {"count": 1, "money": D("3000")}
    elif kind == "status":
        label = {"empty": "ריק", "unknown": '"תווית חדשה"'}.get(value, '"%s"' % STATUS.get(value, ""))
        assert s["said"] == "%s: Status %s" % (ch.OUT_WORD, label)


def test_the_enrollment_statuses_in_the_count_are_the_helpers_in_set():
    assert list(ch.ENROLLMENT_IN) == ["1", "2", "3", "4"]
    assert list(ch.ENROLLMENT_IN.values()) == ["נרשם", "פעיל", "At Risk", "סיים"]
    for idx in ("1", "2", "3", "4"):
        _p, ln = one(voucher="1", status_=idx)
        assert ln["students"] == 1


# ---- 3. what a student paid, and owes ----------------------------------------------------------------------------------
def test_a_student_who_paid_nothing_owes_the_whole_price():
    # E2 is in no payment: only the cohort's link to its enrollments names it
    rows = [enr("E1", voucher="1"), enr("E2", voucher="1")]
    ln = line(part_of([sc(1, paid="4000", enr=["E1"])], [coh(price="12000", roster=["E1", "E2"])], rows))
    assert student(ln, "E2")["paid"] == 0 and student(ln, "E2")["balance"] == D("12000")
    assert student(ln, "E2")["payments"] == [] and ln["part_approved"] == D("20000") and ln["owing"]["approved"] == 2


def test_the_helpers_balance_row_is_paid_money_and_a_settled_student_owes_nothing():
    deposit = sc(1, paid="4000", enr=["E1"])
    balance = sc(2, paid="6000", enr=["E1"], receipt="0", invoice="", ctype="השלמת תשלום")
    balance["name"] = "דנה לוי | השלמת תשלום"
    ln = line(part_of([deposit, balance], [coh(roster=["E1"])], [enr("E1", voucher="4", name="דנה לוי")]))
    s = student(ln, "E1")
    assert s["paid"] == D("10000") and s["balance"] == 0 and s["payments"] == ["1", "2"] and s["part"] == "settled"
    assert ln["paid"] == D("10000") and ln["balance"] == 0 and ln["part_settled"] == 0


def test_a_settled_voucher_whose_payments_fall_short_is_said_in_its_own_part():
    ln = line(part_of([sc(1, paid="4000", enr=["E1"])], [coh(roster=["E1"])], [enr("E1", voucher="4")]))
    assert ln["part_settled"] == D("6000") and ln["part_approved"] == 0
    assert ch.columns_for([ln])[-2]["key"] == "part_no_status" and "part_settled" in [c["key"] for c in ch.columns_for([ln])]
    quiet = line(part_of([sc(1, paid="10000", enr=["E1"])], [coh(roster=["E1"])], [enr("E1", voucher="4")]))
    assert "part_settled" not in [c["key"] for c in ch.columns_for([quiet])]


@pytest.mark.parametrize("kw, net", [
    ({"st": "1"}, "4000"),                                   # שולם
    ({"st": "9"}, "4000"),                                   # ביקש החזר: the money is still in
    ({"st": "4"}, "0"),                                      # תשלום הוחזר, no amount typed: all of it went back
    ({"st": "4", "refund": "4000"}, "0"),
    ({"st": "5", "refund": "1000"}, "3000"),                 # הוחזר חלקי
    ({"st": "5", "refund": "9000"}, "0"),                    # a refund above what came in is capped
    ({"st": "0"}, "0"), ({"st": "2"}, "0"), ({"st": "3"}, "0"), ({"st": ""}, "0"),
    ({"st": "1", "paid": ""}, "0"),                          # שולם without an amount: no money, and said
    ({"st": "31"}, "0"),                                     # a status the desk does not know: no money, and said
])
def test_a_refund_is_netted_as_the_desk_nets_it(kw, net):
    kw = dict({"paid": "4000"}, **kw)
    v = view_of([sc(1, enr=["E1"], **kw)], [coh(roster=["E1"])], [enr("E1", voucher="1")])
    s = student(line(v["b2c"]["cohorts"]), "E1")
    row = next(r for r in v["b2c"]["rows"] if r["id"] == "1")
    assert s["paid"] == D(net) == row["net"] == row["money_in"] - row["refunded"]   # the desk's own money, reused
    assert s["balance"] == D("10000") - D(net)
    assert (ch.NOTES["payment_uncounted"] in s["notes"]) == (kw.get("paid") == "" or kw["st"] == "31")


def test_a_person_with_two_enrollments_is_one_student_and_two_statuses_are_no_status():
    rows = [enr("E1", voucher="1", person="P1"), enr("E2", voucher="1", person="P1", status_="6"),
            enr("E3", voucher="17", person="P2"), enr("E4", voucher="1", person="P2")]
    payments = [sc(1, paid="4000", enr=["E1"]), sc(2, paid="1000", enr=["E2"]), sc(3, paid="2000", enr=["E3"])]
    ln = line(part_of(payments, [coh(roster=["E1", "E2", "E3", "E4"])], rows))
    assert [s["id"] for s in ln["students_list"]] == ["E1", "E3"] and ln["students"] == 2
    one_ = student(ln, "E1")
    # one person: both enrollments' payments, the counted one's voucher (E2 is cancelled, so its status says nothing)
    assert one_["enrollments"] == ["E1", "E2"] and one_["paid"] == D("5000") and one_["part"] == "approved"
    assert ch.NOTES["enrollments"] in one_["notes"]
    two = student(ln, "E3")
    assert two["part"] == "no_status" and two["voucher"] == "טרם הוגש / אושר - ממתין לתשלום"
    assert ch.NOTES["voucher_conflict"] in two["notes"] and ln["part_no_status"] == D("8000")


def test_more_paid_than_the_price_is_said_and_never_nets_another_students_balance():
    rows = [enr("E1", voucher="1"), enr("E2", voucher="1")]
    ln = line(part_of([sc(1, paid="12000", enr=["E1"])], [coh(roster=["E1", "E2"])], rows))
    assert student(ln, "E1")["balance"] == 0 and student(ln, "E1")["overpaid"] == D("2000")
    assert ch.NOTES["overpaid"] in student(ln, "E1")["notes"]
    assert ln["part_approved"] == D("10000") and ln["overpaid"] == D("2000")
    assert ln["expected"] == ln["paid"] + ln["balance"] - ln["overpaid"]


def test_a_payment_of_two_students_is_counted_for_no_one_and_said():
    rows = [enr("E1", voucher="1", person="P1"), enr("E2", voucher="1", person="P2")]
    ln = line(part_of([sc(1, paid="4000", enr=["E1", "E2"])], [coh(roster=["E1", "E2"])], rows))
    assert ln["paid"] == 0 and ln["shared"] == {"count": 1, "money": D("4000")} and ln["balance"] == D("20000")


def test_a_payment_the_desk_files_by_its_lead_is_said_on_its_cohort_and_counted_for_no_student():
    payments = [sc(1, paid="4000", lead=["L1"]), sc(2, paid="4000", enr=["E1"])]
    ln = line(part_of(payments, [coh(roster=["E1"])], [enr("E1", voucher="1")], [lead("L1", "C1")]))
    assert ln["unlinked"] == {"count": 1, "money": D("4000")} and ln["paid"] == D("4000") and ln["students"] == 1


def test_the_enrollments_own_link_decides_and_the_cohorts_link_is_checked_against_it():
    # E2 is in the cohort's link but its own link is another cohort; E3 names the cohort, though the cohort's link lags;
    # E4 is in the link, and monday did not return it
    rows = [enr("E1", voucher="1"), enr("E2", voucher="1", cohort="C2"), enr("E3", voucher="17")]
    ln = line(part_of([], [coh(roster=["E1", "E2", "E4"])], rows))
    assert sorted(s["id"] for s in ln["students_list"]) == ["E1", "E3"]
    assert ln["roster_elsewhere"] == 1 and ln["roster_missing"] == 1


# ---- 4. a cohort without a price -------------------------------------------------------------------------------------
@pytest.mark.parametrize("price, status_, why", [("", "3", "empty"), ("  ", "3", "empty"), ("10-12 אלף", "3", "unreadable"),
                                                 ("10,000 / 12,000", "3", "unreadable"), ("#", "3", "unreadable"),
                                                 ("10 000", "3", "unreadable"), ("0", "3", "zero"),
                                                 ("10,000", "2", "cancelled")])
def test_a_cohort_without_a_clean_price_or_cancelled_is_listed_with_why_and_no_money(price, status_, why):
    part = part_of([sc(1, paid="4000", enr=["E1"])], [coh(price=price, status_=status_, roster=["E1"])],
                   [enr("E1", voucher="1")])
    assert part["cohorts"] == [] and len(part["unpriced"]) == 1
    u = part["unpriced"][0]
    assert u["why"] == why and u["why_text"] == ch.UNPRICED_WHY[why] and u["price_text"] == price.strip()
    assert not {"expected", "paid", "balance", "students_list", "price"} & set(u)       # no money, no students
    assert part["totals"]["count"] == 0 and part["totals"]["balance"] == 0


@pytest.mark.parametrize("text, value", [("10,000", "10000"), ("12000", "12000"), (" 15,000 ", "15000"), ("10,000 ₪", "10000"),
                                         ("₪12,000", "12000"), ("9,999.5", "9999.5"), ("1,234,567", "1234567"),
                                         ('12,000 ש"ח', "12000"), ("１２０００", "12000")])
def test_a_price_is_one_clean_number_as_typed(text, value):
    assert ch.price_of(text) == (D(value), "")


@pytest.mark.parametrize("text, why", [(None, "empty"), ("", "empty"), ("₪", "empty"), ("0", "zero"), ("0.00", "zero"),
                                       ("10,00", "unreadable"), ("10.000", "unreadable"), ("1,0000", "unreadable"),
                                       ("-5000", "unreadable"), ("12000 + 3000", "unreadable"), ("כ-12000", "unreadable"),
                                       ("10,000.505", "unreadable"), ("٤٠٠٠", "unreadable")])
def test_a_price_that_is_not_one_clean_number_is_never_guessed(text, why):
    assert ch.price_of(text) == (None, why)


# ---- 5. the sums tie, and the order ------------------------------------------------------------------------------------
def random_part(rng: random.Random) -> tuple:
    """Random cohorts, students and payments: every voucher and Status, persons with two enrollments, shared payments."""
    cohorts, enrollments, payments = [], [], []
    vouchers = sorted(PLACE)
    for c in range(1, 5):
        ids = []
        for i in range(rng.randint(0, 7)):
            e = "E%d%02d" % (c, i)
            ids.append(e)
            enrollments.append(enr(e, cohort="C%d" % c, voucher=rng.choice(vouchers),
                                   status_=rng.choice(["1", "1", "2", "3", "4", "6", "", "5"]),
                                   person=rng.choice([None, "P%d" % rng.randint(1, 30)]),
                                   cohorts=rng.choice([None] * 9 + [["C%d" % c, "C%d" % (c % 4 + 1)]])))
        cohorts.append(coh("C%d" % c, "A70%d" % c, price=rng.choice(["10,000", "12000", "15,000", "9,999.5", ""]),
                           roster=ids, start="2026-%02d-01" % rng.randint(1, 12)))
    held = [e["id"] for e in enrollments]
    for p in range(1, 40):
        st = rng.choice(["1", "1", "1", "4", "5", "9", "0", "3", "31"])
        payments.append(sc(p, st=st, paid=rng.choice(["4000", "6000", "3950.5", "", "100"]),
                           refund=rng.choice(["", "", "500", "9000"]),
                           enr=rng.sample(held, min(len(held), rng.choice([0, 1, 1, 1, 2])))))
    return payments, cohorts, enrollments


def test_the_sums_tie_through_random_cohorts():
    rng = random.Random(20261126)
    seen = {k: 0 for k in ("counted", "waived", "out", "overpaid", "shared")}
    for _ in range(150):
        payments, cohorts, enrollments = random_part(rng)
        v = view_of(payments, cohorts, enrollments)
        part, rows = v["b2c"]["cohorts"], {r["id"]: r for r in v["b2c"]["rows"]}
        assert part["checks"]["ties"] is True
        for ln in part["cohorts"]:
            counted = [s for s in ln["students_list"] if s["standing"] == "counted"]
            assert ln["students"] == len(counted) and ln["expected"] == ln["price"] * len(counted)
            assert ln["paid"] == sum((s["paid"] for s in counted), D(0))
            for k in ch.PARTS:
                assert ln["part_" + k] == sum((s["balance"] for s in counted if s["part"] == k), D(0))
            assert ln["balance"] == sum((ln["part_" + k] for k in ch.PARTS), D(0))
            assert ln["expected"] == ln["paid"] + ln["balance"] - ln["overpaid"]
            for s in ln["students_list"]:
                # what a student paid is the desk's net of its payments, and its balance the price less it, never below 0
                assert s["paid"] == sum((rows[p]["net"] for p in s["payments"]), D(0))
                if s["standing"] == "counted":
                    assert s["balance"] == max(ln["price"] - s["paid"], D(0))
                    assert s["overpaid"] == max(s["paid"] - ln["price"], D(0))
                else:
                    assert s["balance"] == 0 and s["part"] == ""
                seen[s["standing"]] += 1
                seen["overpaid"] += bool(s["overpaid"])
            seen["shared"] += ln["shared"]["count"]
        total = ch.cohort_totals(part["cohorts"])
        assert part["totals"] == total and total["balance"] == sum((x["balance"] for x in part["cohorts"]), D(0))
        # every payment is counted for at most one student of one cohort
        counted_ids = [p for ln in part["cohorts"] for s in ln["students_list"] for p in s["payments"]]
        assert len(counted_ids) == len(set(counted_ids))
    assert all(n >= 20 for n in seen.values()), seen


def test_a_broken_line_is_said_by_the_check():
    part = part_of([sc(1, paid="4000", enr=["E1"])], [coh(roster=["E1"])], [enr("E1", voucher="1")])
    ln = line(part)
    assert ch._ties(ln)
    for field, value in (("paid", D("4001")), ("part_approved", D("5999")), ("expected", D("9000")), ("students", 2),
                         ("balance", D("1"))):
        assert not ch._ties(dict(ln, **{field: value})), field


def test_the_cohorts_come_money_that_should_have_come_in_first_and_the_students_by_part():
    cohorts = [coh("C1", "A701", roster=["E1"], start="2026-09-01"), coh("C2", "A702", roster=["E2", "E3"]),
               coh("C3", "A703", roster=["E4"], start="2026-10-01"), coh("C4", "A704", roster=["E5"], start="2026-11-01")]
    rows = [enr("E1", "C1", voucher="17"), enr("E2", "C2", voucher="1"), enr("E3", "C2", voucher="3"),
            enr("E4", "C3", voucher="17"), enr("E5", "C4", voucher="1")]
    part = part_of([sc(1, paid="4000", enr=["E5"])], cohorts, rows)
    # approved money first (A702 10,000, A704 6,000), then the most still to come; on a tie, the newest cohort first
    assert [x["code"] for x in part["cohorts"]] == ["A702", "A704", "A703", "A701"]
    rows2 = [enr("E1", voucher="2"), enr("E2", voucher="17"), enr("E3", voucher="1"), enr("E4", voucher="1"),
             enr("E5", voucher="6"), enr("E6", voucher="0")]
    ln = line(part_of([sc(1, paid="9000", enr=["E3"])], [coh(roster=["E1", "E2", "E3", "E4", "E5", "E6"])], rows2))
    assert [s["id"] for s in ln["students_list"]] == ["E4", "E3", "E6", "E2", "E1", "E5"]


def test_the_search_and_the_columns():
    cohorts = [coh("C1", "A701", roster=["E1"]), coh("C2", "A702", roster=["E2"]), coh("C3", "A703", price="")]
    rows = [enr("E1", "C1", voucher="1", name="רונית כהן"), enr("E2", "C2", voucher="1", name="משה לוי")]
    part = part_of([], cohorts, rows)
    assert [x["code"] for x in ch.filter_cohorts(part, "")] == ["A701", "A702"]
    assert [x["code"] for x in ch.filter_cohorts(part, " a702 ")] == ["A702"]
    assert [x["code"] for x in ch.filter_cohorts(part, "כהן")] == ["A701"]           # by a student's name
    assert ch.filter_cohorts(part, "קורס זה לא קיים") == [] and ch.filter_cohorts(None, "") == []
    assert [x["code"] for x in ch.filter_unpriced(part, "a703")] == ["A703"] and ch.filter_unpriced(part, "a701") == []
    keys = [c["key"] for c in ch.columns_for(part["cohorts"])]
    assert keys == ["cohort", "price", "students", "expected", "paid", "part_approved", "part_submitted", "part_hq",
                    "part_not_submitted", "part_no_status", "balance"]
    assert [c["key"] for c in part["columns"]] == keys[:9] + ["part_settled"] + keys[9:]


def test_the_view_carries_the_cohorts_to_a_students_member_only_and_the_payments_rows_keep_their_cohort_id():
    v = view_of([sc(1, paid="4000", enr=["E1"])], [coh(roster=["E1"])], [enr("E1", voucher="1")])
    assert v["b2c"]["rows"][0]["cohort_id"] == "C1" and v["b2c"]["cohorts"]["cohorts"][0]["id"] == "C1"
    assert fd.visible_part(v, b2c=False, b2b=True)["b2c"] is None
    shown = fd.visible_part(v, b2c=True, b2b=False)["b2c"]["cohorts"]
    assert shown["cohorts"][0]["students_list"][0]["id"] == "E1"
    # the legend of the view: every part in Chava's words, and the words the view computes
    terms = {x["term"] for x in shown["legend"]["items"]}
    assert {p["label"] for p in ch.PARTS.values()} <= terms
    assert {ch.EXPECTED_WORD, ch.PAID_WORD, ch.BALANCE_WORD, ch.STUDENTS_WORD, ch.WAIVED_WORD, ch.OUT_WORD} <= terms
    said = {x["term"]: x["text"] for x in shown["legend"]["items"]}
    assert "כסף שכבר היה צריך להיכנס" in said[ch.PARTS["approved"]["label"]]
    assert "צפי תוך שבועיים" in said[ch.PARTS["submitted"]["label"]] and "צפי תוך חודשיים" in said[ch.PARTS["hq"]["label"]]
    assert "כמה כסף תלוי שם" in said[ch.PARTS["not_submitted"]["label"]]


def test_the_payments_check_still_counts_the_enrollments_the_payments_link_to():
    # the reading asks the cohorts' enrollments too since D4; "enrollments missing" stays the payments' own
    s = snap([sc(1, paid="4000", enr=["E1", "E9"])], cohorts=[coh(roster=["E1", "E2"])],
             enrollments=[enr("E1", voucher="1"), enr("E2", voucher="1")])
    s["asked"] = {"enrollments": ["E1", "E2", "E9"], "leads": [], "cohorts": ["C1"], "deals": [], "accounts": []}
    v = fd.build_view(s, TODAY)
    assert v["checks"]["enrollments_linked"] == 2 and v["checks"]["enrollments_found"] == 1
    assert v["data_notes"]["enrollments_missing"] == 1


# ---- the reading ------------------------------------------------------------------------------------------------------
ID_NUMBER = "039482716"


def cohort_world() -> World:
    """Three payments, and the three cohorts of their enrollments: 401 with a price (its students 201, 203 and 204, 203
    and 204 in no payment), 402 without one (202, 205), 403 with a price and cancelled (206)."""
    w = World()
    w.add(SC, 101, "תלמידה-אלף", status=status("שולם", 1), paid="4000", enrollment=rel(201), lead=rel(301))
    w.add(SC, 102, "תלמיד-בית", status=status("שולם", 1), paid="4000", enrollment=rel(202), lead=rel(302))
    w.add(SC, 103, "תלמיד-גימל", status=status("שולם", 1), paid="4000", enrollment=rel(206), lead=rel())
    for e, c, v in ((201, 401, 1), (203, 401, 17), (204, 401, 0), (202, 402, 1), (205, 402, 1), (206, 403, 1)):
        w.add(rd.ENROLLMENTS_BOARD, e, "הרשמה-%d" % e, cohort=rel(c), voucher=status(fd.VOUCHER[str(v)], v),
              status=status("נרשם", 1), person=rel(900 + e))
    w.add(rd.ENROLLMENTS_BOARD, 206, "הרשמה-206", cohort=rel(403), voucher=status("אושר - ממתין לתשלום", 1),
          status=status("נרשם", 1), person=rel(906))
    w.add(rd.LEADS_BOARD, 301, "ליד-אלף", cohort=rel(), rep="")
    w.add(rd.LEADS_BOARD, 302, "ליד-בית", cohort=rel(), rep="")
    w.add(rd.COHORTS_BOARD, 401, "מחזור-מחיר", code="A401", price="10,000", roster=rel(201, 203, 204),
          status=status("פעיל", 3))
    w.add(rd.COHORTS_BOARD, 402, "מחזור-בלי", code="A402", price="", roster=rel(202, 205), status=status("פעיל", 3))
    w.add(rd.COHORTS_BOARD, 403, "מחזור-בוטל", code="A403", price="12,000", roster=rel(206), status=status("בוטל", 2))
    return w


def test_the_reading_reads_the_enrollments_of_the_cohorts_with_a_price_with_the_new_columns():
    w = cohort_world()
    # monday answers an enrollment's ID number (the Directory's mirror) unasked: never kept
    w.extra_cells = {"203": {"lookup_mm2kg3gw": {"text": ID_NUMBER}}}
    r = rd.read_desk(reader_of(w))
    assert r["error"] is None and r["complete"] is True
    # step 5: 203 and 204 by id, in one request more; 205 (no price) and 403's (cancelled) never
    assert r["asked"]["enrollments"] == ["201", "202", "203", "204", "206"] and r["calls"] == 8
    assert sorted(e["id"] for e in r["enrollments"]) == ["201", "202", "203", "204", "206"]
    assert r["checks"]["enrollments"]["asked"] == 5 and r["checks"]["enrollments"]["rows"] == 5
    assert ["203", "204"] in [ids for b, ids in w.by_id_reads() if b == rd.ENROLLMENTS_BOARD]
    asked = [v for q, v in w.queries if "items(ids:" in q and "columns(ids:" not in q]
    by_board = {}
    for v in asked:
        item = w.items[str(v["ids"][0])]
        by_board.setdefault(item["board"], set()).update(v["cols"])
    assert {rd.EN["status"], rd.EN["person"], rd.EN["voucher"], rd.EN["cohort"]} <= by_board[rd.ENROLLMENTS_BOARD]
    assert {rd.CO["price"], rd.CO["roster"]} <= by_board[rd.COHORTS_BOARD]
    assert not set().union(*by_board.values()) & NEVER_READ
    text = json.dumps(r, ensure_ascii=False)
    assert ID_NUMBER not in text and "lookup_mm2kg3gw" not in text
    e = next(x for x in r["enrollments"] if x["id"] == "203")
    assert e[rd.EN["status"] + "#"] == "1" and e[rd.EN["person"]] == ["1103"]
    # and the desk: 401 with its three students, 402 and 403 listed without money
    part = fd.build_view(r, TODAY)["b2c"]["cohorts"]
    ln = part["cohorts"][0]
    assert ln["code"] == "A401" and ln["students"] == 3 and ln["paid"] == D("4000") and ln["expected"] == D("30000")
    assert (ln["part_approved"], ln["part_not_submitted"], ln["part_submitted"]) == (D("6000"), D("10000"), D("10000"))
    assert [(u["code"], u["why"]) for u in part["unpriced"]] == [("A402", "empty"), ("A403", "cancelled")]


def test_a_reading_with_no_cohort_with_a_price_reads_what_it_read_before():
    w = cohort_world()
    w.set(401, price="", touch=False)
    r = rd.read_desk(reader_of(w))
    assert r["asked"]["enrollments"] == ["201", "202", "206"] and r["calls"] == 7


def test_the_body_schema_moved_so_a_reading_kept_before_d4_is_read_whole():
    assert rd.BODY_SCHEMA.startswith("4:")
    for key, col in (("cohorts", rd.CO["price"]), ("cohorts", rd.CO["roster"]), ("enrollments", rd.EN["status"]),
                     ("enrollments", rd.EN["person"])):
        assert col in rd.ROW_COLUMNS[key] and col in rd.CHECKED_COLUMNS[key]


def test_a_missing_new_column_ends_the_reading_instead_of_reading_as_empty():
    w = cohort_world()
    w.columns_missing = {rd.COHORTS_BOARD: [rd.CO["price"]]}
    r = rd.read_desk(reader_of(w))
    assert r["error"] == "unknown_column" and rd.CO["price"] in r["detail"]


def held_and_delta(w: World) -> tuple:
    reader = reader_of(w)
    return reader, rd.read_desk(reader)


def cohorts_of(snapshot: dict) -> dict:
    return fd.build_view(snapshot, TODAY)["b2c"]["cohorts"]


def test_a_refresh_reads_the_cohorts_with_a_price_again_and_a_student_newly_linked_by_id():
    w = cohort_world()
    reader, held = held_and_delta(w)
    # a new student in 401: the enrollment is new (touched), and the cohort's link to it moves - the cohort is NOT touched,
    # since whether monday moves its time then is not verified
    w.add(rd.ENROLLMENTS_BOARD, 207, "הרשמה-207", cohort=rel(401), voucher=status("טרם הוגש", 17),
          status=status("נרשם", 1), person=rel(907))
    w.touch(207)
    w.set(401, roster=rel(201, 203, 204, 207), touch=False)
    reads = len(w.by_id_reads())
    got = rd.read_delta(reader, held)
    read = w.by_id_reads()[reads:]
    assert (rd.COHORTS_BOARD, ["401"]) in read and (rd.ENROLLMENTS_BOARD, ["207"]) in read
    assert got["asked"]["enrollments"] == ["201", "202", "203", "204", "206", "207"] and got["complete"] is True
    assert cohorts_of(got) == cohorts_of(rd.read_desk(reader))
    assert line(cohorts_of(got), "A401")["students"] == 4


def test_a_refresh_sees_a_voucher_a_status_and_a_price_change_as_a_full_reading_does():
    w = cohort_world()
    reader, held = held_and_delta(w)
    w.set(203, voucher=status("אושר - ממתין לתשלום", 1))
    w.set(204, status=status("ביטל השתתפות", 6))
    w.set(402, price="12000")                         # a price typed: its students are read in the same refresh
    got = rd.read_delta(reader, held)
    assert "205" in got["asked"]["enrollments"]
    part = cohorts_of(got)
    assert part == cohorts_of(rd.read_desk(reader))
    assert line(part, "A401")["part_approved"] == D("16000") and line(part, "A401")["out"] == 1
    assert line(part, "A402")["students"] == 2


def test_a_student_who_moved_to_another_cohort_leaves_at_the_next_refresh():
    w = cohort_world()
    reader, held = held_and_delta(w)
    w.set(204, cohort=rel(402))                           # the enrollment's own link moves, the reflections with it
    w.set(401, roster=rel(201, 203), touch=False)
    w.set(402, roster=rel(202, 204, 205), touch=False)
    got = rd.read_delta(reader, held)
    assert "204" not in got["asked"]["enrollments"] and line(cohorts_of(got), "A401")["students"] == 2
    assert cohorts_of(got) == cohorts_of(rd.read_desk(reader))


def roster_world(rng: random.Random) -> World:
    """Payments, enrollments in four cohorts (two with a price), every voucher and Status, persons."""
    w = World()
    for c in range(1, 5):
        w.add(rd.COHORTS_BOARD, 400 + c, "מחזור-%d" % c, code="A40%d" % c, price=["10,000", "", "12000", ""][c - 1],
              status=status("פעיל", 3), roster=rel())
    for e in range(1, 13):
        c = 400 + rng.randint(1, 4)
        v = rng.choice(sorted(k for k in fd.VOUCHER if k))
        w.add(rd.ENROLLMENTS_BOARD, 200 + e, "הרשמה-%d" % e, cohort=rel(c), voucher=status(fd.VOUCHER[v], int(v)),
              status=status("נרשם", 1), person=rel(900 + rng.randint(1, 10)))
    sync_rosters(w)
    for p in range(1, 9):
        w.add(SC, 100 + p, "תלמיד-%d" % p, status=status("שולם", 1), paid=str(rng.randint(10, 60) * 100),
              enrollment=rel(*rng.sample(range(201, 213), rng.choice([0, 1, 1, 1]))), lead=rel())
    return w


def sync_rosters(w: World) -> None:
    """Every cohort's link to its enrollments as the reflection of their own links - without touching the cohort."""
    for cid, item in w.items.items():
        if item["board"] == rd.COHORTS_BOARD:
            ids = sorted(i for i, x in w.items.items() if x["board"] == rd.ENROLLMENTS_BOARD and x["state"] == "active"
                         and cid in (x["cells"].get(rd.EN["cohort"]) or {}).get("linked_item_ids", []))
            item["cells"][rd.CO["roster"]] = rel(*ids)


def test_through_random_edits_a_refresh_holds_the_students_a_full_reading_holds(monkeypatch):
    monkeypatch.setattr(rd, "DELTA_MARGIN", timedelta(0))
    rng = random.Random(20261126)
    w = roster_world(rng)
    h = rd.HeldReading(lambda: reader_of(w), ttl_s=0)
    h.get()
    cohorts = [400 + c for c in range(1, 5)]

    def enrollments():
        return sorted(int(i) for i, x in w.items.items() if x["board"] == rd.ENROLLMENTS_BOARD and x["state"] == "active")

    def voucher():
        v = rng.choice(sorted(k for k in fd.VOUCHER if k) + ["31"])
        w.set(rng.choice(enrollments()), voucher=status(fd.VOUCHER.get(v, "תווית חדשה"), int(v)))

    def standing():
        idx = rng.choice(["1", "2", "6", "7", "5"])
        w.set(rng.choice(enrollments()), status=status(STATUS[idx], int(idx)))

    def moved():
        w.set(rng.choice(enrollments()), cohort=rel(rng.choice(cohorts)))
        sync_rosters(w)

    def joined():
        i = 1000 + len(w.items)
        w.add(rd.ENROLLMENTS_BOARD, i, "חדש-%d" % i, cohort=rel(rng.choice(cohorts)), voucher=status("טרם הוגש", 17),
              status=status("נרשם", 1), person=rel(900 + rng.randint(1, 10)))
        w.touch(i)
        sync_rosters(w)

    def price():
        w.set(rng.choice(cohorts), price=rng.choice(["", "10,000", "15000", "לא ידוע"]))

    def cancelled():
        w.set(rng.choice(cohorts), status=rng.choice([status("בוטל", 2), status("פעיל", 3)]))

    def paid():
        p = rng.choice([100 + i for i in range(1, 9)])
        w.set(p, enrollment=rel(*rng.sample(enrollments(), rng.choice([0, 1, 1]))), paid=str(rng.randint(10, 60) * 100))

    edits = [voucher, standing, moved, joined, price, cancelled, paid]
    students = 0
    for _ in range(80):
        for edit in rng.sample(edits, rng.choice([1, 2, 3])):
            edit()
        kept, last = h.get()
        assert last["error"] is None and kept["kind"] == "delta", h.health()["last_codes"]
        full = rd.read_desk(reader_of(w))
        assert kept["asked"] == full["asked"]
        mine, theirs = cohorts_of(kept), cohorts_of(full)
        assert mine == theirs and mine["checks"]["ties"] is True
        students += sum(len(x["students_list"]) for x in mine["cohorts"])
    assert students > 200


# ---- the routes: the totals and the file of the view, gated as the students' list ------------------------------------
def cohort_snapshot() -> dict:
    """A701 (10,000): E1 approved, 4,000 paid; E2 settled, its deposit and the helper's balance row; E3 waived. A702
    (12,000): E4 not submitted, 4,000 paid; E5 submitted, nothing paid. A703 without a price."""
    balance = sc(3, paid="6000", enr=["E2"], receipt="0", invoice="", ctype="השלמת תשלום")
    balance["name"] = "סטודנט/ית E2 | השלמת תשלום"
    payments = [sc(1, paid="4000", enr=["E1"]), sc(2, paid="4000", enr=["E2"]), balance, sc(4, paid="4000", enr=["E4"])]
    cohorts = [coh("C1", "A701", roster=["E1", "E2", "E3"]), coh("C2", "A702", price="12000", roster=["E4", "E5"]),
               coh("C3", "A703", price="")]
    enrollments = [enr("E1", "C1", voucher="1", name="רונית כהן"), enr("E2", "C1", voucher="4"),
                   enr("E3", "C1", voucher="2"), enr("E4", "C2", voucher="17"), enr("E5", "C2", voucher="0"),
                   enr("E6", "C3", voucher="1")]
    return snap(payments, cohorts=cohorts, enrollments=enrollments)


def _auth(user: int = 1) -> dict:
    return {"Authorization": "Bearer " + session_token(user)}


def test_the_cohorts_totals_are_the_servers_for_the_list_as_filtered_and_for_its_members_only(desk, monkeypatch):  # noqa: F811
    served(monkeypatch, cohort_snapshot())
    body = desk.get(session_token(1)).json()
    taken = body["taken_at"]
    part = body["view"]["b2c"]["cohorts"]
    assert [x["code"] for x in part["cohorts"]] == ["A701", "A702"] and part["checks"]["ties"] is True
    assert body["view"]["b2c"]["cohorts"]["totals"]["balance"] == "26000"          # money as strings, never a float

    def get(qs, user=1):
        return desk.client.get("/api/desk/totals?" + qs + "&taken=" + quote(taken), headers=_auth(user))
    t = get("tab=b2c_cohorts").json()
    assert t["count"] == 2 and t["taken_at"] == taken
    assert {k: t["sums"][k] for k in ("expected", "paid", "balance", "part_approved", "part_submitted",
                                       "part_not_submitted", "part_settled", "students", "waived")} == {
        "expected": "44000", "paid": "18000", "balance": "26000", "part_approved": "6000", "part_submitted": "12000",
        "part_not_submitted": "8000", "part_settled": "0", "students": 4, "waived": 1}
    t = get("tab=b2c_cohorts&q=%D7%9B%D7%94%D7%9F").json()                            # "כהן": a student of A701
    assert t["count"] == 1 and t["sums"]["balance"] == "6000"
    assert get("tab=b2c_cohorts", user=3).json() == {"error": "not_a_board_member"}   # B2B Charge only
    assert get("tab=b2c_cohorts", user=2).status_code == 200                          # Student Charge only
    assert get("tab=b2c_cohorts&state=tasks").json() == {"error": "bad_filter"}
    assert get("tab=b2c_cohorts&period=2026-10").json() == {"error": "bad_filter"}


def test_the_cohorts_file_is_the_view_as_filtered_its_students_sum_to_their_cohort_and_its_unpriced_listed(desk, monkeypatch):  # noqa: F811
    served(monkeypatch, cohort_snapshot())
    desk.get(session_token(1))

    def link(user=1, **body):
        return desk.client.post("/api/desk/export-link", headers=_auth(user), json={"tab": "b2c_cohorts", **body})
    r = link()
    assert r.status_code == 200
    f = desk.client.get(r.json()["url"])
    assert f.status_code == 200 and 'filename="finance-by-cohort-' in f.headers["content-disposition"]
    wb = openpyxl.load_workbook(io.BytesIO(f.content))
    assert wb.sheetnames == ["לפי מחזור", "פרטים", "סטודנטים", "בלי מחיר"]
    ws = wb["לפי מחזור"]
    assert ws.sheet_view.rightToLeft is True and ws.freeze_panes == "A2"
    heads = [x.value for x in ws[1]]
    assert heads[:8] == ["מחזור", "שם המחזור", fd.FLOW_LABEL, "מחיר", ch.STUDENTS_WORD, ch.EXPECTED_WORD, ch.PAID_WORD,
                         ch.PARTS["approved"]["label"]]
    # "אושר - יתרה שולמה, וחסר בתקבולים" holds nothing here, so it is not a column, as on the page
    assert ch.PARTS["settled"]["label"] not in heads and ch.PARTS["no_status"]["label"] in heads
    lines = {ws.cell(row=i, column=1).value: dict(zip(heads, [x.value for x in ws[i]])) for i in (2, 3)}
    assert lines["A701"][ch.PARTS["approved"]["label"]] == 6000 and lines["A701"][ch.BALANCE_WORD] == 6000
    assert lines["A701"]["מחיר"] == 10000 and lines["A701"][ch.STUDENTS_WORD] == 2 and lines["A701"][ch.WAIVED_WORD] == 1
    assert lines["A702"][ch.EXPECTED_WORD] == 24000 and lines["A702"][ch.PARTS["submitted"]["label"]] == 12000
    money = [h for h in heads if h in (ch.EXPECTED_WORD, ch.PAID_WORD, ch.BALANCE_WORD)
             or h in [p["label"] for p in ch.PARTS.values()]]
    # the students: each money column of a cohort's students sums to its line
    st = wb["סטודנטים"]
    sheads = [x.value for x in st[1]]
    srows = [r for r in (dict(zip(sheads, [x.value for x in st[i]])) for i in range(2, st.max_row + 1)) if r["מזהה במאנדיי"]]
    # under them, what the sheet is: its students sum to their cohort's line
    assert any("שווים לשורת המחזור" in str(st.cell(row=i, column=1).value) for i in range(2, st.max_row + 1))
    assert {s["מזהה במאנדיי"] for s in srows} == {"E1", "E2", "E3", "E4", "E5"}
    for code, ln in lines.items():
        for h in money:
            assert sum(s[h] or 0 for s in srows if s["מחזור"] == code) == (ln[h] or 0), (code, h)
    waived = next(s for s in srows if s["מזהה במאנדיי"] == "E3")
    assert waived[ch.PAID_WORD] is None and waived["בחשבון"].startswith(ch.WAIVED_WORD)
    unpriced = wb["בלי מחיר"]
    assert [unpriced.cell(row=2, column=c).value for c in (1, 5)] == ["A703", "אין מחיר"]
    info = {row[0].value: row[1].value for row in wb["פרטים"].iter_rows()}
    assert info["רשימה"] == ch.VIEW_WORD and info["שורות"] == 2 and "שימו לב" not in info
    assert "039482716" not in json.dumps([[c.value for c in row] for s in wb for row in s.iter_rows()], default=str)
    # a search: the cohorts the page shows for it, and the file's name says it
    f = desk.client.get(link(q="כהן").json()["url"])
    wb = openpyxl.load_workbook(io.BytesIO(f.content))
    assert [wb["לפי מחזור"].cell(row=i, column=1).value for i in (2, 3)] == ["A701", None]
    assert "%D7%97%D7%99%D7%A4%D7%95%D7%A9" in f.headers["content-disposition"]           # "חיפוש" in the Hebrew name
    # gated as the students' list; no state, period or company
    assert link(user=3).status_code == 403 and link(state="tasks").status_code == 400 and link(period="2026-10").status_code == 400


def test_a_cohorts_line_that_does_not_tie_is_never_exported_as_complete(desk, monkeypatch):  # noqa: F811
    served(monkeypatch, cohort_snapshot())
    real = ch.build_cohorts
    monkeypatch.setattr(ch, "build_cohorts", lambda s, r: dict(real(s, r), checks={"ties": False}))
    desk.get(session_token(1))
    url = desk.client.post("/api/desk/export-link", headers=_auth(), json={"tab": "b2c_cohorts"}).json()["url"]
    info = {row[0].value: row[1].value for row in openpyxl.load_workbook(
        io.BytesIO(desk.client.get(url).content))["פרטים"].iter_rows()}
    assert "לא שלמה" in info["שימו לב"]
