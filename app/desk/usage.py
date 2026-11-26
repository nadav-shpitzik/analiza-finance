"""How the desk is used: the closed vocabulary of a usage event, and the check every event passes before it is kept in
`desk_events` (migrations/005; Analiza-Monday backlog 0301 D2).

Ported on 24.11 from analiza-metrics app/usage.py at `da671b2`, faithfully: the same kinds, detail keys, ranges and
alphabets, so a row written here reads like a row written there, and the history D5 imports (the desk move plan §4.4) is
one vocabulary. New here: this paragraph, the note on `bill`, the one kind the ported page no longer sends, D4's
`books_fill` with its count `rows`, and R5's three kinds of "לא פער" (`not_a_gap`, `not_a_gap_undo`, `not_a_gap_list`).
Since the
review of D2 (a32395f), three alphabets are narrower than analiza-metrics', each to what its page sends: the visit's id is
letters only, a word carries no number (WORD_RUN, WORD_DIGITS), and a period is a period key the page can name - so a
digit run long enough to be an ID number has no place left in a row. And a kind that is not a string is refused and
counted like any kind outside the list, never a 500.

WHY. Nadav, 16.11: "בוא נוודא שיש לנו גם נתוני שימוש שלה שם כדי שנוכל לשפר את זה עם הזמן" - the finance desk is built
for one bookkeeper (Chava), and what she opens, searches, misses and leaves for monday is what tells the next round what
to build. Before this the only trace was the audit line of each /api call, in a log that is not kept and that never sees
what happens inside the page (a row opened, an action written as the person, a number copied).

WHAT AN EVENT IS. The page (frontend/finance.js `use`) sends a small batch: the desk, a random id for the visit, and
events of the shape {k, t, tab, st, d} - the kind, the page's age in milliseconds, the tab and the list's state, and the
kind's own details. Every kind and every detail key is in the lists below; anything else is not written.

WHAT AN EVENT NEVER IS. A name, an amount, the search's text, an ID number, a bank account, an account or an item id.
That is the page's rule, and this module makes it the table's too: a detail is a number in a range of its own, a yes or
no, a period key, or a word that starts with a lowercase Latin letter and carries no number - so a number, a Hebrew name
or a free text that a page tried to send is refused here, counted, and never written. A search is kept as its shape only:
how many characters, which kind of characters, how many rows it found with the filters and without.
"""
from __future__ import annotations

import re
import threading
from datetime import datetime, timezone
from typing import Any

DESKS = ("finance",)
TABS = ("", "b2c", "b2b")
MAX_EVENTS = 50
# a page open for a week is a page left open; its age is kept, capped
PAGE_MS_MAX = 7 * 24 * 3600 * 1000

# Each kind, and the detail keys it may carry (frontend/finance.js sends every kind here but `bill`; tests/test_desk_usage.py
# holds the two together).
KINDS: dict[str, tuple[str, ...]] = {
    "open": ("ms", "board", "refreshing"),     # the first reading drawn: how long it took, the board, the stored one
    "load_failed": ("code",),                  # the page could not draw a reading: the server's error word
    "tab": (),                                 # a tab chosen (the event's own `tab`)
    "state": (),                               # a tile chosen (the event's own `st`)
    "period": ("period",),                     # a period chosen on the students tab
    "view": ("view",),                         # the companies' list or "לפי לקוח"
    "customer": ("from",),                     # a company's ledger opened (from the aging, a chip) or let go
    "search": ("len", "kind", "results", "unfiltered"),   # a search that settled: its shape, never its text
    "unfilter": ("from", "results"),           # "חיפוש ללא סינון" pressed: under an empty list or in the list's head
    "row": ("state", "open"),                  # a row's details opened or closed, and the row's state
    "open_item": ("where",),                   # a row opened in monday: by its name, the icon, or the details' button
    "act": ("action", "phase", "code"),        # an action written as the person: started, confirmed, done, failed
    # D4 (26.11), new here: "רישום מחשבשבת" pressed - from a row's details, from its document in the books' list, or every
    # proposal at once (`from`: detail, list, all) - and how many rows its batch takes; its life is `act`'s (books_fill)
    "books_fill": ("from", "rows"),
    # R5 (26.11), new here: "לא פער" or "לא להציע" pressed (`phase` open, `from` detail or list), its confirmation
    # cancelled, kept (done) or refused (failed, `code` the server's word) - `alert` the alert's kind, a word of the server's
    # closed list (app/desk/dismissals.py KINDS); "החזרה" of one's own (done, or failed and why); and "הוסתרו כלא פער (N)"
    # opened or closed, under which list (`list`: b2c, b2b, unclear)
    "not_a_gap": ("alert", "phase", "code", "from"),
    "not_a_gap_undo": ("alert", "phase", "code"),
    "not_a_gap_list": ("open", "list"),
    # a company's billing details: edit, confirm, done, failed. Not sent since the port: the page shows them read only
    # until their edit comes back from the browser (finance.js, its change 4; ADR-0188). Kept for that, and for the
    # history D5 imports, which may hold such rows
    "bill": ("phase", "code", "fields"),
    "export": ("list",),                       # an Excel file asked for
    "legend": ("open",),
    "notes": ("open",),                        # "פרטים על הנתונים"
    "refresh": (),                             # the "רענון" button
    "back": ("how",),                          # the page asked again on her return (another tab, or the item card closed)
    "hide": (),                                # the page went out of sight
    "id_shown": (),                            # an ID number read and shown in a payment's details
    "id_empty": (),                            # read, and the lead holds none
    "id_failed": ("code",),                    # not read: why, in the page's words for it
    "id_copied": ("code",),                    # copied ("" when it worked)
    "ask": (),                                 # "חסר כאן משהו? פנייה לנדב" pressed
}
# the detail keys that are numbers, and the largest each may be
NUMBERS = {"ms": 600_000, "len": 200, "results": 100_000, "unfiltered": 100_000, "fields": 10, "rows": 100}
FLAGS = frozenset({"refreshing", "open"})
# a word: a state, an action, a code, a kind of search - lowercase Latin, digits and "_" after a first letter, or empty
WORD = re.compile(r"(?:[a-z][a-z0-9_]{0,39})?")
# ... and never a number: no run of WORD_RUN digits or more, and at most WORD_DIGITS digits in all, so an ID number, an item
# id or an amount cannot pass behind a letter (id039482716, item5093448677, a4000), nor cut by "_" as the page's useWord
# makes of a message (id_039_482_716). The vocabulary's own words carry short runs: b2c, d31_60, http_503 (D2's review)
WORD_RUN = 4
WORD_DIGITS = 6
_DIGIT_RUN = re.compile(r"\d{%d}" % WORD_RUN)
# a period key exactly as app/desk/view.py b2c_periods names one: "" (the whole span), a month ("2026-10"), or a VAT
# period, an odd month and the next of one year ("2026-09+2026-10") - nothing else the page can send (D2's review: any
# four digits, a dash and two more passed, so a number could be sent as a "period")
PERIOD = re.compile(r"(?:20\d{2}-(?:0[1-9]|1[0-2])|(20\d{2})-(?:01\+\1-02|03\+\1-04|05\+\1-06|07\+\1-08|09\+\1-10|11\+\1-12))?")
# the visit's random id: letters only (D2's review: with digits, an ID number passed as a session and was kept on every row
# of its batch). frontend/finance.js useSession makes sixteen
SESSION = re.compile(r"[a-z]{8,40}")


