"""A1 (Analiza-Monday backlog 0301): the receipt date from the books to Student Charge - the rules FR-C1 (the match)
and FR-C5 (the fill), with FR-3, FR-4, FR-7 and FR-L12 where they decide a date; the plan and the read-back routes.

Fixtures are synthetic and shaped like the incidents (the rules' contract §0.2): numbers and amounts mirror the records,
names are invented."""

import json
from datetime import date
from decimal import Decimal

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient

from app import main
from app.recon import reading as rr
from app.recon import receipt_dates as rd
from tests.conftest import KEY, TEST_DATABASE_URL, drop_all, needs_pg
from tests.test_recon_reading import SYNC_KEY, TOKEN, Monday, reader

SC_BASE = int(rr.STUDENT_CHARGE[-4:]) * 10000  # the fake's ids of Student Charge


def row(n: int, ref: str = "", paid: str = "4000", day: str = "", status: str = "1", state: str = "active",
        name: str = "דנה לוי") -> dict:
    """A Student Charge row as the reading holds it (app/monday/client.flatten)."""
    return {"id": str(SC_BASE + n), "name": name, "state": state, "board_id": rr.STUDENT_CHARGE, rd.REF_COLUMN: ref,
            rd.AMOUNT_COLUMN: paid, rd.COLUMN: day, rd.STATUS_COLUMN: "שולם", rd.STATUS_COLUMN + "#": status}


def doc(number, total="4000", day="2026-03-15", doc_type=2, status=1, missing=False, name="שם אחר לגמרי") -> dict:
    """A document as BooksStore.documents_for_matching gives it (with a card name the matcher must never read)."""
    return {"stock_id": int(number), "doc_type": doc_type, "doc_number": str(number), "status": status,
            "value_date": date.fromisoformat(day) if day else None, "total_incl": Decimal(total), "missing": missing,
            "account_name": name}


def buckets(rows, docs) -> dict:
    analysis = rd.analyse(rows, docs)
    return {int(i) - SC_BASE: e["bucket"] for i, e in analysis["rows"].items()}


def fills(rows, docs) -> dict:
    analysis = rd.analyse(rows, docs)
    return {int(i) - SC_BASE: e["books_day"] for i, e in analysis["rows"].items() if e["bucket"] == "fill"}


# ---- FR-C5: the fill --------------------------------------------------------------------------------------------
def test_receipt_date_fills_empty_only():
    """FR-C5: an empty `תאריך תקבול` on a matched row takes the document's value_date; a filled one is never replaced -
    the same day, another day, or something that is not a day."""
    docs = [doc(62500), doc(62501), doc(62502), doc(62503)]
    rows = [row(0, "62500"), row(1, "62501", day="2026-03-15"), row(2, "62502", day="2026-03-17"),
            row(3, "62503", day="לא ידוע")]
    assert buckets(rows, docs) == {0: "fill", 1: "filled_same", 2: "filled_differs", 3: "date_unreadable"}
    analysis = rd.analyse(rows, docs)
    assert analysis["rows"][str(SC_BASE + 2)]["books_day"] == "2026-03-15"  # known, and still not proposed
    actions = rd._plan_body(analysis)["actions"]
    assert actions == [{"item_id": str(SC_BASE), "url": rd.ITEM_URL.format(SC_BASE), "column": "date_mm8yh4bn",
                        "current": "", "proposed": "2026-03-15", "documents": ["62500"]}]


def test_status_that_is_not_money_in_gets_no_date():
    """ADR-0035 (a row is money received) and the column's own meaning: לא שולם, יש בעיה and מלגת אנליזה get no
    receipt date; שולם, תשלום הוחזר, הוחזר חלקי and ביקש החזר do. The status is matched by its index."""
    docs = [doc(62600 + i) for i in range(7)]
    statuses = ["0", "2", "3", "1", "4", "5", "9"]
    rows = [row(i, str(62600 + i), status=s) for i, s in enumerate(statuses)]
    assert buckets(rows, docs) == {0: "status_not_money_in", 1: "status_not_money_in", 2: "status_not_money_in",
                                   3: "fill", 4: "fill", 5: "fill", 6: "fill"}


# ---- FR-C1: the match -------------------------------------------------------------------------------------------
def test_b2c_cited_document_sums():
    """FR-C1: the rows citing one document sum to its total_incl within 1 ₪ a document. One invoice over two rows is
    fine (ADR-0088 §1: 10,000 for 4,000 and 6,000); 63109, one 4,000 invoice cited by two 4,000 rows, is a gap; a row
    over two documents sums them (62894, 62914)."""
    docs = [doc(62510, total="10000"), doc(63109, total="4000"), doc(62894, total="2000"), doc(62914, total="3000")]
    rows = [row(0, "62510", paid="4000"), row(1, "62510", paid="6000"),
            row(2, "63109", paid="4000"), row(3, "63109", paid="4000"),
            row(4, "62894, 62914", paid="5000")]
    assert buckets(rows, docs) == {0: "fill", 1: "fill", 2: "amount_mismatch", 3: "amount_mismatch", 4: "fill"}


