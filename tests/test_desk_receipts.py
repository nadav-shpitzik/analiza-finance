"""The queue "שולם, קבלה לטיפול" split by what the books say (Nadav 26.11: receipts go out slowly). app/desk/books.py
`receipt_in_books` judges each payment of the queue, `merge` lays it on its row (`receipt_books`, `receipt_laid`) from a
current part of this very reading only, and GET /api/desk/totals counts the split (`receipts`, `receipts_split`).

Each rule with its test: a cited invoice-receipt A1 matches, of the row's name ("cited"), one the desk proposes by its name
and amount ("found": the proposal's, so from the era on and gone with "לא להציע"), and every case none is identified - a
tax invoice alone, a draft, a number two documents carry, two candidates of the name and amount, an invoice-receipt A1
refuses (a negative total), a row before the era (proposed nothing), nothing. The second review of 26.11's cases (another
payment's, year's or person's receipt, one the books answered "not found", a number not yet in the copy, "לא להציע") are
tests/test_review_rcpt_*.py. Synthetic fixtures; names and numbers of no one."""
from __future__ import annotations

from urllib.parse import quote

from app.desk import books as db
from app.desk import view as fd
from tests.desk_fixtures import TODAY, reading_of, sc, snap
from tests.test_desk_books import booked, copy  # noqa: F401 - `booked` is the fixture
from tests.test_recon_ref_fill import doc

AMOUNT = "3517"


def pending(id_, name, ref="", made="2026-11-10T08:00:00Z", day="", receipt="0") -> dict:
    """A Student Charge row of the queue ("שולם", "שליחת קבלה: טרם"), as the desk's reading holds it."""
    r = sc(id_, st="1", paid=AMOUNT, invoice=ref, paid_date=day, created=made, lead=["L%s" % id_], receipt=receipt)
    r.update(name=name, state="active")
    return r


ROWS = [pending(1, "נועה ברק", ref="62701"),                     # cites a valid invoice-receipt: cited
        pending(2, "יואב שטרן"),                                 # its uncited invoice-receipt, name and amount: found
        pending(3, "מאיה גולן", made="2026-09-10T08:00:00Z"),     # the same, before the era: proposed nothing, none found
        pending(4, "עידו רז", ref="62704"),                       # cites a valid tax invoice only
        pending(5, "שירה אלון", ref="62705"),                     # cites a draft
        pending(6, "תומר נחום", ref="62706"),                     # cites a number two valid documents carry
        pending(7, "ליאת כרמי"),                                  # two invoice-receipts of its name and amount
        pending(8, "אורי פז"),                                    # nothing in the books
        pending(9, "רוני שגב", ref="62709", receipt="1"),         # its receipt went out: not in the queue
        pending(10, "גל עמית", ref="62710")]                      # an invoice-receipt A1 refuses (a negative total)
DOCS = [doc(62701, total=AMOUNT, name="נועה ברק", card="20001", day="2026-11-10"),
        doc(62702, total=AMOUNT, name="יואב שטרן", card="20002", day="2026-11-11"),
        doc(62703, total=AMOUNT, name="מאיה גולן", card="20003", day="2026-09-12"),
        doc(62704, total=AMOUNT, doc_type=1, name="עידו רז", card="20004", day="2026-11-10"),
        doc(62705, total=AMOUNT, status=0, name="שירה אלון", card="20005", day="2026-11-10"),
        doc(62706, total=AMOUNT, name="תומר נחום", card="20006", day="2026-11-10", stock=1),
        doc(62706, total=AMOUNT, name="תומר נחום", card="20006", day="2026-11-10", stock=2),
        doc(62707, total=AMOUNT, name="ליאת כרמי", card="20007", day="2026-11-09"),
        doc(62717, total=AMOUNT, name="ליאת כרמי", card="20007", day="2026-11-11"),
        doc(62709, total=AMOUNT, name="רוני שגב", card="20009", day="2026-11-10"),
        doc(62710, total="-" + AMOUNT, name="גל עמית", card="20010", day="2026-11-10")]
