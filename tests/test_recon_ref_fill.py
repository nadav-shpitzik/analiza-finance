"""FR-C2 (Analiza-Monday backlog 0301 D3a): an invoice-receipt's number into an empty "אסמכתת חשבונית" on Student Charge -
app/recon/ref_fill.py, the cards it shares with the desk (app/recon/cards.py), and the plan and the read-back routes.

Each condition of the rule with its test (the rules' contract §0.1): the row (FR-C2, ADR-0035, FR-7), the document (FR-L12,
FR-C1, FR-6, FR-3, FR-C4), the equal amount (the contract §0.3), the equal name as a set, and exactly one each way; the
rules file's three named tests; and the invariants: one bucket a row, no document proposed twice, no name or amount in the
plan, and the desk's list and the plan in agreement.

Fixtures are synthetic and shaped like the incidents (the rules' contract §0.2): numbers, amounts and dates mirror the
records, names are invented, and no card carries an ID number."""
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import httpx
import pytest
from fastapi.testclient import TestClient

from app import main
from app.desk import books as db
from app.desk import reading as desk_reading
from app.desk import view as fd
from app.recon import cards
from app.recon import reading as rr
from app.recon import receipt_dates as a1
from app.recon import ref_fill as rf
from tests.conftest import KEY, TEST_DATABASE_URL, drop_all, needs_pg
from tests.desk_fixtures import TODAY, sc, snap
from tests.test_desk_books import run as desk_run
from tests.test_desk_books import seed
from tests.test_recon_reading import SYNC_KEY, TOKEN, Monday, reader

SC_BASE = int(rr.STUDENT_CHARGE[-4:]) * 10000  # the fake's ids of Student Charge, as in A1's tests
NAME = "נועה ברק"
PAID = "3517"  # an amount that no id, number or date of these fixtures holds, so a leak is easy to grep


def row(n: int, ref: str = "", paid: str = PAID, status: str = "1", name: str = NAME, day: str = "",
        made: str = "2026-11-10T08:00:00Z", state: str = "active") -> dict:
    """A Student Charge row as R0's reading holds it (app/monday/client.flatten)."""
    return {"id": str(SC_BASE + n), "name": name, "state": state, "created_at": made, "board_id": rr.STUDENT_CHARGE,
            rf.COLUMN: ref, a1.AMOUNT_COLUMN: paid, a1.COLUMN: day, a1.STATUS_COLUMN: "שולם",
            a1.STATUS_COLUMN + "#": status}


def doc(number, total=PAID, day="2026-11-12", doc_type=2, status=1, missing=False, card="20001", name=NAME,
        stock=None) -> dict:
    """A document as the wide snapshot gives it (BooksStore.matching_snapshot(wide=True)): its card's key and name."""
    total_d = Decimal(total) if total is not None else None
    return {"stock_id": int(stock if stock is not None else number), "doc_type": doc_type, "doc_number": str(number),
            "status": status, "value_date": date.fromisoformat(day) if day else None, "total_incl": total_d,
            "missing": missing, "account_key": card, "account_name": name,
            "total_excl": None if total_d is None else (total_d / Decimal("1.18")).quantize(Decimal("0.01"))}


def b2(n: int, ref: str) -> dict:
    """A B2B Charge row: only what FR-C2 reads of it, the number it cites."""
    return {"id": str(int(rr.B2B_CHARGE[-4:]) * 10000 + n), "name": "חיוב", rf.B2B_REF_COLUMN: ref}


def analysis(rows, docs, b2b=(), keys=None) -> dict:
    return rf.analyse(rows, docs, b2b, keys)


def buckets(rows, docs, b2b=(), keys=None) -> dict:
    return {int(i) - SC_BASE: e["bucket"] for i, e in analysis(rows, docs, b2b, keys)["rows"].items()}


def fills(rows, docs, b2b=(), keys=None) -> dict:
    return {int(i) - SC_BASE: e["proposed"] for i, e in analysis(rows, docs, b2b, keys)["rows"].items()
            if e["bucket"] == "fill"}


# ---- FR-C2, the rules file's three named tests --------------------------------------------------------------------
def test_equal_name_one_candidate_proposes():
    """FR-C2: a paid row with no number, and one uncited invoice-receipt on a student's card with an equal amount and a
    name equal as a set - words of two letters or more, no digits, in any order, any case, whatever between them: the
    plan proposes its number, with the document's date."""
    pairs = [("נועה ברק", "ברק נועה"), ("יואב שטרן", "יואב  שטרן"), ("מאיה גולן", "מאיה-גולן"), ("Ido Raz", "IDO RAZ"),
             ("שירה אלון 2026", "שירה אלון"), ("תומר נחום", "תומר, נחום.")]
    rows = [row(i, name=r) for i, (r, _c) in enumerate(pairs)]
    docs = [doc(62500 + i, card=str(20001 + i), name=c, day="2026-11-%02d" % (10 + i)) for i, (_r, c) in enumerate(pairs)]
    got = analysis(rows, docs)
    assert {int(i) - SC_BASE: (e["bucket"], e["proposed"], e["document_date"]) for i, e in got["rows"].items()} == {
        i: ("fill", str(62500 + i), "2026-11-%02d" % (10 + i)) for i in range(len(pairs))}


def test_similar_name_needs_an_eye():
    """FR-C2: a similar name - a letter more, a word more or less, an initial - never proposes a fill, whatever the
    amount: it needs a person's eye, which the desk's list gives (the document stays on it)."""
    similar = ["נועה ברקן", "נועה ברק כהן", "נ. ברק", "ברק", "נועה בר"]
    rows = [row(i, name=n) for i, n in enumerate(similar)]
    got = analysis(rows, [doc(62500)])
    assert {e["bucket"] for e in got["rows"].values()} == {"no_candidate"}
    assert got["documents"] == {"students": 1, "proposed": 0, "era_listed": 1, "era_left": 1}
    assert cards.name_words("נ. ברק") == frozenset({"ברק"})  # an initial is no word


def test_two_candidates_propose_nothing():
    """FR-C2: exactly one each way. Two invoice-receipts with the row's name and amount propose neither; one invoice-receipt
    that two rows want is proposed to neither. A name shared by two rows of different amounts, each with its own document,
    is no ambiguity: both are proposed."""
    assert buckets([row(0)], [doc(62500), doc(62501, card="20002")]) == {0: "two_candidates"}
    assert buckets([row(0), row(1)], [doc(62500)]) == {0: "document_wanted_twice", 1: "document_wanted_twice"}
    got = buckets([row(0), row(1), row(2, paid="1960")],
                  [doc(62500), doc(62501, card="20002"), doc(62502, card="20003", total="1960")])
    assert got == {0: "two_candidates", 1: "two_candidates", 2: "fill"}
    two_payments = [row(0), row(1, name="ברק נועה", paid="2843.5")]
    assert fills(two_payments, [doc(62500), doc(62501, card="20002", total="2843.5")]) == {0: "62500", 1: "62501"}