def test_the_tolerance_is_one_shekel_a_document_and_the_band_is_never_filled():
    """The contract §0.3: a row against five documents is within 5 ₪; past that and up to 0.1% is the rounding band,
    shown and never filled (Haemek: 3,350 against 3,347.46); past the band it is a mismatch."""
    five = [doc(62700 + i, total="2000") for i in range(5)]
    refs = ", ".join(str(62700 + i) for i in range(5))
    assert buckets([row(0, refs, paid="10004.90")], five) == {0: "fill"}
    assert buckets([row(0, refs, paid="10005.50")], five) == {0: "rounding_band"}
    assert buckets([row(0, refs, paid="10011")], five) == {0: "amount_mismatch"}
    haemek = [doc(62800, total="3347.46")]
    assert buckets([row(0, "62800", paid="3350")], haemek) == {0: "rounding_band"}
    assert buckets([row(0, "62800", paid="3348")], haemek) == {0: "fill"}
    assert buckets([row(0, "62800", paid="3400")], haemek) == {0: "amount_mismatch"}


def test_payer_name_differs_is_not_a_gap():
    """FR-C1 and the law of §5: a match is by the number cited; a card named unlike the row (52 rows on 18.11) still
    matches, because no name is ever read."""
    docs = [doc(62900, name="אבי כהן")]
    assert fills([row(0, "62900", name="רונית מזרחי")], docs) == {0: "2026-03-15"}


def test_type1_alone_is_not_proof_of_payment():
    """FR-C1 (a tax invoice alone is a demand) and ADR-0088 §1 (with a separate receipt the row cites the invoice): the
    day the money came is the receipt's, which R1's linker finds. A group holding a tax invoice gives no date."""
    docs = [doc(50400, doc_type=1, day="2026-03-01"), doc(50401, total="2000", doc_type=1),
            doc(62950, total="2000")]
    rows = [row(0, "50400"), row(1, "50401, 62950")]
    assert buckets(rows, docs) == {0: "invoice_needs_receipt", 1: "invoice_needs_receipt"}


def test_invoice_receipt_pays_itself():
    """FR-L12: an invoice-receipt pays itself, so its value_date is the day the money came in."""
    d = {**doc(62960, day="2026-04-02"), "issue_date": date(2026, 4, 9), "due_date": date(2026, 5, 1)}
    assert fills([row(0, "62960")], [d]) == {0: "2026-04-02"}


def test_two_valid_documents_share_a_number():
    """FR-3: two status-1 documents with one number - neither is matched by it until resolved."""
    docs = [doc(62970), doc(62970, doc_type=31)]
    assert buckets([row(0, "62970")], docs) == {0: "ref_ambiguous"}


def test_draft_and_valid_share_a_number():
    """FR-3 and FR-2: a number is looked up among status-1 documents only - a draft beside a valid document does not
    stop the match, and a number only a draft holds is not in the books."""
    docs = [doc(62980, status=0, day="2026-01-01"), doc(62980), doc(62981, status=0)]
    assert buckets([row(0, "62980"), row(1, "62981")], docs) == {0: "fill", 1: "ref_not_in_books"}
    assert fills([row(0, "62980")], docs) == {0: "2026-03-15"}


def test_negative_invoice_is_a_credit():
    """FR-4: a tax invoice with a negative total is a cancellation; and a credit, a receipt, a proforma or an own
    invoice is not what `אסמכתת חשבונית` cites. None gives a receipt date."""
    docs = [doc(50500, total="-4000", doc_type=1), doc(70100, doc_type=3), doc(31100, doc_type=31),
            doc(40100, doc_type=78), doc(88100, doc_type=88), doc(99100, doc_type=5)]
    rows = [row(i, n) for i, n in enumerate(("50500", "70100", "31100", "40100", "88100", "99100"))]
    assert set(buckets(rows, docs).values()) == {"ref_not_an_invoice"}


def test_out_of_window_is_not_a_gap():
    """FR-7: Student Charge's window is 2026. A document dated outside it is counted, never filled."""
    docs = [doc(62990, day="2025-12-28"), doc(62991, day="2027-01-02"), doc(62992, day="")]
    rows = [row(0, "62990"), row(1, "62991"), row(2, "62992")]
    assert buckets(rows, docs) == {0: "out_of_window", 1: "out_of_window", 2: "out_of_window"}


def test_dates_differ_gives_no_day():
    """The contract §0.5: a row paid on two invoice-receipts of different days - the rule cannot say which day, so it
    says that and fills nothing."""
    docs = [doc(63000, total="2000", day="2026-02-01"), doc(63001, total="2000", day="2026-03-01"),
            doc(63002, total="2000", day="2026-03-01"), doc(63003, total="2000", day="2026-03-01")]
    assert buckets([row(0, "63000, 63001"), row(1, "63002,63003")], docs) == {0: "dates_differ", 1: "fill"}


def test_a_document_that_answered_not_found_gives_no_day():
    """A held document that answered "Stock not found" stays in the books' figures until a person confirms it gone
    (B2), but a fill does not lean on it."""
    assert buckets([row(0, "63010")], [doc(63010, missing=True)]) == {0: "document_missing"}


def test_a_row_whose_document_is_shared_with_an_unmatched_row_is_not_filled():
    """A group is judged on all its rows: when one row that cites the document fails on its own, the others are
    not judged on half the group."""
    docs = [doc(63020, total="8000")]
    rows = [row(0, "63020, 99999"), row(1, "63020"), row(2, "63020", paid="")]
    assert buckets(rows, docs) == {0: "ref_not_in_books", 1: "shares_with_unmatched", 2: "amount_unreadable"}


