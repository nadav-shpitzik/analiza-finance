"""The finance desk's build (app/desk/view.py, Analiza-Monday backlog 0301 D2): analiza-metrics tests/test_finance.py at
`da671b2`, its tests of the computation ported, and the pure ones of tests/test_finance_actions.py with them.

The computation is held to the rules the desk promises: every row placed once and the totals tied; money in Decimal;
statuses by their monday index; partly paid money never shown as open; a refund above its payment capped and flagged; a
charge's cohort by item id - its enrollment's, else its lead's. What changed in the move is held too: no row carries a
bank account or an ID number, and no customer a business number, whatever a row holds."""
from __future__ import annotations

from collections import Counter
from datetime import date
from decimal import Decimal

import pytest

from app.desk import reading as rd
from app.desk import view as fd
from app.monday import client as monday
from app.monday.client import NEVER_READ
from tests.desk_fixtures import (TODAY, D, acct, b2, b2row, cohort, cohort_of, deal, enrollment, lead, reading_of, row_of,
                                 sc, snap, view)


# --- parsing ----------------------------------------------------------------------------------------

def test_terms_count_from_the_end_of_the_demand_month():
    assert fd.due_by_terms(date(2026, 9, 10), "שוטף+45") == (date(2026, 11, 14), True)
    assert fd.due_by_terms(date(2026, 2, 3), "שוטף") == (date(2026, 2, 28), True)
    assert fd.due_by_terms(date(2026, 9, 10), "") == (date(2026, 9, 10), False)


def test_the_lead_is_asked_for_only_when_the_enrollment_names_no_cohort():
    rows = [sc(1, lead=["L1"], enr=["E1"]), sc(2, lead=["L2"], enr=["E2"]), sc(3, lead=["L3"]), sc(4)]
    enrollments = [enrollment("E1", 10), enrollment("E2")]          # E2 names no cohort
    assert fd.enrollment_ids(rows) == ["E1", "E2"]
    assert fd.lead_ids(rows) == ["L1", "L2", "L3"]                    # every lead is read: its name is the payment's
    assert fd.leads_deciding(rows, enrollments) == ["L2", "L3"]
    assert fd.cohort_ids(enrollments, [lead("L2", 11), lead("L3", 10)]) == ["10", "11"]
    # one definition for what is read and what is built from it: the reading's own
    assert fd.leads_deciding is rd.leads_deciding and fd.SC is rd.SC and fd.BOARDS is rd.BOARDS


def test_the_reading_keeps_the_label_and_its_index_apart():
    """analiza-metrics held its own reader's `flatten` to this; the view builds from the reading's (app/monday/client.py)."""
    row = monday.flatten({"id": 1, "name": "x", "created_at": "2026-11-01T00:00:00Z", "column_values": [
        {"id": "color_mm1kxpvt", "text": "שולם", "index": 1, "label": "שולם"},
        {"id": "color_mm4cq1hw", "text": "", "index": None, "label": None},
        {"id": "board_relation_mm2whb5s", "text": "", "linked_item_ids": [77]},
        {"id": "lookup_mm1kr19p", "text": None, "display_value": "A101 | x"},
        {"id": "numeric_mm1kgbnt", "text": "4000"}]})
    assert row["color_mm1kxpvt"] == "שולם" and row["color_mm1kxpvt#"] == "1"
    assert row["color_mm4cq1hw#"] == "" and row["board_relation_mm2whb5s"] == ["77"]
    assert row["lookup_mm1kr19p"] == "A101 | x" and row["numeric_mm1kgbnt"] == "4000"


# --- students ---------------------------------------------------------------------------------------

def test_money_is_decimal_and_a_full_refund_without_amount_refunds_what_was_paid():
    r = fd._b2c_row(sc(1, st="4", paid="4000"), TODAY, Counter())
    assert (r["money_in"], r["refunded"], r["net"]) == (D("4000"), D("4000"), D("0"))
    assert isinstance(r["money_in"], Decimal) and "refund_amount_missing" in r["flags"]


def test_a_partial_refund_without_an_amount_is_flagged_not_guessed():
    r = fd._b2c_row(sc(1, st="5", paid="4000"), TODAY, Counter())
    assert (r["money_in"], r["refunded"], r["net"]) == (D("4000"), D("0"), D("4000"))
    assert "refund_amount_missing" in r["flags"]


def test_a_refund_above_the_payment_is_capped_and_flagged():
    r = fd._b2c_row(sc(1, st="4", paid="4000", refund="15000"), TODAY, Counter())
    assert (r["refunded"], r["net"]) == (D("4000"), D("0")) and "refund_exceeds_paid" in r["flags"]


def test_statuses_are_read_by_index_and_a_renamed_label_is_drift():
    labels = Counter()
    r = fd._b2c_row(dict(sc(1), **{fd.SC["status"]: "שולם בהצלחה"}), TODAY, labels)
    assert r["money_in"] == D("4000") and labels[("drift", fd.SC["status"])] == 1
    odd = fd._b2c_row(sc(2, st="77"), TODAY, labels)
    assert odd["money_in"] == 0 and "status_unknown" in odd["flags"] and "exception" not in odd["flags"]
    assert labels[("unknown", fd.SC["status"])] == 1
    sch = fd._b2c_row(sc(3, st="3"), TODAY, Counter())
    assert "scholarship" in sch["flags"] and "exception" not in sch["flags"]


def test_receipt_not_relevant_is_not_waiting():
    assert "receipt_pending" in fd._b2c_row(sc(1, receipt="0"), TODAY, Counter())["flags"]
    assert "receipt_pending" not in fd._b2c_row(sc(2, receipt="2"), TODAY, Counter())["flags"]


def test_no_payment_carries_a_bank_account_whatever_its_row_holds():
    """analiza-metrics: the account was never read with a reading's row, and a live read (`attach_bank`) put it on the
    rows of "החזר לביצוע" only. Here the server never reads it (NEVER_READ): every row's `bank` is None, a refund to
    execute's too, even one whose row held the four columns; the page reads them in the browser as the person."""
    r = fd._b2c_row(sc(1, credit_approved="3900", planned="2026-11-01", bank="12", bank_branch="345",
                       bank_account="678", bank_holder="ש"), TODAY, Counter())
    assert {"refund_to_execute", "refund_overdue"} <= set(r["flags"]) and r["bank"] is None
    v = view([sc(1, credit_approved="3900", planned="2026-11-01", bank="12", bank_account="998877665"), sc(2),
              sc(3, st="2", credit_approved="100")])
    assert row_of(v, 1)["state"] == "refund_to_execute"
    assert all(x["bank"] is None for x in v["b2c"]["rows"])
    assert not hasattr(fd, "attach_bank") and "bank_unread" not in fd.B2C_NOTES
    assert "998877665" not in str(fd.visible_part(v, True, True))


def test_credit_note_flag_only_from_the_day_the_column_exists():
    assert "credit_ref_missing" not in fd._b2c_row(sc(1, st="4", refund_date="2026-10-01"), TODAY, Counter())["flags"]
    assert "credit_ref_missing" in fd._b2c_row(sc(2, st="4", refund_date="2026-11-02"), TODAY, Counter())["flags"]


def test_enrollment_cohort_wins_then_lead_then_unassigned_and_every_row_lands_once():
    rows = [sc(1, lead=["L1"], enr=["E1"]),   # the enrollment says A101; its lead (never read) does not matter
            sc(2, lead=["L2"]),               # no enrollment: the lead's A102
            sc(3, lead=["L3"]),               # a lead in two cohorts: ambiguous
            sc(4),                            # no link at all
            sc(5, enr=["E5"])]                # a cohort monday no longer returns
    v = view(rows, cohorts=[cohort(10, "A101"), cohort(11, "A102")],
             enrollments=[enrollment("E1", 10), enrollment("E5", 99)], leads=[lead("L2", 11), lead("L3", 10, 11)])
    assert len(cohort_of(v, "A101")) == 1 and len(cohort_of(v, "A102")) == 1
    assert sorted(x["cohort_from"] for x in cohort_of(v, "")) == ["ambiguous", "missing", "no_link"]
    assert {k: v["data_notes"][k] for k in ("cohort_ambiguous", "cohort_no_link", "cohort_missing")} == \
        {"cohort_ambiguous": 1, "cohort_no_link": 1, "cohort_missing": 1}
    assert v["checks"]["ok"] is True and v["checks"]["cohorts_linked"] == 3 and v["checks"]["cohorts_found"] == 2


def test_a_linked_enrollment_monday_no_longer_returns_falls_back_to_the_lead_and_is_a_note():
    rows = [sc(1, lead=["L1"], enr=["E1"])]
    v = view(rows, cohorts=[cohort(11, "A102")], leads=[lead("L1", 11)])   # E1 asked, not returned
    assert len(cohort_of(v, "A102")) == 1
    assert v["data_notes"]["enrollments_missing"] == 1 and v["checks"]["ok"] is True


def test_rows_sharing_a_lead_or_enrollment_are_one_student():
    rows = [sc(1, lead=["L1"]),
            sc(2, lead=["L1"], enr=["E1"], ctype="השלמת תשלום", paid="6000"),
            sc(3, enr=["E1"], paid="100")]
    v = view(rows, cohorts=[cohort(10, "A101")], enrollments=[enrollment("E1", 10)], leads=[lead("L1", 10)])
    assert len({x["person"] for x in cohort_of(v, "A101")}) == 1 and v["b2c"]["students"] == 1
    assert v["b2c"]["money_in"] == D("10100")


def test_a_payment_carries_its_enrollments_voucher_status():
    rows = [sc(1, enr=["E1"]), sc(2, enr=["E2"])]
    v = view(rows, cohorts=[cohort(10, "A101")], enrollments=[enrollment("E1", 10, voucher="1"), enrollment("E2", 10, voucher="5")])
    assert (row_of(v, 1)["voucher"], row_of(v, 1)["voucher_idx"]) == ("אושר - ממתין לתשלום", "1")
    assert row_of(v, 2)["voucher_idx"] == "5" and row_of(v, 2)["voucher"] == ""