def test_a_row_ambiguous_between_two_documents_holds_both():
    """Condition 5, the other way: row 1 (3518.60) is within 1 ₪ of both 62500 (3517.70) and 62501 (3519.50), row 0 (3517)
    of 62500 only. Row 1 may be paid by 62500, so 62500 is wanted twice, and row 0 waits too."""
    got = analysis([row(0, paid="3517"), row(1, paid="3518.60")],
                   [doc(62500, total="3517.70"), doc(62501, card="20002", total="3519.50")])
    entries = {int(i) - SC_BASE: e for i, e in got["rows"].items()}
    assert entries[0]["documents"] == ["62500"] and entries[1]["documents"] == ["62500", "62501"]
    assert (entries[0]["bucket"], entries[1]["bucket"]) == ("document_wanted_twice", "two_candidates")


# ---- condition 1: the row ------------------------------------------------------------------------------------------
NAMES = ["נועה ברק", "יואב שטרן", "מאיה גולן", "עידו רז", "שירה אלון", "תומר נחום", "ליאת כרמי", "אורי פז", "רוני שגב"]


def one_each(rows_kw: list, docs_kw: list = None) -> tuple:
    """Rows and documents of distinct names, each row with its own invoice-receipt: what one row's bucket is, alone."""
    rows = [row(i, name=NAMES[i], **kw) for i, kw in enumerate(rows_kw)]
    docs = [doc(62500 + i, **{"card": str(20001 + i), "name": NAMES[i], **kw})
            for i, kw in enumerate(docs_kw or [{}] * len(rows_kw))]
    return rows, docs


def test_an_archived_row_is_neither_filled_nor_holds_a_document():
    """Like A1: a row archived or deleted between its id and its row is `not_active`, and nothing is written to it; the
    active row of the same name and amount is filled."""
    assert buckets([row(0, state="archived"), row(1)], [doc(62500)]) == {0: "not_active", 1: "fill"}


def test_a_status_that_does_not_say_money_came_in_is_never_filled():
    """FR-C2 ("a paid or refunded row") and ADR-0035, by A1's MONEY_IN by index: שולם, תשלום הוחזר, הוחזר חלקי and ביקש
    החזר are filled; לא שולם, יש בעיה, מלגת אנליזה, an empty status and a label the board added later are not. Such a row
    still holds its document (condition 6), and with no other row of its name and amount nothing is proposed."""
    statuses = ["1", "4", "5", "9", "0", "2", "3", "", "7"]
    rows, docs = one_each([{"status": s} for s in statuses])
    assert buckets(rows, docs) == {0: "fill", 1: "fill", 2: "fill", 3: "fill", 4: "not_money_in", 5: "not_money_in",
                                   6: "not_money_in", 7: "not_money_in", 8: "not_money_in"}
    got = analysis([row(0, status="0")], [doc(62500)])
    assert got["rows"][str(SC_BASE)]["documents"] == ["62500"] and got["documents"]["proposed"] == 0


def test_a_row_that_cites_a_number_is_a1s_never_this_rules():
    """FR-C1 is A1's: a row whose cell cites a number is `has_ref`, whether or not the books hold it, and its number is
    cited, so its document is proposed to nobody - also not to a row of the same name with no number."""
    rows = [row(0, ref="62500"), row(1, ref="99999", name="יואב שטרן"), row(2)]
    assert buckets(rows, [doc(62500)]) == {0: "has_ref", 1: "has_ref", 2: "no_candidate"}


def test_an_unreadable_cell_is_never_filled_and_holds_every_number_it_may_name():
    """A cell A1 cannot read is no empty cell (`ref_unreadable`, for a person); and the numbers it may name (A1's
    `loose_numbers`) are cited, so 62500 - which `62500 62501` may cite - is proposed to nobody. An invisible mark alone
    is empty, as A1 reads a cell: that row is filled, and the plan says the value now as monday holds it."""
    rows = [row(0, ref="62500 62501", name="יואב שטרן"), row(1), row(2, ref="‏", name="מאיה גולן")]
    docs = [doc(62500), doc(62502, card="20003", name="מאיה גולן")]
    got = analysis(rows, docs)
    assert {int(i) - SC_BASE: e["bucket"] for i, e in got["rows"].items()} == {
        0: "ref_unreadable", 1: "no_candidate", 2: "fill"}
    assert got["rows"][str(SC_BASE + 2)]["current"] == "‏"
    assert buckets([row(0, ref="חשבונית")], [doc(62500)]) == {0: "ref_unreadable"}


def test_a_row_with_no_amount_has_nothing_to_equal():
    rows, docs = one_each([{"paid": ""}, {"paid": "₪3517"}, {"paid": "3,517"}, {"paid": "abc"}])
    assert buckets(rows, docs) == {0: "amount_unreadable", 1: "amount_unreadable", 2: "fill", 3: "amount_unreadable"}


def test_a_row_is_dated_in_the_window_by_its_receipt_date_else_the_day_it_was_made():
    """FR-7: Student Charge's window is 2026. A row is dated as the desk files a payment: its "תאריך תקבול" when it is a
    day, else the day it was made in Israel's time - so 22:30 UTC on 31.12.2025 is 1.1.2026, and in the window. Each
    document is near its row (condition 5), so the year alone decides here."""
    rows, docs = one_each([
        {"day": "2025-12-30"},                                          # its receipt date: 2025
        {"made": "2025-12-30T10:00:00Z"},                               # made in 2025, no receipt date
        {"made": "2025-12-31T22:30:00Z"},                               # 1.1.2026 in Israel
        {"day": "2027-01-02"},                                          # 2027
        {"made": ""},                                                   # no day at all
        {"day": "2026-03-01", "made": "2025-12-01T10:00:00Z"},          # the receipt date wins
        {"day": "לא ידוע", "made": "2026-02-01T10:00:00Z"},             # a typed date that is no day: the day made
        {"made": "2026-12-31T22:30:00Z"},                               # 1.1.2027 in Israel
    ], [{"day": d} for d in ("2026-01-05", "2026-01-05", "2026-01-05", "2026-12-28", "2026-11-12", "2026-03-05",
                             "2026-02-03", "2026-12-28")])
    assert buckets(rows, docs) == {0: "out_of_window", 1: "out_of_window", 2: "fill", 3: "out_of_window",
                                   4: "out_of_window", 5: "fill", 6: "fill", 7: "out_of_window"}
    assert rf.row_day(row(0, made="2026-03-01T10:00:00")) == date(2026, 3, 1)  # a stamp with no zone is UTC


def test_the_row_day_is_the_day_the_desk_files_the_payment_under():
    """The desk and the rule date a row alike (app/desk/view.py `_b2c_row`): the receipt date typed, else the day made in
    Israel's time."""
    stamps = [("", "2026-11-10T08:00:00Z"), ("", "2025-12-31T22:30:00Z"), ("2026-03-01", "2025-12-01T10:00:00Z"),
              ("", "2026-06-30T21:30:00Z"), ("", "2026-01-15T10:00:00+02:00")]
    for paid_date, made in stamps:
        desk = sc(1, invoice="", created=made, paid_date=paid_date)
        filed = fd.build_view(snap([desk]), TODAY)
        assert rf.row_day(desk).isoformat() == filed["b2c"]["rows"][0]["date"], (paid_date, made)