def test_a_row_sharing_a_document_with_an_unreadable_cell_is_not_filled():
    """An unreadable cell is never resolved, but the numbers in it still hold their documents: `62894 62914` (a space
    for a comma) may cite 62894 too, so the row citing 62894 alone is not judged on half its group."""
    docs = [doc(62894), doc(62914)]
    rows = [row(0, "62894 62914", paid="8000"), row(1, "62894")]
    assert buckets(rows, docs) == {0: "ref_unreadable", 1: "shares_with_unmatched"}


def test_an_archived_row_is_neither_filled_nor_counted_in_a_sum():
    docs = [doc(63030)]
    rows = [row(0, "63030", state="archived"), row(1, "63030")]
    assert buckets(rows, docs) == {0: "not_active", 1: "fill"}


@pytest.mark.parametrize("text,refs", [
    ("62894, 62914", ["62894", "62914"]), ("62894,62914", ["62894", "62914"]), (" 62894 ", ["62894"]),
    ("‏62894, 62914‎", ["62894", "62914"]), ("062894", ["62894"]), ("62894, 62894", ["62894"]),
    ("", []), (None, []), ("‏", []),
    ("62894,\u202f62914", ["62894", "62914"]), ("628\u200e94", None), ("62894\u200f62914", None),
    ("62894\u00a062914", None),
    ("62894 62914", None), ("62894/62914", None), ("חשבונית 62894", None), ("62894,", None), ("-62894", None),
    ("6289.4", None), ("1234567890123", None), ("\u0666\u0662\u0668\u0669\u0664", None), ("62,894", None),
    ("894", None),
])
def test_refs_are_read_as_adr_0088_writes_them(text, refs):
    """ADR-0088 §1: one number, or several separated by commas. Anything else is unreadable and counted."""
    assert rd.parse_refs(text) == refs


@pytest.mark.parametrize("text,value", [
    ("4000", Decimal("4000")), ("4,000", Decimal("4000")), ("3347.46", Decimal("3347.46")), ("-500", Decimal("-500")),
    ("", None), (None, None), ("4,00", None), ("₪4000", None), ("abc", None), ("4000.5.1", None),
])
def test_an_amount_is_read_strictly(text, value):
    assert rd.parse_amount(text) == value


def test_every_row_is_in_exactly_one_bucket():
    """Spec §6.3.1: the counts add up to the rows read, and every row's bucket is one of BUCKETS."""
    docs = [doc(62500), doc(50400, doc_type=1), doc(62970), doc(62970, doc_type=31), doc(62990, day="2025-01-01")]
    rows = [row(0, "62500"), row(1, "50400"), row(2, "62970"), row(3, "62990"), row(4), row(5, "x"),
            row(6, "77777"), row(7, "62500", state="archived"), row(8, "62500", paid="")]
    analysis = rd.analyse(rows, docs)
    assert sum(analysis["counts"].values()) == len(rows) == len(analysis["rows"])
    assert all(e["bucket"] in rd.BUCKETS for e in analysis["rows"].values())
    assert list(analysis["counts"]) == list(rd.BUCKETS)


# ---- the plan and the read-back, as rules behind the guard --------------------------------------------------------
def a_reading(rows: list) -> dict:
    """A complete reading of the profile, with these Student Charge rows."""
    reading = rr.read_profile(reader(Monday({b.board_id: 1 for b in rr.PROFILE})))
    assert reading["complete"] is True
    reading["boards"]["student_charge"] = rows
    return reading


def test_the_plan_refuses_demo_and_a_reading_that_is_not_whole():
    """The rules' contract §0.6, through `monday_side`."""
    complete = a_reading([row(0, "62500")])
    for company, reading, reason in (("demo", complete, "demo"), ("analiza", None, "no_reading"),
                                     ("analiza", {**complete, "complete": False}, "incomplete")):
        with pytest.raises(rr.NotReady) as err:
            rd.plan(company, reading, [doc(62500)])
        assert err.value.reason == reason
    assert rd.plan("analiza", complete, [doc(62500)])["fill"] == 1


def test_the_plan_says_counts_links_and_days_and_never_a_name_or_an_amount():
    rows = [row(0, "62500", name="ישראל ישראלי"), row(1, "62501", paid="4321"), row(2, "50400"), row(3)]
    docs = [doc(62500, day="2026-01-20"), doc(62501, total="4321", day="2026-03-02"), doc(50400, doc_type=1)]
    body = rd.plan("analiza", a_reading(rows), docs)
    assert body["fill"] == 2 and body["rows"] == 4 and body["counts"]["fill"] == 2
    assert body["fill_by_month"] == {"2026-01": 1, "2026-03": 1}
    assert [a["proposed"] for a in body["actions"]] == ["2026-01-20", "2026-03-02"]  # by day
    assert body["actions"][0]["url"] == f"https://analiza-college-group.monday.com/boards/5093448677/pulses/{SC_BASE}"
    assert body["ids_by_bucket"] == {"invoice_needs_receipt": [str(SC_BASE + 2)]}  # no_ref and fill are not listed
    text = json.dumps(body, ensure_ascii=False)
    for leak in ("ישראל", "דנה", "שם אחר", "4321", "4000"):
        assert leak not in text