def test_every_payment_has_one_state_and_the_first_that_fits_wins():
    rows = [sc(1, st="31"),                                                   # an unknown status: exception
            sc(2, credit_approved="500", planned="2026-11-20"),               # paid, refund approved: to execute
            sc(3, st="4", refund_date="2026-11-02", invoice=""),              # refunded, no credit note typed: no state of its own
            sc(4, invoice="", receipt="0"),                                    # paid, the receipt not sent
            sc(5, invoice="", receipt="1"),                                    # the receipt went out: no reference decides
            sc(6, receipt="0"),                                                # a reference, the receipt not sent
            sc(7, st="9"),                                                     # asked for a refund, documents done
            sc(8),                                                             # done
            sc(9, receipt="2"),                                                # a receipt not needed: done
            sc(10, st="4", refund_date="2026-10-01"),                          # refunded before the credit-note column
            sc(11, st="0", receipt="0"), sc(12, st="3", receipt="2"),          # no money, and no receipt went out
            sc(13, st="5", refund_date="2026-10-01")]                          # partly refunded, the amount not typed
    v = view(rows)
    got = {x["id"]: x["state"] for x in v["b2c"]["rows"]}
    assert got == {"1": "exception", "2": "refund_to_execute", "3": "refunded", "4": "receipt_pending",
                   "5": "done", "6": "receipt_pending", "7": "refund_asked", "8": "done", "9": "done",
                   "10": "refunded", "11": "no_money", "12": "no_money", "13": "exception"}
    whole = v["b2c"]["periods"][0]["states"]
    assert sum(x["count"] for x in whole.values()) == 13 and v["checks"]["b2c_states_partition"] is True
    assert whole["refund_to_execute"]["money"] == D("500") and whole["refunded"]["money"] == D("8000")
    assert "credit_ref_missing" in row_of(v, 3)["flags"]                  # the fact stays on the row


def test_a_payment_is_filed_under_its_payment_date_else_the_israeli_day_it_was_created():
    rows = [sc(1, paid_date="2026-10-31", created="2026-11-02T08:00:00Z"),
            sc(2, created="2026-09-30T22:30:00Z")]                       # 01:30 on 1.10 in Israel
    v = view(rows)
    assert (row_of(v, 1)["date"], row_of(v, 1)["date_from"]) == ("2026-10-31", "paid")
    assert (row_of(v, 2)["date"], row_of(v, 2)["month"], row_of(v, 2)["date_from"]) == ("2026-10-01", "2026-10", "created")
    keys = [p["key"] for p in v["b2c"]["periods"]]
    assert keys == ["", "2026-10", "2026-09+2026-10"]
    vat = v["b2c"]["periods"][2]
    assert vat["label"] == "ספטמבר-אוקטובר 2026" and vat["count"] == 2 and vat["kind"] == "vat"


def test_the_list_filter_is_state_period_and_search_and_refuses_what_it_does_not_know():
    rows = [sc(1, invoice="", receipt="0", created="2026-10-05T08:00:00Z"),
            sc(2, created="2026-10-06T08:00:00Z", paid="3950"),
            sc(3, invoice="", receipt="0", created="2026-09-05T08:00:00Z", paid="1200")]
    b = view(rows)["b2c"]
    assert [x["id"] for x in fd.filter_b2c(b, "tasks", "", "")] == ["1", "3"]
    assert [x["id"] for x in fd.filter_b2c(b, "tasks", "2026-10", "")] == ["1"]
    assert [x["id"] for x in fd.filter_b2c(b, "", "", "3,950")] == ["2"]                # an amount, within a shekel
    assert [x["id"] for x in fd.filter_b2c(b, "", "", "תלמיד/ה 3")] == ["3"]
    assert fd.filter_b2c(b, "", "2031-01", "") == []        # a period no reading lists is an empty one, not an error
    with pytest.raises(ValueError):
        fd.filter_b2c(b, "nope", "", "")
    with pytest.raises(ValueError):
        fd.filter_b2c(b, "", "October", "")


def test_a_short_read_is_not_ok():
    assert view([sc(1)], [b2(1)], sc_count=2)["checks"]["ok"] is False
    assert view([sc(1)], [b2(1)], b2_count=2)["checks"]["ok"] is False


def test_voucher_statuses_are_read_by_index_and_a_new_one_is_said():
    rows = [sc(1, enr=["E1"]), sc(2, enr=["E2"])]
    v = view(rows, cohorts=[cohort(10, "A101")],
             enrollments=[enrollment("E1", 10, voucher="4"), enrollment("E2", 10, voucher="31")])
    assert row_of(v, 1)["voucher"] == "אושר - יתרה שולמה"
    # an enrollment's label: said quietly, and never as payments left out of the money (hostile review 10.11)
    assert v["data_notes"]["voucher_label_unknown"] == 1 and "label_unknown_b2c" not in v["data_notes"]
    assert v["b2c"]["money_in"] == D("8000")


def test_the_rep_of_a_payment_with_two_leads_is_each_name_once_and_a_lead_monday_did_not_return_adds_none():
    """analiza-metrics built the reps through its fake reader; here through the reading's own `lead_reps_of`."""
    s = snap([sc(1, lead=["L1", "L2", "L9"]), sc(2)],
             leads=[lead("L1", rep="סבטלנה"), lead("L2", rep="סבטלנה"), lead("L3", rep="גל")])
    got = reading_of(s)
    assert got["lead_reps"] == {"L1": "סבטלנה", "L2": "סבטלנה", "L9": None}
    v = fd.build_view(got, TODAY)
    assert row_of(v, "1")["rep"] == "סבטלנה" and row_of(v, "2")["rep"] == ""
    # a reading without `lead_reps` builds them from the leads it holds, never fails
    assert row_of(fd.build_view(dict(got, lead_reps=None), TODAY), "1")["rep"] == "סבטלנה"


# --- companies --------------------------------------------------------------------------------------

def test_every_company_charge_has_one_state_and_partly_paid_is_never_open_money():
    rows = [b2(1), b2(2, payment="2"), b2(3, payment="0", due=""), b2(4, billing="0", payment="2"),
            b2(5, billing="1", payment="2"), b2(6, billing="7", amount="", payment="2"), b2(7, billing="2"),
            b2(8, payment="2", due=""), b2(9, billing="1", payment="2", due="2026-12-15"), b2(10, payment="2", due="2026-11-09"),
            b2(11, billing="0"),                         # "paid" before it was invoiced: an exception, not a task to issue
            b2(12, payment="2", due="2026-11-30")]      # billed before its demand day: an exception (round 3)
    v = view(b2_rows=rows)
    got = {x["id"]: x["state"] for x in v["b2b"]["rows"]}
    # "לאישור הנהלת חשבונות" (4) and "מאושר לחיוב" (5) are one state, "לחיוב החודש" (Nadav 11.11)
    assert got == {"1": "paid", "2": "overdue", "3": "partial", "4": "to_bill", "5": "to_bill", "6": "pending",
                   "7": "cancelled", "8": "exception", "9": "planned", "10": "open", "11": "exception", "12": "exception"}
    n = v["b2b"]["numbers"]
    assert sum(x["count"] for x in n.values()) == 12 and v["checks"]["b2b_states_partition"] is True
    # open money: overdue (2), not due yet (10), invoiced without a demand day (8) and before it (12) - partly paid never
    assert (v["b2b"]["open"], v["b2b"]["overdue"]) == (D(4000), D(1000))
    # partly paid without a demand day: flagged since QA 1.4, and still "שולם חלקית", never overdue or open money
    assert "billed_no_due" in b2row(v, 3)["flags"] and "overdue" not in b2row(v, 3)["flags"]
    assert v["checks"]["ok"] is True


def test_a_partly_paid_charge_shows_what_is_left_from_the_paid_column_including_vat():
    v = view(b2_rows=[b2(1, payment="0", amount="10000", paid="5000", due="2026-10-01"), b2(2, payment="0")])
    r = b2row(v, 1)
    assert (r["paid_so_far"], r["balance"], r["days_late"]) == (D("5000"), D("6800.00"), 39)
    assert "partial_amount_unknown" in b2row(v, 2)["flags"] and b2row(v, 2)["balance"] is None
    assert (v["b2b"]["numbers"]["partial"]["balance"], v["b2b"]["numbers"]["partial"]["balance_unknown"]) == (D("6800.00"), 1)


def test_arrears_follow_the_customers_terms():
    rows = [b2(1, payment="2", due="2026-09-10")]
    assert b2row(view(b2_rows=rows, accounts=[acct("A1", "שוטף+45")]), 1)["state"] == "open"
    assert b2row(view(b2_rows=rows, accounts=[acct("A1", "")]), 1)["state"] == "overdue"


def test_warnings_before_issuing_an_invoice():
    rows = [b2(1, billing="0", payment="2", amount="12000", due="2026-10-29"), b2(2, amount="96000", invoice="")]
    v = view(b2_rows=rows, deals=[deal("D1", stage=fd.STAGE_NO_CONTRACT, value="96000")])
    assert {"invoice_late", "no_contract", "over_deal_value"} <= set(b2row(v, 1)["flags"])
    assert "billed_no_invoice" in b2row(v, 2)["flags"] and b2row(v, 2)["state"] == "paid"   # a reference decides no state


def test_the_aging_by_customer_lists_only_customers_with_something_open():
    rows = [b2(1, payment="2", due="2026-09-01", account="A1"), b2(2, billing="0", payment="2", account="A1"),
            b2(3, account="A2"), b2(4, payment="0", paid="100", due="2026-10-20", account="A3")]
    v = view(b2_rows=rows, accounts=[acct("A1"), acct("A2"), acct("A3")])
    by = {c["id"]: c for c in v["b2b"]["customers"]}
    assert set(by) == {"A1", "A3"}                         # A2 has only paid charges
    assert (by["A1"]["overdue"], by["A1"]["to_bill"], by["A1"]["oldest_days"]) == (D(1000), D(1000), 69)
    assert (by["A3"]["partial_count"], by["A3"]["oldest_days"]) == (1, 20)
    assert [c["id"] for c in v["b2b"]["customers"]] == ["A1", "A3"]   # the oldest arrears first