# ---- condition 2: the document -------------------------------------------------------------------------------------
@pytest.mark.parametrize("why,docs,b2b,keys", [
    ("cited by a Student Charge row", [doc(62500)], (), None),
    ("cited by a B2B Charge row", [doc(62500)], ("62500",), None),
    ("cited by a B2B cell that may name it", [doc(62500)], ("62500 50400",), None),
    ("dated 2025", [doc(62500, day="2025-12-30")], (), None),
    ("dated 2027", [doc(62500, day="2027-01-02")], (), None),
    ("no date", [doc(62500, day="")], (), None),
    ("answered not found", [doc(62500, missing=True)], (), None),
    ("a draft", [doc(62500, status=0)], (), None),
    ("another status", [doc(62500, status=2)], (), None),
    ("a credit", [doc(62500, doc_type=3)], (), None),
    ("a receipt", [doc(62500, doc_type=31)], (), None),
    ("a proforma", [doc(62500, doc_type=78)], (), None),
    ("a negative invoice-receipt", [doc(62500, total="-3517")], (), None),
    ("no total", [doc(62500, total=None)], (), None),
    ("a company's card", [doc(62500, card="30001")], (), {"30001": ["חברה בדויה"]}),
    ("an invoice-receipt beside a proforma", [doc(62500), doc(40500, doc_type=78, total="9000")], (), None),
    ("only tax invoices on the card", [doc(50400, doc_type=1)], (), None),
    ("the vouchers' card", [doc(62500, card=cards.VOUCHER_CARD)], (), None),
])
def test_only_an_uncited_students_document_of_the_window_is_a_candidate(why, docs, b2b, keys):
    """Condition 2, each way it fails, as the desk judges a document (`place_of` from the window's first day): status 1,
    a tax invoice or an invoice-receipt, a total that is not negative, dated in 2026, cited by no row of either board (an
    unreadable cell holding what it may name), not "not found", on a students' card - not an Account's key (FR-C4: the
    employment service's 63149 and 63186 on a card with proforma 40522), not unclear, not the vouchers' 10091 (FR-D1)."""
    rows = [row(0)] + ([row(1, ref="62500", name="יואב שטרן")] if why == "cited by a Student Charge row" else [])
    b2b_rows = [b2(i, ref) for i, ref in enumerate(b2b)]
    assert buckets(rows, docs, b2b_rows, keys)[0] == "no_candidate", why
    assert fills(rows, docs, b2b_rows, keys) == {}


def test_only_an_invoice_receipt_is_proposed_and_a_tax_invoice_waits():
    """FR-L12 and FR-C1: an invoice-receipt pays itself; a tax invoice alone is a demand (`b2c_paid_no_proof`). A tax
    invoice with the row's name and amount, on a students' card (one that holds an invoice-receipt too), is counted and
    never proposed - also beside an invoice-receipt of the same name and amount, which may then be the other payment."""
    card = [doc(62490, total="1200", day="2026-09-01")]  # the card's invoice-receipt of another amount: a students' card
    assert buckets([row(0)], card + [doc(50400, doc_type=1)]) == {0: "tax_invoice_waits"}
    assert buckets([row(0)], card + [doc(50400, doc_type=1), doc(62500)]) == {0: "tax_invoice_waits"}
    assert fills([row(0)], card + [doc(62500)]) == {0: "62500"}


def test_a_number_two_valid_documents_carry_is_never_proposed():
    """FR-3: two status-1 documents with one number - written, it would name neither (A1 says `ref_ambiguous`), so it is
    never proposed, even when only one of them is the row's. A draft beside it stops nothing (FR-2); a number A1 would
    not read back as one number (fewer than four digits) is never proposed either."""
    assert buckets([row(0)], [doc(62500), doc(62500, doc_type=31, card="20009", name="אחר", stock=99001)]) == {
        0: "number_ambiguous"}
    assert fills([row(0)], [doc(62500), doc(62500, status=0, stock=99002)]) == {0: "62500"}
    assert buckets([row(0)], [doc(625)]) == {0: "number_ambiguous"}


def test_a_masked_or_empty_card_key_still_allows_a_students_card():
    """D3's side logic, reused (FR-6, the incident of 18.11): a key the copy masked ("#") or left empty names no card,
    and the side is judged by the card's name - an invoice-receipt with no proforma of that name is a student's, and is
    proposed; a proforma of the same name makes it unclear, and it is not. A document with neither a key nor a name is
    judged alone, by its type, but it has no name to equal: never proposed."""
    rows, docs = one_each([{}, {}, {}, {}], [{"card": "#"}, {"card": ""}, {"card": None}, {"card": "AB-#"}])
    assert buckets(rows, docs) == {0: "fill", 1: "fill", 2: "fill", 3: "fill"}
    masked = [doc(62500, card="#"), doc(40500, doc_type=78, card="#", name="נועה  ברק", total="9000")]
    assert buckets([row(0)], masked) == {0: "no_candidate"}
    assert cards.side_of(masked[0], cards.card_sides(masked, {})) == ("unclear", "name_invoice_receipt_and_proforma")
    alone = [doc(62500, card=None, name=None)]
    assert cards.side_of(alone[0], cards.card_sides(alone, {})) == ("student", "invoice_receipt")
    got = analysis([row(0)], alone)
    assert got["rows"][str(SC_BASE)]["bucket"] == "no_candidate" and got["documents"]["era_left"] == 1
    # an Account's key masked as the desk reads it names no company's card either
    assert buckets([row(0)], [doc(62500, card="#")], keys={"#": ["חברה"]}) == {0: "fill"}


# ---- conditions 3 and 4: the amount and the name ---------------------------------------------------------------------
def test_the_amount_is_equal_within_one_shekel_and_never_by_the_rounding_band():
    """The contract §0.3: 1 ₪ for one document. 3517 against 3518 and 3516 is equal; 3518.01 is not, and nor is the
    rounding band (0.1%): past 1 ₪ is `amount_differs`, a document with the name and another amount - a payment with no
    row, or an amount typed wrong."""
    totals = ["3518", "3516", "3517.00", "3518.01", "3515.99", "3520", "1200"]
    rows, docs = one_each([{}] * len(totals), [{"total": t} for t in totals])
    assert buckets(rows, docs) == {0: "fill", 1: "fill", 2: "fill", 3: "amount_differs", 4: "amount_differs",
                                   5: "amount_differs", 6: "amount_differs"}


def test_an_empty_name_on_either_side_is_never_equal():
    """Condition 4 needs both names: a row named only by digits or one letter, or a card with no name, equals nothing -
    not even another empty name."""
    assert buckets([row(0, name="12345 א")], [doc(62500, name="54321")]) == {0: "no_candidate"}
    assert buckets([row(0, name="")], [doc(62500, name="")]) == {0: "no_candidate"}


# ---- a row's name as the person's (`person_name`) ----------------------------------------------------------------------
def completion(n: int, name: str = NAME, paid: str = "8000", made: str = "2026-11-20T09:00:00Z", **kw) -> dict:
    """A balance payment as the helper makes it (analiza-monday-helper `/b2c/completion-charge`, when sales set the voucher
    to "אושר - יתרה שולמה"): named "<student> | השלמת תשלום", its "סוג חיוב" the same words (a label the rule never reads),
    its "תאריך תקבול" empty, so it is dated by the day it was made."""
    r = row(n, name=name + " | השלמת תשלום", paid=paid, made=made, **kw)
    r[rr.SC["ctype"]] = "השלמת תשלום"
    return r


