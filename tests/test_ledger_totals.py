"""B3's totals (backlog 0301; ADR-0187 §2-§5): a month of the ledger report summed by sort code and replaced whole
(app/books/ledger_totals.py, app/books/ledger_store.py, migrations/007), the routes POST /api/books/ledger/run and GET
/api/books/ledger/totals, and /health's books.ledger. Synthetic data only: two months of a small company's books, every
transaction balanced, with the expected totals written out by hand below - never computed by the code under test."""

import copy
import hashlib
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
from app.books.ledger_totals import money_of, sort_of, storable_key, whole_of
from app.wizcloud import WizClient
from tests.conftest import KEY, SESSION, TEST_DATABASE_URL, FakeStore, all_tables, drop_all, needs_pg
from tests.test_ledger import CAPTIONS, DATAFILE, DEFINITION, SECRET, scan, targets

DEMO = "wizdb2245n4"
SYNC_KEY = "ledger-totals-sync-key-0123456789abcdef"
D = Decimal

# key: (sort code, the sort code's name, the account's name). The names of accounts share no word with the names of codes,
# so a scan for an account's name never mistakes a code's name, which the totals do say, for a leak.
ACCOUNTS = {
    "71884": (942, "ספקי ציוד", "אופק תקשורת בעמ"),
    "72005": (942, "ספקי ציוד", "מחשבים פלוס בעמ"),
    "50120": (951, "הוצאות ציוד", "מדפסות וטונר"),
    "50121": (951, "הוצאות ציוד", "נתבים ומתגים"),
    "60231": (300, "מזומנים ושווי מזומנים", "עוש מרכזי"),
    "039337423": (105, "לקוחות תלמידים", "יעל אברהמי"),
    "123456782": (105, "לקוחות תלמידים", "נועם ברקוביץ"),
    "10091": (105, "לקוחות תלמידים", "שוברי מימון"),
    "40001": (410, "הכנסות", "קורסים ערב"),
    "40002": (410, "הכנסות", "סדנאות בוקר"),
    "70010": (720, "מעמ", "עסקאות לתשלום"),
    "40017": (731, "הוצאות משכורת", "שכר עבודה"),
    "40018": (731, "הוצאות משכורת", "עובדים לשלם"),
    "40099": (733, "תגמול מנהלים", "דנה מזרחי"),
    "89999": (990, "רווח והפסד", "העברה שנתית"),
}
EXTRA = {"40098": (733, "תגמול מנהלים", "רון אלמוג")}  # a second manager, only where a test adds one
ID_KEYS = ("039337423", "123456782")  # student cards keyed by ID numbers (ADR-0173 §1): valid check digits both
FULLWIDTH = {"72005": "７２００５"}  # one row writes a key in full-width digits: one account, kept in the copy's form


def tx(day, number, batch, lines, details="תנועה", ref="61290", cost=""):
    """One balanced transaction: [(key, debit, credit)], each line a row of the report keyed by caption (26.11)."""
    assert sum((d or D(0)) for _, d, _ in lines) == sum((c or D(0)) for _, _, c in lines), "a transaction balances"
    rows = []
    for i, (key, debit, credit) in enumerate(lines):
        code, code_name, name = {**ACCOUNTS, **EXTRA}[key]
        counter = next(k for k, _, _ in lines if k != key)
        rows.append(dict(zip(CAPTIONS, [
            f"{day}T00:00:00", f"{day}T00:00:00", FULLWIDTH.get(key, key) if number == 88404 else key, code, code_name, name,
            cost, details, ref, "7731" if i else "", debit, credit, counter, batch, "יומן", number])))
    return rows