def test_verify_marks_a_row_verified_only_when_its_date_is_the_books_day():
    """Spec §7.1.4, on a reading made after the write: the written day is the books' day (verified); a row left empty,
    one written with another day, one whose number changed since, and one the reading does not hold each say so."""
    docs = [doc(62500), doc(62501), doc(62502), doc(62503)]
    after = [row(0, "62500", day="2026-03-15"), row(1, "62501"), row(2, "62502", day="2026-03-16"),
             row(3, "77777", day="2026-03-15")]
    ids = [str(SC_BASE + i) for i in range(5)]
    body = rd.verify("analiza", a_reading(after), docs, ids)
    assert {int(k) - SC_BASE: v["state"] for k, v in body["items"].items()} == {
        0: "verified", 1: "empty", 2: "differs", 3: "ref_not_in_books", 4: "not_found"}
    assert body["verified"] == 1 and body["asked"] == 5
    assert body["items"][str(SC_BASE + 2)] == {"state": "differs", "current": "2026-03-16", "books": "2026-03-15"}


# ---- the routes ---------------------------------------------------------------------------------------------------
class Charges(Monday):
    """The five boards as in R0's tests; Student Charge's rows answer the cells A1 reads. `values`: item id ->
    {column: text}, the status as (label, index)."""

    def __init__(self, values: dict):
        super().__init__()
        self.values = values
        self.sizes[rr.STUDENT_CHARGE] = len(values)

    def ids(self, board_id: str) -> list:
        if board_id == rr.STUDENT_CHARGE:
            return [int(i) for i in self.values]
        return super().ids(board_id)

    def handler(self, request: httpx.Request) -> httpx.Response:
        response = super().handler(request)
        if "items(ids:" not in json.loads(request.content)["query"] or response.status_code != 200:
            return response
        data = response.json()
        for item in data["data"]["items"]:
            for c in item["column_values"]:
                value = self.values.get(item["id"], {}).get(c["id"])
                if value is None:
                    continue
                if c["id"] == rd.STATUS_COLUMN:
                    c.update(text=value[0], label=value[0], index=value[1])
                else:
                    c["text"] = value
        return httpx.Response(200, json=data)


def cells(ref: str, paid: str = "4000", day: str = "", status: tuple = ("שולם", 1)) -> dict:
    return {rd.REF_COLUMN: ref, rd.AMOUNT_COLUMN: paid, rd.COLUMN: day, rd.STATUS_COLUMN: status}


ANALIZA = "wizdb2245n5"


def seed_books(url: str, docs: list, completed: bool = True) -> None:
    with psycopg.connect(url, autocommit=True) as conn:
        for d in docs:
            conn.execute(
                "INSERT INTO books_documents (company, stock_id, doc_type, doc_number, status, value_date, total_incl, "
                "account_name, content_hash) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'h')",
                (ANALIZA, d["stock_id"], d["doc_type"], d["doc_number"], d["status"], d["value_date"], d["total_incl"],
                 d["account_name"]))
        if completed:
            conn.execute("INSERT INTO books_sync_runs (company, trigger, finished_at, complete, ok) "
                         "VALUES (%s, 'test', now(), true, true)", (ANALIZA,))


@pytest.fixture
def pg():
    drop_all(TEST_DATABASE_URL)
    from app.migrate import apply_migrations

    apply_migrations(TEST_DATABASE_URL)
    yield TEST_DATABASE_URL
    drop_all(TEST_DATABASE_URL)


@pytest.fixture
def actions_app(pg, monkeypatch):
    monkeypatch.setenv("WIZ_PRIVATE_KEY", KEY)
    monkeypatch.setenv("DATABASE_URL", pg)
    monkeypatch.setenv("BOOKS_SYNC_KEY", SYNC_KEY)
    monkeypatch.setenv("MONDAY_TOKEN", TOKEN)
    monkeypatch.setenv("WIZ_COMPANY", "analiza")
    fake = Charges({str(SC_BASE + 0): cells("62500"), str(SC_BASE + 1): cells("62501", day="2026-03-17"),
                    str(SC_BASE + 2): cells(""), str(SC_BASE + 3): cells("50400"),
                    str(SC_BASE + 4): cells("62502", status=("לא שולם", 0))})
    main._store_cache.update(at=0.0, key=None, value=None)
    main.app.state.monday_transport = httpx.MockTransport(fake.handler)
    yield TestClient(main.app), fake, pg
    main.app.state.monday_transport = None
    main._store_cache.update(at=0.0, key=None, value=None)


AUTH = {"Authorization": f"Bearer {SYNC_KEY}"}
PLAN = {"kind": "b2c_receipt_date_fill"}
DOCS = [doc(62500, day="2026-03-15"), doc(62501, day="2026-03-16"), doc(62502, day="2026-03-18"),
        doc(50400, doc_type=1, day="2026-03-01")]


@needs_pg
def test_the_plan_route_reads_monday_now_and_answers_the_plan(actions_app):
    client, fake, pg = actions_app
    seed_books(pg, DOCS)
    body = client.post("/api/actions/plan", headers=AUTH, json=PLAN).json()
    assert body["ok"] is True and body["company"] == "analiza" and body["books"] == {"documents": 4}
    assert body["fill"] == 1 and body["actions"][0]["item_id"] == str(SC_BASE)
    assert body["actions"][0]["proposed"] == "2026-03-15" and body["actions"][0]["current"] == ""
    assert {k: v for k, v in body["counts"].items() if v} == {
        "no_ref": 1, "invoice_needs_receipt": 1, "status_not_money_in": 1, "filled_differs": 1, "fill": 1}
    from app.recon.store import load_reading

    kept = load_reading(pg)  # the reading was kept, and is what the rule ran on
    assert kept["complete"] is True and body["read_at"] == kept["read_at"]
    text = json.dumps(body, ensure_ascii=False)
    for leak in ("שם ", "שם אחר", TOKEN, SYNC_KEY, "4000"):
        assert leak not in text