def test_person_name_is_the_text_before_the_first_pipe():
    assert cards.person_name("נועה ברק | השלמת תשלום") == "נועה ברק"
    assert cards.person_name("נועה ברק") == "נועה ברק"                              # no pipe: the name as it is
    assert cards.person_name("נועה ברק | השלמת תשלום | 2") == "נועה ברק"            # two pipes: cut at the first
    assert cards.person_name(None) == "" and cards.person_name("| השלמת תשלום") == ""


def test_a_completion_row_is_proposed_its_students_balance_invoice_receipt():
    """The live case of 25.11: a balance payment "X | השלמת תשלום" (8,000, its receipt date empty) is X's row, so X's
    invoice-receipt of 8,000 near the day it was made is proposed to it, and reads back verified. X's old 4,000 deposit row,
    which cites its own number, is no rival (it has a number, and another amount)."""
    rows = [completion(0), row(1, ref="62300", paid="4000", made="2026-10-15T09:00:00Z")]
    docs = [doc(62520, total="8000", day="2026-11-21"), doc(62300, total="4000", day="2026-10-15")]
    got = analysis(rows, docs)
    assert {int(i) - SC_BASE: (e["bucket"], e["proposed"]) for i, e in got["rows"].items()} == {
        0: ("fill", "62520"), 1: ("has_ref", None)}
    after = [dict(rows[0], **{rf.COLUMN: "62520"}), rows[1]]
    body = rf.verify("analiza", a_reading(after), docs, [str(SC_BASE)])
    assert body["items"][str(SC_BASE)] == {"state": "verified", "document": "62520"}


def test_the_desk_lists_the_balance_document_with_the_completion_row_shown_whole():
    """D3's candidates meet a row as the person's too (the index of `compute`), and show the row's full name, so Chava sees
    which row it is: the balance document lists the completion row and the old deposit row of the same student. A card
    whose own name holds a pipe is taken whole, and names no candidate."""
    rows = [sc(1, invoice="", paid="8000", created="2026-11-20T09:00:00Z"),
            sc(2, invoice="62300", paid="4000", created="2026-10-15T09:00:00Z")]
    rows[0]["name"], rows[1]["name"] = "נועה ברק | השלמת תשלום", "נועה ברק"
    docs = [doc(62520, total="8000", day="2026-11-21"), doc(62300, total="4000", day="2026-10-26"),
            doc(62521, total="5000", day="2026-11-21", card="20009", name="נועה ברק | חברה")]
    part, _v, _r = desk_run(rows, docs, [], {})
    listed = {e["number"]: e for e in part["students"]}
    assert set(listed) == {"62520", "62521"}                                       # 62300 is cited by the deposit row
    assert [(c["id"], c["name"]) for c in listed["62520"]["candidates"]] == [("1", "נועה ברק | השלמת תשלום"),
                                                                             ("2", "נועה ברק")]
    assert listed["62521"]["candidates"] == []
    assert {i: e["proposed"] for i, e in rf.analyse(rows, docs)["rows"].items()} == {"1": "62520", "2": None}


def test_a_cards_name_is_never_cut_and_a_similar_name_stays_unproposed():
    """`person_name` is a row's only: a card's name is compared whole, in the plan and in the read-back. And a completion
    row whose name differs from its card by spelling stays unproposed, as the two live ones of 25.11 must: an added middle
    name, or a letter more ("מירון" for "מרון") - a similar name needs a person's eye (FR-C2)."""
    piped = [doc(62500, name="נועה ברק | חברה")]
    assert buckets([row(0)], piped) == {0: "no_candidate"}
    body = rf.verify("analiza", a_reading([row(0, ref="62500")]), piped, [str(SC_BASE)])
    assert body["items"][str(SC_BASE)]["state"] == "name_differs"
    similar = [completion(0, name="דנה רות לוי"), completion(1, name="יעל מירון")]
    docs = [doc(62520, total="8000", card="20002", name="דנה לוי"), doc(62521, total="8000", card="20003", name="יעל מרון")]
    assert buckets(similar, docs) == {0: "no_candidate", 1: "no_candidate"}


# ---- condition 5: near in time -------------------------------------------------------------------------------------
def test_a_document_is_near_its_row_within_thirty_days_before_or_after():
    """Condition 5: course prices repeat, so a document of the row's name and amount is a candidate only when its date is
    at most FILL_WINDOW_DAYS (30) days before or after the row's day; at 31 days either way it is `date_far`, counted and
    never proposed. The row's day is the desk's: made at 22:30 UTC on 14.06 is 15.06 in Israel, 30 days before 15.07."""
    assert rf.FILL_WINDOW_DAYS == 30
    rows, docs = one_each([{"day": "2026-06-15"}] * 4 + [{"made": "2026-06-14T22:30:00Z"}] * 2,
                          [{"day": d} for d in ("2026-05-16", "2026-05-15", "2026-07-15", "2026-07-16",
                                                "2026-07-15", "2026-07-16")])
    assert buckets(rows, docs) == {0: "fill", 1: "date_far", 2: "fill", 3: "date_far", 4: "fill", 5: "date_far"}


def test_the_window_comes_after_the_amount_and_before_the_document_kind():
    """`date_far` is the row's when its name and amount meet only documents a season away - after `amount_differs` (a near
    document of another amount is no candidate either) and before the kind: a far tax invoice makes nobody wait, and a far
    invoice-receipt is no second candidate (another payment of the same price), so the near one is proposed."""
    far_equal_near_other = [doc(62500, day="2026-03-01"), doc(62501, card="20002", total="2843.5")]
    assert buckets([row(0)], far_equal_near_other) == {0: "date_far"}
    card = [doc(62490, total="1200", day="2026-09-01")]  # the card's invoice-receipt of another amount: a students' card
    assert fills([row(0)], card + [doc(50400, doc_type=1, day="2026-03-01"), doc(62500)]) == {0: "62500"}
    assert fills([row(0)], [doc(62500), doc(62501, card="20002", day="2026-05-01")]) == {0: "62500"}


# ---- condition 6: who holds a document --------------------------------------------------------------------------------
def test_a_rival_whose_status_says_no_money_came_blocks_the_fill():
    """Condition 6, whatever the rival's status: a row of the same name and amount, near in time, with an empty cell, still
    marked "לא שולם" or "יש בעיה" - or any status that says no money came, or a label the desk does not know - may be the
    document's real row (statuses lag: the case of 23.11). The paid row is not filled (`document_wanted_twice`), and the
    rival is never filled either: condition 1 decides who may receive a number."""
    for status in ("0", "2", "3", "", "7"):
        got = analysis([row(0), row(1, status=status, made="2026-11-20T08:00:00Z")], [doc(62500)])
        entries = {int(i) - SC_BASE: e for i, e in got["rows"].items()}
        assert (entries[0]["bucket"], entries[1]["bucket"]) == ("document_wanted_twice", "not_money_in"), status
        assert entries[1]["documents"] == ["62500"] and got["documents"]["proposed"] == 0
    # from the paid row's side, two documents of its own stay `two_candidates`, rival or not
    assert buckets([row(0), row(1, status="0")], [doc(62500), doc(62501, card="20002")]) == {
        0: "two_candidates", 1: "not_money_in"}
    # a rival that holds two documents holds both: the paid row's one is wanted twice
    got = buckets([row(0, paid="3517"), row(1, status="0", paid="3518.60")],
                  [doc(62500, total="3517.70"), doc(62501, card="20002", total="3519.50")])
    assert got == {0: "document_wanted_twice", 1: "not_money_in"}