def test_the_companies_filter_is_state_customer_and_search():
    rows = [b2(1, payment="2", due="2026-09-01", account="A1", amount="1500.5"), b2(2, billing="0", payment="2", account="A1"),
            b2(3, payment="2", due="2026-08-01", account="A2", invoice="INV-9")]
    b = view(b2_rows=rows, accounts=[acct("A1"), acct("A2")])["b2b"]
    assert [x["id"] for x in fd.filter_b2b(b, "overdue", "", "")] == ["3", "1"]      # the most days late first
    assert [x["id"] for x in fd.filter_b2b(b, "tasks", "A1", "")] == ["1", "2"]      # the oldest demand first
    assert [x["id"] for x in fd.filter_b2b(b, "", "", "1501.49")] == ["1"]
    assert [x["id"] for x in fd.filter_b2b(b, "", "", "inv-9")] == ["3"]
    with pytest.raises(ValueError):
        fd.filter_b2b(b, "nope", "", "")


def test_the_reading_keeps_status_index_zero():
    row = monday.flatten({"id": 1, "name": "x", "created_at": "", "column_values": [
        {"id": "color_mm4bhvta", "text": "שולם חלקית", "index": 0, "label": "שולם חלקית"}]})
    assert row["color_mm4bhvta#"] == "0"
    assert b2row(view(b2_rows=[b2(1, payment="0")]), 1)["state"] == "partial"


def test_a_label_added_in_monday_is_said_and_never_becomes_a_task():
    v = view([sc(1, st="6", paid="5000")], [b2(2, payment="3", due="2026-09-01")])
    assert v["b2c"]["money_in"] == 0 and v["data_notes"]["label_unknown_b2c"] == 1
    assert v["data_notes"]["label_unknown_b2b"] == 1
    row = b2row(v, 2)
    assert "status_unknown" in row["flags"] and "overdue" not in row["flags"] and row["state"] == "exception"
    assert v["b2b"]["open"] == 0                                    # an unknown payment is never chased as open money


def test_a_company_charge_without_an_amount_is_flagged_not_a_silent_zero():
    v = view(b2_rows=[b2(1, billing="0", payment="2", amount=""), b2(2, billing="7", payment="2", amount="")])
    assert "amount_missing" in b2row(v, 1)["flags"] and "amount_missing" not in b2row(v, 2)["flags"]
    assert b2row(v, 1)["state"] == "exception" and v["data_notes"]["amount_missing_b2b"] == 1


def test_a_deal_split_across_two_customers_is_one_deal():
    rows = [b2(1, account="A1"), b2(2, account="A2"), b2(3, account="A2", deal="")]
    v = view(b2_rows=rows, accounts=[acct("A1"), acct("A2")], deals=[deal("D1", payer="A1")])
    assert v["b2b"]["deals"] == 1 and v["data_notes"]["deals_split"] == 1 and v["data_notes"]["payer_mismatch"] == 1


def test_a_linked_deal_monday_no_longer_returns_is_a_note_not_a_short_read():
    s = snap(b2_rows=[b2(1, deal="D9")])
    s["asked"] = {"deals": ["D9"], "accounts": ["A1"]}
    v = fd.build_view(s, TODAY)
    assert v["checks"]["ok"] is True and v["data_notes"]["deals_missing"] == 1


# --- one reader's answer ----------------------------------------------------------------------------

def test_a_tab_the_reader_may_not_see_is_removed_not_hidden():
    v = view([sc(1, invoice="")], [b2(1, billing="0"), b2(2, payment="2", account="A1", deal="D1")])
    part = fd.visible_part(v, b2c=True, b2b=False)
    assert part["tabs"] == {"b2c": True, "b2b": False} and part["b2b"] is None
    assert not any(k.startswith("b2b") or k.startswith("deals") for k in part["checks"])
    assert part["checks"]["ok"] is True
    assert fd.visible_part(v, b2c=False, b2b=True)["b2c"] is None


def test_terms_the_desk_does_not_know_are_a_note():
    v = view(b2_rows=[b2(1)], accounts=[acct("A1", "שוטף+90")])
    assert v["data_notes"]["terms_unknown"] == 1


def test_label_drift_stays_on_its_own_tab():
    rows = [dict(b2(1), **{fd.B2["billing"]: "חויב סופית"})]
    v = view([sc(1)], rows)
    assert v["label_drift"] == {fd.B2["billing"]: 1}
    only_students = fd.visible_part(v, b2c=True, b2b=False)
    assert only_students["label_drift"] == {} and "label_drift_b2b" not in only_students["data_notes"]
    assert fd.visible_part(v, b2c=False, b2b=True)["data_notes"]["label_drift_b2b"] == 1


def test_the_search_compares_amounts_in_agorot_within_a_shekel_and_reads_references():
    rows = [sc(1, paid="3950.5", invoice="INV-77"), sc(2, paid="8192.88", st="4", refund_date="2026-10-01",
                                                      credit_ref="ZK-5")]
    b = view(rows)["b2c"]

    def ids(q):
        return [x["id"] for x in fd.filter_b2c(b, "", "", q)]
    assert ids("3951.49") == ["1"] and ids("3951.5") == [] and ids("3,950") == ["1"]
    assert ids("8191.89") == ["2"] and ids("8191.88") == []
    assert ids("inv-77") == ["1"] and ids("zk-5") == ["2"]
    assert ids("٣٩٥٠") == [] and ids("３９５０") == []          # only ASCII digits are an amount, as in the page


# --- the hostile review of the companies tab, 10.11 ---------------------------------------------------

def test_a_charge_without_a_customer_is_a_customer_of_its_own_not_every_customer():
    rows = [b2(1, payment="2", due="2026-09-01", account=""), b2(2, payment="2", due="2026-09-01", account="A1")]
    v = view(b2_rows=rows)
    none = [c for c in v["b2b"]["customers"] if c["id"] == fd.NO_ACCOUNT]
    assert len(none) == 1 and none[0]["name"] == "ללא חברה"
    assert [x["id"] for x in fd.filter_b2b(v["b2b"], "", fd.NO_ACCOUNT, "")] == ["1"]


def test_a_fully_paid_partial_charge_is_no_arrears_and_one_without_an_amount_has_no_balance():
    rows = [b2(1, payment="0", amount="1000", paid="1180", due="2026-07-01", account="A1"),
            b2(2, payment="2", amount="9000", due="2026-10-01", account="A2"),
            b2(3, payment="0", amount="", paid="5000", account="A3")]
    v = view(b2_rows=rows, accounts=[acct("A1"), acct("A2"), acct("A3")])
    assert (b2row(v, 1)["balance"], b2row(v, 1)["days_late"]) == (D("0.00"), None)
    assert v["b2b"]["customers"][0]["id"] == "A2"                   # the one who owes comes first
    assert b2row(v, 3)["balance"] is None and b2row(v, 3)["state"] == "exception"


def test_a_customers_terms_are_the_accounts_whatever_its_rows_say():
    v = view(b2_rows=[b2(1, payment="2", due="", account="A1")], accounts=[acct("A1", "שוטף+60")])
    assert v["b2b"]["customers"][0]["terms"] == "שוטף+60"


# --- the ledger, the aging bands, the totals (backlog 0281, Nadav 10.11 "תעשה את 1 2 4 6") ----------------------

def test_open_money_is_aged_by_days_past_due_and_the_bands_add_up_to_it():
    assert [fd.age_band(due, TODAY) for due in ("2026-11-20", "2026-11-09", "2026-10-30", "2026-09-30", "2026-08-31",
                                                "2026-08-10", "")] == \
        ["current", "current", "d1_30", "d31_60", "d61_90", "d90", "no_due"]
    rows = [b2(1, payment="2", due="2026-11-20", account="A1"), b2(2, payment="2", due="2026-10-30", account="A1", amount="500"),
            b2(3, payment="2", due="2026-07-01", account="A1", amount="250"), b2(4, payment="2", due="", account="A1", amount="75"),
            b2(5, payment="0", paid="100", due="2026-07-01", account="A1")]          # partly paid: not open money
    v = view(b2_rows=rows)
    c = v["b2b"]["customers"][0]
    assert c["aging"] == {"current": D(1000), "d1_30": D(500), "d31_60": 0, "d61_90": 0, "d90": D(250), "no_due": D(75)}
    assert sum(c["aging"].values()) == c["open"] == v["b2b"]["open"]


def test_the_ledger_is_one_customers_charges_oldest_first_with_what_is_owed_so_far():
    rows = [b2(1, payment="2", due="2026-10-01", account="A1", amount="300"), b2(2, account="A1", due="2026-09-01", amount="999"),
            b2(3, payment="2", due="2026-08-01", account="A1", amount="200"), b2(4, payment="2", due="2026-08-15", account="A2")]
    v = view(b2_rows=rows, accounts=[acct("A1"), acct("A2")])
    ledger = fd.filter_b2b(v["b2b"], "", "A1", "")
    assert [(x["id"], x["ledger_open"]) for x in ledger] == [("3", D(200)), ("2", D(200)), ("1", D(500))]
    mine = v["b2b"]["by_customer"]["A1"]
    assert (mine["count"], mine["open"], mine["numbers"]["paid"]["count"]) == (3, D(500), 1)


def test_a_total_over_partly_paid_charges_without_a_typed_amount_is_no_total():
    v = view(b2_rows=[b2(1, payment="0", amount="1000", paid="500"), b2(2, payment="0", amount="2000")])
    t = fd.list_totals("b2b", fd.filter_b2b(v["b2b"], "partial", "", ""))
    assert (t["paid_so_far"], t["balance"], t["partial_unknown"]) == (None, None, 1)
    t = fd.list_totals("b2b", fd.filter_b2b(v["b2b"], "partial", "", "")[:0] + [b2row(v, 1)])
    assert (t["paid_so_far"], t["balance"]) == (D("500"), D("680.00"))


def test_the_aging_bands_turn_at_30_60_and_90_days():
    days = {"2026-10-10": "d1_30", "2026-10-09": "d31_60", "2026-09-10": "d31_60", "2026-09-09": "d61_90",
            "2026-08-11": "d61_90", "2026-08-10": "d90"}
    assert {due: fd.age_band(due, TODAY) for due in days} == days


# --- round 3 (Nadav 11.11): the refund's two dates, one state before billing, sentences, monday's own names ----------

def test_the_legal_last_day_is_the_request_day_plus_14_calendar_days_as_in_innas_app():
    assert fd.REFUND_DUE_DAYS == 14 and fd.LAST_DAY_LABEL == "מועד אחרון להחזר"
    assert fd.refund_last_day(date(2026, 10, 15)) == date(2026, 10, 29)
    assert fd.refund_last_day(date(2026, 12, 25)) == date(2027, 1, 8)
    assert fd.refund_last_day(date(2028, 2, 20)) == date(2028, 3, 5)          # 2028 has a 29 February
    assert fd.refund_last_day(None) is None
    # monday's own title of date_mm8cp8gj since 11.11, and not the legal day's name
    assert fd.PLANNED_LABEL == "מועד העברה מתוכנן" and fd.SC["planned"] == "date_mm8cp8gj"
    assert fd.EN["asked"] == "date_mm8csw5y"