@needs_pg
def test_the_verify_route_reads_back_with_the_service_token(actions_app):
    client, fake, pg = actions_app
    seed_books(pg, DOCS)
    fake.values[str(SC_BASE)][rd.COLUMN] = "2026-03-15"  # the session wrote it
    body = client.post("/api/actions/verify", headers=AUTH,
                       json={**PLAN, "item_ids": [str(SC_BASE), str(SC_BASE + 1), SC_BASE + 9]}).json()
    assert body["ok"] is True and body["verified"] == 1
    assert {k: v["state"] for k, v in body["items"].items()} == {
        str(SC_BASE): "verified", str(SC_BASE + 1): "differs", str(SC_BASE + 9): "not_found"}


@needs_pg
def test_the_action_routes_need_the_key_a_kind_and_item_ids(actions_app):
    client, fake, pg = actions_app
    assert client.post("/api/actions/plan", json=PLAN).status_code == 401
    assert client.post("/api/actions/plan", headers=AUTH, json={"kind": "b2c_ref_fill"}).status_code == 400
    assert client.post("/api/actions/plan", headers=AUTH).status_code == 400
    for ids in (None, [], ["12a"], [True], [{"id": 1}], ["1"] * (main.VERIFY_MAX_IDS + 1)):
        assert client.post("/api/actions/verify", headers=AUTH, json={**PLAN, "item_ids": ids}).status_code == 400
    assert fake.queries == []  # nothing was read for a refused request


@needs_pg
def test_the_plan_route_refuses_demo_before_reading(actions_app, monkeypatch):
    client, fake, pg = actions_app
    monkeypatch.setenv("WIZ_COMPANY", "demo")
    seed_books(pg, DOCS)
    assert client.post("/api/actions/plan", headers=AUTH, json=PLAN).json() == {
        "ok": False, "error": "not_ready", "reason": "demo"}
    assert fake.queries == []


@needs_pg
def test_the_plan_route_refuses_a_books_copy_with_a_hole_or_never_completed(actions_app):
    """Spec §6.1.5: a copy with a hole, or one that never finished a run, changes nothing that is shown."""
    client, fake, pg = actions_app
    seed_books(pg, DOCS, completed=False)
    assert client.post("/api/actions/plan", headers=AUTH, json=PLAN).json() == {
        "ok": False, "error": "books_not_ready", "reason": "never_completed", "last_run_reasons": []}
    seed_books(pg, [doc(62504)])  # 62503 is missing between 62502 and 62504
    assert client.post("/api/actions/plan", headers=AUTH, json=PLAN).json() == {
        "ok": False, "error": "books_not_ready", "reason": "holes", "last_run_reasons": []}


@needs_pg
def test_the_plan_route_refuses_a_reading_that_came_short(actions_app):
    client, fake, pg = actions_app
    seed_books(pg, DOCS)
    fake.count_off[rr.DEALS] = 1
    body = client.post("/api/actions/plan", headers=AUTH, json=PLAN).json()
    assert body["ok"] is False and body["error"] == "not_ready" and body["reason"] == "incomplete"
    assert body["reading"]["checks"]["deals"]["ok"] is False


@needs_pg
def test_the_books_store_gives_numbers_days_and_totals_and_never_a_name(pg):
    seed_books(pg, [doc(62500), doc(62501, status=0)])
    with psycopg.connect(pg, autocommit=True) as conn:
        conn.execute("UPDATE books_documents SET missing_since = now() WHERE stock_id = 62501")
        conn.execute("INSERT INTO books_documents (company, stock_id, doc_number, status, content_hash, gone_at) "
                     "VALUES (%s, 1, '1', 1, 'h', now())", (ANALIZA,))
    from app.books.store import BooksStore

    with BooksStore(pg).session(ANALIZA + ":t") as s:
        snapshot = s.matching_snapshot(ANALIZA)
    assert {k: v for k, v in snapshot.items() if k != "documents"} == {
        "retry_waiting": 0, "last_run_complete": True, "last_run_ok": True, "last_run_reasons": [],
        "ever_completed": True}
    got = snapshot["documents"]
    assert got == [
        {"stock_id": 62500, "doc_type": 2, "doc_number": "62500", "status": 1, "value_date": date(2026, 3, 15),
         "total_incl": Decimal("4000"), "missing": False},
        {"stock_id": 62501, "doc_type": 2, "doc_number": "62501", "status": 0, "value_date": date(2026, 3, 15),
         "total_incl": Decimal("4000"), "missing": True}]


# ---- the review of 23.11 (an adversarial agent on ddd637c): each finding held by its own proof ---------------------
def test_group_sum_hides_a_row_paid_past_its_only_document():
    """Review 23.11, finding 1: a group's sums can tie while one row is paid past its only document - row 0 cites
    63100 (7,000) alone and says 8,000; row 1 cites 63100 and 63101 (3,000) and says 2,000. Several rows over several
    documents is a tangle: neither row is filled."""
    docs = [doc(63100, total="7000", day="2026-04-01"), doc(63101, total="3000", day="2026-04-01")]
    rows = [row(0, "63100", paid="8000"), row(1, "63100, 63101", paid="2000")]
    assert buckets(rows, docs) == {0: "group_tangled", 1: "group_tangled"}