def test_an_archived_rival_or_one_with_no_amount_does_not_block():
    """Condition 6 needs an active row with an amount equal to the document's: an archived row of the same name, and one
    whose "סכום ששולם" is empty or not a number (whatever its status), hold nothing, so the paid row is filled."""
    for rival in (row(1, state="archived"), row(1, state="archived", status="0"), row(1, paid=""), row(1, paid="",
                  status="0"), row(1, paid="abc", status="2")):
        assert fills([row(0), rival], [doc(62500)]) == {0: "62500"}, rival


def test_a_rival_with_a_number_another_amount_another_name_or_far_in_time_does_not_block():
    """Condition 6 asks the rival what it asks the row, but its status: a row whose cell cites a number or holds text A1
    cannot read, one of another amount, one of a similar name, and one 31 days from the document hold nothing."""
    rivals = [row(1, ref="62480", status="0"), row(1, ref="חשבונית", status="0"), row(1, paid="2843.5", status="0"),
              row(1, name="נועה ברקן", status="0"), row(1, day="2026-12-13", status="0"),
              row(1, day="2026-10-12", status="0")]
    for rival in rivals:
        assert fills([row(0), rival], [doc(62500), doc(62480, card="20009", name="אחר")]) == {0: "62500"}, rival
    # 30 days from the document, the rival blocks
    assert buckets([row(0), row(1, day="2026-12-12", status="0")], [doc(62500)]) == {
        0: "document_wanted_twice", 1: "not_money_in"}


def test_a_rival_made_in_late_december_blocks_an_invoice_receipt_of_early_january():
    """Condition 6, whatever the rival's year: the 30 days around the document decide, not Student Charge's year (FR-7
    decides only who may receive a fill). A row made on 25.12.2025, unpaid or paid, may be the real row of an
    invoice-receipt of 5.1.2026: the row made on 6.1.2026 is not filled."""
    docs = [doc(62500, day="2026-01-05")]
    for status in ("0", "1"):
        got = buckets([row(0, made="2026-01-06T08:00:00Z"), row(1, made="2025-12-25T08:00:00Z", status=status)], docs)
        assert got == {0: "document_wanted_twice", 1: "not_money_in" if status == "0" else "out_of_window"}, status
    assert fills([row(0, made="2026-01-06T08:00:00Z"), row(1, made="2025-12-05T08:00:00Z")], docs) == {0: "62500"}


# ---- condition 5 and the plan -----------------------------------------------------------------------------------------
def rich() -> tuple:
    """Rows and documents that land in every bucket: (rows, documents, B2B rows, keys)."""
    rows = [row(0, state="archived", name="אורי פז"),                       # not_active
            row(1, status="0", name="רוני שגב"),                             # not_money_in
            row(2, ref="62480", name="גל עמית"),                             # has_ref
            row(3, ref="62481 62482", name="גל עמית"),                       # ref_unreadable
            row(4, paid="", name="ליאת כרמי"),                               # amount_unreadable
            row(5, made="2025-11-02T10:00:00Z", name="תומר נחום"),           # out_of_window
            row(6, name="שם שאין לו מסמך"),                                  # no_candidate
            row(7, name="שירה אלון", paid="2275"),                           # amount_differs
            row(8, name="עידו רז"),                                          # tax_invoice_waits
            row(9, name="מאיה גולן"),                                        # two_candidates
            row(10, name="רוני אבן"),                                        # number_ambiguous
            row(11, name="יואב שטרן"), row(12, name="שטרן יואב"),            # document_wanted_twice
            row(13), row(14, name="דור לב", paid="4180", day="2026-04-02"),  # fill, fill (before the desk's era)
            row(15, name="עמית שלו", made="2026-03-01T10:00:00Z")]            # date_far
    docs = [doc(62480, card="20100", name="גל עמית"), doc(62481, card="20100", name="גל עמית"),
            doc(62482, card="20100", name="גל עמית"),
            doc(62483, card="20101", name="תומר נחום"),
            doc(62484, card="20102", name="שירה אלון", total="2843.5"),
            doc(62485, card="20103", name="עידו רז", total="1200"), doc(50400, doc_type=1, card="20103", name="עידו רז"),
            doc(62486, card="20104", name="מאיה גולן"), doc(62487, card="20105", name="מאיה גולן"),
            doc(62488, card="20106", name="רוני אבן"),
            doc(62488, doc_type=31, card="20106", name="רוני אבן", stock=99003),
            doc(62489, card="20107", name="יואב שטרן"),
            doc(62490, card="20001"), doc(62491, card="20108", name="דור לב", total="4180", day="2026-04-02"),
            doc(62492, card="20109", name="ליאת כרמי"),
            doc(62493, card="30001", name="חברה בדויה"),                     # a company's: never a student's
            doc(62494, card="20110", name="אפרת חן"),                        # a student's with no row: stays listed
            doc(62496, card="20111", name="עמית שלו")]                       # row 15's name and amount, a season on
    return rows, docs, [b2(0, "62495")], {"30001": ["חברה בדויה"]}


def test_every_row_is_in_exactly_one_bucket_and_no_document_is_proposed_twice():
    """Spec §6.3.1: the counts add up to the rows read, each row's bucket is one of BUCKETS, in their order - and this
    fixture reaches every one of them. No document is proposed to two rows, and no row is proposed two documents."""
    rows, docs, b2b, keys = rich()
    got = analysis(rows, docs, b2b, keys)
    assert sum(got["counts"].values()) == len(rows) == len(got["rows"])
    assert list(got["counts"]) == list(rf.BUCKETS) and all(e["bucket"] in rf.BUCKETS for e in got["rows"].values())
    assert {e["bucket"] for e in got["rows"].values()} == set(rf.BUCKETS)
    assert [int(i) - SC_BASE for i, e in got["rows"].items() if e["bucket"] == "fill"] == [13, 14]
    proposed = [e["proposed"] for e in got["rows"].values() if e["bucket"] == "fill"]
    assert len(proposed) == len(set(proposed)) == got["documents"]["proposed"] == 2
    assert all(isinstance(p, str) and a1.parse_refs(p) == [p] for p in proposed)
    # the pool: the uncited students' documents of 2026; of them the desk lists those from ERA_FROM
    assert got["documents"] == {"students": 13, "proposed": 2, "era_listed": 12, "era_left": 11}


def a_reading(rows: list, b2b: list = (), accounts: list = ()) -> dict:
    """A complete reading of the profile with these rows on Student Charge, B2B Charge and Accounts."""
    reading = rr.read_profile(reader(Monday({b.board_id: 1 for b in rr.PROFILE})))
    assert reading["complete"] is True
    reading["boards"].update(student_charge=list(rows), b2b_charge=list(b2b), accounts=list(accounts))
    return reading


def accounts_of(keys: dict) -> list:
    return [{"id": str(int(rr.ACCOUNTS[-4:]) * 10000 + i), "name": names[0], rr.AC["hash_key"]: key}
            for i, (key, names) in enumerate(keys.items())]