class Refused(Exception):
    """A batch this module will not read at all: its envelope is not a usage batch. Its events are each checked."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def word_ok(v: Any) -> bool:
    """A word of the vocabulary: WORD's alphabet, and no number in it (WORD_RUN, WORD_DIGITS)."""
    return (isinstance(v, str) and WORD.fullmatch(v) is not None and _DIGIT_RUN.search(v) is None
            and sum(ch.isdigit() for ch in v) <= WORD_DIGITS)


def _value_ok(key: str, v: Any) -> bool:
    if key in NUMBERS:
        return type(v) is int and 0 <= v <= NUMBERS[key]
    if key in FLAGS:
        return type(v) is bool
    if key == "period":
        return isinstance(v, str) and PERIOD.fullmatch(v) is not None
    return word_ok(v)


def clean_event(e: Any) -> dict | None:
    """One event as a row's fields, or None when any part of it is outside the lists."""
    if not isinstance(e, dict) or set(e) - {"k", "t", "tab", "st", "d"}:
        return None
    kind, t, tab, st, d = e.get("k"), e.get("t", 0), e.get("tab", ""), e.get("st", ""), e.get("d", {})
    # a kind or a tab that is not a string is refused before it is looked up: a list or an object as `k` raised TypeError
    # (unhashable) out of here, a 500 that lost the batch's good events and counted nothing (D2's review)
    if not isinstance(kind, str) or kind not in KINDS or type(t) is not int or t < 0:
        return None
    if not isinstance(tab, str) or tab not in TABS:
        return None
    if not word_ok(st) or not isinstance(d, dict):
        return None
    allowed = KINDS[kind]
    if any(k not in allowed or not _value_ok(k, v) for k, v in d.items()):
        return None
    return {"kind": kind, "page_ms": min(t, PAGE_MS_MAX), "tab": tab, "state": st, "detail": dict(d)}


def clean_batch(body: Any, user_id: int) -> tuple[list[dict], int]:
    """(the rows to write, how many events were refused). Raises Refused("bad_usage") for an envelope that is not a
    usage batch: a desk outside DESKS, a session id of another shape, no events, or more than MAX_EVENTS."""
    if not isinstance(body, dict):
        raise Refused("bad_usage")
    desk, session, events = body.get("desk"), body.get("session"), body.get("events")
    if desk not in DESKS or not isinstance(session, str) or SESSION.fullmatch(session) is None:
        raise Refused("bad_usage")
    if not isinstance(events, list) or not events or len(events) > MAX_EVENTS:
        raise Refused("bad_usage")
    rows, refused = [], 0
    for e in events:
        row = clean_event(e)
        if row is None:
            refused += 1
            continue
        rows.append(dict(row, user_id=int(user_id), session=session, desk=desk))
    return rows, refused


class UsageStats:
    """What /health says about this instance's usage batches: how many were written, events refused, writes that failed,
    and the last one's time and error class - never a person, a kind or a detail."""

    def __init__(self):
        self.batches = 0
        self.stored = 0
        self.refused = 0
        self.failed = 0
        self.last_at: str | None = None
        self.last_error: str | None = None
        self._lock = threading.Lock()

    def note(self, stored: int = 0, refused: int = 0, error: str | None = None) -> None:
        with self._lock:
            self.batches += 1
            self.stored += stored
            self.refused += refused
            self.failed += int(error is not None)
            self.last_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            self.last_error = error

    def health(self) -> dict:
        with self._lock:
            return {"batches": self.batches, "stored": self.stored, "refused": self.refused, "failed": self.failed,
                    "last_at": self.last_at, "last_error": self.last_error}