def test_the_two_shapes_adr_0088_names_are_matched_and_nothing_else():
    """ADR-0088 §1: one document over several rows, one row over several documents. A chain that joins both is a
    tangle even when every sum agrees."""
    docs = [doc(63110, total="6000"), doc(63111, total="2000"), doc(63112, total="3000"), doc(63113, total="1000")]
    star_doc = [row(0, "63110", paid="2500"), row(1, "63110", paid="3500")]
    star_row = [row(2, "63111, 63112", paid="5000")]
    chain = [row(3, "63113", paid="500"), row(4, "63113, 63112", paid="3500")]
    assert buckets(star_doc + star_row, docs) == {0: "fill", 1: "fill", 2: "fill"}
    assert buckets(chain, docs[3:] + docs[2:3]) == {3: "group_tangled", 4: "group_tangled"}


def test_an_unreadable_cell_in_arabic_indic_digits_still_holds_its_document():
    """Review 23.11, finding 3: an unreadable cell holds the documents its digits may name, in any script, and two
    neighbouring runs joined (`62,894`); so the row citing 62894 alone is not judged on half its group."""
    docs = [doc(62894)]
    for cell in ("\u0666\u0662\u0668\u0669\u0664", "62,894", "62 894"):
        assert buckets([row(0, "62894"), row(1, cell)], docs) == {0: "shares_with_unmatched", 1: "ref_unreadable"}, cell
    assert rd.loose_numbers("62,894, 70100") == ["62", "894", "70100", "62894", "89470100"]


@needs_pg
def test_verify_counts_each_asked_row_once(actions_app):
    """Review 23.11, finding 2: the same id asked twice (`"1"` and `1`) is one row - asking [A, A, B] with B not
    written said `asked 2, verified 2`."""
    client, fake, pg = actions_app
    seed_books(pg, DOCS)
    fake.values[str(SC_BASE)][rd.COLUMN] = "2026-03-15"
    ids = [str(SC_BASE), SC_BASE, str(SC_BASE + 3)]
    body = client.post("/api/actions/verify", headers=AUTH, json={**PLAN, "item_ids": ids}).json()
    assert body["asked"] == 2 and body["verified"] == 1 and sum(body["counts"].values()) == 2
    assert rd.verify("analiza", a_reading([row(0, "62500", day="2026-03-15")]), [doc(62500)],
                     [str(SC_BASE), SC_BASE])["asked"] == 1


@needs_pg
def test_the_plan_never_matches_a_copy_with_a_hole_in_what_it_read(actions_app, monkeypatch):
    """Review 23.11, finding 5: the holes are judged on the very documents matched, read in one snapshot - a tick
    that saves 62510 just before leaves 62503-62509 missing from them, and the plan refuses."""
    client, fake, pg = actions_app
    seed_books(pg, DOCS)
    from app.books.store import BooksStore

    original = BooksStore.matching_snapshot

    def tick_saves_first(self, company):
        with psycopg.connect(pg, autocommit=True) as conn:
            conn.execute("INSERT INTO books_documents (company, stock_id, doc_type, doc_number, status, value_date, "
                         "total_incl, content_hash) VALUES (%s, 62510, 2, '62510', 1, '2026-03-20', 4000, 'h')", (ANALIZA,))
        return original(self, company)

    monkeypatch.setattr(BooksStore, "matching_snapshot", tick_saves_first)
    assert client.post("/api/actions/plan", headers=AUTH, json=PLAN).json() == {
        "ok": False, "error": "books_not_ready", "reason": "holes", "last_run_reasons": []}


@needs_pg
def test_the_plan_refuses_a_copy_whose_last_run_left_a_document_unread(actions_app):
    """Review 23.11, finding 4: 'a run ever completed' is not 'the copy is whole now'. The last finished run was cut
    with 62503 jumped over and never read (past the last number held, so no hole shows): refused. And ids waiting in
    books_retry refuse it too."""
    client, fake, pg = actions_app
    seed_books(pg, DOCS)
    with psycopg.connect(pg, autocommit=True) as conn:
        conn.execute("INSERT INTO books_sync_runs (company, trigger, finished_at, complete, ok) "
                     "VALUES (%s, 'tick', now(), false, true)", (ANALIZA,))
        conn.execute("INSERT INTO books_retry (company, stock_id, kind, attempts) VALUES (%s, 62503, 'jumped', 0)",
                     (ANALIZA,))
    assert client.post("/api/actions/plan", headers=AUTH, json=PLAN).json() == {
        "ok": False, "error": "books_not_ready", "reason": "last_run_incomplete", "last_run_reasons": []}
    with psycopg.connect(pg, autocommit=True) as conn:
        conn.execute("INSERT INTO books_sync_runs (company, trigger, finished_at, complete, ok) "
                     "VALUES (%s, 'tick', now(), true, true)", (ANALIZA,))  # complete, and yet 62503 still waits
    assert client.post("/api/actions/plan", headers=AUTH, json=PLAN).json() == {
        "ok": False, "error": "books_not_ready", "reason": "retry_waiting", "last_run_reasons": []}
    with psycopg.connect(pg, autocommit=True) as conn:
        conn.execute("DELETE FROM books_retry")
    assert client.post("/api/actions/plan", headers=AUTH, json=PLAN).json()["ok"] is True