def test_the_plan_refuses_demo_and_a_reading_that_is_not_whole():
    """The rules' contract §0.6, through `monday_side`."""
    complete = a_reading([row(0)])
    for company, reading, reason in (("demo", complete, "demo"), ("analiza", None, "no_reading"),
                                     ("analiza", {**complete, "complete": False}, "incomplete")):
        for call in (lambda: rf.plan(company, reading, [doc(62500)]),
                     lambda: rf.verify(company, reading, [doc(62500)], [str(SC_BASE)])):
            with pytest.raises(rr.NotReady) as err:
                call()
            assert err.value.reason == reason
    assert rf.plan("analiza", complete, [doc(62500)])["fill"] == 1


def test_the_plan_never_takes_a_missing_board_for_an_empty_one():
    """A reading without B2B Charge's rows is an error, never a board that cites nothing (its documents would turn into
    documents no row cites)."""
    reading = a_reading([row(0)])
    del reading["boards"]["b2b_charge"]
    with pytest.raises(KeyError):
        rf.plan("analiza", reading, [doc(62500)])


def test_the_plan_says_counts_links_numbers_and_dates_and_never_a_name_or_an_amount():
    """Spec §7.1.1: per row to fill its link, the value now, the number proposed and the document's date; every bucket's
    count; the ids of the rows a person can decide; the students' documents counted apart. No name, no amount."""
    rows, docs, b2b, keys = rich()
    body = rf.plan("analiza", a_reading(rows, b2b, accounts_of(keys)), docs)
    assert body["kind"] == "b2c_ref_fill" and body["class"] == "A" and body["column"] == "text_mm6d6s97"
    assert body["rows"] == len(rows) and body["fill"] == 2 and body["fill_by_month"] == {"2026-04": 1, "2026-11": 1}
    pulse = "https://analiza-college-group.monday.com/boards/5093448677/pulses/%d"
    assert body["actions"] == [   # by the document's date
        {"item_id": str(SC_BASE + 14), "url": pulse % (SC_BASE + 14), "column": "text_mm6d6s97", "value_now": "",
         "value_proposed": "62491", "document_date": "2026-04-02"},
        {"item_id": str(SC_BASE + 13), "url": pulse % (SC_BASE + 13), "column": "text_mm6d6s97", "value_now": "",
         "value_proposed": "62490", "document_date": "2026-11-12"}]
    assert body["ids_by_bucket"] == {b: sorted(str(SC_BASE + n) for n in ns) for b, ns in {
        "ref_unreadable": [3], "amount_unreadable": [4], "amount_differs": [7], "date_far": [15],
        "tax_invoice_waits": [8], "two_candidates": [9], "number_ambiguous": [10],
        "document_wanted_twice": [11, 12]}.items()}
    assert body["documents"] == {"students": 13, "proposed": 2, "era_listed": 12, "era_left": 11}
    text = json.dumps(body, ensure_ascii=False)
    names = {r["name"] for r in rows} | {d["account_name"] for d in docs} | {"חברה בדויה"}
    for leak in {w for n in names for w in n.split()} | names:
        assert leak not in text, leak
    for amount in ("3517", "3,517", "2843.5", "2,843", "2275", "1200", "1,200", "4180", "4,180", "9000"):
        assert amount not in text, amount


# ---- the desk and the plan agree ---------------------------------------------------------------------------------------
def test_the_desk_lists_every_document_the_plan_proposes_with_its_row_among_the_candidates():
    """One fixture, the desk's D3 and the plan: every document the plan proposes from the desk's era (ERA_FROM) is on the
    desk's list of the students' documents no charge cites, with the proposed row among its candidates by name; one
    before the era is the cleanup list (backlog 0301 C1), never on the list (`outside`). And what the plan says the list
    holds, and leaves on it, is what the desk lists."""
    rows = [sc(1, invoice="", paid=PAID, created="2026-11-10T08:00:00Z"),
            sc(2, invoice="", paid=PAID, created="2026-11-11T08:00:00Z"),
            sc(3, invoice="", paid="2843.5", created="2026-11-12T08:00:00Z"),
            sc(4, invoice="62485", paid="1960"),
            sc(5, invoice="", paid="4180", paid_date="2026-04-02"),
            sc(6, invoice="", paid=PAID, created="2026-11-13T08:00:00Z"),
            sc(7, invoice="", paid=PAID, st="0"),
            sc(8, invoice="", paid="2275", created="2026-11-14T08:00:00Z"),
            sc(9, invoice="", paid="8000", created="2026-11-17T08:00:00Z")]
    names = {"1": "נועה ברק", "2": "יואב שטרן", "3": "שטרן יואב", "4": "מאיה גולן", "5": "דור לב", "6": "מאיה גולן",
             "7": "עידו רז", "8": "שירה אלון", "9": "שירה אלון | השלמת תשלום"}
    for r in rows:
        r["name"] = names[r["id"]]
    docs = [doc(62480, day="2026-11-12"),                                                     # proposed to 1
            doc(62481, card="20002", name="יואב שטרן", day="2026-11-12"),                     # proposed to 2
            doc(62482, card="20003", name="יואב שטרן", total="2843.5", day="2026-11-13"),    # proposed to 3
            doc(62483, card="20004", name="דור לב", total="4180", day="2026-04-02"),         # proposed to 5: before the era
            doc(62484, card="20005", name="מאיה גולן", day="2026-11-14"),                     # proposed to 6 (4 cites one)
            doc(62485, card="20006", name="מאיה גולן", total="1960", day="2026-11-01"),      # cited by 4
            doc(62486, card="20007", name="עידו רז", day="2026-11-15"),                       # 7: no money came in
            doc(62487, card="20008", name="שירה אלון", day="2026-11-16"),                     # 8: another amount
            doc(62488, card="30001", name="חברה בדויה", day="2026-11-17"),                    # a company's
            doc(62489, card="20008", name="שירה אלון", total="8000", day="2026-11-18")]       # 9's balance
    keys = {"30001": ["חברה בדויה"]}
    got = rf.analyse(rows, docs, [], keys)
    part, _view, _reading = desk_run(rows, docs, [], keys)
    listed = {e["number"]: e for e in part["students"]}
    proposal = {e["proposed"]: i for i, e in got["rows"].items() if e["bucket"] == "fill"}
    assert proposal == {"62480": "1", "62481": "2", "62482": "3", "62483": "5", "62484": "6", "62489": "9"}
    for number, item_id in proposal.items():
        d = next(x for x in docs if x["doc_number"] == number)
        if d["value_date"] >= cards.ERA_FROM:
            assert number in listed and item_id in [c["id"] for c in listed[number]["candidates"]], number
        else:
            assert number not in listed and part["places"][d["stock_id"]] == "outside"
    assert got["documents"]["era_listed"] == len(part["students"]) == 7
    assert got["documents"]["era_left"] == len([n for n in listed if n not in proposal]) == 2
    # and the rule's pool from the era is the desk's list, document for document
    pool = {d["doc_number"] for d in docs
            if cards.place_of(d, cards.cited_numbers(rows, rf.COLUMN), cards.card_sides(docs, keys))[0] == "students"}
    assert pool == set(listed)


