"""B3's totals (backlog 0301; ADR-0187 §2-§5; the owner's merge of 26.11): months of the ledger report summed by sort code,
the salary codes and every line fewer than three accounts fill merged into one line a month, and each month replaced whole
(app/books/ledger_totals.py, app/books/ledger_store.py, migrations/007); the dry listing; the routes POST
/api/books/ledger/run and GET /api/books/ledger/totals; /health's books.ledger. Synthetic data only: four months of a small
company's books, every transaction balanced, with the expected totals written out by hand below - never computed by the
code under test."""

import copy
import hashlib
import itertools
import json
import logging
import re
import time
from decimal import Decimal

import httpx
import psycopg
import pytest

from app.books import ledger, ledger_store, ledger_totals
from app.books.ledger_store import LEDGER_TABLES, LedgerStore
from app.books.ledger_totals import MERGED_CODE, MERGED_NAME, money_of, salary_codes, sort_of, storable_key, whole_of
from app.wizcloud import WizClient
from tests.conftest import KEY, SESSION, TEST_DATABASE_URL, FakeStore, all_tables, drop_all, needs_pg
from tests.test_ledger import CAPTIONS, DATAFILE, DEFINITION, SECRET, scan, targets

DEMO = "wizdb2245n4"
SYNC_KEY = "ledger-totals-sync-key-0123456789abcdef"
SALARY = "731, 610,733"  # the expense, the liabilities it is owed on, and one manager's code
D = Decimal

# key: (sort code, the sort code's name, the account's name). The names of accounts share no word with the names of codes or
# with the merged line's, so a scan for an account's name never mistakes a name the totals do say for a leak.
ACCOUNTS = {
    "71884": (942, "ספקי ציוד", "אופק תקשורת בעמ"),
    "72005": (942, "ספקי ציוד", "מחשבים פלוס בעמ"),
    "73010": (942, "ספקי ציוד", "רשתות הצפון בעמ"),
    "50120": (951, "הוצאות ציוד", "מדפסות וטונר"),
    "50121": (951, "הוצאות ציוד", "נתבים ומתגים"),
    "50122": (951, "הוצאות ציוד", "כבלים וחיבורים"),
    "60231": (300, "מזומנים ושווי מזומנים", "עוש מרכזי"),
    "039337423": (105, "לקוחות תלמידים", "יעל אברהמי"),
    "123456782": (105, "לקוחות תלמידים", "נועם ברקוביץ"),
    "10091": (105, "לקוחות תלמידים", "שוברי מימון"),
    "40001": (410, "הכנסות", "קורסים ערב"),
    "40002": (410, "הכנסות", "סדנאות בוקר"),
    "40003": (410, "הכנסות", "הרצאות חוץ"),
    "70010": (720, "מעמ", "עסקאות לתשלום"),
    "70011": (720, "מעמ", "תשומות שוטפות"),
    "40017": (731, "הוצאות משכורת", "משכורות ברוטו"),
    "40019": (731, "הוצאות משכורת", "פנסיה מעביד"),
    "40021": (731, "הוצאות משכורת", "ביטוח לאומי מעביד"),
    "61001": (610, "התחייבויות לעובדים", "נטו לתשלום"),
    "61002": (610, "התחייבויות לעובדים", "קופות גמל"),
    "61003": (610, "התחייבויות לעובדים", "ניכויים במקור"),
    "40099": (733, "תגמול מנהלים", "דנה מזרחי"),
    "52001": (960, "הוצאות רכב", "דלק"),
    "52002": (960, "הוצאות רכב", "חניות"),
    "52003": (960, "הוצאות רכב", "תיקונים"),
    "89999": (990, "רווח והפסד", "העברה שנתית"),
}
ID_KEYS = ("039337423", "123456782")  # student cards keyed by ID numbers (ADR-0173 §1): valid check digits both
FULLWIDTH = {"72005": "７２００５"}  # one row writes a key in full-width digits: one account, kept in the copy's form


def tx(day, number, batch, lines, details="שורה", ref="61290", cost=""):
    """One balanced transaction: [(key, debit, credit)], each line a row of the report keyed by caption (26.11)."""
    assert sum((d or D(0)) for _, d, _ in lines) == sum((c or D(0)) for _, _, c in lines), "a transaction balances"
    rows = []
    for i, (key, debit, credit) in enumerate(lines):
        code, code_name, name = ACCOUNTS[key]
        counter = next(k for k, _, _ in lines if k != key)
        rows.append(dict(zip(CAPTIONS, [
            f"{day}T00:00:00", f"{day}T00:00:00", FULLWIDTH.get(key, key) if number == 88404 else key, code, code_name, name,
            cost, details, ref, "7731" if i else "", debit, credit, counter, batch, "יומן", number])))
    return rows


def invoices(month, numbers, batch, amounts, days=(10, 11, 12)):
    """Three suppliers invoiced: each supplier credited, its expense account debited."""
    out = []
    for day, number, (expense, supplier), amount in zip(days, numbers, (("50120", "71884"), ("50121", "72005"),
                                                                         ("50122", "73010")), amounts):
        out += tx(f"{month}-{day:02d}", number, batch, [(expense, D(amount), None), (supplier, None, D(amount))],
                  f"חשבונית רכש {number + 1000}")
    return out


def payments(month, numbers, batch, amounts, days=(20, 21, 22)):
    """Three suppliers paid from the bank."""
    out = []
    for day, number, supplier, amount in zip(days, numbers, ("71884", "72005", "73010"), amounts):
        out += tx(f"{month}-{day:02d}", number, batch, [(supplier, D(amount), None), ("60231", None, D(amount))],
                  "העברה בנקאית")
    return out


OCTOBER = (
    tx("2026-10-05", 88401, 7712, [("50120", D("1935.47"), None), ("71884", None, D("1935.47"))], "חשבונית רכש 88412", "61377",
       "PRJ-77")
    + tx("2026-10-12", 88402, 7712, [("50121", D("2210.19"), None), ("72005", None, D("2210.19"))], "חשבונית רכש 90127")
    + tx("2026-10-13", 88413, 7712, [("50122", D("1317.52"), None), ("73010", None, D("1317.52"))], "חשבונית רכש 90311")
    + tx("2026-10-20", 88403, 7740, [("71884", D("1517.38"), None), ("60231", None, D("1517.38"))], "העברה בנקאית")
    + tx("2026-10-21", 88404, 7740, [("72005", D("803.61"), None), ("60231", None, D("803.61"))], "העברה בנקאית")
    + tx("2026-10-22", 88414, 7740, [("73010", D("641.09"), None), ("60231", None, D("641.09"))], "העברה בנקאית")
    + tx("2026-10-06", 88405, 7712, [("039337423", D("3543.81"), None), ("40001", None, D("3003.23")),
                                     ("70010", None, D("540.58"))], "חשבונית קבלה", "62502", "PRJ-91")
    + tx("2026-10-07", 88406, 7712, [("60231", D("3543.81"), None), ("039337423", None, D("3543.81"))], "קבלה", "62503")
    + tx("2026-10-14", 88407, 7712, [("123456782", D("2951.17"), None), ("40002", None, D("2500.99")),
                                     ("70010", None, D("450.18"))], "חשבונית קבלה", "62511")
    + tx("2026-10-15", 88408, 7712, [("10091", D("1183.27"), None), ("40003", None, D("1002.77")),
                                     ("70011", None, D("180.50"))], "חשבונית שובר", "62519")
    + tx("2026-10-28", 88409, 7790, [("40017", D("38213.77"), None), ("40019", D("6003.41"), None), ("40021", D("3996.59"), None),
                                     ("61001", None, D("37151.21")), ("61002", None, D("7062.57")),
                                     ("61003", None, D("3999.99"))], "תלוש חודשי")
    + tx("2026-10-28", 88410, 7790, [("40099", D("31000.29"), None), ("60231", None, D("31000.29"))], "תלוש הנהלה")
    + tx("2026-10-08", 88415, 7712, [("52001", D("812.33"), None), ("52002", D("145.90"), None), ("52003", D("1203.44"), None),
                                     ("60231", None, D("2161.67"))], "תדלוק וחניה")
    + tx("2026-10-18", 88416, 7712, [("60231", D("97.22"), None), ("52001", None, D("61.15")), ("52002", None, D("36.07"))],
         "החזר הוצאה")
    + tx("2026-10-31", 88411, 9998, [("89999", D("5463.18"), None), ("50120", None, D("1935.47")),
                                     ("50121", None, D("2210.19")), ("50122", None, D("1317.52"))], "העברת הכנסות והוצאות", "")
)
SEPTEMBER = (invoices("2026-09", (88301, 88302, 88303), 7701, ("611.23", "702.45", "377.19"))
             + payments("2026-09", (88304, 88305, 88306), 7702, ("412.36", "233.58", "151.07")))
AUGUST = invoices("2026-08", (88201, 88202, 88203), 7651, ("120.41", "230.52", "340.63"))
JUNE = (invoices("2026-06", (88701, 88702, 88703), 7501, ("150.15", "260.26", "370.37"))
        + tx("2026-06-15", 88704, 7502, [("70010", D("250.25"), None), ("60231", None, D("250.25"))], "הפקדה שגויה")
        + tx("2026-06-16", 88705, 7502, [("70010", D("-250.25"), None), ("60231", None, D("-250.25"))], "ביטול הפקדה"))
JULY = (invoices("2026-07", (88601, 88602, 88603), 7601, ("210.11", "320.22", "430.33"), days=(3, 4, 5))
        + payments("2026-07", (88604, 88605, 88606), 7602, ("105.05", "160.16", "215.17"), days=(10, 11, 12))
        + tx("2026-07-15", 88607, 7603, [("60231", D("590.59"), None), ("70010", None, D("90.09")),
                                         ("70011", None, D("500.50"))], "הפקדה"))

# By hand: (sort code) -> (debit, credit, rows, accounts, masked keys); MERGED_CODE is the month's merged line.
# October: 942, 951 and 410 stand (three accounts each, each side filled by three or by none). Into the merged line go
# 731, 610 and 733 (salary codes), 300 (the bank, one account) and 720 (VAT, two), and 105 (one student's receipt fills
# its credit) and 960 (two refunds fill its credit): 16 accounts, both sides filled by many, so it is kept.
# No two accounts hold nine tenths of a side that stands (rule (b), checked by hand; every sum on these sides is positive,
# so the two largest are the nearest pair): 410's credit 6506.99, 3003.23 + 2500.99 = 5504.22, the rest 1002.77 against a
# tenth of 650.70; 942's debit 2962.08, 1517.38 + 803.61 = 2320.99, the rest 641.09 against 296.21; 942's credit and 951's
# debit 5463.18, 2210.19 + 1935.47 = 4145.66, the rest 1317.52 against 546.32; the merged debit 92695.01, 38213.77 +
# 31000.29 = 69214.06, the rest 23480.95 against 9269.50; the merged credit 89150.10, 37151.21 + 36124.04 = 73275.25, the
# rest 15874.85 against 8915.01. So they all still stand.
OCTOBER_LINES = {
    410: (D("0"), D("6506.99"), 3, 3, 0),
    942: (D("2962.08"), D("5463.18"), 6, 3, 0),
    951: (D("5463.18"), D("0"), 3, 3, 0),  # batch 9998 credits its three accounts: outside the line
    MERGED_CODE: (D("92695.01"), D("89150.10"), 26, 16, 2),
}
OCTOBER_COUNTS = {"rows": 42, "sort_codes": 10, "kept_lines": 4, "merged_codes": 7, "merged": "kept",
                  "merged_by": {"salary": 3, "small": 2, "side": 2, "balance": 0}, "masked_keys": 2, "batch_9998_rows": 4}