WANT = {"1": {"number": "62701", "how": "cited"}, "2": {"number": "62702", "how": "found"},
        "3": {}, "4": {}, "5": {}, "6": {}, "7": {}, "8": {}, "10": {}}


def computed(rows=ROWS, docs=DOCS) -> tuple:
    reading = reading_of(snap(list(rows), []))
    view = fd.build_view(reading, TODAY)
    return db.compute(reading, view, copy(list(docs)), {}), view


def current(part: dict, view: dict) -> dict:
    """The part as DeskBooks shows it when it is current and of this very reading."""
    return dict(part, state="ok", reasons=[], as_of=None, key="k", reading_at=view["read_at"])


def test_each_payment_of_the_queue_is_judged_by_what_the_books_hold():
    part, view = computed()
    assert {r["id"] for r in view["b2c"]["rows"] if r["state"] == "receipt_pending"} == set(WANT)
    assert part["receipts"] == WANT                       # 9's receipt went out: it is not judged
    # "found" is the proposal's (the second review of 26.11: one "לא להציע" could not take back): 2 is proposed, 3 is not
    assert "2" in part["fills"] and "3" not in part["fills"]
    assert part["fills"]["2"]["values"]["number"] == "62702"


def test_only_a_proposed_number_is_found_whatever_else_the_entries_carry():
    """`receipt_in_books`'s own contract: only the desk's proposal of a number (D4's `fill`, {"values": {"number"}}) is a
    receipt found by name and amount; a proposal of a date alone is none; an entry A1 did not match cites nothing."""
    idx = db.index_copy([])
    assert db.receipt_in_books(None, "x", {"values": {"number": "62799", "date": "2026-11-10"}}, idx) == {
        "number": "62799", "how": "found"}
    assert db.receipt_in_books(None, "x", {"values": {"date": "2026-11-10"}}, idx) == {}
    assert db.receipt_in_books(None, "x", None, idx) == {}
    assert db.receipt_in_books({"bucket": "amount_mismatch", "documents": ["62799"], "refused": []}, "x", None, idx) == {}


def test_merge_lays_it_on_the_queue_from_a_current_part_of_this_reading():
    part, view = computed()
    rows = {r["id"]: r for r in db.merge(view, current(part, view))["b2c"]["rows"]}
    assert rows["1"]["receipt_books"] == {
        "issued": True, "number": "62701", "how": "cited", "text": "הופקה בחשבשבת",
        "title": "חשבונית מס/קבלה 62701 תקפה בחשבשבת, המספר שבאסמכתת חשבונית, באותו שם ובאותו סכום. " + fd.RECEIPT_ISSUED_TODO}
    assert rows["2"]["receipt_books"]["how"] == "found"
    assert rows["2"]["receipt_books"]["title"].startswith("חשבונית מס/קבלה 62702 תקפה בחשבשבת, אחת בלבד בשם ובסכום")
    assert "30 יום" in rows["2"]["receipt_books"]["title"]
    # Nadav 27.11: issuing does not send it - a green chip says what is left, in its title
    assert rows["2"]["receipt_books"]["title"].endswith(fd.RECEIPT_ISSUED_TODO)
    assert fd.RECEIPT_ISSUED_TODO == "ההפקה בחשבשבת לא שולחת אותה: יש לשלוח אותה ולסמן \"יצאה\" בשליחת קבלה."
    for i in ("3", "4", "5", "6", "7", "8", "10"):
        assert rows[i]["receipt_books"] == {"issued": False, "text": "לא זוהתה בחשבשבת", "title": fd.RECEIPT_UNSEEN_WHY}
    assert "receipt_books" not in rows["9"]
    # the chip's words are short and never the proposal's ("בחשבשבת: N"): a row proposed a number carries both, apart
    assert not any(c.isdigit() for c in fd.RECEIPT_ISSUED_SAID + fd.RECEIPT_UNSEEN_SAID)
    assert rows["2"]["receipt_books"]["text"] != fd._BOOKS_REF_FILL.format(number="62702")


def test_a_held_part_or_one_of_another_reading_lays_nothing():
    part, view = computed()
    for shown in (dict(current(part, view), state="held"), dict(current(part, view), reading_at="2026-01-01T00:00:00+00:00"),
                  dict(current(part, view), state="none")):
        assert not any("receipt_books" in r for r in db.merge(view, shown)["b2c"]["rows"])