def test_the_desk_and_the_rule_share_one_code():
    """The helpers both need are app/recon/cards.py's, imported by the desk, never copied; and the column both read an
    Account's key from is one."""
    for name in ("name_words", "card_sides", "side_card", "card_of", "account_keys_of", "cited_numbers", "place_of",
                 "side_of", "norm_key", "norm_name", "ERA_FROM", "VOUCHER_CARD", "NAME_CARD", "KEY_MASK", "PLACES"):
        assert getattr(db, name) is getattr(cards, name), name
    assert (db._money, db._number, db._day) == (cards.money_of, cards.number_of, cards.day_of)
    assert desk_reading.AC["hash_key"] == rr.AC["hash_key"] == "text_mm3yprqa"
    assert desk_reading.B2["invoice"] == rf.B2B_REF_COLUMN and desk_reading.SC["invoice"] == rf.COLUMN
    assert set(db.UNCLEAR_WHY) == set(cards.UNCLEAR_CODES)


# ---- the read-back ---------------------------------------------------------------------------------------------------
def test_verify_says_verified_only_for_one_valid_students_invoice_receipt_of_the_row():
    """Spec §7.1.4, on a reading made after the write: verified when the cell cites exactly one number, A1 matches it
    (FR-C1), no B2B row cites it, it is on a students' card, and the row's name and amount are the card's and the
    document's. Each other case says what it is, from a closed list."""
    docs = [doc(62500),                                                          # row 0's
            doc(62510, card="20010", name="אורי פז"), doc(62511, card="20011", name="אורי פז"),
            doc(50400, doc_type=1, card="20002", name="יואב שטרן"),
            doc(62502, card="30001"),                                             # a company's card, named as row 5
            doc(62501, card="20002", name="יואב שטרן"),
            doc(62503, card="20003", name="מאיה גולן"),
            doc(62504, card="20004", name="עידו רז", total="7034"),
            doc(62505, card="20005", name="שירה אלון"),
            doc(62506, card="20006", name="יואב שטרן"),
            doc(62507, card=cards.VOUCHER_CARD, name="רוני שגב"),                  # the vouchers' card (FR-D1)
            doc(62508, card=None, name=None),                                     # judged alone: a student's, no name
            doc(62509, card="20012", name="גל עמית", day="2026-05-01"),           # a season before the row
            doc(62513, card="20013", name="תומר נחום")]
    after = [row(0, ref="62500"),                                   # verified
             row(1),                                                # empty: not written
             row(2, ref="62510, 62511", name="אורי פז"),             # several numbers
             row(3, ref="62599"),                                   # no such document
             row(4, ref="50400", name="יואב שטרן"),                 # a tax invoice: A1 waits for its receipt
             row(5, ref="62502"),                                   # a company's card
             row(6, ref="62501"),                                   # written on a row of another name
             row(7, ref="62503", name="מאיה גולן"),                  # B2B Charge cites it too
             row(8, ref="62504", name="עידו רז"), row(9, ref="62504", name="עידו רז"),  # shared since: 3517 + 3517
             row(10, ref="62505", name="שירה אלון", state="archived"),
             row(11, ref="62520 62521"),                            # unreadable
             row(12, ref="62506", name="יואב שטרן", paid="2000"),    # A1: the sum differs
             row(13, ref="62507", name="רוני שגב"),                  # the vouchers' card: never a student's
             row(14, state="archived"),                             # archived, whatever its cell holds
             row(15, ref="62508", name="12 א"),                     # no name on either side: never equal
             row(16, ref="62509", name="גל עמית"),                  # its document a season before it (condition 5)
             row(17, ref="62513", name="תומר נחום", made="")]       # no day at all: near nothing
    accounts = accounts_of({"30001": ["חברה בדויה"]})
    ids = [str(SC_BASE + i) for i in range(19)]
    body = rf.verify("analiza", a_reading(after, [b2(0, "62503")], accounts), docs, ids)
    states = {int(k) - SC_BASE: v["state"] for k, v in body["items"].items()}
    assert states == {0: "verified", 1: "empty", 2: "several_numbers", 3: "ref_not_in_books", 4: "invoice_needs_receipt",
                      5: "not_a_students_card", 6: "name_differs", 7: "cited_by_b2b", 8: "amount_differs",
                      9: "amount_differs", 10: "not_active", 11: "ref_unreadable", 12: "amount_mismatch",
                      13: "not_a_students_card", 14: "not_active", 15: "name_differs", 16: "date_far",
                      17: "date_far", 18: "not_found"}
    assert body["verified"] == 1 and body["asked"] == 19 and set(body["counts"]) <= set(rf.VERIFY_STATES)
    # the number is said only when a status-1 document carries it: never the cell's text
    assert body["items"][str(SC_BASE)] == {"state": "verified", "document": "62500"}
    assert body["items"][str(SC_BASE + 3)]["document"] is None and body["items"][str(SC_BASE + 2)]["document"] is None


def test_verify_counts_each_asked_row_once_and_its_states_are_closed():
    body = rf.verify("analiza", a_reading([row(0, ref="62500")]), [doc(62500)], [str(SC_BASE), SC_BASE, str(SC_BASE)])
    assert body["asked"] == 1 and body["verified"] == 1
    assert len(rf.VERIFY_STATES) == len(set(rf.VERIFY_STATES))
    assert rf.A1_MATCHED == ("status_not_money_in", "date_unreadable", "filled_same", "filled_differs", "fill")
    assert "invoice_needs_receipt" in rf.VERIFY_STATES and "no_ref" not in rf.VERIFY_STATES


def test_a_plan_written_as_proposed_reads_back_verified():
    """The plan and the read-back ask one question: every number the plan proposes, written in its row, is verified."""
    rows, docs, b2b, keys = rich()
    plan = rf.plan("analiza", a_reading(rows, b2b, accounts_of(keys)), docs)
    written = {a["item_id"]: a["value_proposed"] for a in plan["actions"]}
    after = [dict(r, **{rf.COLUMN: written[r["id"]]}) if r["id"] in written else r for r in rows]
    body = rf.verify("analiza", a_reading(after, b2b, accounts_of(keys)), docs, list(written))
    assert body["verified"] == len(written) == 2


# ---- the routes ----------------------------------------------------------------------------------------------------
class Boards(Monday):
    """The five boards as in R0's tests, with the rows FR-C2 reads: board id -> {item id: {column: text, "name": ...}},
    a status as (label, index)."""

    def __init__(self, rows: dict):
        super().__init__()
        self.rows = rows
        for board, items in rows.items():
            self.sizes[board] = len(items)

    def ids(self, board_id: str) -> list:
        if board_id in self.rows:
            return [int(i) for i in self.rows[board_id]]
        return super().ids(board_id)

    def handler(self, request: httpx.Request) -> httpx.Response:
        response = super().handler(request)
        if "items(ids:" not in json.loads(request.content)["query"] or response.status_code != 200:
            return response
        data = response.json()
        for item in data["data"]["items"]:
            cells = next((items[item["id"]] for items in self.rows.values() if item["id"] in items), {})
            item["name"] = cells.get("name", item["name"])
            for c in item["column_values"]:
                value = cells.get(c["id"])
                if value is None:
                    continue
                if c["id"] == a1.STATUS_COLUMN:
                    c.update(text=value[0], label=value[0], index=value[1])
                else:
                    c["text"] = value
        return httpx.Response(200, json=data)