OCTOBER = (
    tx("2026-10-05", 88401, 7712, [("50120", D("1935.47"), None), ("71884", None, D("1935.47"))], "חשבונית רכש 88412", "61377",
       "PRJ-77")
    + tx("2026-10-12", 88402, 7712, [("50121", D("2210.19"), None), ("72005", None, D("2210.19"))], "חשבונית רכש 90127")
    + tx("2026-10-20", 88403, 7740, [("71884", D("1517.38"), None), ("60231", None, D("1517.38"))], "העברה בנקאית")
    + tx("2026-10-21", 88404, 7740, [("72005", D("803.61"), None), ("60231", None, D("803.61"))], "העברה בנקאית")
    + tx("2026-10-06", 88405, 7712, [("039337423", D("3543.81"), None), ("40001", None, D("3003.23")),
                                     ("70010", None, D("540.58"))], "חשבונית קבלה", "62502", "PRJ-91")
    + tx("2026-10-07", 88406, 7712, [("60231", D("3543.81"), None), ("039337423", None, D("3543.81"))], "קבלה", "62503")
    + tx("2026-10-14", 88407, 7712, [("123456782", D("2951.17"), None), ("40001", None, D("2500.99")),
                                     ("70010", None, D("450.18"))], "חשבונית קבלה", "62511")
    + tx("2026-10-15", 88408, 7712, [("10091", D("1183.27"), None), ("40002", None, D("1002.77")),
                                     ("70010", None, D("180.50"))], "חשבונית שובר", "62519")
    + tx("2026-10-28", 88409, 7790, [("40017", D("48213.77"), None), ("40018", None, D("48213.77"))], "תלוש חודשי")
    + tx("2026-10-28", 88410, 7790, [("40099", D("31000.29"), None), ("60231", None, D("31000.29"))], "תלוש הנהלה")
    + tx("2026-10-31", 88411, 9998, [("89999", D("4145.66"), None), ("50120", None, D("1935.47")),
                                     ("50121", None, D("2210.19"))], "העברת הכנסות והוצאות", "")
)
SEPTEMBER = (
    tx("2026-09-10", 88301, 7701, [("50120", D("611.23"), None), ("71884", None, D("611.23"))], "חשבונית רכש 87110")
    + tx("2026-09-11", 88302, 7701, [("50121", D("702.45"), None), ("72005", None, D("702.45"))], "חשבונית רכש 87114")
)
# October, by hand: (sort code) -> (debit, credit, rows, accounts, masked keys). None: one account alone fills that side.
# 105: three cards debited, one credited (a receipt) - the credit is one student's payment, so it is withheld.
# 731: one account debited and one credited - each side is one account's own, so both are withheld.
# 951: batch 9998 credits both its accounts; outside the line, so its credit is 0 and its rows 2.
# 300 (the bank), 720 (VAT) and 733 (one manager's salary) hold one account each: never stored.
OCTOBER_LINES = {
    105: (D("7678.25"), None, 4, 3, 2),
    410: (D("0"), D("6506.99"), 3, 2, 0),
    731: (None, None, 2, 2, 0),
    942: (D("2320.99"), D("4145.66"), 4, 2, 0),
    951: (D("4145.66"), D("0"), 2, 2, 0),
}
OCTOBER_COUNTS = {"rows": 26, "sort_codes": 8, "kept_lines": 5, "single_account": 3, "single_sides": 3, "masked_keys": 2,
                  "batch_9998_rows": 3}
OCTOBER_BATCH = (3, D("4145.66"), D("4145.66"))
SEPTEMBER_LINES = {942: (D("0"), D("1313.68"), 2, 2, 0), 951: (D("1313.68"), D("0"), 2, 2, 0)}
NAMES = {code: name for code, name, _ in ACCOUNTS.values()}
MAP = {key: code for key, (code, _, _) in ACCOUNTS.items() if key not in ID_KEYS}  # (c): the keys that are no ID number

ROWS = OCTOBER + SEPTEMBER
# What a row holds that no answer may say: everything but the sort code and its name, which the totals route says by design.
PRIVATE = targets([{k: v for k, v in r.items() if k not in ("קוד מיון", "שם קוד מיון")} for r in ROWS]) - {"9998"}
EVERYTHING = targets(ROWS) - {"9998"}  # the transfer batch's number is a constant the answers name ("batch_9998_rows")
# What an answer may say: closed words, captions, field names. No fixture value may be a piece of one.
LEGIT = " ".join(CAPTIONS + list(ledger_totals.RUN_REASONS) + list(ledger_totals.FIELDS) + [
    "batch_9998_rows", "batch_9998_debit", "batch_9998_credit", "single_account", "single_sides", "masked_keys",
    "kept_lines", "sort_codes", "key_conflicts", "computed_at", "2026-10", "2026-09", "demo", "skipped", "request_s"])