OCTOBER_BATCH = (4, D("5463.18"), D("5463.18"))
# The months below have a merged line that cannot stand. A month's lines net to zero, so it is never withheld while a line
# stands: the standing lines go into it, the fewest accounts first and then the lowest code, until it stands (`balance`).
# 942 and 951 have three accounts each in every one of them, so 942 goes in first; one is enough each time.
# September: the bank alone goes in (one account): it cannot stand. With 942 in it, by hand:
#   debit:  the three suppliers paid, 412.36 + 233.58 + 151.07 = 797.01 (the bank has no debit): the two largest 645.94,
#           the rest 151.07 against a tenth of 79.70;
#   credit: the bank 412.36 + 233.58 + 151.07 = 797.01, the three invoices 611.23 + 702.45 + 377.19 = 1690.87, so 2487.88
#           over four accounts: the two largest 797.01 + 702.45 = 1499.46, the rest 988.42 against 248.79;
#   rows 3 + 6 = 9, accounts 1 + 3 = 4: it stands. 951 stands beside it (611.23 + 702.45 + 377.19 = 1690.87, the two
#   largest 1313.68, the rest 377.19 against 169.09), and the nets sum to zero: 1690.87 + (797.01 - 2487.88).
SEPTEMBER_LINES = {951: (D("1690.87"), D("0"), 3, 3, 0), MERGED_CODE: (D("797.01"), D("2487.88"), 9, 4, 0)}
SEPTEMBER_COUNTS = {"rows": 12, "sort_codes": 3, "kept_lines": 2, "merged_codes": 2, "merged": "kept",
                    "merged_by": {"salary": 0, "small": 1, "side": 0, "balance": 1}, "masked_keys": 0, "batch_9998_rows": 0}
# July: the bank (one account) and VAT (two) go in: three accounts, but the bank alone fills the debit. With 942 in it:
#   debit:  the bank's deposit 590.59, the suppliers paid 105.05 + 160.16 + 215.17 = 480.38, so 1070.97 over four
#           accounts: the two largest 590.59 + 215.17 = 805.76, the rest 265.21 against a tenth of 107.10;
#   credit: the bank 105.05 + 160.16 + 215.17 = 480.38, VAT 90.09 + 500.50 = 590.59, the invoices 210.11 + 320.22 + 430.33
#           = 960.66, so 2031.63 over six accounts: the two largest 500.50 + 480.38 = 980.88, the rest 1050.75 against
#           203.16;
#   rows 4 (the bank) + 2 (VAT) + 6 = 12, accounts 1 + 2 + 3 = 6: it stands. 951 stands beside it (960.66, the two
#   largest 750.55, the rest 210.11 against 96.07), and the nets sum to zero: 960.66 + (1070.97 - 2031.63).
JULY_LINES = {951: (D("960.66"), D("0"), 3, 3, 0), MERGED_CODE: (D("1070.97"), D("2031.63"), 12, 6, 0)}
JULY_COUNTS = {"rows": 15, "sort_codes": 4, "kept_lines": 2, "merged_codes": 3, "merged": "kept",
               "merged_by": {"salary": 0, "small": 2, "side": 0, "balance": 1}, "masked_keys": 0, "batch_9998_rows": 0}
# June: VAT and the bank go in (one account each), and a deposit undone the next day leaves both with nothing on either
# side: two accounts, under three. With 942 in it:
#   debit:  VAT's 250.25 - 250.25 = 0, and the suppliers have none: an empty side;
#   credit: the bank's 250.25 - 250.25 = 0, the invoices 150.15 + 260.26 + 370.37 = 780.78 over three accounts: the two
#           largest 630.63, the rest 150.15 against a tenth of 78.08;
#   rows 2 (VAT) + 2 (the bank) + 3 = 7, accounts 1 + 1 + 3 = 5: it stands. 951 stands beside it (780.78, the rest
#   150.15 the same), and the nets sum to zero: 780.78 + (0 - 780.78).
JUNE_LINES = {951: (D("780.78"), D("0"), 3, 3, 0), MERGED_CODE: (D("0"), D("780.78"), 7, 5, 0)}
JUNE_COUNTS = {"rows": 10, "sort_codes": 4, "kept_lines": 2, "merged_codes": 3, "merged": "kept",
               "merged_by": {"salary": 0, "small": 2, "side": 0, "balance": 1}, "masked_keys": 0, "batch_9998_rows": 0}
# August: nothing to merge.
AUGUST_LINES = {942: (D("0"), D("691.56"), 3, 3, 0), 951: (D("691.56"), D("0"), 3, 3, 0)}
AUGUST_COUNTS = {"rows": 6, "sort_codes": 2, "kept_lines": 2, "merged_codes": 0, "merged": "none",
                 "merged_by": {"salary": 0, "small": 0, "side": 0, "balance": 0}, "masked_keys": 0, "batch_9998_rows": 0}
MONTHS = {"2026-06": (JUNE, JUNE_LINES), "2026-07": (JULY, JULY_LINES), "2026-08": (AUGUST, AUGUST_LINES), "2026-09": (SEPTEMBER, SEPTEMBER_LINES),
          "2026-10": (OCTOBER, OCTOBER_LINES)}
NAMES = {code: name for code, name, _ in ACCOUNTS.values()}
MAP = {key: code for key, (code, _, _) in ACCOUNTS.items() if key not in ID_KEYS}  # (c): the keys that are no ID number

ROWS = JUNE + JULY + AUGUST + SEPTEMBER + OCTOBER
# What a row holds that no answer may say: everything but the sort code and its name, which the totals and the dry listing
# say by design.
PRIVATE = targets([{k: v for k, v in r.items() if k not in ("קוד מיון", "שם קוד מיון")} for r in ROWS]) - {"9998"}
EVERYTHING = targets(ROWS) - {"9998"}  # the transfer batch's number is a constant the answers name ("batch_9998_rows")
TOTALS = {format(v, "f") for _, lines in MONTHS.values() for line in lines.values() for v in line[:2] if v} | {
    format(OCTOBER_BATCH[1], "f")}
# What an answer may say: closed words, captions, field names, the merged line's name. No fixture value may be a piece of one.
LEGIT = " ".join(CAPTIONS + list(ledger_totals.RUN_REASONS) + list(ledger_totals.FIELDS) + list(ledger_totals.MERGE_WHY)
                 + list(ledger_totals.MERGED_STATES) + list(ledger_totals.SALARY_STATES) + [MERGED_NAME] + [
    "batch_9998_rows", "batch_9998_debit", "batch_9998_credit", "merged_codes", "merged_by", "masked_keys", "kept_lines",
    "sort_codes", "key_conflicts", "computed_at", "salary_codes_unseen", "accounts", "2026-10", "2026-09", "demo", "skipped",
    "request_s", "dry", "codes"])
# What the totals route says by design: the codes, their names, the merged line's, and the kept totals.
SAID = {str(c) for c in NAMES} | set(NAMES.values()) | {MERGED_NAME, str(MERGED_CODE)} | TOTALS


def test_the_fixture_is_what_the_tests_say():
    assert len(OCTOBER) == OCTOBER_COUNTS["rows"] and len(PRIVATE) > 120
    assert [v for v in EVERYTHING if v in LEGIT] == []
    # an amount the totals say is a sum, never one row's: the one exception is the 9998 transfer's single debit row
    singles = {format(r[c], "f") for r in ROWS for c in ("חובה שקל", "זכות שקל") if r[c] is not None}
    assert singles & SAID == {"5463.18"}
    for key in ID_KEYS:  # an Israeli ID's check digit (a valid number makes the test a fair one)
        digits = [int(c) * (1 + i % 2) for i, c in enumerate(key)]
        assert sum(d // 10 + d % 10 for d in digits) % 10 == 0
    for month, (rows, expected) in MONTHS.items():  # every month keeps every line, and they net to zero
        outside = [r for r in rows if r["מספר מנה"] != 9998]
        assert sum((r["חובה שקל"] or D(0)) - (r["זכות שקל"] or D(0)) for r in outside) == 0
        assert sum(d - c for d, c, *_ in expected.values()) == 0, month
        assert sum(line[2] for line in expected.values()) == len(outside), month


# ---- one cell, and the salary list ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("raw,kept", [
    ("40017", "40017"), (" 40017 ", "40017"), ("７２００５", "72005"), ("AB-77", "AB-77"), ("10091", "10091"),
    ("039337423", None), ("123456782", None), ("39337423", None), ("200150", None),  # six digits or more, no shape
    ("51-234567-8", None), ("0393 37423", None), ("039-337-423", None),  # an ID written in groups is one number
    ("393374.23", None),  # an ID as an amount: a shape the cleaning keeps, so the key's own digit rule refuses it
    ("2026-10-01", None),  # a date: kept by the cleaning, never a key here
    ("a@b.co.il", None), ("AB#12", None), ("", None), ("   ", None), ("x" * 65, None),
])
def test_a_key_is_kept_only_in_the_copys_form_and_never_when_it_could_be_an_id(raw, kept):
    assert storable_key(raw) == kept


def test_the_cells_are_read_as_the_live_answer_carries_them():
    assert [sort_of(v) for v in (105, "105", D("105"), 105.0, " 942 ", None, "", "abc", True, 10.5, 1000000000, -1, "-1")] == [
        105, 105, 105, 105, 942, None, None, None, None, None, None, None, None]  # -1 is the merged line's, never a row's
    assert [money_of(v) for v in (None, "", D("48213.77"), 12, 1935.47, "1,935.47", "1935.47-", "-3.5")] == [
        D(0), D(0), D("48213.77"), D(12), D("1935.47"), D("1935.47"), D("-1935.47"), D("-3.5")]
    assert [money_of(v) for v in ("abc", True, float("nan"), D("Infinity"), [1], "1.2.3")] == [None] * 6
    assert [whole_of(v) for v in (9998, "9998", D("9998.0"), 9998.0, " 9998 ", "9998א", D("9998.5"), False)] == [
        9998, 9998, 9998, 9998, 9998, None, None, None]