def charge(name: str, ref: str = "", paid: str = PAID, status: tuple = ("שולם", 1)) -> dict:
    return {"name": name, rf.COLUMN: ref, a1.AMOUNT_COLUMN: paid, a1.COLUMN: "", a1.STATUS_COLUMN: status}


ANALIZA = "wizdb2245n5"
B2_BASE, AC_BASE = int(rr.B2B_CHARGE[-4:]) * 10000, int(rr.ACCOUNTS[-4:]) * 10000
AUTH = {"Authorization": f"Bearer {SYNC_KEY}"}
PLAN = {"kind": "b2c_ref_fill"}
DOCS = [doc(62500), doc(62501, card="20002", name="יואב שטרן"), doc(62502, card="20003", name="מאיה גולן"),
        doc(62503, card="30001", name="חברה בדויה"), doc(50400, doc_type=1, card="20003", name="מאיה גולן", total="9000")]


@pytest.fixture
def pg():
    drop_all(TEST_DATABASE_URL)
    from app.migrate import apply_migrations

    apply_migrations(TEST_DATABASE_URL)
    yield TEST_DATABASE_URL
    drop_all(TEST_DATABASE_URL)


@pytest.fixture
def ref_app(pg, monkeypatch):
    monkeypatch.setenv("WIZ_PRIVATE_KEY", KEY)
    monkeypatch.setenv("DATABASE_URL", pg)
    monkeypatch.setenv("BOOKS_SYNC_KEY", SYNC_KEY)
    monkeypatch.setenv("MONDAY_TOKEN", TOKEN)
    monkeypatch.setenv("WIZ_COMPANY", "analiza")
    fake = Boards({
        rr.STUDENT_CHARGE: {str(SC_BASE): charge("נועה ברק"), str(SC_BASE + 1): charge("יואב שטרן"),
                            str(SC_BASE + 2): charge("מאיה גולן", ref="62502"),
                            str(SC_BASE + 3): charge("שם אחר", status=("לא שולם", 0))},
        rr.B2B_CHARGE: {str(B2_BASE): {rf.B2B_REF_COLUMN: "62501"}},
        rr.ACCOUNTS: {str(AC_BASE): {"name": "חברה בדויה", rr.AC["hash_key"]: "30001"}}})
    main._store_cache.update(at=0.0, key=None, value=None)
    main.app.state.monday_transport = httpx.MockTransport(fake.handler)
    yield TestClient(main.app), fake, pg
    main.app.state.monday_transport = None
    main._store_cache.update(at=0.0, key=None, value=None)


@needs_pg
def test_the_plan_route_reads_monday_now_and_answers_the_ref_fill_plan(ref_app):
    client, fake, pg = ref_app
    seed(pg, DOCS)
    body = client.post("/api/actions/plan", headers=AUTH, json=PLAN).json()
    assert body["ok"] is True and body["kind"] == "b2c_ref_fill" and body["books"] == {"documents": 5}
    assert [(a["item_id"], a["value_now"], a["value_proposed"], a["document_date"]) for a in body["actions"]] == [
        (str(SC_BASE), "", "62500", "2026-11-12")]
    assert {k: v for k, v in body["counts"].items() if v} == {"fill": 1, "no_candidate": 1, "has_ref": 1,
                                                             "not_money_in": 1}
    # the pool: 62500, and tax invoice 50400 on a students' card (it holds invoice-receipt 62502); 62501 B2B cites
    assert body["documents"] == {"students": 2, "proposed": 1, "era_listed": 2, "era_left": 1}
    from app.recon.store import load_reading

    assert body["read_at"] == load_reading(pg)["read_at"]   # the rule ran on the reading kept
    text = json.dumps(body, ensure_ascii=False)
    for leak in ("נועה", "ברק", "יואב", "שטרן", "חברה", "שם אחר", PAID, "9000", TOKEN, SYNC_KEY):
        assert leak not in text, leak


@needs_pg
def test_the_verify_route_reads_back_a_written_number(ref_app):
    client, fake, pg = ref_app
    seed(pg, DOCS)
    fake.rows[rr.STUDENT_CHARGE][str(SC_BASE)][rf.COLUMN] = "62500"      # the session wrote it
    fake.rows[rr.STUDENT_CHARGE][str(SC_BASE + 1)][rf.COLUMN] = "62501"  # and one B2B Charge cites
    body = client.post("/api/actions/verify", headers=AUTH,
                       json={**PLAN, "item_ids": [str(SC_BASE), str(SC_BASE + 1), SC_BASE + 9]}).json()
    assert body["ok"] is True and body["verified"] == 1
    assert {k: v["state"] for k, v in body["items"].items()} == {
        str(SC_BASE): "verified", str(SC_BASE + 1): "cited_by_b2b", str(SC_BASE + 9): "not_found"}


@needs_pg
def test_ref_fill_reads_the_wide_snapshot_and_a1_still_the_narrow_one(ref_app, monkeypatch):
    """FR-C2 judges cards, so it asks the wide snapshot, in the same one transaction; A1 never reads a card, and its
    snapshot is asked exactly as before."""
    client, fake, pg = ref_app
    seed(pg, DOCS)
    from app.books.store import BooksStore

    asked, original = [], BooksStore.matching_snapshot

    def record(self, company, **kw):
        asked.append(kw)
        return original(self, company, **kw)

    monkeypatch.setattr(BooksStore, "matching_snapshot", record)
    assert client.post("/api/actions/plan", headers=AUTH, json=PLAN).json()["ok"] is True
    assert client.post("/api/actions/plan", headers=AUTH, json={"kind": "b2c_receipt_date_fill"}).json()["ok"] is True
    assert asked == [{"wide": True}, {}]


@needs_pg
def test_the_wide_matching_snapshot_is_the_desks_and_the_narrow_one_a1s_key_for_key(pg):
    seed(pg, DOCS)
    from app.books.store import BooksStore

    store = BooksStore(pg)
    with store.session(ANALIZA + ":t") as s:
        narrow, wide = s.matching_snapshot(ANALIZA), s.matching_snapshot(ANALIZA, wide=True)
    desk = store.desk_snapshot(ANALIZA)
    assert wide == desk
    assert set(narrow) == {"documents", "retry_waiting", "last_run_complete", "last_run_ok", "last_run_reasons",
                           "ever_completed"}
    assert set(narrow["documents"][0]) == {"stock_id", "doc_type", "doc_number", "status", "value_date", "total_incl",
                                           "missing"}


@needs_pg
def test_the_ref_fill_plan_refuses_demo_and_a_copy_that_is_not_whole(ref_app, monkeypatch):
    client, fake, pg = ref_app
    seed(pg, DOCS + [doc(62505, card="20009", name="אחר")])  # 62504 is missing between 62503 and 62505
    assert client.post("/api/actions/plan", headers=AUTH, json=PLAN).json() == {
        "ok": False, "error": "books_not_ready", "reason": "holes", "last_run_reasons": []}
    monkeypatch.setenv("WIZ_COMPANY", "demo")
    queries = len(fake.queries)
    assert client.post("/api/actions/plan", headers=AUTH, json=PLAN).json() == {
        "ok": False, "error": "not_ready", "reason": "demo"}
    assert len(fake.queries) == queries