def test_a_payment_carries_its_enrollments_request_day_and_a_refund_to_execute_is_flagged_by_both_dates():
    rows = [sc(1, enr=["E1"], credit_approved="1000", planned="2026-11-15"),   # last day 03.11 passed, planned ahead
            sc(2, enr=["E2"], credit_approved="500", planned="2026-11-01"),    # last day 15.11 ahead, planned passed
            sc(3, enr=["E3"], credit_approved="700", planned="2026-11-20"),    # the enrollment has no request day
            sc(4, credit_approved="700"),                                      # no enrollment at all
            sc(5, enr=["E5"], st="9"),                                         # asked, not approved: no refund flags
            sc(6, enr=["E6"], credit_approved="100", planned="2026-11-20")]    # last day is today: not passed
    v = view(rows, enrollments=[enrollment("E1", asked="2026-10-20"), enrollment("E2", asked="2026-11-01"),
                                enrollment("E3"), enrollment("E5", asked="2026-11-01"),
                                enrollment("E6", asked="2026-10-26")])
    got = {i: (row_of(v, i)["asked_date"], row_of(v, i)["refund_last_day"]) for i in range(1, 7)}
    assert got == {1: ("2026-10-20", "2026-11-03"), 2: ("2026-11-01", "2026-11-15"), 3: ("", ""), 4: ("", ""),
                   5: ("2026-11-01", "2026-11-15"), 6: ("2026-10-26", "2026-11-09")}
    flags = {i: set(row_of(v, i)["flags"]) & {"refund_overdue", "refund_last_day_passed", "refund_last_day_unknown"}
             for i in range(1, 7)}
    assert flags == {1: {"refund_last_day_passed"}, 2: {"refund_overdue"}, 3: {"refund_last_day_unknown"},
                     4: {"refund_last_day_unknown"}, 5: set(), 6: set()}
    assert [fd.is_refund_row(row_of(v, i)) for i in range(1, 7)] == [True] * 6
    assert fd.is_refund_row(row_of(view([sc(7)]), 7)) is False


def test_a_payment_on_two_enrollments_counts_from_the_earlier_request_day():
    v = view([sc(1, enr=["E1", "E2"], credit_approved="10")],
             enrollments=[enrollment("E1", asked="2026-11-02"), enrollment("E2", asked="2026-10-30")])
    assert row_of(v, 1)["refund_last_day"] == "2026-11-13"


def test_refunds_to_execute_come_unknown_last_day_first_then_passed_oldest_first_then_by_planned_date():
    def refund(i, enr, planned):
        return sc(i, enr=[enr] if enr else None, credit_approved="100", planned=planned,
                  created="2026-10-%02dT09:00:00Z" % i)
    rows = [refund(1, "E1", "2026-11-10"), refund(2, None, ""), refund(3, "E3", "2026-11-15"), refund(4, None, "2026-11-30"),
            refund(5, "E5", "2026-11-30"), refund(6, "E6", ""), refund(7, "E7", "2026-11-20"), refund(8, "E8", "2026-11-30")]
    v = view(rows, enrollments=[enrollment("E1", asked="2026-11-06"),     # last day 20.11: by planned 10.11
                                enrollment("E3", asked="2026-10-18"),     # passed 01.11
                                enrollment("E5", asked="2026-10-16"),     # passed 30.10: the oldest
                                enrollment("E6", asked="2026-11-08"),     # ahead, no planned date: last
                                enrollment("E7", asked="2026-11-01"),     # ahead, planned 20.11
                                enrollment("E8", asked="2026-11-05")])    # ahead, planned 30.11
    order = [x["id"] for x in fd.filter_b2c(v["b2c"], "refund_to_execute", "", "")]
    assert order == ["4", "2", "5", "3", "1", "7", "8", "6"]


def test_the_two_before_billing_labels_are_one_state_and_it_keeps_its_warnings():
    rows = [b2(1, billing="0", payment="2", amount="12000", due="2026-10-29"), b2(2, billing="1", payment="2", due="2026-11-20"),
            b2(3, billing="1", payment="2", due=""), b2(4, billing="0", payment="2", due="2026-12-01")]
    v = view(b2_rows=rows, deals=[deal("D1", stage=fd.STAGE_NO_CONTRACT, value="1000")])
    assert [b2row(v, i)["state"] for i in (1, 2, 3, 4)] == ["to_bill", "to_bill", "to_bill", "planned"]
    assert {"invoice_late", "no_contract", "over_deal_value"} <= set(b2row(v, 1)["flags"])
    assert {"no_contract", "over_deal_value"} <= set(b2row(v, 4)["flags"])          # a planned charge keeps them too
    labels = {k: s["label"] for k, s in fd.B2B_STATES.items()}
    assert (labels["to_bill"], labels["planned"], labels["open"]) == \
        ("לחיוב החודש", "לחיוב בחודש הבא ואילך", "חויב, ממתין לפירעון")
    assert "approval" not in fd.B2B_STATES and "to_invoice" not in fd.B2B_STATES
    assert fd.B2B_TASK_STATES == ["exception", "to_bill", "overdue", "partial"]
    assert v["b2b"]["customers"][0]["to_bill"] == D(12000 + 1000 + 1000)   # both labels add up in one column
    with pytest.raises(ValueError):
        fd.filter_b2b(v["b2b"], "approval", "", "")


def test_a_charge_billed_before_its_demand_day_is_an_exception_and_still_owed():
    rows = [b2(1, payment="2", due="2026-11-10"), b2(2, payment="1", due="2026-12-01"), b2(3, payment="2", due=""),
            b2(4, payment="2", due="2026-11-09"), b2(5, billing="1", payment="2", due="2026-11-10")]
    v = view(b2_rows=rows)
    assert [b2row(v, i)["state"] for i in range(1, 6)] == ["exception", "exception", "exception", "open", "to_bill"]
    assert "billed_before_demand" in b2row(v, 1)["flags"] and "billed_before_demand" in b2row(v, 2)["flags"]
    assert "billed_no_due" in b2row(v, 3)["flags"] and "billed_before_demand" not in b2row(v, 5)["flags"]
    # billed and not paid is money owed, whatever its state: it ages as "טרם הגיע מועד"
    c = v["b2b"]["customers"][0]
    assert (c["aging"]["current"], c["aging"]["no_due"], c["open"]) == (D(2000), D(1000), D(3000))


def test_the_aging_says_what_partly_paid_charges_without_a_paid_amount_bill():
    rows = [b2(1, payment="0", amount="1000", paid="590", account="A1"), b2(2, payment="0", amount="2000", account="A1"),
            b2(3, payment="0", amount="3000.50", account="A1"), b2(4, payment="0", amount="500", account="A2")]
    v = view(b2_rows=rows, accounts=[acct("A1"), acct("A2")])
    by = {c["id"]: c for c in v["b2b"]["customers"]}
    a1 = by["A1"]
    assert (a1["partial_count"], a1["partial_unknown"], a1["partial_unknown_amount"], a1["partial_balance"]) == \
        (3, 2, D("5000.50"), D("590.00"))
    t = fd.list_totals("b2b_customers", v["b2b"]["customers"])
    assert (t["partial_count"], t["partial_unknown"], t["partial_unknown_amount"]) == (4, 3, D("5500.50"))


def test_every_state_on_both_tabs_has_its_sentence_and_the_sentence_names_mondays_labels():
    for states in (fd.B2C_STATES, fd.B2B_STATES):
        for key, spec in states.items():
            assert spec["about"].strip(), key
    v = view([sc(1)], [b2(1)])
    for tab in ("b2c", "b2b"):
        assert all(s["about"] for s in v[tab]["states"]) and len(v[tab]["states"]) == len(
            fd.B2C_STATES if tab == "b2c" else fd.B2B_STATES)
    assert '"%s"' % fd.BILLING[fd.B_APPROVAL] in fd.B2B_STATES["to_bill"]["about"]
    assert '"%s"' % fd.BILLING[fd.B_APPROVED] in fd.B2B_STATES["to_bill"]["about"]
    assert '"%s"' % fd.PAY[fd.P_REFUNDED] in fd.B2C_STATES["refunded"]["about"]
    assert '"%s"' % fd.RECEIPT[fd.R_PENDING] in fd.B2C_STATES["receipt_pending"]["about"]
    assert fd.LAST_DAY_LABEL in fd.B2C_STATES["refund_to_execute"]["about"]
    assert fd.PLANNED_LABEL in fd.B2C_STATES["refund_to_execute"]["about"]


def test_the_legend_defines_each_word_the_desk_computes_and_no_monday_column():
    # since QA 2.2 the legend has four parts: the desk's words, the states, the chips, and monday's columns that need a word
    b2c, b2b = fd.legend_terms("b2c"), fd.legend_terms("b2b")
    for word in ("מועד פירעון", "בפיגור", "לחיוב החודש", "יתרה פתוחה", "לפי מועד פירעון", "יתרה לגבייה", "שיבוץ",
                 "לטיפול", "לפי לקוח", "לא בפיגור", "ללא מועד פירעון", "מה לבדוק", "סטטוס", "Account משלם"):
        assert word in b2b, word
    for word in (fd.LAST_DAY_LABEL, "תאריך יצירה", "שיבוץ", "לטיפול", "מחזור", "סטודנטים", "תקבול", "סטודנט/ית",
                 "פרטי חשבון", "עבר", "לפני", "סכום שלא נספר", "נוהל הרשמה", "ליד מקור", "סטטוס שובר משה\"ע"):
        assert word in b2c, word
    words = dict(fd.LEGEND["b2c"])
    assert "14" in words[fd.LAST_DAY_LABEL] and fd.PLANNED_LABEL in words[fd.LAST_DAY_LABEL]
    # "מתוכה בפיגור" is the money of "בפיגור" alone; only the days late are counted on a partly paid charge too
    b2b_words = dict(fd.LEGEND["b2b"])
    assert "חלקית" not in b2b_words["מתוכה בפיגור"] and "חלקית" in b2b_words["ימים בפיגור"]
    # monday's own columns are named, not defined: the planned transfer date has no entry of its own, nor the amount
    assert fd.PLANNED_LABEL not in b2c and "סכום לחיוב (ללא מע\"מ)" not in b2b and "מצב" not in b2c | b2b
    v = view([sc(1)], [b2(1)])
    assert [p["title"] for p in v["b2c"]["legend"]] == list(fd.LEGEND_TITLES)
    assert v["b2c"]["legend"][0]["items"][0].keys() == {"term", "text"}
    assert len(v["b2b"]["legend"][0]["items"]) == len(fd.LEGEND["b2b"])