@pytest.mark.parametrize("text,state,codes", [
    ("731,610,733", "set", {731, 610, 733}), (" 731 , 610 ", "set", {731, 610}), ("731,", "set", {731}),
    ("731,731", "set", {731}), ("-5", "set", {-5}),
    (None, "missing", set()), ("", "missing", set()), ("   ", "missing", set()),
    ("731,abc", "bad", set()), ("731;610", "bad", set()), ("731 610", "bad", set()), (",", "bad", set()),
    ("731,-1", "bad", set()), ("1000000000", "bad", set()), ("7.5", "bad", set()), ("שכר", "bad", set()),
])
def test_the_salary_codes_are_an_explicit_list_or_nothing(text, state, codes):
    assert salary_codes(text) == (state, frozenset(codes))


def one_side(amounts, index) -> dict:
    """A line's accounts, each with its own sum on one side (0 the debit, 1 the credit) and nothing on the other."""
    return {f"acct{i}": [D(a), D(0)] if index == 0 else [D(0), D(a)] for i, a in enumerate(amounts)}


@pytest.mark.parametrize("amounts,thin", [
    ((), False),  # a side no account fills is empty and exposes no one
    (("70.01",), True), (("70.01", "-70.01"), True),  # (a): one or two accounts fill it, whatever their total
    (("500.00", "-300.00", "-200.00"), False),  # (b) never: a total of zero has no one near it
    (("5000.00", "300.00", "-300.00"), True),  # the review's: three fill it, two cancel, the total is one account's own
    (("900.00", "250.00", "-150.00"), False),  # T 1000.00: one account exactly a tenth away, |T - s| = T / 10, is not near
    (("900.01", "250.00", "-150.01"), True),  # T 1000.00: one account 99.99 away, just under a tenth
    (("800.00", "100.00", "100.00"), False),  # T 1000.00: two accounts (800 + 100) exactly a tenth away
    (("800.00", "100.01", "99.99"), True),  # T 1000.00: two accounts (800 + 100.01) 99.99 away
    (("-900.00", "-250.00", "150.00"), False), (("-900.01", "-250.00", "150.01"), True),  # T -1000.00: a tenth of |T|
    (("500.00", "300.00", "200.00"), False),  # T 1000.00 is 500 + 500, but an account is never its own partner
    (("500.00", "500.00", "60.00"), True),  # T 1060.00: two accounts of one sum are two (1000.00, 60.00 away)
    # T 950.00, a tenth 95.00: 400.00 + 500.00 is 50.00 away. The two largest by value (10000.00 + 500.00) are 9550.00
    # away and the two largest by size (10000.00 - 10000.00) 950.00, so only a search over every two accounts finds it
    (("10000.00", "-10000.00", "400.00", "500.00", "50.00"), True),
])
def test_a_side_is_thin_when_one_or_two_accounts_fill_it_or_hold_its_total_to_within_a_tenth(amounts, thin):
    for index in (0, 1):  # the debit and the credit are read alike
        assert ledger_totals._thin(one_side(amounts, index)) is thin, index
    # in any order of the accounts, and with accounts of no sum on that side beside them (they fill nothing)
    reordered = one_side(amounts[::-1], 0)
    reordered.update({f"none{i}": [D(0), D(0)] for i in range(3)})
    assert ledger_totals._thin(reordered) is thin


# ---- the vendor --------------------------------------------------------------------------------------------------------


class Books:
    """lb19 for the ledger report: createSession, and exportData answering the rows the request's four parameters cut -
    dates and sort codes - at most `cap` of them, ordered by date. No rows is "data not found", in `empty`'s form."""

    def __init__(self, rows=ROWS, cap=10000):
        self.rows = list(rows)
        self.cap = cap
        self.calls = []  # (sort_from, sort_to, date_from, date_to) per exportData
        self.requests = []
        self.empty = "error"  # or "status": the two forms the vendor's "data not found" may take
        self.fail = None  # an httpx.Response every exportData answers with
        self.ignore = set()  # "sort", "date": cuts the vendor does not apply

    def handler(self, request):
        self.requests.append(request)
        path = request.url.path.lstrip("/")
        if path.startswith("createSession/"):
            return httpx.Response(200, text=SESSION)
        if path != "ExportDataApi/exportData":
            return httpx.Response(404, text="not found")
        body = json.loads(request.read())
        assert body["datafile"] == DATAFILE
        value = {p["p_name"]: p["defVal"] for p in body["parameters"]}
        low, high = int(value["__MUSTACH_P0__"]), int(value["__MUSTACH_P1__"])
        first, last = value["__MUSTACH_P2__"].strip("'"), value["__MUSTACH_P3__"].strip("'")
        self.calls.append((low, high, first, last))
        if self.fail is not None:
            return self.fail

        def day(r):  # a row keyed by caption or by column id; a cell that is no date or no code is never cut away
            when = r.get("תאריך", r.get("COL0"))
            return when[:10] if isinstance(when, str) and re.match(r"\d{4}-\d{2}-\d{2}", when) else None

        def code(r):
            value = r.get("קוד מיון", r.get("COL3"))
            return value if isinstance(value, int) else None

        rows = [r for r in sorted(self.rows, key=lambda r: str(r.get("תאריך", r.get("COL0"))))
                if ("date" in self.ignore or day(r) is None or first <= day(r) <= last)
                and ("sort" in self.ignore or code(r) is None or low <= code(r) <= high)]
        if not rows:
            if self.empty == "error":
                return httpx.Response(200, json={"statusCode": 500, "status": {"errors": "Data not found"}})
            return httpx.Response(200, json={"statusCode": 200, "status": "data not found"})
        text = json.dumps({"statusCode": 200, "repdata": rows[: self.cap]}, default=float, ensure_ascii=False)
        return httpx.Response(200, text=text)

    def client(self, clock=time.time):
        return WizClient("lb19.wizcloud.co.il", DEMO, KEY, transport=httpx.MockTransport(self.handler), clock=clock)


def secret(**changes) -> str:
    data = copy.deepcopy(DEFINITION)
    data.update(changes)
    return json.dumps(data, ensure_ascii=False)


@pytest.fixture
def pg():
    drop_all(TEST_DATABASE_URL)
    from app.migrate import apply_migrations

    apply_migrations(TEST_DATABASE_URL)
    yield TEST_DATABASE_URL
    drop_all(TEST_DATABASE_URL)


def run(books, url, months, text=SECRET, box_s=60.0, salary=SALARY):
    return ledger_totals.run(books.client(), LedgerStore(url), DEMO, months, text, time.monotonic() + box_s, trigger="test",
                             salary_text=salary)


def dry(books, months, text=SECRET, salary=SALARY, box_s=60.0):
    return ledger_totals.listing(books.client(), months, text, time.monotonic() + box_s, salary_text=salary)


def sql(url, query, args=()):
    with psycopg.connect(url, autocommit=True) as conn:
        cur = conn.execute(query, args)
        return cur.fetchall() if cur.description else None


def lines(url, month="2026-10") -> dict:
    return {r[0]: tuple(r[1:]) for r in sql(
        url, "SELECT sort_code, debit, credit, row_count, account_count, masked_keys FROM ledger_totals "
             "WHERE company = %s AND month = %s ORDER BY sort_code", (DEMO, ledger_store.first_day(month)))}


def month_row(url, month="2026-10"):
    rows = sql(url, "SELECT row_count, sort_codes, kept_lines, merged_codes, merged_state, masked_keys, batch_9998_rows, "
                    "batch_9998_debit, batch_9998_credit, parts, run_id FROM ledger_months WHERE company = %s AND month = %s",
               (DEMO, ledger_store.first_day(month)))
    return rows[0] if rows else None


def counts(url, *tables) -> dict:
    return {t: sql(url, f"SELECT count(*) FROM {t}")[0][0] for t in tables}


def every_table(url) -> str:
    """Every row of every table the migrations make, as text."""
    out = []
    with psycopg.connect(url, autocommit=True) as conn:
        for table in all_tables():
            out += [r[0] for r in conn.execute(f"SELECT row_to_json(t)::text FROM {table} t").fetchall()]
    return "\n".join(out)


def stored(answer: dict) -> dict:
    return {"stored": True, "reason": None, "cut": False, **answer}


# ---- a month, summed and merged ---------------------------------------------------------------------------------------


@needs_pg
def test_a_month_is_summed_by_sort_code_with_one_merged_line_and_only_the_three_things_are_kept(pg):
    books = Books()
    out = run(books, pg, ["2026-10"])
    assert (out["ok"], out["skipped"], out["reasons"]) == (True, False, [])
    assert out["months"]["2026-10"] == stored({"parts": 1, **OCTOBER_COUNTS})
    assert books.calls == [(-999999999, 999999999, "2026-10-01", "2026-10-31")]  # one call, the whole range
    assert lines(pg) == OCTOBER_LINES
    assert month_row(pg)[:9] == (42, 10, 4, 7, "kept", 2, *OCTOBER_BATCH)
    # (b) every code seen, by its name; (c) every key that is no ID number, by its own code - never the merged line's
    assert dict(sql(pg, "SELECT sort_code, name FROM ledger_sort_codes WHERE company = %s", (DEMO,))) == NAMES
    assert dict(sql(pg, "SELECT account_key, sort_code FROM ledger_accounts WHERE company = %s", (DEMO,))) == MAP
    # nothing else: no column for a name, details, a reference, a counter-account or a cost code
    columns = {c for cols in LEDGER_TABLES.values() for c in cols}
    for forbidden in ("account_name", "name_of_account", "details", "reference", "counter", "cost_code", "account_total"):
        assert not any(forbidden in c for c in columns), forbidden
    assert scan(every_table(pg), PRIVATE - SAID - set(MAP)) == []


@needs_pg
def test_the_salary_codes_are_merged_even_when_they_could_stand(pg):
    # a listed code goes into the merged line even when it could stand: 410 listed beside the salary codes (three accounts,
    # no two of them nine tenths of its credit). By hand, the merged line is October's with 410's line in it: debit
    # 92695.01 + 0, credit 89150.10 + 6506.99 = 95657.09, rows 26 + 3, accounts 16 + 3 (40001-40003 are in no other merged
    # code), masked 2 + 0. Its credit's two largest are still 37151.21 + 36124.04 = 73275.25, the rest 22381.84 against a
    # tenth of 9565.71: kept
    out = run(Books(), pg, ["2026-10"], salary="731,610,733,410")
    assert lines(pg) == {942: OCTOBER_LINES[942], 951: OCTOBER_LINES[951],
                         MERGED_CODE: (D("92695.01"), D("95657.09"), 29, 19, 2)}
    assert out["months"]["2026-10"]["merged_by"] == {"salary": 4, "small": 2, "side": 2, "balance": 0}
    # only the manager's code listed: the salary expense and its liabilities have three accounts each and one side each,
    # but two of them hold more than nine tenths of it (731's debit: 38213.77 + 6003.41 = 44217.18 of 48213.77, the rest
    # 3996.59 under a tenth, 4821.38; 610's credit: 37151.21 + 7062.57 = 44213.78, the rest 3999.99), so both go in as
    # `side` and the month is the same. Standing, each would show the month's salaries whole (the liabilities code mirrors
    # the expense), so both are listed all the same
    out = run(Books(), pg, ["2026-10"], salary="733")
    assert lines(pg) == OCTOBER_LINES
    assert out["months"]["2026-10"]["merged_by"] == {"salary": 1, "small": 2, "side": 4, "balance": 0}
    run(Books(), pg, ["2026-10"])  # all three listed: both go into the merged line
    assert 731 not in lines(pg) and 610 not in lines(pg) and lines(pg) == OCTOBER_LINES