# What the totals route says by design: the codes, their names, and the kept totals. Never what a row held besides.
SAID = {str(c) for c in NAMES} | set(NAMES.values()) | {
    format(v, "f") for lines in (OCTOBER_LINES, SEPTEMBER_LINES) for line in lines.values() for v in line[:2] if v} | {
    format(OCTOBER_BATCH[1], "f")}


def test_the_fixture_is_what_the_tests_say():
    assert len(OCTOBER) == OCTOBER_COUNTS["rows"] and len(PRIVATE) > 80
    assert [v for v in EVERYTHING if v in LEGIT] == []
    # an amount the totals say is a sum, never one row's: the one exception is the 9998 transfer's single debit row
    singles = {format(r[c], "f") for r in ROWS for c in ("חובה שקל", "זכות שקל") if r[c] is not None}
    assert singles & SAID == {"4145.66"}
    for key in ID_KEYS:  # an Israeli ID's check digit (a valid number makes the test a fair one)
        digits = [int(c) * (1 + i % 2) for i, c in enumerate(key)]
        assert sum(d // 10 + d % 10 for d in digits) % 10 == 0


# ---- one cell -----------------------------------------------------------------------------------------------------------


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
    assert [sort_of(v) for v in (105, "105", D("105"), 105.0, " 942 ", None, "", "abc", True, 10.5, 1000000000)] == [
        105, 105, 105, 105, 942, None, None, None, None, None, None]
    assert [money_of(v) for v in (None, "", D("48213.77"), 12, 1935.47, "1,935.47", "1935.47-", "-3.5")] == [
        D(0), D(0), D("48213.77"), D(12), D("1935.47"), D("1935.47"), D("-1935.47"), D("-3.5")]
    assert [money_of(v) for v in ("abc", True, float("nan"), D("Infinity"), [1], "1.2.3")] == [None] * 6
    assert [whole_of(v) for v in (9998, "9998", D("9998.0"), 9998.0, " 9998 ", "9998א", D("9998.5"), False)] == [
        9998, 9998, 9998, 9998, 9998, None, None, None]


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


def run(books, url, months, text=SECRET, box_s=60.0):
    return ledger_totals.run(books.client(), LedgerStore(url), DEMO, months, text, time.monotonic() + box_s, trigger="test")


def sql(url, query, args=()):
    with psycopg.connect(url, autocommit=True) as conn:
        cur = conn.execute(query, args)
        return cur.fetchall() if cur.description else None


def lines(url, month="2026-10") -> dict:
    return {r[0]: tuple(r[1:]) for r in sql(
        url, "SELECT sort_code, debit, credit, row_count, account_count, masked_keys FROM ledger_totals "
             "WHERE company = %s AND month = %s ORDER BY sort_code", (DEMO, ledger_store.first_day(month)))}


def month_row(url, month="2026-10"):
    rows = sql(url, "SELECT row_count, sort_codes, kept_lines, single_account, single_sides, masked_keys, batch_9998_rows, "
                    "batch_9998_debit, batch_9998_credit, parts, run_id FROM ledger_months WHERE company = %s AND month = %s",
               (DEMO, ledger_store.first_day(month)))
    return rows[0] if rows else None


def every_table(url) -> str:
    """Every row of every table the migrations make, as text."""
    out = []
    with psycopg.connect(url, autocommit=True) as conn:
        for table in all_tables():
            out += [r[0] for r in conn.execute(f"SELECT row_to_json(t)::text FROM {table} t").fetchall()]
    return "\n".join(out)


# ---- a month, summed ---------------------------------------------------------------------------------------------------


@needs_pg
def test_a_month_is_summed_by_sort_code_and_only_the_three_things_are_kept(pg):
    books = Books()
    out = run(books, pg, ["2026-10"])
    assert (out["ok"], out["skipped"], out["reasons"]) == (True, False, [])
    assert out["months"]["2026-10"] == {"stored": True, "reason": None, "cut": False, "parts": 1, **OCTOBER_COUNTS}
    assert books.calls == [(-999999999, 999999999, "2026-10-01", "2026-10-31")]  # one call, the whole range
    assert lines(pg) == OCTOBER_LINES
    assert month_row(pg)[:9] == (26, 8, 5, 3, 3, 2, *OCTOBER_BATCH)
    # (b) every code seen, by its name; (c) every key that is no ID number, by its code - in the copy's form
    assert dict(sql(pg, "SELECT sort_code, name FROM ledger_sort_codes WHERE company = %s", (DEMO,))) == NAMES
    assert dict(sql(pg, "SELECT account_key, sort_code FROM ledger_accounts WHERE company = %s", (DEMO,))) == MAP
    # nothing else: no column for a name, details, a reference, a counter-account or a cost code
    columns = {c for cols in LEDGER_TABLES.values() for c in cols}
    for forbidden in ("account_name", "name_of_account", "details", "reference", "counter", "cost_code", "account_total"):
        assert not any(forbidden in c for c in columns), forbidden
    assert scan(every_table(pg), PRIVATE - SAID - set(MAP)) == []


@needs_pg
def test_the_months_of_a_run_are_each_their_own(pg):
    out = run(Books(), pg, ["2026-09", "2026-10"])
    assert out["ok"] and list(out["months"]) == ["2026-09", "2026-10"]
    assert lines(pg, "2026-09") == SEPTEMBER_LINES and lines(pg) == OCTOBER_LINES
    assert out["months"]["2026-09"] == {"stored": True, "reason": None, "cut": False, "parts": 1, "rows": 4, "sort_codes": 2,
                                        "kept_lines": 2, "single_account": 0, "single_sides": 0, "masked_keys": 0,
                                        "batch_9998_rows": 0}
    assert month_row(pg, "2026-09")[6:9] == (0, D(0), D(0))


@needs_pg
@pytest.mark.parametrize("batch", [9998, "9998", " 9998", 9998.0])
def test_batch_9998_is_counted_apart_and_never_in_a_line(pg, batch):
    books = Books([dict(r, **{"מספר מנה": batch}) if r["מספר מנה"] == 9998 else r for r in ROWS])
    assert run(books, pg, ["2026-10"])["months"]["2026-10"]["batch_9998_rows"] == 3
    assert lines(pg)[951] == (D("4145.66"), D("0"), 2, 2, 0)  # without the transfer: rows 2, credit 0
    assert 990 not in lines(pg)  # the P&L account's code is seen only in the transfer: no line
    assert month_row(pg)[6:9] == OCTOBER_BATCH


@needs_pg
def test_a_transfer_batch_the_report_is_defined_without_changes_nothing(pg):
    books = Books([r for r in ROWS if r["מספר מנה"] != 9998])
    out = run(books, pg, ["2026-10"])
    assert out["months"]["2026-10"]["batch_9998_rows"] == 0 and out["months"]["2026-10"]["rows"] == 23
    assert lines(pg) == OCTOBER_LINES  # the lines are the same with or without the transfer
    assert month_row(pg)[6:9] == (0, D(0), D(0))


@needs_pg
def test_one_accounts_money_is_never_stored_alone(pg):
    run(Books(), pg, ["2026-10"])
    held = lines(pg)
    for code in (300, 720, 733):  # the bank, VAT, one manager's salary: one account each
        assert code not in held
    assert held[105][1] is None and held[731][:2] == (None, None)  # a side one account alone fills
    text = every_table(pg)
    # the manager, the salary, one student's payment, the bank's credits, VAT's credits
    for amount in ("31000.29", "48213.77", "3543.81", "33321.28", "1171.26"):
        assert amount not in text
    # a second manager on the code makes it a line: its debit has two accounts, its credit one (withheld). Two accounts
    # are not anonymity: 32200.60 is 96% the first manager's (no dominance rule: the report of 26.11). One alone again,
    # and the line leaves.
    second = tx("2026-10-29", 88412, 7790, [("40098", D("1200.31"), None), ("40099", None, D("1200.31"))], "תיקון")
    out = run(Books(ROWS + second), pg, ["2026-10"])
    assert out["months"]["2026-10"]["single_account"] == 2 and lines(pg)[733] == (D("32200.60"), None, 3, 2, 0)
    run(Books(), pg, ["2026-10"])
    assert 733 not in lines(pg) and lines(pg) == OCTOBER_LINES


@needs_pg
def test_the_database_refuses_a_line_of_one_account_and_a_key_that_could_be_an_id(pg):
    with pytest.raises(psycopg.errors.CheckViolation):
        sql(pg, "INSERT INTO ledger_totals (company, month, sort_code, debit, credit, row_count, account_count, masked_keys, "
                "run_id) VALUES ('x', '2026-10-01', 1, 1, 1, 1, 1, 0, 1)")
    for key in ("039337423", "#", "", "3933742.3", "12-34-56"):
        with pytest.raises(psycopg.errors.CheckViolation):
            sql(pg, "INSERT INTO ledger_accounts (company, account_key, sort_code, run_id) VALUES ('x', %s, 1, 1)", (key,))
    with pytest.raises(psycopg.errors.CheckViolation):
        sql(pg, "INSERT INTO ledger_totals (company, month, sort_code, row_count, account_count, masked_keys, run_id) "
                "VALUES ('x', '2026-10-02', 1, 2, 2, 0, 1)")


@needs_pg
def test_an_id_like_key_is_never_stored_hashed_or_kept_in_any_form(pg):
    out = run(Books(), pg, ["2026-09", "2026-10"])
    assert out["months"]["2026-10"]["masked_keys"] == 2 and lines(pg)[105][4] == 2
    text = every_table(pg)
    for key in ID_KEYS:
        forms = {key, key.lstrip("0"), *(key[i:i + 7] for i in range(len(key) - 6))}
        for form in list(forms):
            for algo in ("sha256", "sha1", "md5", "sha512", "blake2b"):
                forms.add(hashlib.new(algo, form.encode()).hexdigest())
                forms.add(hashlib.new(algo, form.encode()).hexdigest()[:16])
        assert [f for f in forms if f in text] == []
    assert sql(pg, "SELECT count(*) FROM ledger_accounts WHERE account_key !~ '^[0-9]{5}$'")[0][0] == 0
    assert dump_answer(out) and scan(dump_answer(out), EVERYTHING) == []


def dump_answer(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


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
    run(Books(changed), pg, ["2026-10"])
    held = lines(pg)
    assert held[105] == (D("6494.98"), None, 3, 2, 2)  # 7678.25 - 1183.27; the voucher card left the code
    assert held[942][0] == D("2320.61")  # 1517.00 + 803.61
    assert 410 not in held  # one income account left on the code: the line left the store
    assert sql(pg, "SELECT count(*) FROM ledger_totals WHERE month = '2026-10-01'")[0][0] == 4


@needs_pg
def test_a_month_that_is_refused_keeps_what_was_stored(pg):
    stored = run(Books(), pg, ["2026-10"])
    unbalanced = [r for r in ROWS if not (r["מספר תנועה"] == 88405 and r["מפתח חשבון"] == "70010")]  # VAT's line lost
    out = run(Books(unbalanced), pg, ["2026-10"])
    assert (out["ok"], out["reasons"]) == (False, ["unbalanced"])
    assert out["months"]["2026-10"]["stored"] is False and out["months"]["2026-10"]["rows"] == 25
    assert lines(pg) == OCTOBER_LINES and month_row(pg)[10] == stored["run_id"]


@needs_pg
@pytest.mark.parametrize("empty", ["error", "status"])
def test_data_not_found_is_an_empty_month_unless_the_month_held_rows(pg, empty):
    books = Books(SEPTEMBER)
    books.empty = empty
    out = run(books, pg, ["2026-10", "2026-12"])  # nothing in the books for either
    assert out["ok"] and out["months"]["2026-12"]["rows"] == 0 and out["months"]["2026-12"]["stored"] is True
    assert month_row(pg, "2026-12")[:3] == (0, 0, 0)
    run(Books(), pg, ["2026-10"])
    assert lines(pg) == OCTOBER_LINES
    out = run(books, pg, ["2026-10"])  # a month held whole now answers nothing: doubted, never trusted
    assert (out["ok"], out["reasons"], out["months"]["2026-10"]["rows"]) == (False, ["emptied"], 0)
    assert lines(pg) == OCTOBER_LINES


# ---- a month read whole, or not at all ---------------------------------------------------------------------------------


@needs_pg
def test_a_month_at_the_limit_is_split_by_sort_code_until_each_part_is_under_it(pg):
    books = Books(cap=6)
    out = run(books, pg, ["2026-10"], text=secret(limit="6"))
    month = out["months"]["2026-10"]
    assert out["ok"] and month["stored"] and not month["cut"] and month["parts"] == len(books.calls) > 1
    assert lines(pg) == OCTOBER_LINES and month["rows"] == 26
    # every part is a sort-code range of the month, the ranges cover the whole range once, and the leaves were under the cap
    assert {c[2:] for c in books.calls} == {("2026-10-01", "2026-10-31")}
    leaves = [c[:2] for c in books.calls if sum(1 for r in OCTOBER if c[0] <= r["קוד מיון"] <= c[1]) < 6]
    assert sorted(leaves)[0][0] == -999999999 and sorted(leaves)[-1][1] == 999999999
    assert all(a[1] + 1 == b[0] for a, b in zip(sorted(leaves), sorted(leaves)[1:]))
    assert sum(1 for r in OCTOBER for low, high in leaves if low <= r["קוד מיון"] <= high) == 26


@needs_pg
def test_a_part_of_one_sort_code_at_the_limit_is_cut_and_nothing_is_stored(pg):
    run(Books(), pg, ["2026-10"])
    books = Books(cap=3)  # 105 alone holds four rows
    out = run(books, pg, ["2026-10"], text=secret(limit="3"))
    assert (out["ok"], out["reasons"]) == (False, ["cut"])
    month = out["months"]["2026-10"]
    assert (month["stored"], month["cut"], month["rows"], month["kept_lines"]) == (False, True, None, None)
    assert lines(pg) == OCTOBER_LINES  # what was stored stays


@needs_pg
def test_a_split_that_needs_too_many_calls_is_cut(pg, monkeypatch):
    monkeypatch.setattr(ledger_totals, "MAX_PARTS", 3)
    books = Books(cap=6)
    out = run(books, pg, ["2026-10"], text=secret(limit="6"))
    assert out["reasons"] == ["cut"] and out["months"]["2026-10"]["cut"] and len(books.calls) == 3
    assert lines(pg) == {} and month_row(pg) is None


@needs_pg
def test_an_answer_at_exactly_the_limit_is_cut_and_under_it_is_whole(pg):
    books = Books(OCTOBER, cap=26)
    out = run(books, pg, ["2026-10"], text=secret(limit="26"))  # 26 rows at a limit of 26: may be cut, so split
    assert out["ok"] and out["months"]["2026-10"]["parts"] > 1
    books = Books(OCTOBER, cap=27)
    out = run(books, pg, ["2026-10"], text=secret(limit="27"))
    assert out["ok"] and out["months"]["2026-10"]["parts"] == 1


@needs_pg
def test_a_date_cut_the_vendor_ignored_is_outside_and_never_summed(pg):
    books = Books()
    books.ignore = {"date"}
    out = run(books, pg, ["2026-10"])
    assert (out["ok"], out["reasons"]) == (False, ["outside"])
    assert out["months"]["2026-10"]["outside"] == len(SEPTEMBER) and lines(pg) == {}


def test_a_part_whose_rows_fall_outside_its_sort_codes_is_outside():
    """A sort-code cut narrower than the whole range was never seen live (26.11): a part answered with codes it did not
    ask for is never summed."""
    definition = ledger.load_definition(secret(limit="6"))
    asked = []
    answers = iter([{"repdata": OCTOBER[:6]},  # the whole range at the limit: codes 300, 942 and 951, split at 300
                    {"repdata": OCTOBER[:2]}])  # the part up to 300, answered with 951 and 942

    def call(parameters, deadline):
        asked.append((parameters[0]["defVal"], parameters[1]["defVal"]))
        return next(answers)

    result = ledger_totals.read_month(call, definition, "2026-10", time.monotonic() + 60)
    assert (result.reason, result.stored, result.outside, result.parts) == ("outside", False, 2, 2)
    assert asked == [("-999999999", "999999999"), ("-999999999", "300")]
    assert result.lines == [] and result.keys == {} and "outside" in result.answer()


@needs_pg
@pytest.mark.parametrize("change,field", [
    ({"תאריך": "לא תאריך"}, "date"), ({"קוד מיון": "קוד"}, "sort_code"), ({"מפתח חשבון": None}, "key"),
    ({"מפתח חשבון": "  "}, "key"), ({"חובה שקל": "מאה"}, "debit"), ({"זכות שקל": True}, "credit"),
    ({"מספר מנה": "מנה"}, "batch"),
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
TOTALS = "/api/books/ledger/totals"


@needs_pg
def test_both_routes_need_the_sync_key_before_anything(app, pg):
    client, books = app
    for headers in ({}, {"Authorization": "Bearer wrong"}, {"Authorization": SYNC_KEY}):
        assert client.post(RUN, json={"months": ["2026-10"]}, headers=headers).status_code == 401
        assert client.post(RUN, content=b"{not json", headers=headers).status_code == 401
        assert client.get(TOTALS + "?from=2026-10", headers=headers).status_code == 401
    assert books.requests == [] and sql(pg, "SELECT count(*) FROM ledger_runs")[0][0] == 0


@needs_pg
def test_the_run_route_runs_the_last_two_months_in_israel_time_and_says_counts_only(app, pg, caplog):
    client, books = app
    caplog.set_level(logging.DEBUG)
    r = client.post(RUN, headers=AUTH)
    body = r.json()
    assert r.status_code == 200 and body["ok"] is True and body["company"] == "demo"
    assert list(body["months"]) == ["2026-10", "2026-11"] and body["months"]["2026-11"]["rows"] == 0
    assert body["months"]["2026-10"] == {"stored": True, "reason": None, "cut": False, "parts": 1, **OCTOBER_COUNTS}
    assert lines(pg) == OCTOBER_LINES
    body = client.post(RUN, json={"months": ["2026-10", "2026-09"]}, headers=AUTH).json()
    assert list(body["months"]) == ["2026-09", "2026-10"]
    assert scan(r.text, EVERYTHING) == [] and scan(caplog.text, EVERYTHING) == [] and KEY not in caplog.text


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
    b'{"company": "demo"}', json.dumps({"months": [f"2026-{m:02d}" for m in range(1, 13)] + ["2025-12"]}).encode(),
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
    r = client.post(RUN, json={"months": ["2026-10"]}, headers=AUTH)
    assert r.status_code == 200 and (r.json()["reasons"], r.json()["error"]) == (["crashed"], "ValueError")
    assert "48213" not in r.text and "ישראל" not in r.text


@needs_pg
def test_the_totals_route_says_the_kept_lines_and_never_a_row(app, pg, caplog):
    client, _ = app
    caplog.set_level(logging.DEBUG)
    client.post(RUN, json={"months": ["2026-09", "2026-10"]}, headers=AUTH)
    r = client.get(TOTALS + "?from=2026-09&to=2026-10", headers=AUTH)
    body = r.json()
    assert body["ok"] is True and (body["from"], body["to"]) == ("2026-09", "2026-10")
    got = {(x["month"], x["sort_code"]): (x["debit"], x["credit"], x["rows"], x["accounts"], x["masked_keys"], x["name"])
           for x in body["lines"]}
    want = {(m, code): (None if d is None else format(d, "f"), None if c is None else format(c, "f"), rows, accounts, masked,
                        NAMES[code])
            for m, held in (("2026-09", SEPTEMBER_LINES), ("2026-10", OCTOBER_LINES))
            for code, (d, c, rows, accounts, masked) in held.items()}
    assert got == want and [x["month"] for x in body["lines"]] == sorted(x["month"] for x in body["lines"])
    october = body["months"]["2026-10"]
    assert (october["batch_9998_rows"], october["batch_9998_debit"], october["batch_9998_credit"]) == (3, "4145.66", "4145.66")
    assert (october["single_account"], october["single_sides"], october["kept_lines"]) == (3, 3, 5)
    assert scan(r.text, PRIVATE - SAID) == [] and scan(caplog.text, EVERYTHING) == []
    assert client.get(TOTALS + "?from=2026-10", headers=AUTH).json()["months"].keys() == {"2026-10"}
    assert client.get(TOTALS + "?from=2025-01&to=2025-03", headers=AUTH).json()["lines"] == []


@needs_pg
@pytest.mark.parametrize("query", ["", "?from=2026-13", "?from=2026-10&to=2026-09", "?to=2026-10", "?from=2020-01&to=2026-10",
                                   "?from=x"])
def test_a_totals_query_that_is_not_one_is_bad_request(app, pg, query):
    client, _ = app
    body = client.get(TOTALS + query, headers=AUTH).json()
    assert (body["ok"], body["error"]) == (False, "bad_request")


@needs_pg
def test_health_says_the_last_run_and_the_months_held_never_a_total(app, pg):
    from app import main

    client, books = app
    h = client.get("/health").json()
    assert h["books"]["ledger"] == {"last_run": None, "months": []}
    assert {t: h["store"]["tables"][t] for t in LEDGER_TABLES} == dict.fromkeys(LEDGER_TABLES, "present")
    books.rows = [r for r in ROWS if not (r["מספר תנועה"] == 88405 and r["מפתח חשבון"] == "70010")]  # October unbalanced
    client.post(RUN, json={"months": ["2026-09", "2026-10"]}, headers=AUTH)
    main._store_cache.update(at=0.0, key=None, value=None)
    r = client.get("/health")
    ledger_block = r.json()["books"]["ledger"]
    assert ledger_block["months"] == ["2026-09"] and ledger_block["last_run"]["ok"] is False
    assert ledger_block["last_run"]["reasons"] == ["unbalanced"] and ledger_block["last_run"]["finished_at"]
    assert scan(r.text, PRIVATE) == [] and "1313.68" not in r.text and "4145.66" not in r.text


@needs_pg
def test_health_without_the_ledgers_tables_says_none_and_lists_them_absent(app, pg):
    from app import main

    client, _ = app
    sql(pg, "DROP TABLE " + ", ".join(LEDGER_TABLES))
    main._store_cache.update(at=0.0, key=None, value=None)
    h = client.get("/health").json()
    assert h["books"]["ledger"] is None
    assert {t: h["store"]["tables"][t] for t in LEDGER_TABLES} == dict.fromkeys(LEDGER_TABLES, "absent")
    assert set(LEDGER_TABLES) <= set(h["store"]["missing_columns"])


# ---- the migration -----------------------------------------------------------------------------------------------------


def test_the_migration_only_creates_and_names_the_stores_columns():
    import re

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
    assert "ledger_runs" in checked_tables() and "007_ledger_totals.sql" in [p.name for p in MIGRATIONS_DIR.glob("*.sql")]


@needs_pg
def test_the_migration_runs_twice(pg):
    from app.migrate import MIGRATIONS_DIR, apply_migrations

    sql_text = (MIGRATIONS_DIR / "007_ledger_totals.sql").read_text(encoding="utf-8")
    with psycopg.connect(pg, autocommit=True) as conn:
        conn.execute(sql_text)
        conn.execute(sql_text)
    assert apply_migrations(pg)["missing"] == []
