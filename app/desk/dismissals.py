""""לא פער": what may be dismissed, the evidence each dismissal is bound to, and how a part of the books is shown with
what was dismissed hidden (Analiza-Monday backlog 0301 R5; the spec §6.1 item 4, §2.4 and §6.4.1). The table is
app/desk/dismissals_store.py's (migrations/008); the routes are app/desk/routes.py's; DeskBooks (app/desk/books.py) reads
the live dismissals once per computation and hides with `hide`.

WHAT MAY BE DISMISSED (KINDS, a closed list): every alert of the books the desk shows -
- the three marks on a Student Charge row (`books_ref_invalid`, `books_amount_differs`, `books_receipt_unpaid`), its
  subject the row's item id;
- a document in a list of documents with no charge, its subject the document's number, a kind per list - the students'
  (`books_doc_students`), the companies' (`books_doc_companies`), the unclear (`books_doc_unclear`): the list says whose
  question it is (spec §6.4.1), so a document that moves to another list is another alert;
- what "רישום מחשבשבת" proposes (`books_ref_fill`, `books_date_fill`), its subject the row's item id - pressed as "לא
  להציע": a wrong proposal must stop showing too.
Each kind's boards (BOARDS): a member of each may dismiss it, sees it hidden, and undoes their own.

THE EVIDENCE (EVIDENCE says each kind's fields, and the functions below are the only place they are read): a sha256 over
the canonical JSON of a fixed set of fields per kind (`digest`), computed by the server from its own computation and
never taken from the page. A change in any field brings the alert back ("חזר"); nothing that is only words is in it (a
chip's text, a legend, a row's name), so a change of wording never brings back what was decided. Amounts in one form
(4000, 4,000.00 and 4000.0 are one amount), numbers without a leading zero, days as ISO dates, ids and numbers sorted.
- A mark (`mark_evidence`):
  - `row`, the subject: the hash is bound to the row it was decided on;
  - `cited`, the numbers the row cites in "אסמכתת חשבונית" as A1 reads them: a number added, removed or retyped is a new
    question to the books;
  - `amount`, "סכום ששולם" as A1 reads it: compared with the books (`books_amount_differs`), and it decides which way
    `books_receipt_unpaid` is read (empty, or equal to the documents);
  - `status`, "סטטוס תשלום" by its index: what the row says of the money - `books_receipt_unpaid` is a status against
    the books, and a new status is a new claim;
  - `documents`, what the copy holds under each cited number, in any status - type, status, total including VAT, day,
    and whether it answered "not found": the other side of every comparison (a document issued, cancelled, re-typed,
    re-totalled or re-dated is new evidence; a draft is evidence too, "טיוטה");
  - `group`, for a row judged with the rows that cite the same documents: their ids and A1's two sums - another row
    joining, leaving or changing its amount changes the comparison the mark says.
  Not in it: the row's name (a renamed row is the same gap), its date (it decides the era only, never the mark), its
  "שליחת קבלה" (no mark reads it), a card's name or key (no mark compares them).
- A document (`document_evidence`):
  - `number`, the subject;
  - `type`: a tax invoice and an invoice-receipt are different claims (an invoice-receipt says the money came in);
  - `status`: a document that stops being valid is another document;
  - `total` and `total_excl`: what it bills;
  - `date`: its day decides the windows and the era;
  - `card`: whose it is - the card's name as the copy holds it (already cleaned of ID numbers), compared as cards are
    told apart (`norm_name`): a document moved to another customer's card is another question;
  - `side`: the list, and for an unclear one why its side is unclear (`card_sides`): a card that gains a proforma, or an
    Account's key, is a question for other people.
  Not in it: the card's key (never in an answer, and a hash of a short key with the rest known gives the key back), the
  candidates by name or the Accounts that name the card (monday's side, and a name is never evidence of a match, the
  rules' §5), and a proposal on the document (an alert of its own).
- A proposal (`fill_evidence`): `row`, and what it would write - `number` into "אסמכתת חשבונית" and `date` into "תאריך
  תקבול". A proposal that would write anything else is another proposal; the facts it rests on (the name, the amount, the
  window) are the rules' and decide only whether it is made.

HOW A PART IS SHOWN (`hide`): an alert whose (kind, subject) has a live dismissal with the very evidence it carries now
is taken out of the part - out of the marks (so out of "לטיפול" and its counts), out of its list, off its row - and kept
under `hidden`, by tab, with who dismissed it and when. One whose live dismissal carries other evidence stays, with
`returned` ("חזר"): who dismissed it and when. A live dismissal whose alert is gone (the gap was fixed) hides nothing
and is said nowhere; it stays live, so the same evidence coming back is hidden again.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from typing import Any, Iterable, Optional

from ..recon import receipt_dates as a1
from ..recon.cards import day_of, money_of, norm_name, number_of
from .view import NAG_FILL_WORD, NAG_WORD

MARK_KINDS = ("books_ref_invalid", "books_amount_differs", "books_receipt_unpaid")
# a document's kind by the list it is in (app/desk/books.py `compute`: the students', the companies', the unclear)
DOC_KINDS = {"students": "books_doc_students", "companies": "books_doc_companies", "unclear": "books_doc_unclear"}
FILL_KINDS = ("books_ref_fill", "books_date_fill")
KINDS = MARK_KINDS + tuple(DOC_KINDS.values()) + FILL_KINDS
# whose each kind is: the boards a reader must be a member of (spec §6.4.1; app/desk/gate.py Visitor's tabs)
BOARDS = {**{k: ("b2c",) for k in MARK_KINDS + FILL_KINDS},
          "books_doc_students": ("b2c",), "books_doc_companies": ("b2b",), "books_doc_unclear": ("b2c", "b2b")}
# which part of the answer a kind's hidden ones are listed in: the students' tab, the companies', or the unclear list
TAB = {**{k: "b2c" for k in MARK_KINDS + FILL_KINDS}, "books_doc_students": "b2c", "books_doc_companies": "b2b",
       "books_doc_unclear": "unclear"}
# the press's word, the legend's (app/desk/view.py; frontend/finance.js NAG_WORD holds the same, by a test)
WORD = {k: NAG_FILL_WORD if k in FILL_KINDS else NAG_WORD for k in KINDS}
# Each kind's fields, in the order the functions below build them (a test holds the two together)
EVIDENCE = {**{k: ("kind", "row", "cited", "amount", "status", "documents", "group") for k in MARK_KINDS},
            **{k: ("kind", "number", "type", "status", "total", "total_excl", "date", "card", "side")
               for k in DOC_KINDS.values()},
            **{k: ("kind", "row", "number", "date") for k in FILL_KINDS}}
# a subject: an item id or a document's number, digits only (migrations/008 checks the same)
SUBJECT_MAX = 20


# ---- the evidence ------------------------------------------------------------------------------------------------------
def digest(fields: dict) -> str:
    """sha256 of the fields as canonical JSON: keys sorted, no spaces, UTF-8. The fields are JSON's own types only (a
    Decimal or a date that reached here unconverted raises, never hashes as some text of it)."""
    text = json.dumps(fields, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def amount_text(v: Any) -> Optional[str]:
    """An amount in one form: 4000, 4000.00 and 4000.0 are "4000", 3347.460 is "3347.46"; None for none."""
    d = money_of(v)
    if d is None or not d.is_finite():
        return None
    return format((d + 0).normalize(), "f")


def day_text(v: Any) -> Optional[str]:
    day = day_of(v)
    return day.isoformat() if day else None


def _ids(values: Iterable) -> list:
    """Ids or numbers, once each, in numeric order (digit strings of any length, never converted)."""
    return sorted({str(v) for v in values}, key=lambda s: (len(s), s))


def _type(v: Any) -> Any:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None if v is None else str(v)


def held_by_number(documents: Iterable[dict]) -> dict:
    """{number: [every document of the copy that carries it, in any status]}: what a mark's evidence says the books hold
    under a number it cites (A1's own index holds the valid ones only; a draft is evidence too)."""
    out: dict = defaultdict(list)
    for d in documents:
        number = number_of(d)
        if number is not None:
            out[number].append(d)
    return out


def _held(d: dict) -> dict:
    return {"type": _type(d.get("doc_type")), "status": _type(d.get("status")), "total": amount_text(d.get("total_incl")),
            "date": day_text(d.get("value_date")), "missing": bool(d.get("missing"))}


def mark_evidence(kind: str, item_id: str, entry: dict, row: dict, held: dict) -> dict:
    """A mark's fields: A1's judgement of the row (`entry`: its numbers and its group), the view's row (its status by
    index, and its "סכום ששולם" as the reading holds it, `paid_raw`), and what the copy holds (`held_by_number`)."""
    cited = _ids(entry.get("documents") or [])
    group = entry.get("group")
    documents = sorted((dict(_held(d), number=n) for n in cited for d in held.get(n, ())),
                       key=lambda x: json.dumps(x, sort_keys=True))
    return {"kind": kind, "row": str(item_id), "cited": cited,
            "amount": amount_text(a1.parse_amount(row.get("paid_raw"))),
            "status": str(row.get("status_idx") or ""), "documents": documents,
            "group": None if group is None else {"rows": _ids(group["rows"]), "paid": amount_text(group["paid"]),
                                                 "total": amount_text(group["total"])}}


def document_evidence(kind: str, doc: dict, side: str) -> dict:
    """A listed document's fields. `side`: its list, and for an unclear one why its side is unclear ("unclear:<why>")."""
    return {"kind": kind, "number": number_of(doc), "type": _type(doc.get("doc_type")), "status": _type(doc.get("status")),
            "total": amount_text(doc.get("total_incl")), "total_excl": amount_text(doc.get("total_excl")),
            "date": day_text(doc.get("value_date")), "card": norm_name(doc.get("account_name")), "side": side}


def fill_evidence(kind: str, item_id: str, values: dict) -> dict:
    """A proposal's fields: the row, and what it would write."""
    return {"kind": kind, "row": str(item_id), "number": values.get("number"), "date": values.get("date")}


# ---- a part, with what was dismissed hidden ------------------------------------------------------------------------------
def _by(d: dict) -> dict:
    return {"id": d["id"], "by": d["by"], "at": d["at"]}


def _row_item(kind: str, item_id: str, mark: dict, d: dict) -> dict:
    return dict(_by(d), kind=kind, subject=str(item_id), word=WORD[kind], name=mark.get("name", ""), chip=mark.get("chip", ""))


def _doc_item(e: dict, d: dict) -> dict:
    return dict(_by(d), kind=e["kind"], subject=e["subject"], word=WORD[e["kind"]],
                **{k: e.get(k) for k in ("number", "type_name", "date", "total_incl", "total_excl", "card")})


def hide(part: dict, live: Iterable[dict]) -> dict:
    """The part as it is shown: every alert a live dismissal holds with its evidence taken out and listed under `hidden`
    ({"b2c", "b2b", "unclear"}: the tab, or the unclear list, it would be shown in; oldest dismissal first), and every
    alert whose live dismissal holds other evidence marked `returned`. Nothing else of the part changes; the part given
    is not changed. app/desk/books.py `merge` counts both by kind, of what it lays."""
    held = {(d["kind"], str(d["subject"])): d for d in live}
    out = dict(part)
    hidden: dict = {"b2c": [], "b2b": [], "unclear": []}

    def judge(kind: str, subject: str, evidence: Optional[str]) -> tuple:
        """("hidden", d), ("returned", d) or (None, None)."""
        d = held.get((kind, subject))
        if d is None or not evidence:
            return None, None
        return ("hidden", d) if d["evidence"] == evidence else ("returned", d)

    marks: dict = {}
    for item_id, ms in (part.get("marks") or {}).items():
        keep = []
        for m in ms:
            how, d = judge(m["kind"], str(item_id), m.get("evidence"))
            if how == "hidden":
                hidden["b2c"].append(_row_item(m["kind"], item_id, m, d))
                continue
            if how == "returned":
                m = dict(m, returned=_by(d))
            keep.append(m)
        if keep:
            marks[item_id] = keep
    out["marks"] = marks

    fills: dict = {}
    withdrawn = set()
    for item_id, fill in (part.get("fills") or {}).items():
        m = fill["mark"]
        how, d = judge(m["kind"], str(item_id), m.get("evidence"))
        if how == "hidden":
            hidden["b2c"].append(_row_item(m["kind"], item_id, m, d))
            withdrawn.add(str(item_id))
            continue
        if how == "returned":
            fill = dict(fill, mark=dict(m, returned=_by(d)))
        fills[item_id] = fill
    out["fills"] = fills

    for place, tab in (("students", "b2c"), ("companies", "b2b"), ("unclear", "unclear")):
        shown = []
        for e in part.get(place) or []:
            how, d = judge(e.get("kind", ""), str(e.get("subject") or ""), e.get("evidence"))
            if how == "hidden":
                hidden[tab].append(_doc_item(e, d))
                continue
            if how == "returned":
                e = dict(e, returned=_by(d))
            if e.get("proposal") and str(e["proposal"].get("item_id")) in withdrawn:
                # its proposal was dismissed ("לא להציע"): the document stays, without the line that proposes it
                e = {k: v for k, v in e.items() if k != "proposal"}
            shown.append(e)
        out[place] = shown

    out["hidden"] = {tab: sorted(items, key=lambda x: x["id"]) for tab, items in hidden.items()}
    return out


def live_signature(live: Optional[Iterable[dict]]) -> str:
    """The live dismissals a part was shown by, in a few characters: a row is never changed while it is live (an undo or a
    replacement ends it), so its id names it. "" for none read."""
    ids = ",".join(str(d["id"]) for d in sorted(live or (), key=lambda d: d["id"]))
    return hashlib.sha256(ids.encode("ascii")).hexdigest()[:16] if ids else ""