@needs_pg
def test_a_small_line_is_merged_and_a_line_of_three_accounts_stands(pg):
    out = run(Books(), pg, ["2026-10"])
    held = lines(pg)
    assert 300 not in held and 720 not in held  # the bank (one account) and VAT (two)
    assert held[410][3] == 3  # three income accounts: exactly at the threshold, a line of its own
    assert out["months"]["2026-10"]["merged_by"]["small"] == 2


@needs_pg
def test_a_line_whose_side_few_accounts_fill_is_merged_whole(pg):
    out = run(Books(), pg, ["2026-10"])
    held = lines(pg)
    # 105: three cards debited, one student's receipt the only credit; 960: three expenses, two refunds the only credits
    assert 105 not in held and 960 not in held
    assert held[951][1] == 0 and held[410][0] == 0  # a side no account fills is empty and exposes no one: they stand
    assert out["months"]["2026-10"]["merged_by"]["side"] == 2
    for amount in ("3543.81", "97.22", "7678.25", "2161.67"):  # the receipt, the refunds, 105's and 960's totals
        assert amount not in every_table(pg)


@needs_pg
def test_a_merged_line_that_cannot_stand_takes_the_standing_lines_until_it_does(pg):
    out = run(Books(), pg, ["2026-06", "2026-07", "2026-08", "2026-09"])
    assert out["ok"] is True
    assert out["months"]["2026-06"] == stored({"parts": 1, **JUNE_COUNTS})  # two accounts whose sides cancel to zero
    assert out["months"]["2026-09"] == stored({"parts": 1, **SEPTEMBER_COUNTS})  # the bank alone
    assert out["months"]["2026-07"] == stored({"parts": 1, **JULY_COUNTS})  # three accounts, the bank alone in the debit
    assert out["months"]["2026-08"] == stored({"parts": 1, **AUGUST_COUNTS})  # nothing to merge
    assert lines(pg, "2026-09") == SEPTEMBER_LINES and lines(pg, "2026-07") == JULY_LINES
    assert lines(pg, "2026-08") == AUGUST_LINES and lines(pg, "2026-06") == JUNE_LINES
    assert month_row(pg, "2026-09")[2:5] == (2, 2, "kept") and month_row(pg, "2026-07")[2:5] == (2, 3, "kept")
    assert month_row(pg, "2026-06")[2:5] == (2, 3, "kept")
    assert month_row(pg, "2026-08")[2:5] == (2, 0, "none")
    text = every_table(pg)
    # July's bank, the one account of its old merged line's debit: its own debit, its own net, and its own credit (the
    # three payments, which 942's debit was while it stood). The merged line's debit, 1070.97, is four accounts' now
    for amount in ("590.59", "110.21", "480.38"):
        assert amount not in text


def row(day, key, code, debit=None, credit=None, number=89900):
    """One row of the report keyed by caption, for a month built by hand with accounts the fixture does not have."""
    return dict(zip(CAPTIONS, [f"{day}T00:00:00", f"{day}T00:00:00", key, code, "קוד", "חשבון", "", "שורה", "61290", "",
                               debit, credit, "", 7001, "יומן", number]))


def finished(rows: list, month: str, salary=frozenset()) -> ledger_totals.MonthResult:
    """A month read whole from these rows by sort-code range, summed and merged, in memory."""
    def call(parameters, deadline):
        low, high = int(parameters[0]["defVal"]), int(parameters[1]["defVal"])
        return {"repdata": [r for r in rows if low <= r["קוד מיון"] <= high]}

    result, acc = ledger_totals.read_month(call, ledger.load_definition(SECRET), month, time.monotonic() + 60)
    assert acc is not None, result.reason
    return acc.finish(result, salary)


def test_the_standing_lines_go_into_the_merged_line_the_fewest_accounts_first():
    """The bank pays four expense accounts (520, four accounts) and three suppliers invoice three more (942 and 951, three
    each). The bank alone (300) cannot stand. 942 goes in first - three accounts, before 520's four though its code is
    higher, and before 951's three by its code - and one is enough: the merged line's credit, the bank's 1000.00 and the
    invoices 100.00 + 200.00 + 300.00, is 1600.00 over four accounts, its two largest 1300.00, the rest 300.00 against a
    tenth of 160.00. In code order (520 first) or 951 first, the bank's credit would still stand alone, and a second line
    would go in."""
    rows = ([row("2026-02-03", "60231", 300, credit=D("1000.00"))]
            + [row("2026-02-03", key, 520, debit=D("250.00")) for key in ("52101", "52102", "52103", "52104")]
            + [row("2026-02-05", key, 942, credit=D(a), number=89901) for key, a in
               (("71884", "100.00"), ("72005", "200.00"), ("73010", "300.00"))]
            + [row("2026-02-05", key, 951, debit=D(a), number=89901) for key, a in
               (("50120", "100.00"), ("50121", "200.00"), ("50122", "300.00"))])
    result = finished(rows, "2026-02")
    assert [(x.sort_code, x.debit, x.credit, x.rows, x.accounts) for x in result.lines] == [
        (520, D("1000.00"), D("0"), 4, 4), (951, D("600.00"), D("0"), 3, 3), (MERGED_CODE, D("0"), D("1600.00"), 4, 4)]
    assert (result.merged, result.kept_lines, result.merged_codes) == ("kept", 3, 2)
    assert result.merged_by == {"salary": 0, "small": 1, "side": 0, "balance": 1}


# ---- one person's money, never a stored side: the hostile review of 26.11 -----------------------------------------------


def own_sums(rows: list) -> dict:
    """{sort code: {account: [debit, credit]}} of every account in these rows outside batch 9998, from the fixture alone."""
    out: dict = {}
    for r in rows:
        if r["מספר מנה"] == 9998:
            continue
        held = out.setdefault(r["קוד מיון"], {}).setdefault(ledger_totals.identity(r["מפתח חשבון"]), [D(0), D(0)])
        held[0] += r["חובה שקל"] or D(0)
        held[1] += r["זכות שקל"] or D(0)
    return out


def exposed(rows: list, held: dict) -> list:
    """Every stored side one or two accounts hold, by brute force over the rows and apart from the module's search: a side
    some accounts fill but fewer than three, or whose total one account's sum, or two accounts' sum, comes within a tenth
    of. A kept line's accounts are its code's; the merged line's, those of every code that is not a line of its own. Each
    stored line is first checked to be exactly its accounts' sums, and their number."""
    own = own_sums(rows)
    out = []
    for code, (debit, credit, _rows, accounts, _masked) in held.items():
        mine: dict = {}
        for c, of_code in own.items():
            if (c not in held) if code == MERGED_CODE else (c == code):
                for who, sums in of_code.items():
                    into = mine.setdefault(who, [D(0), D(0)])
                    into[0] += sums[0]
                    into[1] += sums[1]
        assert len(mine) == accounts, code
        for index, total in ((0, debit), (1, credit)):
            sums = [s[index] for s in mine.values() if s[index] != 0]
            assert sum(sums, D(0)) == total, (code, index)
            near = sums + [a + b for a, b in itertools.combinations(sums, 2)]
            if 0 < len(sums) < 3 or (total != 0 and any(abs(total - s) * 10 < abs(total) for s in near)):
                out.append((code, ("debit", "credit")[index]))
    return out


# The review's months: a lecturer's invoice, and last month's misposting moved between two other suppliers as +300/-300
CANCELLED_SUPPLIER = (
    tx("2026-05-10", 89501, 7801, [("50120", D("3000.00"), None), ("50121", D("1000.00"), None),
                                   ("50122", D("1000.00"), None), ("71884", None, D("5000.00"))], "חשבונית מרצה")
    + tx("2026-05-12", 89502, 7801, [("72005", None, D("300.00")), ("73010", None, D("-300.00"))], "סיווג מחדש"))
# the manager's pay from the bank, and two reclassifications between the VAT accounts, +500/-500 and +200/-200
CANCELLED_VAT = (
    tx("2026-03-28", 89301, 7701, [("40099", D("31000.29"), None), ("60231", None, D("31000.29"))], "תלוש הנהלה")
    + tx("2026-03-05", 89302, 7701, [("70010", D("500.00"), None), ("70011", D("-500.00"), None)], "סיווג מחדש")
    + tx("2026-03-06", 89303, 7701, [("70010", None, D("200.00")), ("70011", None, D("-200.00"))], "סיווג מחדש"))
# the manager's pay accrued against the three liabilities accounts, and a purchase from the three suppliers
MANAGER_ALONE = (
    tx("2026-04-28", 89401, 7901, [("40099", D("31000.29"), None), ("61001", None, D("25000.00")),
                                   ("61002", None, D("3000.29")), ("61003", None, D("3000.00"))], "תלוש הנהלה")
    + tx("2026-04-10", 89402, 7901, [("50120", D("100.00"), None), ("50121", D("200.00"), None),
                                     ("50122", D("300.00"), None), ("71884", None, D("100.00")),
                                     ("72005", None, D("200.00")), ("73010", None, D("300.00"))], "רכש"))


@needs_pg
def test_a_side_whose_other_accounts_cancel_is_never_one_accounts_own_amount(pg):
    """The review's finding 1, in a line that stood: supplier 71884 credited 5,000.00, and 72005 and 73010 credited 300.00
    and -300.00. 942's credit is filled by three accounts and its total is 71884's own 5,000.00 - by count it stood. By
    rule (b) one account is within a tenth of it (it is all of it): 942 goes in as `side`. Its merged line cannot stand,
    and with 951 in it its credit is still 71884's, so no line is left and the month keeps none: 951 kept beside a
    withheld merged line would give it away, minus 951's net being 71884's invoice."""
    month = "2026-05"
    out = run(Books(CANCELLED_SUPPLIER), pg, [month])
    assert out["ok"] is True and out["months"][month]["stored"] is True
    assert out["months"][month]["merged_by"] == {"salary": 0, "small": 0, "side": 1, "balance": 1}
    held = lines(pg, month)
    assert held == {} and month_row(pg, month)[2:5] == (0, 2, "withheld")
    assert exposed(CANCELLED_SUPPLIER, held) == [] and "5000.00" not in every_table(pg)
    own = {(code, who): sums for code, accounts in own_sums(CANCELLED_SUPPLIER).items() for who, sums in accounts.items()}
    for code, (debit, credit, *_rest) in held.items():  # the review's own check: no stored side is one account's sum
        for index, total in ((0, debit), (1, credit)):
            filling = [k for (c, k), s in own.items() if c == code and s[index] != 0]
            assert not (total != 0 and len(filling) > 1 and any(own[(code, k)][index] == total for k in filling))


