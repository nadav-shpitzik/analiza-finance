"""How the desk is used: the closed vocabulary of a usage event, and the check every event passes before it is kept in
`desk_events` (migrations/005; Analiza-Monday backlog 0301 D2).

Ported on 24.11 from analiza-metrics app/usage.py at `da671b2`, faithfully: the same kinds, detail keys, ranges and
alphabets, so a row written here reads like a row written there, and the history D5 imports (the desk move plan §4.4) is
one vocabulary. New here: this paragraph, and the note on `bill`, the one kind the ported page no longer sends.

WHY. Nadav, 16.11: "בוא נוודא שיש לנו גם נתוני שימוש שלה שם כדי שנוכל לשפר את זה עם הזמן" - the finance desk is built
for one bookkeeper (Chava), and what she opens, searches, misses and leaves for monday is what tells the next round what
to build. Before this the only trace was the audit line of each /api call, in a log that is not kept and that never sees
what happens inside the page (a row opened, an action written as the person, a number copied).

WHAT AN EVENT IS. The page (frontend/finance.js `use`) sends a small batch: the desk, a random id for the visit, and
events of the shape {k, t, tab, st, d} - the kind, the page's age in milliseconds, the tab and the list's state, and the
kind's own details. Every kind and every detail key is in the lists below; anything else is not written.

WHAT AN EVENT NEVER IS. A name, an amount, the search's text, an ID number, a bank account, an account or an item id.
That is the page's rule, and this module makes it the table's too: a detail is a number in a range of its own, a yes or
no, a period key, or a word that starts with a lowercase Latin letter - so a number, a Hebrew name or a free text that a
page tried to send is refused here, counted, and never written. A search is kept as its shape only: how many
characters, which kind of characters, how many rows it found with the filters and without.
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
NUMBERS = {"ms": 600_000, "len": 200, "results": 100_000, "unfiltered": 100_000, "fields": 10}
FLAGS = frozenset({"refreshing", "open"})
# a word: a state, an action, a code, a kind of search - lowercase Latin, digits and "_" after a first letter, or empty
WORD = re.compile(r"(?:[a-z][a-z0-9_]{0,39})?")
# a period key as app/desk/view.py b2c_periods names it: "", "2026-10", "2026-09+2026-10"
PERIOD = re.compile(r"(?:\d{4}-\d{2}(?:\+\d{4}-\d{2})?)?")
SESSION = re.compile(r"[a-z0-9]{8,40}")


class Refused(Exception):
    """A batch this module will not read at all: its envelope is not a usage batch. Its events are each checked."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _value_ok(key: str, v: Any) -> bool:
    if key in NUMBERS:
        return type(v) is int and 0 <= v <= NUMBERS[key]
    if key in FLAGS:
        return type(v) is bool
    if key == "period":
        return isinstance(v, str) and PERIOD.fullmatch(v) is not None
    return isinstance(v, str) and WORD.fullmatch(v) is not None


def clean_event(e: Any) -> dict | None:
    """One event as a row's fields, or None when any part of it is outside the lists."""
    if not isinstance(e, dict) or set(e) - {"k", "t", "tab", "st", "d"}:
        return None
    kind, t, tab, st, d = e.get("k"), e.get("t", 0), e.get("tab", ""), e.get("st", ""), e.get("d", {})
    if kind not in KINDS or type(t) is not int or t < 0 or tab not in TABS:
        return None
    if not isinstance(st, str) or WORD.fullmatch(st) is None or not isinstance(d, dict):
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