def test_an_out_of_window_document_is_out_of_window_even_when_the_amounts_differ():
    """Review 23.11, finding 6 (FR-7): out of the window is never a gap, so it is judged before the sum."""
    docs = [doc(62990, day="2025-12-28"), doc(62991, total="3347.46", day="2025-12-30")]
    rows = [row(0, "62990", paid="3000"), row(1, "62991", paid="3350")]
    assert buckets(rows, docs) == {0: "out_of_window", 1: "out_of_window"}


def test_a_row_lands_in_the_first_bucket_that_fits_whatever_the_order_of_its_numbers():
    """Review 23.11, finding 7: two bad numbers in one cell give the first bucket in BUCKETS, not the first typed."""
    docs = [doc(70100, doc_type=3), doc(62970), doc(62970, doc_type=31)]
    for ref in ("70100, 99999", "99999, 70100"):
        assert buckets([row(0, ref)], docs) == {0: "ref_not_in_books"}, ref
    for ref in ("70100, 62970", "62970, 70100"):
        assert buckets([row(0, ref)], docs) == {0: "ref_ambiguous"}, ref


# ---- the mutants that survived the first tests (review 23.11) ------------------------------------------------------
def test_an_underpaid_row_is_a_mismatch_not_a_fill():
    """2,000 paid against a 4,000 invoice-receipt is a gap: the difference is judged both ways."""
    assert buckets([row(0, "63120", paid="2000")], [doc(63120, total="4000")]) == {0: "amount_mismatch"}


def test_the_boundaries_of_the_tolerance_and_the_band():
    """Exactly 1 ₪ a document is within the tolerance; exactly 0.1% is still the band."""
    assert buckets([row(0, "63130", paid="4001")], [doc(63130, total="4000")]) == {0: "fill"}
    assert buckets([row(0, "63130, 63131", paid="8002")], [doc(63130), doc(63131)]) == {0: "fill"}
    assert buckets([row(0, "63132", paid="3003")], [doc(63132, total="3000")]) == {0: "rounding_band"}
    assert buckets([row(0, "63132", paid="3003.01")], [doc(63132, total="3000")]) == {0: "amount_mismatch"}


def test_a_group_is_judged_in_the_order_of_buckets():
    """A document not found, then the window, then the shape, then the sum, then the tax invoice, then the days."""
    assert buckets([row(0, "63140", paid="1")], [doc(63140, missing=True, day="2025-01-01")]) == {0: "document_missing"}
    assert buckets([row(0, "63141", paid="1")], [doc(63141, day="2025-01-01")]) == {0: "out_of_window"}
    assert buckets([row(0, "50600", paid="1")], [doc(50600, doc_type=1)]) == {0: "amount_mismatch"}
    two = [doc(63142, total="2000", day="2026-02-01"), doc(50601, total="2000", doc_type=1, day="2026-03-01")]
    assert buckets([row(0, "63142, 50601")], two) == {0: "invoice_needs_receipt"}


def test_the_plan_lists_the_band_and_proposes_nothing_for_it():
    rows = [row(0, "62800", paid="3350"), row(1, "62801")]
    body = rd.plan("analiza", a_reading(rows), [doc(62800, total="3347.46"), doc(62801)])
    assert body["ids_by_bucket"] == {"rounding_band": [str(SC_BASE)]}
    assert [a["item_id"] for a in body["actions"]] == [str(SC_BASE + 1)]


def test_verify_never_takes_a_row_the_books_give_no_day_for_or_an_unreadable_date():
    """Only `filled_same` is verified: a row whose status says no money came in, even with the books' day in it, or a
    date cell that is not a date, is not."""
    docs = [doc(62500), doc(62501)]
    after = [row(0, "62500", day="2026-03-15", status="0"), row(1, "62501", day="15/03/2026")]
    body = rd.verify("analiza", a_reading(after), docs, [str(SC_BASE), str(SC_BASE + 1)])
    assert {int(k) - SC_BASE: v["state"] for k, v in body["items"].items()} == {
        0: "status_not_money_in", 1: "date_unreadable"}
    assert body["verified"] == 0


@needs_pg
def test_a_plan_asked_while_a_reading_or_another_plan_runs_is_busy(actions_app):
    client, fake, pg = actions_app
    seed_books(pg, DOCS)
    from app.books.store import BooksStore
    from app.recon.store import reading_lock

    with reading_lock(pg) as held:
        assert held is True
        assert client.post("/api/actions/plan", headers=AUTH, json=PLAN).json() == {
            "ok": False, "error": "busy", "detail": "another reading is running"}
    with BooksStore(pg).session(ANALIZA + ":actions") as s:
        assert s is not None
        assert client.post("/api/actions/plan", headers=AUTH, json=PLAN).json() == {"ok": False, "error": "busy"}