@needs_pg
def test_the_merged_line_kept_by_cancelling_accounts_is_never_the_managers_own_pay(pg):
    """The review's finding 1, in the merged line, where the salaries are. The merged line (733 the salary code, 300 the
    bank's one account, 720 VAT's two) has four accounts and each side filled by three: by count it was kept, its debit
    the manager's own pay and its credit the bank's own. By rule (b) each side is one account's to within a tenth (the VAT
    accounts cancel), so it does not stand, and with no other line in the month it is withheld."""
    month = "2026-03"
    out = run(Books(CANCELLED_VAT), pg, [month])
    assert out["ok"] is True and out["months"][month]["stored"] is True
    assert month_row(pg, month)[2:5] == (0, 3, "withheld") and lines(pg, month) == {}
    manager = own_sums(CANCELLED_VAT)[733]["40099"][0]
    assert manager == D("31000.29") and "31000.29" not in every_table(pg)
    # The review's month with three supplier invoices in it, so that 942 and 951 stand and the merged line can be kept,
    # as the review ran it. With 942 in it, its debit is still the manager's to within a tenth; with 951 too, by hand:
    #   debit:  31000.29 + 500.00 - 500.00, and the invoices 3100.17 + 2950.40 + 3410.05 = 9460.62, so 40460.91 over
    #           six accounts: the nearest pair 31000.29 + 3410.05 = 34410.34, 6050.57 away against a tenth of 4046.09;
    #   credit: the bank 31000.29, VAT 200.00 - 200.00, the invoices 9460.62, so 40460.91, the nearest pair the same;
    #   rows 6 + 6 = 12, accounts 4 + 3 + 3 = 10: kept, the month's one line, and no side of it one account's
    rows = CANCELLED_VAT + invoices("2026-03", (89304, 89305, 89306), 7702, ("3100.17", "2950.40", "3410.05"))
    out = run(Books(rows), pg, [month])
    assert out["ok"] is True and month_row(pg, month)[4] == "kept"
    merged = lines(pg, month)[MERGED_CODE]
    assert merged[0] != manager, "the stored merged line's debit is the manager's own pay, %s" % merged[0]
    assert lines(pg, month) == {MERGED_CODE: (D("40460.91"), D("40460.91"), 12, 10, 0)}
    assert out["months"][month]["merged_by"] == {"salary": 1, "small": 2, "side": 0, "balance": 2}
    assert exposed(rows, lines(pg, month)) == []
    run(Books(CANCELLED_VAT), pg, [month])  # the invoices gone again: the month keeps no line, and the kept one leaves
    assert lines(pg, month) == {} and month_row(pg, month)[2:5] == (0, 3, "withheld")


@needs_pg
def test_a_withheld_merged_line_of_one_account_is_not_derived_from_the_kept_lines(pg):
    """The review's finding 2: only the manager's code a salary code (as the salary test runs it), her pay accrued against
    the liabilities code 610, three accounts credited. 610's credit is hers to within a tenth (25000.00 + 3000.29 of
    31000.29: `side`), and the merged line, her account and 610's, cannot stand: its debit is hers alone. It took 942 and
    then 951 and still does not stand (her 31000.29 is within a tenth of its debit, 31600.29), so the month keeps no line.
    Under the old rule 951 and 942 stood beside a withheld merged line, and minus their nets was her pay to the agora."""
    month = "2026-04"
    out = run(Books(MANAGER_ALONE), pg, [month], salary="733")
    assert out["ok"] is True and month_row(pg, month)[4] == "withheld"
    held = lines(pg, month)
    assert MERGED_CODE not in held
    revealed = -sum(d - c for d, c, *_ in held.values())  # the month balances: minus the kept nets is the withheld net
    manager = own_sums(MANAGER_ALONE)[733]["40099"]
    assert revealed != manager[0] - manager[1], "the manager's own pay, %s, follows from the stored lines" % revealed
    assert held == {} and month_row(pg, month)[2:5] == (0, 4, "withheld")  # no line of the month: nothing to derive from
    assert out["months"][month]["merged_by"] == {"salary": 1, "small": 0, "side": 1, "balance": 2}
    assert "31000.29" not in every_table(pg)


@needs_pg
def test_the_balance_reveals_nothing_a_month_does_not_keep(pg):
    """The leak of 26.11, closed: a month's lines net to zero, so a withheld cell's net would be minus the kept lines'. The
    merged line is never withheld while another line stands, so a month keeps every line or none: in every month the kept
    nets sum to zero and nothing is derived - not September's bank (its own net, under the old rule), not April's
    manager. Each kept line is recomputed from the rows, and no side of it is one or two accounts' to within a tenth."""
    months = {**{m: rows for m, (rows, _) in MONTHS.items()}, "2026-04": MANAGER_ALONE}
    run(Books(ROWS + MANAGER_ALONE), pg, sorted(months))
    for month, rows in months.items():
        held = lines(pg, month)
        kept, state = month_row(pg, month)[2], month_row(pg, month)[4]
        assert -sum(d - c for d, c, *_ in held.values()) == 0, month  # the balance gives nothing that is not kept
        assert kept == len(held), month
        if state == "withheld":
            assert held == {}, month  # every line of the month withheld
        else:  # every row outside batch 9998 is in a kept line: no line of the month is withheld
            assert sum(line[2] for line in held.values()) == sum(1 for r in rows if r["מספר מנה"] != 9998), month
        assert exposed(rows, held) == [], month
    assert {m: month_row(pg, m)[4] for m in sorted(months)} == {
        "2026-04": "withheld", "2026-06": "kept", "2026-07": "kept", "2026-08": "none", "2026-09": "kept", "2026-10": "kept"}
    text = every_table(pg)
    for amount in ("31000.29", "590.59", "110.21"):  # the manager's pay; July's bank's own debit and net
        assert amount not in text


@needs_pg
def test_nothing_is_stored_without_the_salary_codes_and_the_counts_are_said(pg):
    for salary, reason in ((None, "salary_codes_not_set"), ("", "salary_codes_not_set"), ("731,abc", "salary_codes_bad")):
        out = run(Books(), pg, ["2026-09", "2026-10"], salary=salary)
        assert (out["ok"], out["reasons"]) == (False, [reason])
        assert out["months"]["2026-10"] == {"stored": False, "reason": reason, "cut": False, "parts": 1, "rows": 42,
                                            "sort_codes": 10, "kept_lines": None, "merged_codes": None, "merged": None,
                                            "merged_by": None, "masked_keys": 2, "batch_9998_rows": 4}
        assert counts(pg, "ledger_totals", "ledger_months", "ledger_sort_codes", "ledger_accounts") == dict.fromkeys(
            ("ledger_totals", "ledger_months", "ledger_sort_codes", "ledger_accounts"), 0)
    assert sql(pg, "SELECT ok, reasons FROM ledger_runs ORDER BY run_id") == [
        (False, ["salary_codes_not_set"]), (False, ["salary_codes_not_set"]), (False, ["salary_codes_bad"])]


@needs_pg
@pytest.mark.parametrize("batch", [9998, "9998", " 9998", 9998.0])
def test_batch_9998_is_counted_apart_and_never_in_a_line(pg, batch):
    books = Books([dict(r, **{"מספר מנה": batch}) if r["מספר מנה"] == 9998 else r for r in ROWS])
    assert run(books, pg, ["2026-10"])["months"]["2026-10"]["batch_9998_rows"] == 4
    assert lines(pg)[951] == (D("5463.18"), D("0"), 3, 3, 0)  # without the transfer: rows 3, credit 0
    assert 990 not in lines(pg)  # the P&L account's code is seen only in the transfer: no line, not merged either
    assert month_row(pg)[6:9] == OCTOBER_BATCH


@needs_pg
def test_a_transfer_batch_the_report_is_defined_without_changes_nothing(pg):
    out = run(Books([r for r in ROWS if r["מספר מנה"] != 9998]), pg, ["2026-10"])
    assert out["months"]["2026-10"]["batch_9998_rows"] == 0 and out["months"]["2026-10"]["rows"] == 38
    assert lines(pg) == OCTOBER_LINES  # the lines are the same with or without the transfer
    assert month_row(pg)[6:9] == (0, D(0), D(0))


@needs_pg
def test_the_database_refuses_a_line_of_fewer_than_three_and_a_key_that_could_be_an_id(pg):
    with pytest.raises(psycopg.errors.CheckViolation):
        sql(pg, "INSERT INTO ledger_totals (company, month, sort_code, debit, credit, row_count, account_count, masked_keys, "
                "run_id) VALUES ('x', '2026-10-01', 1, 1, 1, 2, 2, 0, 1)")
    for key in ("039337423", "#", "", "3933742.3", "12-34-56"):
        with pytest.raises(psycopg.errors.CheckViolation):
            sql(pg, "INSERT INTO ledger_accounts (company, account_key, sort_code, run_id) VALUES ('x', %s, 1, 1)", (key,))
    with pytest.raises(psycopg.errors.CheckViolation):
        sql(pg, "INSERT INTO ledger_totals (company, month, sort_code, debit, credit, row_count, account_count, masked_keys, "
                "run_id) VALUES ('x', '2026-10-02', 1, 1, 1, 3, 3, 0, 1)")
    with pytest.raises(psycopg.errors.NotNullViolation):  # no side is ever withheld on its own any more
        sql(pg, "INSERT INTO ledger_totals (company, month, sort_code, credit, row_count, account_count, masked_keys, "
                "run_id) VALUES ('x', '2026-10-01', 1, 1, 3, 3, 0, 1)")
    with pytest.raises(psycopg.errors.CheckViolation):
        sql(pg, "INSERT INTO ledger_months (company, month, row_count, sort_codes, kept_lines, merged_codes, merged_state, "
                "masked_keys, batch_9998_rows, batch_9998_debit, batch_9998_credit, parts, run_id) "
                "VALUES ('x', '2026-10-01', 1, 1, 1, 1, 'partly', 0, 0, 0, 0, 1, 1)")


@needs_pg
def test_an_id_like_key_is_never_stored_hashed_or_kept_in_any_form(pg):
    out = run(Books(), pg, list(MONTHS))
    assert out["months"]["2026-10"]["masked_keys"] == 2 and lines(pg)[MERGED_CODE][4] == 2
    text = every_table(pg)
    for key in ID_KEYS:
        forms = {key, key.lstrip("0"), *(key[i:i + 7] for i in range(len(key) - 6))}
        for form in list(forms):
            for algo in ("sha256", "sha1", "md5", "sha512", "blake2b"):
                forms.add(hashlib.new(algo, form.encode()).hexdigest())
                forms.add(hashlib.new(algo, form.encode()).hexdigest()[:16])
        assert [f for f in forms if f in text] == []
    assert sql(pg, "SELECT count(*) FROM ledger_accounts WHERE account_key !~ '^[0-9]{5}$'")[0][0] == 0
    assert scan(dump_answer(out), EVERYTHING) == [] and not re.search(r"\d\.\d", dump_answer(out))  # no amount at all