def test_the_split_is_counted_once_on_the_rows_of_the_list():
    part, view = computed()
    merged = db.merge(view, current(part, view))
    queue = fd.filter_b2c(merged["b2c"], "receipt_pending", "", "")
    assert db.receipts_split(queue) == {"issued": 2, "not": 7, "later": 0, "unknown": 0}
    # a search narrows it with the list; with no part laid every row of the queue is unknown
    assert db.receipts_split(fd.filter_b2c(merged["b2c"], "receipt_pending", "", "יואב")) == {"issued": 1, "not": 0,
                                                                                              "later": 0, "unknown": 0}
    bare = db.merge(view, dict(current(part, view), state="held"))
    assert db.receipts_split(fd.filter_b2c(bare["b2c"], "receipt_pending", "", "")) == {"issued": 0, "not": 0,
                                                                                         "later": 0, "unknown": 9}
    # rows of other states are never counted
    assert db.receipts_split(fd.filter_b2c(merged["b2c"], "", "", "")) == {"issued": 2, "not": 7, "later": 0, "unknown": 0}


def test_the_legend_says_what_each_means_and_that_none_identified_is_not_none_issued():
    words = dict(fd.LEGEND["b2c"])
    said = next(t for k, t in words.items() if fd.RECEIPT_UNSEEN_SAID in k)
    assert fd.RECEIPT_ISSUED_SAID in said and fd.RECEIPT_ISSUED_TODO in said and "מתחת לשיבוץ" in said
    assert "ייתכן שהקבלה הופקה בשם או בסכום אחר" in said and "30 יום" in said


# ---- the route: GET /api/desk/totals counts the split of the list the page shows ---------------------------------------------
def test_the_totals_of_the_queue_carry_the_split_and_no_other_list_does(booked):
    from tests.test_desk import session_token

    # the queue's one row of this world cites the copy's invoice-receipt, at its amount (3,950 against 4,000 is A1's
    # amount_mismatch: never "issued", the second review of 26.11)
    booked.w.set(103, touch=False, invoice="62503", paid="4000")
    body = booked.get(1).json()
    queue = [r for r in body["view"]["b2c"]["rows"] if r["state"] == "receipt_pending"]
    assert queue and all("receipt_books" in r for r in queue)
    taken, key = quote(body["taken_at"]), quote(body["view"]["books"]["key"])
    auth = {"Authorization": "Bearer " + session_token(1)}
    path = "/api/desk/totals?tab=b2c&state=%s&taken=%s&books=%s"
    t = booked.client.get(path % ("receipt_pending", taken, key), headers=auth).json()
    want = {"issued": sum(1 for r in queue if r["receipt_books"]["issued"]),
            "not": sum(1 for r in queue if r["receipt_books"]["issued"] is False), "later": 0, "unknown": 0}
    assert t["count"] == len(queue) and t["receipts"] == want and want["issued"] >= 1
    for other in ("", "tasks", "done"):
        assert "receipts" not in booked.client.get(path % (other, taken, key), headers=auth).json()


def test_the_legend_says_what_is_left_of_an_issued_receipt_and_no_sentence_ends_twice():
    """Nadav 27.11: issuing a receipt in Hashavshevet does not send it, so the legend's entry for the receipts' three chips
    says what is left; and a sentence that ends in a constant's own period never gets a second one (the entry said
    "בסכום אחר.." until 27.11). Every legend, every term and sentence: exactly two periods are a slip, an ellipsis is not."""
    import re
    entry = [s for t, s in fd.LEGEND["b2c"] if t.startswith(fd.RECEIPT_ISSUED_SAID)]
    assert len(entry) == 1 and fd.RECEIPT_ISSUED_TODO in entry[0]
    assert "לפי מצבה בפועל" not in entry[0]
    twice = re.compile(r"(?<!\.)\.\s?\.(?!\.)")
    for tab, entries in fd.LEGEND.items():
        for term, said in entries:
            assert not twice.search(term) and not twice.search(said), (tab, term)