def test_what_the_desk_says_keeps_the_house_style_no_em_dash_no_arrow():
    texts = [s["label"] + s["about"] for st in (fd.B2C_STATES, fd.B2B_STATES) for s in st.values()]
    texts += [t + x for tab in fd.LEGEND.values() for t, x in tab]
    texts += [t + x for tab in fd.LEGEND_MONDAY.values() for t, x in tab] + list(fd.TASKS_ABOUT.values())
    texts += [t + x for tab in fd.LEGEND_FLAGS.values() for _k, t, x in tab] + list(fd.WHAT_TO_CHECK.values())
    from app.desk import export as fx
    texts += [c[0] for c in fx.B2C_COLUMNS + fx.B2B_COLUMNS] + [fx.ID_NUMBERS_NOT_IN_FILE]
    texts += [fd.aging_file_header({"group": g, "header": h}) for _k, h, g, _kind, _w in fd.AGING_COLUMNS]
    bad = [t for t in texts if "\u2014" in t or "\u2192" in t or "->" in t]
    assert not bad, bad


def test_the_view_is_schema_finance_3():
    assert fd.VIEW_SCHEMA == "finance/3" and view([sc(1)], [b2(1)])["schema"] == "finance/3"


# --- the hostile review of round 3 (11.11) ---------------------------------------------------------------------------

def test_each_states_sentence_holds_for_the_rows_at_its_edges():
    # "חריג, לבדיקה" (students): a refund above its payment sits here, and its money IS counted (capped) - the period's
    # figure even says how much of "הוחזרו" is in it. The sentence may not call such rows uncounted.
    v = view([sc(1, st="4", paid="4000", refund="5000")])
    assert row_of(v, 1)["state"] == "exception"
    assert v["b2c"]["periods"][0]["states"]["exception"]["money_in"] == D(4000) == v["b2c"]["money_in"]
    assert "לספור" not in fd.B2C_STATES["exception"]["about"]
    # "חויב, ממתין לפירעון": a company without payment terms (17 of 22 on 09.11), on its demand day, is in this
    # state with its due date TODAY - neither "the end of the month plus the terms" nor "not arrived yet" is true of it
    v = view(b2_rows=[b2(1, payment="2", due=TODAY.isoformat())], accounts=[acct("A1", "")])
    assert (b2row(v, 1)["state"], b2row(v, 1)["due"]) == ("open", TODAY.isoformat())
    about = fd.B2B_STATES["open"]["about"]
    assert "מועד דרישת התשלום עצמו" in about and "עוד לא עבר" in about
    # "ממתין לנתונים": a charge with its amount typed sits here and its tile sums it - "no amount yet" is false of it
    v = view(b2_rows=[b2(1, billing="7", payment="2", amount="5000"), b2(2, billing="", payment="", amount="1200")])
    assert [b2row(v, i)["state"] for i in (1, 2)] == ["pending", "pending"]
    assert v["b2b"]["numbers"]["pending"]["money"] == D(6200)
    assert "אין סכום" not in fd.B2B_STATES["pending"]["about"]


def test_a_payment_says_whether_it_is_linked_to_an_enrollment_at_all():
    v = view([sc(1, credit_approved="500", lead=["L1"]), sc(2, credit_approved="500", enr=["E2"])],
             enrollments=[enrollment("E2")], leads=[lead("L1")])
    assert [row_of(v, i)["enrollment_linked"] for i in (1, 2)] == [False, True]
    assert all("refund_last_day_unknown" in row_of(v, i)["flags"] for i in (1, 2))


# --- QA of slice 1 (11.11, 14:37-15:06): money and work ----------------------------------------------------------------

def test_an_approved_refund_whose_status_carries_no_money_is_an_exception_and_says_why():
    """QA 1.1: 3275077350 - 4,000 to credit, its last day passed on 05.11, and its status turned "יש בעיה". It fell into
    "ללא תקבול" with its amount struck through, out of every work list. 2945713395 - "יש בעיה", 5,000 paid, a 6,000 refund."""
    rows = [sc(1, st="2", paid="4000", credit_approved="4000", planned="2026-11-01", enr=["E1"]),
            sc(2, st="2", paid="5000", refund="6000"),
            sc(3, st="0", credit_approved="100"), sc(4, st="", credit_approved="100"), sc(5, st="3", credit_approved="100"),
            sc(6, st="9", credit_approved="100"),                     # asked, and a credit typed: no approval writes this
            sc(7, st="2", receipt="0"), sc(8, st="0", paid="300", receipt="0")]   # no refund, no receipt: no money, no task
    v = view(rows, enrollments=[enrollment("E1", asked="2026-10-22")])
    assert [row_of(v, i)["state"] for i in range(1, 9)] == ["exception"] * 6 + ["no_money", "no_money"]
    one = row_of(v, 1)
    # the chip says why, and the refund keeps its clock: it is late twice over
    assert {"credit_on_status", "refund_last_day_passed", "refund_overdue"} <= set(one["flags"])
    # analiza-metrics attached a bank account to "החזר לביצוע" only and checked this one got none; here no row gets one
    assert "refund_to_execute" not in one["flags"] and one["bank"] is None
    assert "refund_amount_on_status" in row_of(v, 2)["flags"] and "credit_on_status" not in row_of(v, 2)["flags"]
    assert row_of(v, 2)["refund_typed"] == D(6000)
    assert all("credit_on_status" in row_of(v, i)["flags"] for i in (3, 4, 5, 6))
    # nothing came in on 1-5 by their status, so nothing of theirs is counted in ("ביקש החזר" did bring 6's), and the
    # totals still tie
    assert v["b2c"]["money_in"] == D(4000) and v["checks"]["ok"] is True
    assert "יש סכום לזיכוי" in fd.B2C_STATES["exception"]["about"]


def test_no_label_combination_drops_an_approved_refund_out_of_every_task_list():
    """QA 1.1, the general case: every payment status and every receipt label monday has (and an empty one, and one the
    desk does not know), with "סכום לזיכוי" typed. Until the status says the money went back ("תשלום הוחזר" / "הוחזר
    חלקי"), the refund is somebody's work: it is in a task state. A refund amount or date on a status that says no refund
    is an exception whatever the credit."""
    statuses, receipts = list(fd.PAY) + ["", "77"], list(fd.RECEIPT) + ["", "77"]
    extras = [{}, {"refund": "500"}, {"refund": "0"}, {"refund_date": "2026-11-01"}, {"paid": ""}]
    rows, case = [], {}
    for st in statuses:
        for rc in receipts:
            for credit in ("500", ""):
                for extra in extras:
                    i = len(rows) + 1
                    rows.append(sc(i, st=st, receipt=rc, credit_approved=credit, **extra))
                    case[str(i)] = (st, rc, credit, extra)
    v = view(rows)
    for r in v["b2c"]["rows"]:
        st, rc, credit, extra = case[r["id"]]
        if st in fd.REFUNDED:
            continue                                   # the refund went out: "החזר בוצע", or "חריג" when its amount is off
        if credit:
            assert r["state"] in fd.TASK_STATES, (st, rc, credit, extra, r["state"])
        if (extra.get("refund") not in (None, "0")) or extra.get("refund_date"):
            assert r["state"] == "exception", (st, rc, credit, extra, r["state"])


def test_a_paid_status_with_a_refund_amount_or_date_is_an_exception_that_says_which():
    """QA 1.2: 3274787698 was "שולם" with "סכום החזר" and "תאריך החזר" filled, and showed as a refund to execute."""
    rows = [sc(1, credit_approved="1000", refund="1000", refund_date="2026-11-11", planned="2026-11-15"),
            sc(2, refund="300"), sc(3, refund_date="2026-11-02"), sc(4, refund="0"),
            sc(5, st="9", refund_date="2026-11-02")]
    v = view(rows)
    assert [row_of(v, i)["state"] for i in range(1, 6)] == ["exception", "exception", "exception", "done", "exception"]
    assert {"refund_amount_on_status", "refund_date_on_status"} <= set(row_of(v, 1)["flags"])
    assert set(row_of(v, 2)["flags"]) & {"refund_amount_on_status", "refund_date_on_status"} == {"refund_amount_on_status"}
    assert set(row_of(v, 3)["flags"]) & {"refund_amount_on_status", "refund_date_on_status"} == {"refund_date_on_status"}
    assert row_of(v, 1)["bank"] is None          # never a bank account here: not in "החזר לביצוע", and none read at all
    assert fd.filter_b2c(v["b2c"], "refund_to_execute", "", "") == []


def test_a_typed_zero_refund_is_said_as_zero_not_as_untyped():
    """QA 2.10: 2829373007 said "סכום החזר לא הוקלד" where monday holds 0."""
    rows = [sc(1, st="5", paid="0", refund="0"), sc(2, st="5", paid="4000"), sc(3, st="4", paid="4000", refund="0"),
            sc(4, st="4", paid="4000")]
    v = view(rows)
    assert "refund_amount_zero" in row_of(v, 1)["flags"] and "refund_amount_missing" not in row_of(v, 1)["flags"]
    assert "refund_amount_missing" in row_of(v, 2)["flags"] and "refund_amount_zero" not in row_of(v, 2)["flags"]
    assert [row_of(v, i)["state"] for i in range(1, 5)] == ["exception", "exception", "refunded", "refunded"]
    # "תשלום הוחזר" says all of it went back, a 0 typed or nothing typed alike (the status rules, knowledge ADR-0023)
    assert row_of(v, 3)["refunded"] == row_of(v, 4)["refunded"] == D(4000)
    assert "refund_amount_zero" in row_of(v, 3)["flags"]


