"""FR-C2: an invoice-receipt's number into an empty "אסמכתת חשבונית" on Student Charge (Analiza-Monday backlog 0301 D3a,
Nadav 26.11; the rule FR-C2, with FR-3, FR-6, FR-7, FR-C1 and FR-L12 where they decide; spec §7).

"אסמכתת חשבונית" (`text_mm6d6s97`) is how a payment cites the document that proves it: A1, the desk's marks and its list
of documents no charge cites all read it. Nobody fills it day to day (26.11: every row paid since 17.11 had it empty), so
every new invoice-receipt lands on the desk's list. This rule proposes the number where the row and the books leave one
answer; a person approves, a session writes it through the monday connector, and `verify` reads it back. The service
writes nothing (spec §7.3), and the number written - not the name - is then the match (the law of the rules' §5).

A ROW IS PROPOSED ONE NUMBER (`fill`) ONLY WHEN ALL OF THESE HOLD:
1. **The row** (FR-C2: "a paid or refunded row with no number"): active; its "סטטוס תשלום" says money came in (A1's
   MONEY_IN, by index: שולם, תשלום הוחזר, הוחזר חלקי, ביקש החזר; ADR-0035); its cell is empty as A1 reads one (`_clean`: an
   invisible mark alone is empty) - a cell that cites a number, or one A1 cannot read, is never a candidate; its "סכום
   ששולם" is a number (A1's `parse_amount`); and it is dated in Student Charge's window (FR-7, 2026). A1 dates no row, so
   a row is dated as the desk files a payment: its "תאריך תקבול" read as A1 reads a date (`parse_day`), else the day it was
   made, in Israel's time (`row_day`).
2. **The document**: one of the students' documents of the window, as the desk judges one (app/recon/cards.py `place_of`,
   asked from the window's first day instead of the desk's era): status 1, a tax invoice or an invoice-receipt whose total
   is not negative, dated in 2026, cited by no row of either charge board (`cited_numbers`: an unreadable cell holds every
   number it may name), not answered "not found", not on the vouchers' card 10091, and on a students' card (`side_of`: no
   Account's key, not unclear; a masked or empty key is judged by the card's name, or the document alone by its type).
   **Only an invoice-receipt is proposed** (type 2: it pays itself, FR-L12). A tax invoice alone is a demand (FR-C1,
   `b2c_paid_no_proof`): one that meets 3 and 4 makes the row wait for a person (`tax_invoice_waits`).
3. **An equal amount:** the row's "סכום ששולם" and the document's `total_incl` within TOLERANCE_PER_DOC (1 ₪ for one
   document, the contract §0.3). There is no rounding band here: anything past 1 ₪ is `amount_differs`.
4. **An equal name as a set** (`name_words`: words of two letters or more, none with a digit, in any order, NFKC and case
   folded): the row's name and the card's, neither empty. A similar name is never proposed: it needs a person's eye,
   which the desk's list gives.
5. **Exactly one each way:** the row has exactly one document that meets 2 to 4, an invoice-receipt; its number names it
   alone (FR-3: no other status-1 document carries it, and A1 reads it back as one number); and no other row that meets 1
   has that document among its own (a row that has two holds both).

Every row lands in exactly one bucket, the first that fits in BUCKETS, so the counts add up to the rows read. The plan is
what `fill` holds: per row its link, the value now, the number proposed and the document's date - never a name or an
amount. `documents` counts, apart from the rows, the students' documents of the window that no row cites, how many the
plan proposes, how many of them the desk lists (from ERA_FROM, Chava's start) and how many of those the plan leaves on the
desk's list.

`verify` reads a reading made after the write: a row is `verified` when its cell cites exactly one number, A1 matches it
(FR-C1 and A1's FR-3, FR-4, FR-7 and FR-L12: one valid invoice-receipt of the window, not "not found", the rows citing it
summing to its total), no B2B Charge row cites it, it is on a students' card, and the row's name and amount equal the
card's and the document's. Otherwise its state says what it is (VERIFY_STATES).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

from ..books.doctypes import role
from . import receipt_dates as a1
from .cards import (ERA_FROM, INVOICE, VOUCHER_CARD, account_keys_of, card_of, card_sides, cited_numbers, day_of,
                    money_of, name_words, number_of, place_of, side_of)
from .reading import B2, STUDENT_CHARGE, monday_side

KIND = "b2c_ref_fill"
COLUMN = a1.REF_COLUMN  # text_mm6d6s97, אסמכתת חשבונית
B2B_REF_COLUMN = B2["invoice"]  # text_mm3y6snz: a number B2B Charge cites is never a student's to fill
WINDOW_START = date(a1.WINDOW_YEAR, 1, 1)  # FR-7: Student Charge's window, from its first day
DESK_TZ = ZoneInfo("Asia/Jerusalem")  # the day a row was made, as the desk files a payment

# The first that fits wins; every row is in exactly one.
BUCKETS = (
    "not_active",             # archived or deleted between the ids and the rows: nothing is written to it
    "not_money_in",           # its status does not say money came in (לא שולם, יש בעיה, מלגת אנליזה, empty, unknown)
    "has_ref",                # its cell cites a number: A1's (FR-C1), never this rule's
    "ref_unreadable",         # its cell is not empty and not a list of numbers: a person reads it
    "amount_unreadable",      # its "סכום ששולם" is empty or not a number: there is no amount to equal
    "out_of_window",          # dated outside 2026 (FR-7), or with no day at all
    "no_candidate",           # no students' document of the window, uncited, carries its name
    "amount_differs",         # documents carry its name, none its amount: a payment with no row, or an amount typed wrong
    "tax_invoice_waits",      # a tax invoice carries its name and amount: a demand, never proof of money (FR-C1)
    "two_candidates",         # two or more invoice-receipts carry its name and amount
    "number_ambiguous",       # its one invoice-receipt's number would not name it alone (FR-3)
    "document_wanted_twice",  # its one invoice-receipt is among another row's documents too
    "fill",                   # exactly one each way: the plan
)
# The buckets whose rows the plan lists by id: what a person's eye can decide. A row that already cites a number, one
# whose status says no money came, one outside the window and one with no document of its name are counted only.
LISTED = ("ref_unreadable", "amount_unreadable", "amount_differs", "tax_invoice_waits", "two_candidates",
          "number_ambiguous", "document_wanted_twice")

# A1's buckets of a row it matched (FR-C1 holds: the group's rows and documents tie, no tax invoice, one day); any other
# bucket of A1 is a reason the number written is not proof (`verify`)
A1_MATCHED = a1.BUCKETS[a1.BUCKETS.index("status_not_money_in"):]
# What `verify` says of an asked row, closed: verified; not in the reading, or not active; its cell empty, unreadable,
# or citing more than one number; A1's reason the number is no match (any bucket of A1 before A1_MATCHED); a number B2B
# Charge cites too, one on a card that is not a students', a name or an amount that is not the document's
VERIFY_STATES = (("verified", "not_found", "not_active", "empty", "ref_unreadable", "several_numbers")
                 + tuple(b for b in a1.BUCKETS[a1.BUCKETS.index("ref_not_in_books"):] if b not in A1_MATCHED)
                 + ("cited_by_b2b", "not_a_students_card", "name_differs", "amount_differs"))


def row_day(row: dict) -> Optional[date]:
    """The day a row is dated by (FR-7): its "תאריך תקבול" as A1 reads a date cell (`parse_day`), else the day it was
    made, in Israel's time - the date the desk files a payment under (app/desk/view.py `_b2c_row`: monday's created_at is
    UTC, and a payment at 01:00 on the 1st belongs to the 1st). None with neither."""
    typed = a1.parse_day(row.get(a1.COLUMN))
    if typed is not None:
        return typed
    stamp = str(row.get("created_at") or "").strip()
    try:
        made = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return a1.parse_day(stamp)
    return (made if made.tzinfo else made.replace(tzinfo=timezone.utc)).astimezone(DESK_TZ).date()


def _equal_amount(amount, doc: dict) -> bool:
    total = money_of(doc.get("total_incl"))
    return total is not None and abs(amount - total) <= a1.TOLERANCE_PER_DOC


def _names_alone(number: Optional[str], by_number: dict) -> bool:
    """Whether a document's number, written in the cell, names it and nothing else: A1 reads it back as one number, and
    one status-1 document carries it (FR-3)."""
    return number is not None and a1.parse_refs(number) == [number] and len(by_number.get(number) or []) == 1


def analyse(rows: list, documents: Iterable[dict], b2b_rows: Iterable[dict] = (), keys: Optional[dict] = None) -> dict:
    """Every Student Charge row in one bucket. `documents`: the copy's held documents with their cards (the wide
    snapshot); `b2b_rows`: B2B Charge's rows (a number they cite is no student's); `keys`: the Accounts' cards
    (`account_keys_of`). Returns {"rows": {item_id: {"bucket", "current", "documents", "proposed", "document_date"}},
    "counts": {bucket: n}, "documents": {"students", "proposed", "era_listed", "era_left"}}: `documents` of a row are the
    numbers of its documents of 2 to 4, `proposed` the one number of a `fill`."""
    rows, documents, keys = list(rows), list(documents), keys or {}
    by_number = a1.index_documents(documents)
    cited = cited_numbers(rows, COLUMN) | cited_numbers(list(b2b_rows), B2B_REF_COLUMN)
    sides = card_sides(documents, keys)
    pool = [d for d in documents if place_of(d, cited, sides, era_from=WINDOW_START)[0] == "students"]
    by_words: dict = defaultdict(list)  # a card's name as a set of words -> its documents of the pool
    for d in pool:
        words = name_words(d.get("account_name"))
        if words:  # a card with no name is never indexed, so a row with no name finds nothing (condition 4)
            by_words[words].append(d)

    out: dict = {}
    wants: dict = {}  # item id -> its documents of 2 to 4, for every row that meets 1 and has some
    for row in rows:
        item_id = str(row.get("id"))
        entry = {"bucket": None, "current": str(row.get(COLUMN) or "").strip(), "documents": [], "proposed": None,
                 "document_date": None}
        out[item_id] = entry
        refs = a1.parse_refs(row.get(COLUMN))
        amount = a1.parse_amount(row.get(a1.AMOUNT_COLUMN))
        day = row_day(row)
        words = name_words(row.get("name"))
        if row.get("state", "active") != "active":
            entry["bucket"] = "not_active"
        elif str(row.get(a1.STATUS_COLUMN + "#", "")) not in a1.MONEY_IN:
            entry["bucket"] = "not_money_in"
        elif refs:
            entry["bucket"] = "has_ref"
        elif refs is None:
            entry["bucket"] = "ref_unreadable"
        elif amount is None:
            entry["bucket"] = "amount_unreadable"
        elif day is None or day.year != a1.WINDOW_YEAR:
            entry["bucket"] = "out_of_window"
        elif not by_words.get(words):
            entry["bucket"] = "no_candidate"
        else:
            equal = [d for d in by_words[words] if _equal_amount(amount, d)]
            if not equal:
                entry["bucket"] = "amount_differs"
            else:
                wants[item_id] = equal
                entry["documents"] = sorted({number_of(d) or "" for d in equal}, key=lambda n: (len(n), n))

    wanted_by: dict = defaultdict(set)  # a document (its stock id) -> the rows that hold it among their own
    for item_id, docs in wants.items():
        for d in docs:
            wanted_by[d["stock_id"]].add(item_id)
    for item_id, docs in wants.items():
        entry = out[item_id]
        if any(role(d.get("doc_type")) == INVOICE for d in docs):
            entry["bucket"] = "tax_invoice_waits"
        elif len(docs) > 1:
            entry["bucket"] = "two_candidates"
        elif not _names_alone(number_of(docs[0]), by_number):
            entry["bucket"] = "number_ambiguous"
        elif wanted_by[docs[0]["stock_id"]] != {item_id}:
            entry["bucket"] = "document_wanted_twice"
        else:
            entry.update(bucket="fill", proposed=number_of(docs[0]), document_date=day_of(docs[0]["value_date"]).isoformat())

    counts = {b: 0 for b in BUCKETS}
    for entry in out.values():
        counts[entry["bucket"]] += 1
    proposed = {docs[0]["stock_id"] for item_id, docs in wants.items() if out[item_id]["bucket"] == "fill"}
    era = [d for d in documents if place_of(d, cited, sides, era_from=ERA_FROM)[0] == "students"]  # the desk's list
    return {"rows": out, "counts": counts,
            "documents": {"students": len(pool), "proposed": len(proposed), "era_listed": len(era),
                          "era_left": sum(1 for d in era if d["stock_id"] not in proposed)}}


def _boards(reading: dict) -> tuple:
    """(Student Charge's rows, B2B Charge's, the Accounts' cards) of a whole reading - by key, never a default: a board
    missing from it is an error, not an empty board whose cited numbers would turn into documents no row cites."""
    boards = reading["boards"]
    return boards["student_charge"], boards["b2b_charge"], account_keys_of(boards["accounts"])


def _plan_body(analysis: dict) -> dict:
    actions = []
    by_month: dict = defaultdict(int)
    for item_id, entry in sorted(analysis["rows"].items(), key=lambda kv: (kv[1]["document_date"] or "", kv[0])):
        if entry["bucket"] != "fill":
            continue
        actions.append({"item_id": item_id, "url": a1.ITEM_URL.format(item_id), "column": COLUMN,
                        "value_now": entry["current"], "value_proposed": entry["proposed"],
                        "document_date": entry["document_date"]})
        by_month[entry["document_date"][:7]] += 1
    return {"kind": KIND, "class": "A", "board": STUDENT_CHARGE, "column": COLUMN,
            "rows": len(analysis["rows"]), "counts": analysis["counts"], "fill": len(actions),
            "fill_by_month": dict(sorted(by_month.items())),
            "ids_by_bucket": {b: sorted(i for i, e in analysis["rows"].items() if e["bucket"] == b)
                              for b in LISTED if analysis["counts"][b]},
            "documents": analysis["documents"], "actions": actions}


@monday_side
def plan(company: str, reading: dict, documents: Iterable[dict]) -> dict:
    """The dry run (spec §7.1.1): every empty "אסמכתת חשבונית" the books fill, with the item's link, the value now, the
    number proposed and the document's date, the count of every bucket, and the students' documents counted apart. Never a
    name or an amount. Refuses (NotReady) on Demo and on a reading that is not whole."""
    rows, b2b_rows, keys = _boards(reading)
    return _plan_body(analyse(rows, documents, b2b_rows, keys))


def _read_back(row: Optional[dict], matched: Optional[dict], by_number: dict, b2b_cited: set, sides: dict) -> str:
    """One asked row's state (VERIFY_STATES), in their order."""
    if row is None:
        return "not_found"
    if row.get("state", "active") != "active":
        return "not_active"
    refs = a1.parse_refs(row.get(COLUMN))
    if refs is None:
        return "ref_unreadable"
    if not refs:
        return "empty"
    if len(refs) > 1:
        return "several_numbers"
    if matched["bucket"] not in A1_MATCHED:  # FR-C1, as A1 judges the number and its group
        return matched["bucket"]
    number, doc = refs[0], by_number[refs[0]][0]  # A1 matched it: exactly one status-1 invoice-receipt carries it
    if number in b2b_cited:
        return "cited_by_b2b"
    if card_of(doc) == VOUCHER_CARD or side_of(doc, sides)[0] != "student":
        return "not_a_students_card"
    words = name_words(row.get("name"))
    if not words or words != name_words(doc.get("account_name")):
        return "name_differs"
    # the row's own amount (A1 read it, or the row were `amount_unreadable`): A1 judged its group's sum, and a document
    # the plan proposed was cited by no row - shared since, it is no longer the fill that was approved
    if not _equal_amount(a1.parse_amount(row.get(a1.AMOUNT_COLUMN)), doc):
        return "amount_differs"
    return "verified"


@monday_side
def verify(company: str, reading: dict, documents: Iterable[dict], item_ids: Iterable) -> dict:
    """The read-back (spec §7.1.4), on a reading made after the write: each asked row `verified`, or what it is
    (VERIFY_STATES). `document` is the number the cell cites when it cites one that a status-1 document carries - never
    the cell's text, which can hold anything a person typed."""
    rows, b2b_rows, keys = _boards(reading)
    documents = list(documents)
    by_number = a1.index_documents(documents)
    matched = a1.analyse(rows, documents)["rows"]
    b2b_cited = cited_numbers(b2b_rows, B2B_REF_COLUMN)
    sides = card_sides(documents, keys)
    by_id = {str(r.get("id")): r for r in rows}
    states, counts = {}, defaultdict(int)
    for item_id in dict.fromkeys(str(i) for i in item_ids):  # once each: a repeated id counted twice says "all verified"
        row = by_id.get(item_id)
        state = _read_back(row, matched.get(item_id), by_number, b2b_cited, sides)
        refs = a1.parse_refs(row.get(COLUMN)) if row is not None else None
        cites = refs[0] if refs and len(refs) == 1 and refs[0] in by_number else None
        states[item_id] = {"state": state, "document": cites}
        counts[state] += 1
    return {"kind": KIND, "asked": len(states), "verified": counts.get("verified", 0), "counts": dict(counts),
            "items": states}