def dump_answer(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


# ---- the dry listing -----------------------------------------------------------------------------------------------------

LISTED = [  # Sep and Oct: per code its name, its distinct accounts in both months (masked counted too), on the list or not
    (105, 3, 2, False), (300, 1, 0, False), (410, 3, 0, False), (610, 3, 0, True), (720, 2, 0, False), (731, 3, 0, True),
    (733, 1, 0, True), (942, 3, 0, False), (951, 3, 0, False), (960, 3, 0, False)]


@needs_pg
def test_the_dry_listing_names_each_code_counts_its_accounts_and_stores_nothing(pg):
    books = Books()
    out = dry(books, ["2026-09", "2026-10"])
    assert (out["ok"], out["dry"], out["reasons"], out["salary_codes"], out["salary_codes_unseen"]) == (
        True, True, [], "set", 0)
    assert out["codes"] == [{"sort_code": c, "name": NAMES[c], "accounts": n, "masked_keys": m, "salary": s}
                            for c, n, m, s in LISTED]  # 990, seen only in the transfer, is no line: not listed
    assert out["months"]["2026-10"] == {"read": True, "reason": None, "cut": False, "parts": 1, "rows": 42, "sort_codes": 10}
    assert counts(pg, *LEDGER_TABLES) == dict.fromkeys(LEDGER_TABLES, 0)
    text = dump_answer(out)
    assert scan(text, PRIVATE - SAID) == [] and [t for t in TOTALS if t in text] == []  # no row's value
    assert not re.search(r"\d\.\d", text)  # and no amount at all: every number it says is a count
    assert set(out) == {"ok", "dry", "reasons", "salary_codes", "months", "codes", "salary_codes_unseen"}
    # without the list: the same categories, and nothing said of a list
    out = dry(books, ["2026-09", "2026-10"], salary=None)
    assert out["ok"] and out["salary_codes"] == "missing" and "salary_codes_unseen" not in out
    assert [(c["sort_code"], c["accounts"]) for c in out["codes"]] == [(c, n) for c, n, _, _ in LISTED]
    assert all("salary" not in c for c in out["codes"])
    assert dry(books, ["2026-10"], salary="731,9999")["salary_codes_unseen"] == 1  # a listed code no row carries


@needs_pg
def test_a_month_the_dry_listing_cannot_read_whole_is_said_and_lists_nothing(pg):
    books = Books([r for r in ROWS if not (r["מספר תנועה"] == 88405 and r["מפתח חשבון"] == "70010")])  # unbalanced
    out = dry(books, ["2026-09", "2026-10"])
    assert (out["ok"], out["reasons"]) == (False, ["unbalanced"])
    assert out["months"]["2026-10"]["read"] is False and out["months"]["2026-10"]["reason"] == "unbalanced"
    assert [c["sort_code"] for c in out["codes"]] == [300, 942, 951]  # September's only
    assert dry(Books(), ["2026-10"], text=None)["reasons"] == ["not_set"]


# ---- a month, replaced -------------------------------------------------------------------------------------------------


@needs_pg
def test_a_rerun_replaces_the_month_whole_with_no_duplicates(pg):
    first = run(Books(), pg, ["2026-09", "2026-10"])
    second = run(Books(), pg, ["2026-10"])
    assert lines(pg) == OCTOBER_LINES and lines(pg, "2026-09") == SEPTEMBER_LINES
    assert sql(pg, "SELECT count(*) FROM ledger_totals")[0][0] == len(OCTOBER_LINES) + len(SEPTEMBER_LINES)
    assert sql(pg, "SELECT DISTINCT run_id FROM ledger_totals WHERE month = '2026-10-01'") == [(second["run_id"],)]
    assert month_row(pg)[10] == second["run_id"] and month_row(pg, "2026-09")[10] == first["run_id"]  # September untouched
    # the books changed: the voucher card's invoice was deleted, and a payment's amount was corrected
    changed = [r for r in ROWS if r["מספר תנועה"] != 88408]
    changed = [dict(r, **{"חובה שקל": D("1517.00")}) if r["מספר תנועה"] == 88403 and r["חובה שקל"] else
               dict(r, **{"זכות שקל": D("1517.00")}) if r["מספר תנועה"] == 88403 and r["זכות שקל"] else r for r in changed]
    out = run(Books(changed), pg, ["2026-10"])
    assert out["months"]["2026-10"]["merged_by"] == {"salary": 3, "small": 4, "side": 1, "balance": 0}
    # 410 lost an account: it left the store as a line and went into the merged line, as did 105
    assert lines(pg) == {942: (D("2961.70"), D("5463.18"), 6, 3, 0), 951: (D("5463.18"), D("0"), 3, 3, 0),
                         MERGED_CODE: (D("91511.74"), D("94473.44"), 26, 16, 2)}


@needs_pg
def test_a_month_that_is_refused_keeps_what_was_stored(pg):
    kept = run(Books(), pg, ["2026-10"])
    unbalanced = [r for r in ROWS if not (r["מספר תנועה"] == 88405 and r["מפתח חשבון"] == "70010")]  # VAT's line lost
    out = run(Books(unbalanced), pg, ["2026-10"])
    assert (out["ok"], out["reasons"]) == (False, ["unbalanced"])
    assert out["months"]["2026-10"]["stored"] is False and out["months"]["2026-10"]["rows"] == 41
    assert lines(pg) == OCTOBER_LINES and month_row(pg)[10] == kept["run_id"]


@needs_pg
@pytest.mark.parametrize("empty", ["error", "status"])
def test_data_not_found_is_an_empty_month_unless_the_month_held_rows(pg, empty):
    books = Books(SEPTEMBER)
    books.empty = empty
    out = run(books, pg, ["2026-10", "2026-12"])  # nothing in the books for either
    assert out["ok"] and out["months"]["2026-12"]["rows"] == 0 and out["months"]["2026-12"]["stored"] is True
    assert out["months"]["2026-12"]["merged"] == "none" and month_row(pg, "2026-12")[:5] == (0, 0, 0, 0, "none")
    run(Books(), pg, ["2026-10"])
    assert lines(pg) == OCTOBER_LINES
    out = run(books, pg, ["2026-10"])  # a month held whole now answers nothing: doubted, never trusted
    assert (out["ok"], out["reasons"], out["months"]["2026-10"]["rows"]) == (False, ["emptied"], 0)
    assert lines(pg) == OCTOBER_LINES


# ---- a month read whole, or not at all ---------------------------------------------------------------------------------


@needs_pg
def test_a_month_at_the_limit_is_split_by_sort_code_until_each_part_is_under_it(pg):
    books = Books(cap=8)
    out = run(books, pg, ["2026-10"], text=secret(limit="8"))
    month = out["months"]["2026-10"]
    assert out["ok"] and month["stored"] and not month["cut"] and month["parts"] == len(books.calls) > 1
    assert lines(pg) == OCTOBER_LINES and month["rows"] == 42
    # every part is a sort-code range of the month, the ranges cover the whole range once, and the leaves were under the cap
    assert {c[2:] for c in books.calls} == {("2026-10-01", "2026-10-31")}
    leaves = [c[:2] for c in books.calls if sum(1 for r in OCTOBER if c[0] <= r["קוד מיון"] <= c[1]) < 8]
    assert sorted(leaves)[0][0] == -999999999 and sorted(leaves)[-1][1] == 999999999
    assert all(a[1] + 1 == b[0] for a, b in zip(sorted(leaves), sorted(leaves)[1:]))
    assert sum(1 for r in OCTOBER for low, high in leaves if low <= r["קוד מיון"] <= high) == 42


@needs_pg
def test_a_part_of_one_sort_code_at_the_limit_is_cut_and_nothing_is_stored(pg):
    run(Books(), pg, ["2026-10"])
    out = run(Books(cap=7), pg, ["2026-10"], text=secret(limit="7"))  # the bank alone holds seven rows
    assert (out["ok"], out["reasons"]) == (False, ["cut"])
    month = out["months"]["2026-10"]
    assert (month["stored"], month["cut"], month["rows"], month["kept_lines"]) == (False, True, None, None)
    assert lines(pg) == OCTOBER_LINES  # what was stored stays


@needs_pg
def test_a_split_that_needs_too_many_calls_is_cut(pg, monkeypatch):
    monkeypatch.setattr(ledger_totals, "MAX_PARTS", 3)
    books = Books(cap=8)
    out = run(books, pg, ["2026-10"], text=secret(limit="8"))
    assert out["reasons"] == ["cut"] and out["months"]["2026-10"]["cut"] and len(books.calls) == 3
    assert lines(pg) == {} and month_row(pg) is None


@needs_pg
def test_an_answer_at_exactly_the_limit_is_cut_and_under_it_is_whole(pg):
    out = run(Books(OCTOBER, cap=42), pg, ["2026-10"], text=secret(limit="42"))  # 42 rows at a limit of 42: may be cut
    assert out["ok"] and out["months"]["2026-10"]["parts"] > 1
    out = run(Books(OCTOBER, cap=43), pg, ["2026-10"], text=secret(limit="43"))
    assert out["ok"] and out["months"]["2026-10"]["parts"] == 1


def big_month(month: str, count: int) -> list:
    """`count` supplier invoices, two rows each: the three suppliers in turn, each credited (942) and its expense account
    debited (951) 12.34, 23.45 and 34.56 an invoice, on a day from the 1st to the 28th."""
    out = []
    for i in range(count):
        expense, supplier = (("50120", "71884"), ("50121", "72005"), ("50122", "73010"))[i % 3]
        amount = (D("12.34"), D("23.45"), D("34.56"))[i % 3]
        out += tx(f"{month}-{i % 28 + 1:02d}", 90000 + i, 7001, [(expense, amount, None), (supplier, None, amount)],
                  "חשבונית רכש")
    return out


def test_a_part_of_split_at_rows_is_never_summed_whole():
    """The file's limit (10000) is not sent: an answer of SPLIT_AT rows may be one the vendor cut at a cap of its own, so
    it is split as an answer at the limit is. Two rows fewer is one part, summed whole."""
    definition = ledger.load_definition(SECRET)
    assert (definition.limit, ledger_totals.SPLIT_AT) == (10000, 900)
    for count, parts in ((450, 3), (449, 1)):  # 900 rows, then 898 (a transaction is two rows)
        rows = big_month("2026-05", count)
        asked = []

        def call(parameters, deadline):
            low, high = int(parameters[0]["defVal"]), int(parameters[1]["defVal"])
            asked.append((low, high))
            return {"repdata": [r for r in rows if low <= r["קוד מיון"] <= high]}

        result, acc = ledger_totals.read_month(call, definition, "2026-05", time.monotonic() + 60)
        assert (result.reason, result.parts, acc.rows) == (None, parts, 2 * count), count
        assert asked == [(-999999999, 999999999), (-999999999, 942), (943, 999999999)][:parts], count


@needs_pg
def test_a_cap_of_the_vendors_own_never_cuts_a_month_silently(pg):
    """The review's finding 3. A cap of the vendor's own cuts an answer at exactly the cap, between two rows ordered by
    date; a cut between two whole transactions leaves the month balanced, and the month was stored as whole. October 2026
    came back whole in one answer of 902 rows (26.11), so no fixed cap is under 902 (the review's 30 is ruled out). Here
    the vendor caps at 1000 a month of 600 invoices, 1200 rows: its first answer is the first 500 invoices, balanced. It is
    split at 942 (942 and 951 the codes it carried), and each half, 600 rows, is summed. By hand: each supplier has 200
    invoices, 200 x 12.34 = 2468.00, 200 x 23.45 = 4690.00 and 200 x 34.56 = 6912.00, so 14070.00 on 942's credit and on
    951's debit, 600 rows and three accounts each; the two largest 11602.00, the rest 2468.00 against a tenth of 1407.00."""
    month = "2026-05"
    books = Books(big_month(month, 600), cap=1000)
    out = run(books, pg, [month])
    assert out["ok"] is True and out["months"][month] == stored({
        "parts": 3, "rows": 1200, "sort_codes": 2, "kept_lines": 2, "merged_codes": 0, "merged": "none",
        "merged_by": {"salary": 0, "small": 0, "side": 0, "balance": 0}, "masked_keys": 0, "batch_9998_rows": 0})
    assert [c[:2] for c in books.calls] == [(-999999999, 999999999), (-999999999, 942), (943, 999999999)]
    assert lines(pg, month) == {942: (D("0"), D("14070.00"), 600, 3, 0), 951: (D("14070.00"), D("0"), 600, 3, 0)}
    assert month_row(pg, month)[0] == 1200
    # one sort code of SPLIT_AT rows or more in a month is `cut` (a split by date would be the next step): 900 invoices,
    # 942's half alone is 900 rows. Nothing is stored and what was stays
    books = Books(big_month(month, 900), cap=1000)
    out = run(books, pg, [month])
    assert (out["ok"], out["reasons"], out["months"][month]["cut"], out["months"][month]["parts"]) == (False, ["cut"], True, 2)
    assert [c[:2] for c in books.calls] == [(-999999999, 999999999), (-999999999, 942)]
    assert lines(pg, month) == {942: (D("0"), D("14070.00"), 600, 3, 0), 951: (D("14070.00"), D("0"), 600, 3, 0)}


@needs_pg
def test_a_date_cut_the_vendor_ignored_is_outside_and_never_summed(pg):
    books = Books()
    books.ignore = {"date"}
    out = run(books, pg, ["2026-10"])
    assert (out["ok"], out["reasons"]) == (False, ["outside"])
    assert out["months"]["2026-10"]["outside"] == len(ROWS) - len(OCTOBER) and lines(pg) == {}


def test_a_part_whose_rows_fall_outside_its_sort_codes_is_outside():
    """A sort-code cut narrower than the whole range was never seen live (26.11): a part answered with codes it did not
    ask for is never summed."""
    definition = ledger.load_definition(secret(limit="6"))
    asked = []
    answers = iter([{"repdata": OCTOBER[:6]},  # the whole range at the limit: codes 942 and 951, split at 942
                    {"repdata": [OCTOBER[0], OCTOBER[2]]}])  # the part up to 942, answered with two rows of 951

    def call(parameters, deadline):
        asked.append((parameters[0]["defVal"], parameters[1]["defVal"]))
        return next(answers)

    result, acc = ledger_totals.read_month(call, definition, "2026-10", time.monotonic() + 60)
    assert (result.reason, result.stored, result.outside, result.parts, acc) == ("outside", False, 2, 2, None)
    assert asked == [("-999999999", "999999999"), ("-999999999", "942")]
    assert result.lines == [] and result.keys == {} and "outside" in result.answer()


@needs_pg
@pytest.mark.parametrize("change,field", [
    ({"תאריך": "לא תאריך"}, "date"), ({"קוד מיון": "קוד"}, "sort_code"), ({"קוד מיון": MERGED_CODE}, "sort_code"),
    ({"מפתח חשבון": None}, "key"), ({"מפתח חשבון": "  "}, "key"), ({"חובה שקל": "מאה"}, "debit"),
    ({"זכות שקל": True}, "credit"), ({"מספר מנה": "מנה"}, "batch"),
])
def test_a_row_that_cannot_be_read_stores_nothing_and_says_which_field(pg, change, field):
    rows = [dict(r, **change) if r["מספר תנועה"] == 88402 and r["מפתח חשבון"] == "72005" else r for r in ROWS]
    out = run(Books(rows), pg, ["2026-10"])
    assert (out["ok"], out["reasons"]) == (False, ["unreadable"])
    assert out["months"]["2026-10"]["unreadable"] == {field: 1} and lines(pg) == {}


@needs_pg
def test_rows_without_a_column_the_totals_read_store_nothing(pg):
    rows = [{k: v for k, v in r.items() if k != "מספר מנה"} for r in ROWS]  # the batch: 9998 could not be told apart
    out = run(Books(rows), pg, ["2026-10"])
    assert out["reasons"] == ["columns"] and lines(pg) == {}
    rows = [{k: v for k, v in r.items() if k != "שם קוד מיון"} for r in ROWS]  # the name is not needed for a total
    assert run(Books(rows), pg, ["2026-10"])["ok"] is True and lines(pg) == OCTOBER_LINES


@needs_pg
def test_the_rows_read_by_column_id_or_by_place_are_summed_the_same(pg):
    by_id = [{f"COL{i}": r[c] for i, c in enumerate(CAPTIONS)} for r in ROWS]
    assert run(Books(by_id), pg, ["2026-10"])["ok"] and lines(pg) == OCTOBER_LINES


@needs_pg
def test_vendor_failures_and_the_box_store_nothing_and_say_a_closed_word(pg):
    books = Books()
    books.fail = httpx.Response(502, text="bad gateway")
    out = run(books, pg, ["2026-09", "2026-10"])
    assert out["reasons"] == ["wizcloud"] and out["months"]["2026-10"]["kind"] == "http_error"
    books.fail = httpx.Response(200, json={"statusCode": 500, "status": {"errors": "params not found or not match"}})
    out = run(books, pg, ["2026-10"])
    assert (out["months"]["2026-10"]["reason"], out["months"]["2026-10"]["vendor"]) == ("wizcloud", "params_mismatch")
    books.fail = httpx.Response(200, json={"statusCode": 200, "status": "No Permission"})
    out = run(books, pg, ["2026-10"])
    assert (out["months"]["2026-10"]["reason"], out["months"]["2026-10"]["vendor"]) == ("bad_answer", "no_permission")
    books.fail = None
    out = run(books, pg, ["2026-09", "2026-10"], box_s=0.5)
    assert out["reasons"] == ["out_of_time"] and all(m["reason"] == "out_of_time" for m in out["months"].values())
    assert lines(pg) == {} and lines(pg, "2026-09") == {}
    assert sql(pg, "SELECT ok, reasons, finished_at IS NOT NULL FROM ledger_runs ORDER BY run_id DESC LIMIT 1") == [
        (False, ["out_of_time"], True)]


@needs_pg
def test_a_definition_that_cannot_run_opens_no_session_and_writes_nothing(pg):
    books = Books()
    for text, reason in ((None, "not_set"), ("{}", "bad_definition"), (secret(limit=""), "no_limit")):
        out = run(books, pg, ["2026-10"], text=text)
        assert (out["ok"], out["reasons"]) == (False, [reason])
    assert books.requests == [] and sql(pg, "SELECT count(*) FROM ledger_runs")[0][0] == 0


@needs_pg
def test_a_second_run_of_the_company_is_skipped(pg):
    with LedgerStore(pg).session(DEMO) as held:
        assert held is not None
        out = run(Books(), pg, ["2026-10"])
    assert (out["ok"], out["skipped"], out["months"]) == (True, True, {})
    assert run(Books(), pg, ["2026-10"])["skipped"] is False


@needs_pg
def test_a_crash_finishes_the_runs_row_and_says_its_class(pg, monkeypatch):
    def boom(*args, **kwargs):
        raise ValueError("ישראל ישראלי 48213.77")

    monkeypatch.setattr(ledger_totals, "read_month", boom)
    with pytest.raises(ValueError):
        run(Books(), pg, ["2026-10"])
    assert sql(pg, "SELECT ok, reasons, note FROM ledger_runs") == [(False, ["crashed"], "the run stopped on ValueError")]


# ---- the routes --------------------------------------------------------------------------------------------------------


@pytest.fixture
def app(monkeypatch, clock):
    from fastapi.testclient import TestClient

    from app import main

    books = Books()
    monkeypatch.setenv("WIZ_PRIVATE_KEY", KEY)
    monkeypatch.setenv("BOOKS_SYNC_KEY", SYNC_KEY)
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("WIZ_LEDGER_REPORT", SECRET)
    monkeypatch.setenv("LEDGER_SALARY_CODES", SALARY)
    monkeypatch.delenv("WIZ_COMPANY", raising=False)
    monkeypatch.delenv("WIZ_SERVER", raising=False)
    main._cache.update(at=0.0, result=None)
    main._store_cache.update(at=0.0, key=None, value=None)
    main.app.state.transport = httpx.MockTransport(books.handler)
    main.app.state.clock = clock
    main.app.state.client = None
    main.app.state.store_factory = lambda cfg: FakeStore()
    clock.t = 1_794_000_000.0  # 2026-11-06 in Israel: the last two months are October and November
    yield TestClient(main.app), books
    main.app.state.transport = None
    main.app.state.clock = __import__("time").time
    main.app.state.client = None
    main.app.state.store_factory = None
    main._cache.update(at=0.0, result=None)
    main._store_cache.update(at=0.0, key=None, value=None)


AUTH = {"Authorization": f"Bearer {SYNC_KEY}"}
RUN = "/api/books/ledger/run"
TOTALS_URL = "/api/books/ledger/totals"


@needs_pg
def test_both_routes_need_the_sync_key_before_anything(app, pg):
    client, books = app
    for headers in ({}, {"Authorization": "Bearer wrong"}, {"Authorization": SYNC_KEY}):
        assert client.post(RUN, json={"months": ["2026-10"]}, headers=headers).status_code == 401
        assert client.post(RUN, json={"months": ["2026-10"], "dry": True}, headers=headers).status_code == 401
        assert client.post(RUN, content=b"{not json", headers=headers).status_code == 401
        assert client.get(TOTALS_URL + "?from=2026-10", headers=headers).status_code == 401
    assert books.requests == [] and sql(pg, "SELECT count(*) FROM ledger_runs")[0][0] == 0


@needs_pg
def test_the_run_route_runs_the_last_two_months_in_israel_time_and_says_counts_only(app, pg, caplog):
    client, books = app
    caplog.set_level(logging.DEBUG)
    r = client.post(RUN, headers=AUTH)
    body = r.json()
    assert r.status_code == 200 and body["ok"] is True and body["company"] == "demo"
    assert list(body["months"]) == ["2026-10", "2026-11"] and body["months"]["2026-11"]["rows"] == 0
    assert body["months"]["2026-10"] == stored({"parts": 1, **OCTOBER_COUNTS})
    assert lines(pg) == OCTOBER_LINES
    body = client.post(RUN, json={"months": ["2026-10", "2026-09"]}, headers=AUTH).json()
    assert list(body["months"]) == ["2026-09", "2026-10"]
    assert scan(r.text, EVERYTHING) == [] and scan(caplog.text, EVERYTHING) == [] and KEY not in caplog.text


@needs_pg
def test_the_dry_listing_through_the_route_stores_nothing_and_says_no_amount(app, pg, caplog):
    client, books = app
    caplog.set_level(logging.DEBUG)
    r = client.post(RUN, json={"months": ["2026-09", "2026-10"], "dry": True}, headers=AUTH)
    body = r.json()
    assert r.status_code == 200 and body["dry"] is True and body["ok"] is True and body["company"] == "demo"
    assert [(c["sort_code"], c["name"], c["accounts"], c["salary"]) for c in body["codes"]] == [
        (c, NAMES[c], n, s) for c, n, _, s in LISTED]
    assert counts(pg, *LEDGER_TABLES) == dict.fromkeys(LEDGER_TABLES, 0)
    assert scan(r.text, PRIVATE - SAID) == [] and [t for t in TOTALS if t in r.text] == []
    assert not re.search(r"\d\.\d", json.dumps({k: v for k, v in body.items() if k != "request_s"}))
    assert scan(caplog.text, EVERYTHING) == []
    assert client.post(RUN, json={"dry": False, "months": ["2026-10"]}, headers=AUTH).json()["months"]["2026-10"]["stored"]


@pytest.mark.parametrize("now,months", [
    ("2026-10-31T22:30:00+00:00", ["2026-10", "2026-11"]),  # already November in Israel
    ("2026-10-31T21:30:00+00:00", ["2026-09", "2026-10"]),
    ("2027-01-01T00:00:00+02:00", ["2026-12", "2027-01"]),
    ("2026-03-31T22:30:00+00:00", ["2026-03", "2026-04"]),  # summer time: UTC+3
])
def test_the_default_months_are_israels(now, months):
    from datetime import datetime

    from app.main import default_months

    assert default_months(datetime.fromisoformat(now).timestamp()) == months


@needs_pg
@pytest.mark.parametrize("content", [
    b"{not json", b"\xff\xfe", b"[]", b'"2026-10"', b'{"months": []}', b'{"months": "2026-10"}', b'{"months": ["2026-13"]}',
    b'{"months": ["2026-10", "2026-10"]}', b'{"months": [202610]}', b'{"months": ["2026-10"], "company": "analiza"}',
    b'{"company": "demo"}', b'{"dry": "true"}', b'{"dry": 1, "months": ["2026-10"]}', b'{"dry": null}',
    json.dumps({"months": [f"2026-{m:02d}" for m in range(1, 13)] + ["2025-12"]}).encode(),
])
def test_a_body_that_is_not_one_is_bad_request_never_echoed_and_never_run(app, pg, content):
    client, books = app
    r = client.post(RUN, content=content, headers={**AUTH, "Content-Type": "application/json"})
    body = r.json()
    assert r.status_code == 200 and (body["ok"], body["reasons"], body["months"]) == (False, ["bad_request"], {})
    assert "202610" not in r.text and "analiza" not in r.text and "2026-13" not in r.text
    assert books.requests == [] and sql(pg, "SELECT count(*) FROM ledger_runs")[0][0] == 0


@needs_pg
def test_twelve_months_are_one_run(app, pg):
    client, books = app
    months = [f"2026-{m:02d}" for m in range(1, 13)]
    body = client.post(RUN, json={"months": months}, headers=AUTH).json()
    assert body["ok"] and list(body["months"]) == months and len(books.calls) == 12


@needs_pg
def test_a_crash_in_the_route_is_a_200_with_its_class_only(app, pg, monkeypatch):
    client, _ = app

    def boom(*args, **kwargs):
        raise ValueError("ישראל ישראלי 48213.77")

    monkeypatch.setattr(ledger_totals, "read_month", boom)
    for payload in ({"months": ["2026-10"]}, {"months": ["2026-10"], "dry": True}):
        r = client.post(RUN, json=payload, headers=AUTH)
        assert r.status_code == 200 and (r.json()["reasons"], r.json()["error"]) == (["crashed"], "ValueError")
        assert "48213" not in r.text and "ישראל" not in r.text


@needs_pg
def test_the_totals_route_says_the_kept_lines_and_the_merged_one_and_never_a_row(app, pg, caplog):
    client, _ = app
    caplog.set_level(logging.DEBUG)
    client.post(RUN, json={"months": ["2026-09", "2026-10"]}, headers=AUTH)
    r = client.get(TOTALS_URL + "?from=2026-09&to=2026-10", headers=AUTH)
    body = r.json()
    assert body["ok"] is True and (body["from"], body["to"]) == ("2026-09", "2026-10")
    got = {(x["month"], x["sort_code"]): (x["debit"], x["credit"], x["rows"], x["accounts"], x["masked_keys"], x["name"],
                                          x["merged"]) for x in body["lines"]}
    want = {(m, code): (format(d, "f"), format(c, "f"), rows, accounts, masked,
                        MERGED_NAME if code == MERGED_CODE else NAMES[code], code == MERGED_CODE)
            for m, held in (("2026-09", SEPTEMBER_LINES), ("2026-10", OCTOBER_LINES))
            for code, (d, c, rows, accounts, masked) in held.items()}
    assert got == want and [x["month"] for x in body["lines"]] == sorted(x["month"] for x in body["lines"])
    october, september = body["months"]["2026-10"], body["months"]["2026-09"]
    assert (october["batch_9998_rows"], october["batch_9998_debit"], october["batch_9998_credit"]) == (4, "5463.18", "5463.18")
    assert (october["merged"], october["merged_codes"], october["kept_lines"]) == ("kept", 7, 4)
    assert (september["merged"], september["merged_codes"], september["kept_lines"]) == ("kept", 2, 2)
    assert scan(r.text, PRIVATE - SAID) == [] and scan(caplog.text, EVERYTHING) == []
    assert client.get(TOTALS_URL + "?from=2026-10", headers=AUTH).json()["months"].keys() == {"2026-10"}
    assert client.get(TOTALS_URL + "?from=2025-01&to=2025-03", headers=AUTH).json()["lines"] == []


@needs_pg
@pytest.mark.parametrize("query", ["", "?from=2026-13", "?from=2026-10&to=2026-09", "?to=2026-10", "?from=2020-01&to=2026-10",
                                   "?from=x"])
def test_a_totals_query_that_is_not_one_is_bad_request(app, pg, query):
    client, _ = app
    body = client.get(TOTALS_URL + query, headers=AUTH).json()
    assert (body["ok"], body["error"]) == (False, "bad_request")


@needs_pg
def test_health_says_the_last_run_the_months_held_and_the_salary_list_set_never_a_total(app, pg, monkeypatch):
    from app import main

    client, books = app
    r = client.get("/health")
    assert r.json()["books"]["ledger"] == {"salary_codes": "set", "last_run": None, "months": []}
    assert {t: r.json()["store"]["tables"][t] for t in LEDGER_TABLES} == dict.fromkeys(LEDGER_TABLES, "present")
    assert SALARY not in r.text and "731, 610" not in r.text
    books.rows = [r for r in ROWS if not (r["מספר תנועה"] == 88405 and r["מפתח חשבון"] == "70010")]  # October unbalanced
    client.post(RUN, json={"months": ["2026-09", "2026-10"]}, headers=AUTH)
    main._store_cache.update(at=0.0, key=None, value=None)
    r = client.get("/health")
    ledger_block = r.json()["books"]["ledger"]
    assert ledger_block["months"] == ["2026-09"] and ledger_block["last_run"]["ok"] is False
    assert ledger_block["last_run"]["reasons"] == ["unbalanced"] and ledger_block["last_run"]["finished_at"]
    assert scan(r.text, PRIVATE) == [] and [t for t in TOTALS if t in r.text] == []
    for value, state in (("", "missing"), ("731,x", "bad"), ("731", "set")):
        monkeypatch.setenv("LEDGER_SALARY_CODES", value)
        assert client.get("/health").json()["books"]["ledger"]["salary_codes"] == state


@needs_pg
def test_health_without_the_ledgers_tables_lists_them_absent_and_still_says_the_list(app, pg):
    from app import main

    client, _ = app
    sql(pg, "DROP TABLE " + ", ".join(LEDGER_TABLES))
    main._store_cache.update(at=0.0, key=None, value=None)
    h = client.get("/health").json()
    assert h["books"]["ledger"] == {"salary_codes": "set"}
    assert {t: h["store"]["tables"][t] for t in LEDGER_TABLES} == dict.fromkeys(LEDGER_TABLES, "absent")
    assert set(LEDGER_TABLES) <= set(h["store"]["missing_columns"])


# ---- the migration -----------------------------------------------------------------------------------------------------


def test_the_migration_only_creates_and_names_the_stores_columns():
    from app.migrate import MIGRATIONS_DIR, checked_tables

    sql_text = (MIGRATIONS_DIR / "007_ledger_totals.sql").read_text(encoding="utf-8")
    code = re.sub(r"--[^\n]*", "", sql_text)
    assert not re.search(r"\b(DROP|ALTER|DELETE|UPDATE|INSERT|TRUNCATE|GRANT|COPY)\b", code.upper())
    statements = [s.strip() for s in code.split(";") if s.strip()]
    assert all(s.startswith(("CREATE TABLE IF NOT EXISTS", "CREATE INDEX IF NOT EXISTS")) for s in statements)
    for table, columns in LEDGER_TABLES.items():
        body = code[code.index(f"CREATE TABLE IF NOT EXISTS {table} ("):]
        body = body[: body.index(";")]
        assert [line.split()[0] for line in body.splitlines()[1:] if line.startswith("  ") and not line.startswith("  PRIMARY")
                and not line.lstrip().startswith(("AND", "CHECK"))] == list(columns), table
        assert checked_tables()[table] == columns
    assert "account_count >= 3" in code and "007_ledger_totals.sql" in [p.name for p in MIGRATIONS_DIR.glob("*.sql")]


@needs_pg
def test_the_migration_runs_twice(pg):
    from app.migrate import MIGRATIONS_DIR, apply_migrations

    sql_text = (MIGRATIONS_DIR / "007_ledger_totals.sql").read_text(encoding="utf-8")
    with psycopg.connect(pg, autocommit=True) as conn:
        conn.execute(sql_text)
        conn.execute(sql_text)
    assert apply_migrations(pg)["missing"] == []