def test_a_status_value_without_a_label_is_said_not_shown_empty():
    """QA 2.10: the row's "ערוץ תשלום" held a value with no label in the column, and the page showed it empty."""
    r = sc(1)
    r[fd.SC["channel"]], r[fd.SC["channel"] + "#"] = "", "17"
    plain = sc(2)
    plain[fd.SC["channel"]], plain[fd.SC["channel"] + "#"] = "אשראי", "0"
    v = view([r, plain, sc(3)])
    assert row_of(v, 1)["unnamed"] == ["channel"] and row_of(v, 2)["unnamed"] == [] and row_of(v, 3)["unnamed"] == []


def test_refunds_on_the_same_transfer_day_come_by_their_last_day():
    """QA 2.6: rows with the same "מועד העברה מתוכנן" came by descending id, so one with 5 days left was 11th of 12."""
    def refund(i, enr, planned):
        return sc(i, enr=[enr], credit_approved="100", planned=planned, created="2026-10-%02dT09:00:00Z" % i)
    rows = [refund(1, "E1", "2026-11-20"), refund(2, "E2", "2026-11-20"), refund(3, "E3", "2026-11-20"),
            refund(4, "E4", "2026-11-15")]
    v = view(rows, enrollments=[enrollment("E1", asked="2026-11-08"),      # last day 22.11
                                enrollment("E2", asked="2026-11-01"),      # last day 15.11: the nearest
                                enrollment("E3", asked="2026-11-05"),      # last day 19.11
                                enrollment("E4", asked="2026-11-09")])     # planned earlier: first
    assert [x["id"] for x in fd.filter_b2c(v["b2c"], "refund_to_execute", "", "")] == ["4", "2", "3", "1"]


def test_charges_of_a_company_without_terms_are_marked_and_their_share_of_arrears_is_said():
    """QA 1.3: 8 of the 10 charges "בפיגור" were of companies without "תנאי תשלום", their due date the demand day itself.
    The rule stays (DUE_RULE); the page marks it and says how much of the arrears it makes."""
    rows = [b2(1, payment="2", due="2026-10-31", account="A1", amount="200000"),    # no terms: due 31.10, 9 days late
            b2(2, payment="2", due="2026-09-10", account="A2", amount="27757"),     # שוטף+30: due 31.10, late too
            b2(3, billing="0", payment="2", account="A1", due="2026-11-20"), b2(4, payment="1", account="A1"),
            b2(5, billing="2", account="A1"), b2(6, payment="2", due="2026-10-01", account="A3", amount="5"),
            b2(7, payment="2", due="2026-10-01", account="", amount="7")]          # no company at all: no terms to have
    v = view(b2_rows=rows, accounts=[acct("A1", ""), acct("A2", "שוטף+30"), acct("A3", "אחר")])
    flagged = {i for i in range(1, 8) if "terms_missing" in b2row(v, i)["flags"]}
    assert flagged == {1, 3}                    # to collect or to bill; not paid, not cancelled, not "אחר", not no company
    assert (b2row(v, 1)["due"], b2row(v, 1)["state"]) == ("2026-10-31", "overdue")          # the rule itself is unchanged
    assert v["b2b"]["overdue"] == D(227769) and v["b2b"]["overdue_no_terms"] == D(200000)


def test_a_billed_charge_without_a_demand_day_is_flagged_whatever_it_paid_and_counted_once():
    """QA 1.4: 2960243096 (חויב, שולם חלקית, 305,077) had no "מועד דרישת תשלום" and was marked nowhere."""
    rows = [b2(1, payment="0", amount="258539", paid="100000", due="", invoice="INV-1"),
            b2(2, payment="1", due="", invoice="INV-2"), b2(3, payment="2", due="", invoice=""), b2(4, payment="", due=""),
            b2(5, payment="2", due="2026-09-01")]
    v = view(b2_rows=rows)
    assert all("billed_no_due" in b2row(v, i)["flags"] for i in range(1, 5)) and "billed_no_due" not in b2row(v, 5)["flags"]
    # where each sits: still owed in full, an exception; partly paid, her work already ("שולם חלקית"), its balance there
    # and only there; paid, nothing to collect - flagged where it is
    assert [b2row(v, i)["state"] for i in range(1, 6)] == ["partial", "paid", "exception", "exception", "overdue"]
    assert v["checks"]["b2b_states_partition"] is True and v["checks"]["b2b_money_ties"] is True
    c = v["b2b"]["customers"][0]
    assert c["aging"]["no_due"] == D(2000) and c["open"] == D(3000) == sum(c["aging"].values())
    assert c["partial_balance"] == b2row(v, 1)["balance"] == D("205076.02")
    assert b2row(v, 1)["open_money"] == 0                                   # a partly paid charge is never open money


def test_the_done_sentence_holds_for_its_rows_and_says_what_keeps_a_refund_out():
    """QA 2.11: "הושלם" said "סטטוס תשלום 'שולם', ושליחת קבלה 'יצאה' או 'לא רלוונטי'" - true of refunds to execute too,
    which are not there."""
    v = view([sc(1, receipt="1"), sc(2, receipt="1", credit_approved="500"), sc(3, receipt="2", refund_date="2026-11-01")])
    assert [row_of(v, i)["state"] for i in (1, 2, 3)] == ["done", "refund_to_execute", "exception"]
    about = fd.B2C_STATES["done"]["about"]
    assert "סכום לזיכוי" in about and "סכום החזר" in about and "תאריך החזר" in about and "החזר לביצוע" in about


def test_the_done_refunds_tile_and_the_refunded_figure_have_different_names():
    # Nadav 11.11 (QA): "הוחזרו" (the period's figure, every refund including those under "חריג") and the tile of
    # clean refunds carried near-identical names with different numbers. The tile is "החזר בוצע", the pair of
    # "החזר לביצוע"; no state may be called "הוחזר" or "הוחזרו" again.
    labels = [v["label"] for v in fd.B2C_STATES.values()]
    assert fd.B2C_STATES["refunded"]["label"] == "החזר בוצע"
    assert fd.B2C_STATES["refund_to_execute"]["label"] == "החזר לביצוע"
    assert not {"הוחזר", "הוחזרו"} & set(labels)


# --- what the desk may write, as the person (analiza-metrics tests/test_finance_actions.py, its pure tests) -----------

# the task's list, verbatim (backlog 0280 round 3 item 6, spec §2.5): every column the browser may write, and no other -
# and since D2 (ADR-0188, the desk move plan §1b item 4) a customer's four billing details on Accounts, which the person
# writes from the browser where analiza-metrics' server wrote them
ALLOWED = {
    # D4: "תאריך תקבול" (date_mm8yh4bn), written by "רישום מחשבשבת" only, only empty (spec §7.3: the list gains one column)
    fd.STUDENT_CHARGE: {"color_mm1kxpvt", "numeric_mm62za1f", "text_mm8fjzfj", "text_mm6d6s97", "color_mm4cq1hw",
                        "date_mm8yh4bn"},
    fd.B2B_CHARGE: {"color_mm3zx2n7", "text_mm3y6snz", "date_mm4a6y8y", "color_mm4bhvta", "numeric_mm8zqhag"},
    fd.ACCOUNTS_BOARD: {"text_mm3y3gm7", "text_mm3yprqa", "text_mm3yfrew", "dropdown_mm3yd36d"},
}


def test_the_actions_write_exactly_the_allowed_columns_and_nothing_on_any_other_board():
    writes = fd.desk_writes()
    assert {b: set(cols) for b, cols in writes.items()} == ALLOWED
    # a status column is allowed with the indexes the actions set, never with its every label (review 11.11)
    assert {b: {c: v for c, v in cols.items() if isinstance(v, list)} for b, cols in writes.items() if b != fd.ACCOUNTS_BOARD} == {
        fd.STUDENT_CHARGE: {"color_mm1kxpvt": [4, 5], "color_mm4cq1hw": [1]},
        fd.B2B_CHARGE: {"color_mm3zx2n7": [6], "color_mm4bhvta": [0, 1]}}
    # the billing details: each text by its own rule, the terms by the form's ids (analiza-metrics account_writer's)
    assert writes[fd.ACCOUNTS_BOARD] == {fd.AC["vat_id"]: "vat_id", fd.AC["hash_key"]: "hash_key",
                                         fd.AC["invoice_name"]: "invoice_name",
                                         fd.AC["terms"]: {"dropdown": sorted(int(i) for i in fd.ACCOUNT_TERMS)}}
    assert writes[fd.ACCOUNTS_BOARD][fd.AC["terms"]] == {"dropdown": [1, 2, 3, 4, 5]}
    # the bank columns, the ID number and "סכום לזיכוי" are never written from the desk
    every = {c for cols in writes.values() for c in cols}
    bank = {fd.SC[k] for k in ("bank_holder", "bank", "bank_branch", "bank_account")}
    assert not every & (bank | {fd.LD["id_number"], fd.SC["credit_approved"], fd.SC["planned"]})
    # a column the server never reads is never written either - but the business number, which the person writes from
    # the browser (ADR-0188) and the server never reads (ADR-0173 §1)
    assert every & NEVER_READ == {fd.AC["vat_id"]}


def test_a_status_is_written_by_its_index_from_the_live_dictionaries_with_its_label():
    by_col = {fd.SC["status"]: fd.PAY, fd.SC["receipt"]: fd.RECEIPT, fd.B2["billing"]: fd.BILLING, fd.B2["payment"]: fd.B2B_PAY}
    seen = {}
    for key, a in fd.DESK_ACTIONS.items():
        for step in a["steps"]:
            for w in step:
                if w["kind"] == "status":
                    assert w["label"] == by_col[w["column"]][w["index"]], key
                    seen[(w["column"], w["index"])] = key
    assert seen == {(fd.SC["status"], "4"): "refund_done", (fd.SC["status"], "5"): "refund_partial",
                    (fd.SC["receipt"], "1"): "receipt_sent", (fd.B2["billing"], "6"): "billed",
                    (fd.B2["payment"], "1"): "b2b_paid", (fd.B2["payment"], "0"): "b2b_partial"}


def test_a_partial_refund_types_its_amount_before_its_status():
    steps = fd.DESK_ACTIONS["refund_partial"]["steps"]
    assert [[w["column"] for w in s] for s in steps] == [[fd.SC["refund"]], [fd.SC["status"]]]
    assert steps[0][0]["input"] and steps[0][0]["required"] and steps[0][0]["kind"] == "number"