@needs_pg
def test_the_rule_runs_on_the_reading_recon_readings_holds(actions_app, monkeypatch):
    """The user's words (23.11): monday is read through R0 (recon_readings). What the store gives back is what is
    matched - here a row changed in the store between the save and the rule is the one planned."""
    client, fake, pg = actions_app
    seed_books(pg, DOCS)
    from app.recon import store as recon_store

    original = recon_store.load_reading

    def stored(url, name=rr.PROFILE_NAME, body=True):
        kept = original(url, name, body)
        for r in kept["boards"]["student_charge"]:
            if r["id"] == str(SC_BASE + 1):
                r[rd.COLUMN] = ""  # as if the store held it empty
        return kept

    monkeypatch.setattr(recon_store, "load_reading", stored)
    body = client.post("/api/actions/plan", headers=AUTH, json=PLAN).json()
    assert sorted(a["item_id"] for a in body["actions"]) == [str(SC_BASE), str(SC_BASE + 1)]


@needs_pg
def test_verify_takes_a_thousand_ids(actions_app):
    client, fake, pg = actions_app
    seed_books(pg, DOCS)
    ids = [str(SC_BASE + i) for i in range(main.VERIFY_MAX_IDS)]
    body = client.post("/api/actions/verify", headers=AUTH, json={**PLAN, "item_ids": ids}).json()
    assert body["ok"] is True and body["asked"] == main.VERIFY_MAX_IDS


# ---- the second review of 23.11 (on 02f2851) -----------------------------------------------------------------------
@pytest.mark.parametrize("cell", ["62894\u200f62914", "\u200e62894\u200e\u200f62914\u200e", "62894\u200b62914",
                                  "\u206662894\u2069\u206662914\u2069", "62894\ufeff62914", "62894\u00a062914"])
def test_a_mark_between_two_numbers_does_not_merge_them_into_one(cell):
    """Second review 23.11: dropping a mark merged two numbers into one (6289462914) that held neither, and the row
    citing 62894 alone was filled though 62894 is cited twice. A mark is a boundary: the cell is unreadable and holds
    both."""
    docs = [doc(62894), doc(62914)]
    assert buckets([row(0, "62894"), row(1, cell)], docs) == {0: "shares_with_unmatched", 1: "ref_unreadable"}


def test_an_unreadable_cell_keeps_the_numbers_a_mark_separates():
    docs = [doc(62001), doc(62003)]
    got = buckets([row(0, "62001"), row(1, "62003"), row(2, "חשבונית 62001\u200f62003")], docs)
    assert got == {0: "shares_with_unmatched", 1: "shares_with_unmatched", 2: "ref_unreadable"}
    assert rd.loose_numbers("628\u200e94") == ["628", "94", "62894"]


class Interleave:
    """A connection that lets another one commit right after the statement that names `after`: a tick's save landing
    between the snapshot's statements."""

    def __init__(self, conn, after: str, action):
        self._conn, self._after, self._action, self.fired = conn, after, action, False

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def execute(self, query, *args, **kwargs):
        cur = self._conn.execute(query, *args, **kwargs)
        if not self.fired and self._after in str(query):
            self.fired = True
            self._action()
        return cur


@needs_pg
def test_the_snapshot_is_one_read_even_when_a_tick_commits_between_its_statements(pg):
    """Second review 23.11: without REPEATABLE READ each statement saw its own moment. A tick that commits a failed
    read and a run cut short right after the documents are read must not change what the same snapshot says."""
    seed_books(pg, [doc(62500)])
    from app.books.store import BooksStore

    def tick():
        with psycopg.connect(pg, autocommit=True) as conn:
            conn.execute("INSERT INTO books_retry (company, stock_id, kind) VALUES (%s, 62501, 'http')", (ANALIZA,))
            conn.execute("INSERT INTO books_sync_runs (company, trigger, finished_at, complete, ok) "
                         "VALUES (%s, 'tick', now(), false, false)", (ANALIZA,))

    with BooksStore(pg).session(ANALIZA + ":t") as s:
        s._conn = Interleave(s._conn, "FROM books_documents", tick)
        snapshot = s.matching_snapshot(ANALIZA)
        assert s._conn.fired is True
    assert (snapshot["retry_waiting"], snapshot["last_run_complete"], snapshot["last_run_ok"]) == (0, True, True)


@needs_pg
def test_a_tick_in_progress_does_not_refuse_the_plan(actions_app):
    """A run that has not finished is not 'the last finished run': a plan asked during a tick is judged on the one
    before it."""
    client, fake, pg = actions_app
    seed_books(pg, DOCS)
    with psycopg.connect(pg, autocommit=True) as conn:
        conn.execute("INSERT INTO books_sync_runs (company, trigger) VALUES (%s, 'tick')", (ANALIZA,))
    assert client.post("/api/actions/plan", headers=AUTH, json=PLAN).json()["ok"] is True


@needs_pg
def test_a_last_run_that_was_not_ok_refuses_the_plan(actions_app):
    """Second review 23.11 (a note, taken): with `canary` the tick trusted no 'not found', so a document deleted from
    the books would still be matched. A complete run that was not ok refuses, and says why."""
    client, fake, pg = actions_app
    seed_books(pg, DOCS)
    with psycopg.connect(pg, autocommit=True) as conn:
        conn.execute("INSERT INTO books_sync_runs (company, trigger, finished_at, complete, ok, reasons) "
                     "VALUES (%s, 'tick', now(), true, false, '{canary}')", (ANALIZA,))
    assert client.post("/api/actions/plan", headers=AUTH, json=PLAN).json() == {
        "ok": False, "error": "books_not_ready", "reason": "last_run_not_ok", "last_run_reasons": ["canary"]}