def test_billed_writes_its_invoice_only_when_typed_and_the_demand_day_only_when_empty():
    step, = fd.DESK_ACTIONS["billed"]["steps"]
    by = {w["column"]: w for w in step}
    assert by[fd.B2["invoice"]]["skip_empty"] is True and by[fd.B2["invoice"]]["required"] is False
    assert by[fd.B2["due"]] == {"column": fd.B2["due"], "title": "מועד דרישת תשלום", "kind": "date", "today": True,
                                "if_empty": True}
    # what it says to the person, without the task's number (MON-92, QA 24.11: kept in the comment)
    assert fd.DESK_ACTIONS["billed"]["after"] == "מועד דרישת תשלום נכתב רק כשהוא ריק במאנדיי, והוא היום."


@pytest.mark.parametrize("row, offers", [
    (sc(1, credit_approved="1000", planned="2026-11-20"), ["refund_done", "refund_partial", "sc_invoice"]),
    (sc(2, receipt="0"), ["receipt_sent", "sc_invoice"]),
    (sc(3, st="4", refund="4000", refund_date="2026-11-02"), ["credit_ref", "sc_invoice"]),
    (sc(4, st="5", refund="10", refund_date="2026-11-02"), ["credit_ref", "sc_invoice"]),
    (sc(5), ["sc_invoice"]),                                     # הושלם: only its invoice's reference
    (sc(6, st="9", receipt="1"), ["sc_invoice"]),
    (sc(7, st="0"), []), (sc(8, st="2"), []), (sc(9, st="3"), []), (sc(10, st="77"), []),
    # an approved refund on "יש בעיה" is an exception, not a refund to execute: nothing to mark from here
    (sc(11, st="2", credit_approved="1000"), []),
])
def test_each_payment_offers_the_actions_of_its_state(row, offers):
    v = view([row])
    assert row_of(v, row["id"])["actions"] == offers


@pytest.mark.parametrize("row, offers", [
    (b2(1, billing="0", payment="2", due="2026-11-20"), ["billed"]),     # לחיוב החודש
    (b2(2, billing="1", payment="2", due=""), ["billed"]),
    (b2(3, billing="1", payment="2", due="2026-12-20"), []),             # planned for a later month: not billed early
    (b2(4, billing="6", payment="2", due="2026-09-01"), ["b2b_paid", "b2b_partial"]),   # בפיגור
    (b2(5, billing="6", payment="0", paid="100"), ["b2b_paid", "b2b_partial"]),         # שולם חלקית
    (b2(6, billing="6", payment="", due="2026-11-05"), ["b2b_paid", "b2b_partial"]),    # billed, payment empty
    (b2(7, billing="6", payment="1"), []), (b2(8, billing="2"), []), (b2(9, billing="7", amount=""), []),
    (b2(10, billing="0", payment="1"), []),                              # "שולם" before "חויב": an exception, not to bill
])
def test_each_company_charge_offers_the_actions_of_its_state(row, offers):
    v = view([sc(1)], [row])
    assert b2row(v, row["id"])["actions"] == offers


def test_a_company_row_carries_its_two_status_indexes():
    v = view([sc(1)], [b2(1, billing="6", payment="0", paid="100")])
    r = b2row(v, 1)
    assert (r["billing_idx"], r["payment_idx"]) == ("6", "0") and (r["billing"], r["payment"]) == ("חויב", "שולם חלקית")


def test_the_ledger_carries_three_billing_details_and_never_a_business_number():
    """analiza-metrics carried all four as monday shows them, the business number among them. Here `vat_id` is None,
    whatever a row holds: the reading never holds it (NEVER_READ), and None is not "" (an empty cell in monday)."""
    a1 = dict(acct("A1", "שוטף+30"), **{fd.AC["vat_id"]: " 512345678 ", fd.AC["hash_key"]: "10269",
                                        fd.AC["invoice_name"]: "חברה בע\"מ"})
    v = fd.build_view(snap([sc(1)], [b2(1), b2(2, account="")], accounts=[a1]), TODAY)
    by = v["b2b"]["by_customer"]
    assert by["A1"]["billing"] == {"vat_id": None, "hash_key": "10269", "invoice_name": "חברה בע\"מ",
                                   "terms": "שוטף+30"}
    assert by[fd.NO_ACCOUNT]["billing"] is None
    assert "512345678" not in str(v) and fd.AC["vat_id"] in NEVER_READ


def test_the_billing_form_names_the_four_columns_the_reading_checks():
    """analiza-metrics held the reading to the columns its account writer wrote. Nothing writes them here (ADR-0188):
    the form the page draws names the same four, the business number's column among them, which the reading checks to
    exist and never reads."""
    form = fd.account_form()
    assert {f["key"]: f["column"] for f in form["fields"]} == {k: fd.AC[k] for k in fd.BILLING_DETAILS}
    assert set(rd.CHECKED_COLUMNS["accounts"]) == set(fd.AC.values())
    assert set(rd.ROW_COLUMNS["accounts"]) == set(fd.AC.values()) - {fd.AC["vat_id"]}
    assert form["board"] == fd.ACCOUNTS_BOARD
    # the terms the desk counts by are the form's labels, and every label but "אחר" has its days
    assert set(fd.TERMS_DAYS) == {t["label"] for t in form["terms"]} - {"אחר"}


def test_each_tab_carries_its_own_actions_and_a_tab_the_reader_may_not_see_carries_none():
    v = view([sc(1)], [b2(1)])
    assert v["b2c"]["actions"]["board"] == fd.STUDENT_CHARGE and v["b2b"]["actions"]["board"] == fd.B2B_CHARGE
    assert [a["key"] for a in v["b2c"]["actions"]["list"]] == [k for k, a in fd.DESK_ACTIONS.items() if a["board"] == "sc"]
    assert v["b2b"]["account_form"]["fields"][0] == {"key": "vat_id", "title": "ח.פ", "column": "text_mm3y3gm7",
                                                     "kind": "text", "hint": "9 ספרות, בלי מקפים"}
    assert [t["label"] for t in v["b2b"]["account_form"]["terms"]] == ["שוטף", "שוטף+30", "שוטף+45", "שוטף+60", "אחר"]
    only_students = fd.visible_part(v, b2c=True, b2b=False)
    assert only_students["b2b"] is None and "account_form" not in str(only_students)


def test_the_house_style_holds_in_every_word_an_action_shows():
    for a in fd.DESK_ACTIONS.values():
        for text in [a["button"], a.get("after", "")] + [w["title"] for s in a["steps"] for w in s] + [
                c["title"] for c in a.get("rests_on", [])]:
            assert "\u2014" not in text and "\u2192" not in text and "->" not in text, text
    form = fd.account_form()
    for text in [form["mon43"]] + [f["title"] for f in form["fields"]] + [f["hint"] for f in form["fields"]]:
        assert "\u2014" not in text and "\u2192" not in text, text


def test_the_words_slice_3_draws_are_defined_in_the_legend():
    """The one-name norm (round 3): every word the desk adds is defined once in its tab's legend, every column it names is
    monday's own title with where it comes from. "פרטי חיוב" says the edit is the person's own since ADR-0188: in
    analiza-metrics it said the server wrote it ("בידי השרת"), which this service never does."""
    for tab in ("b2c", "b2b"):
        assert {"פעולות", "נשמר, מתעדכן"} <= fd.legend_terms(tab), tab
    assert {"פרטי חיוב", "ח.פ", "מפתח חשבשבת", "חשבונית על שם", "תנאי תשלום"} <= fd.legend_terms("b2b")
    said = {i["term"]: i["text"] for p in fd._legend_out("b2b") for i in p["items"]}
    # MON-43's rule, said without its number (QA 24.11)
    assert "מקבלת ח.פ בלי מפתח" in said["מפתח חשבשבת"] and "MON-" not in said["מפתח חשבשבת"]
    assert "בשמך" in said["פרטי חיוב"] and "בידי השרת" not in said["פרטי חיוב"]
    # what the page does (the adversarial review of 62fd993): written from the browser as the person, the before and the
    # after in monday's activity log, and an Update on the company only with the permission to write one - never promised
    bill = said["פרטי חיוב"]
    assert "נשמרת מהדפדפן בשמך" in bill and "יומן הפעילות של לוח החברות שומר מה היה ומה נכתב" in bill
    assert "רק כשלאפליקציה יש הרשאה לכתוב עדכונים במאנדיי" in bill and "ועל החברה נכתב עדכון בשמך" not in bill
    # the ID number is never in the Excel file now (app/desk/export.py), and the legend no longer says it is
    students = {i["term"]: i["text"] for p in fd._legend_out("b2c") for i in p["items"]}
    # D2, 1.1: two actions on the row itself, by their buttons' own names, for several rows at once, never over a change
    acts = students["פעולות"]
    assert fd.DESK_ACTIONS["receipt_sent"]["button"] in acts and fd.DESK_ACTIONS["refund_done"]["button"] in acts
    assert "בשורה עצמה" in acts and "עד 25" in acts and "שורה שהשתנתה במאנדיי מאז הקריאה לא נכתבת" in acts
    assert "אקסל" not in students["ת.ז"] and "בשמך" in students["ת.ז"]


# --- the reading to the view, end to end (what analiza-metrics' reader tests held of the view) -----------------------

def test_the_desks_reading_builds_a_whole_view():
    """analiza-metrics read a fake monday with its own reader and built the view from it; here the desk's reading
    (app/desk/reading.py) of the D1 test's monday: every board whole, every payment placed by its enrollment's cohort or
    its lead's, each with its lead's name and Owner, and a refund's last day from its enrollment's request day."""
    from tests.test_desk import reader_of, world

    w = world()
    w.items["201"]["cells"][rd.EN["asked"]] = "2026-10-20"
    w.items["101"]["cells"][rd.SC["credit_approved"]] = "1000"
    # and monday answering, unasked, the three values that are never read: none may reach the view
    w.extra_cells = {"301": {rd.LD["id_number"]: {"text": "012345678"}},
                     "701": {rd.AC["vat_id"]: {"text": "512345678"}},
                     "101": {rd.SC["bank_account"]: {"text": "998877665"}}}
    reading = rd.read_desk(reader_of(w))
    v = fd.build_view(reading, TODAY)
    assert reading["complete"] is True and v["checks"]["ok"] is True
    rows = {x["id"]: x for x in v["b2c"]["rows"]}
    assert {i: (x["cohort_code"], x["cohort_from"]) for i, x in rows.items()} == {
        "101": ("C1", "enrollment"), "102": ("C2", "lead"), "103": ("", "no_cohort")}
    assert {i: (x["lead_names"], x["rep"]) for i, x in rows.items()} == {
        "101": (["ליד-אלף"], "נציגה-אחת"), "102": (["ליד-בית"], ""), "103": (["ליד-גימל"], "נציג-שני")}
    assert (rows["101"]["asked_date"], rows["101"]["refund_last_day"], rows["101"]["state"]) == \
        ("2026-10-20", "2026-11-03", "refund_to_execute")
    assert [(x["id"], x["account"], x["deal"], x["payer"]) for x in v["b2b"]["rows"]] == [
        ("501", "חברה-משלמת", "עסקה-1", "חברה-אם"), ("502", "חברה-משלמת", "עסקה-2", "")]
    assert v["b2b"]["by_customer"]["701"]["billing"] == {"vat_id": None, "hash_key": "77001",
                                                         "invoice_name": "חשבונית-על-שם", "terms": "שוטף+30"}
    assert not any(value in str(v) for value in ("012345678", "512345678", "998877665"))


# --- QA of D2 (24.11) --------------------------------------------------------------------------------------------------

def test_a_rows_reasons_are_its_chips_every_one_in_order_and_each_chip_has_one_place_in_it():
    """The Excel files' "מה לבדוק" (view.row_reasons): the page's chips of the row, all of them, in FLAG_ORDER, which holds
    every chip of the legend once - a new chip with no place in it would be left out of the files."""
    assert len(fd.FLAG_ORDER) == len(set(fd.FLAG_ORDER))
    assert set(fd.FLAG_ORDER) == {k for tab in fd.LEGEND_FLAGS.values() for k, _t, _x in tab}
    v = view([sc(1, st="", credit_approved="100", refund="5"), sc(2, receipt="0", invoice="", lead=["L1"]),
              sc(3, credit_approved="100", lead=["L1"], enr=["E3"])],
             leads=[lead("L1")], enrollments=[enrollment("E3", asked="2026-11-05")])
    assert fd.row_reasons("b2c", row_of(v, 1)) == ["סכום לזיכוי בסטטוס ריק", "סכום החזר בסטטוס ריק",
                                                   "שליחת קבלה: יצאה, בסטטוס ריק", fd.LAST_DAY_LABEL + " לא ידוע",
                                                   "בלי הרשמה ובלי ליד מקור"]
    assert fd.row_reasons("b2c", row_of(v, 2)) == ["חסרה אסמכתת חשבונית", "שליחת קבלה: טרם"]
    # a flag that names the row's state ("החזר לביצוע") is no reason, as it is no chip on the page
    assert row_of(v, 3)["state"] == "refund_to_execute" and fd.row_reasons("b2c", row_of(v, 3)) == []


def test_the_refunded_sentence_says_its_two_cases_apart_and_names_every_row_it_sends_to_the_exceptions():
    """QA 24.11: "החזר בוצע" said '"הוחזר חלקי" בלי סכום החזר או עם 0, וסכום החזר גדול מסכום ששולם, נמצאים ב"חריג"' - the
    "ו" read as one row with both. Two cases now ("וגם"), and the sentence holds at its edges: every combination of the two
    statuses with a refund, a payment and a receipt is in "החזר בוצע" unless it is a case the sentence names."""
    rows, case = [], {}
    for st in (fd.P_REFUNDED, fd.P_PART):
        for refund in ("", "0", "100", "9000"):
            for paid in ("4000", ""):
                for receipt in ("1", "2", "0", "77"):
                    rows.append(sc(len(rows) + 1, st=st, refund=refund, paid=paid, receipt=receipt, lead=["L1"]))
                    case[str(len(rows))] = (st, refund, paid, receipt)
    v = view(rows, leads=[lead("L1")])
    for r in v["b2c"]["rows"]:
        st, refund, paid, receipt = case[r["id"]]
        named = (st == fd.P_PART and refund in ("", "0")) or (refund == "9000") or not paid or receipt == "77"
        assert r["state"] == ("exception" if named else "refunded"), case[r["id"]]
    about = fd.B2C_STATES["refunded"]["about"]
    assert "וגם תקבולים" in about and "נמצאים" not in about
    for words in ("בלי סכום החזר או עם 0", "גדול מסכום ששולם", "סכום ששולם בהם לא הוקלד", "שהמסך לא מכיר"):
        assert words in about, words


def test_no_word_the_desk_shows_carries_an_internal_task_number():
    """QA 24.11: "(MON-86)" in the confirmation of "סימון "תשלום הוחזר"", "(MON-43)" in the billing form. A task number is
    ours, not the person's: it stays in the code's comments. Everything the server sends the page, and both files."""
    import json
    import re
    from app.desk import export as fx
    v = view([sc(1, credit_approved="100"), sc(2, invoice="", receipt="0")], [b2(1), b2(2, billing="0", payment="2")])
    said = json.dumps(fd.json_safe(v), ensure_ascii=False)
    assert not re.search(r"MON-\d", said), re.findall(r"[^\"]{0,40}MON-\d+", said)
    for book in (fx.b2c_workbook(v["b2c"]["rows"], {}), fx.b2b_workbook(v["b2b"]["rows"], {})):
        import io
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(book))
        assert not [c.value for ws in wb for row in ws.iter_rows() for c in row if "MON-" in str(c.value or "")]


def test_the_legend_says_what_all_holds_and_its_other_words_plainly():
    """QA 24.11, the legend's wording: "הכל" (a tile on both tabs, and a file's list) had no entry; "פרטי חשבון" said
    "...שאינה מקלידה באישור ההחזר", which reads as "which is not typing"; "תווית שהמסך לא מכיר" said a label came "after the
    screen was written" - "נכתב" is the word of the desk's own writes."""
    for tab in ("b2c", "b2b"):
        assert "הכל" in fd.legend_terms(tab)
        flags = {k: x for k, _t, x in fd.LEGEND_FLAGS[tab]}
        assert "אחרי שהמסך נבנה" in flags["status_unknown"] and "נכתב" not in flags["status_unknown"]
    words = dict(fd.LEGEND["b2c"])
    assert '"ללא תקבול"' in words["הכל"] and "סכום השיבוצים" in words["הכל"]
    assert '"בוטל"' in dict(fd.LEGEND["b2b"])["הכל"] and "בכרטסת שלה" in dict(fd.LEGEND["b2b"])["הכל"]
    bank = words["פרטי חשבון"]
    assert "שאינה" not in bank and "ממולאות כשההחזר מאושר" in bank and '"החזר לביצוע"' in bank
    # each tab's "הכל" holds every row of its tab: the sum of its tiles
    v = view([sc(1), sc(2, st="0", receipt="0"), sc(3, st="4")], [b2(1), b2(2, billing="2")])
    whole = v["b2c"]["periods"][0]
    assert whole["count"] == sum(x["count"] for x in whole["states"].values()) == 3
    assert len(fd.filter_b2b(v["b2b"], "", "", "")) == sum(x["count"] for x in v["b2b"]["numbers"].values()) == 2


def test_a_receipt_sent_for_a_payment_that_brought_no_money_in_is_an_exception_that_says_so():
    """QA 24.11 (recommended; Nadav may veto it): a payment in "ללא תקבול" whose "שליחת קבלה" is "יצאה", one with an
    invoice's reference - a receipt went out for money that never came in, and no rule caught it. It is in "חריג, לבדיקה"
    now, its chip naming the receipt and the row's own status; a receipt not sent, or not relevant, leaves it where it was."""
    rows, case = [], {}
    for st in (fd.P_UNPAID, fd.P_PROBLEM, fd.P_SCHOLARSHIP, ""):
        for receipt in (fd.R_SENT, fd.R_PENDING, fd.R_NOT_RELEVANT, ""):
            rows.append(sc(len(rows) + 1, st=st, receipt=receipt, paid="2500", invoice="INV-7", lead=["L1"]))
            case[str(len(rows))] = (st, receipt)
    v = view(rows, leads=[lead("L1")])
    for r in v["b2c"]["rows"]:
        sent = case[r["id"]][1] == fd.R_SENT
        assert (r["state"], "receipt_no_money" in r["flags"]) == (("exception", True) if sent else ("no_money", False)), \
            case[r["id"]]
        assert r["money_in"] == 0 and r["actions"] == []
    one = next(r for r in v["b2c"]["rows"] if case[r["id"]] == (fd.P_UNPAID, fd.R_SENT))
    assert fd.row_reasons("b2c", one) == ['שליחת קבלה: יצאה, בסטטוס "לא שולם"']
    # the counts: the four sent move to "חריג", the twelve others stay in "ללא תקבול", and no money moves with them
    whole = v["b2c"]["periods"][0]["states"]
    assert (whole["exception"]["count"], whole["no_money"]["count"], whole["exception"]["money"]) == (4, 12, 0)
    assert v["b2c"]["money_in"] == 0 and v["checks"]["b2c_states_partition"] is True and v["checks"]["ok"] is True
    # money that came in, or a status the desk does not know (already "חריג"), is no such case
    other = view([sc(1, lead=["L1"]), sc(2, st="9", lead=["L1"]), sc(3, st="77", lead=["L1"])], leads=[lead("L1")])
    assert not any("receipt_no_money" in r["flags"] for r in other["b2c"]["rows"])
    # said where the desk says why: the exceptions' sentence, its chip in the legend, and "ללא תקבול" says it is not there
    assert 'שליחת קבלה "יצאה", וסטטוס התשלום "לא שולם"' in fd.B2C_STATES["exception"]["about"]
    assert 'ושליחת קבלה אינה "יצאה"' in fd.B2C_STATES["no_money"]["about"]


def test_every_exception_says_why_in_a_chip_of_its_tabs_legend():
    """A flag that puts a row in "חריג, לבדיקה" has a chip, so the page's "מה לבדוק" and the Excel files say why."""
    for tab, exceptions in (("b2c", fd.B2C_EXCEPTIONS), ("b2b", fd.B2B_EXCEPTIONS)):
        assert exceptions <= {k for k, _t, _x in fd.LEGEND_FLAGS[tab]} and exceptions <= set(fd.FLAG_ORDER), tab
