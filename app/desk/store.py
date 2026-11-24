"""The desk's stored reading (`desk_readings`, migrations/006; Analiza-Monday backlog 0301 D2), ported on 24.11 from
analiza-metrics' `finance_readings` (its migrations/003, app/store.py `finance_reading_load` / `finance_reading_save` and
app/finance_mirror.py, at da671b2).

WHY. A full reading costs about 9 s and 28 monday requests (measured live 24.11), and D1 held it only in the memory of one
Autoscale instance: an instance that woke from zero made the first open wait for one. The last reading is kept here, one
row, overwritten: an instance that holds none answers with it at once, marked refreshing, and the next refresh reads only
what changed since it (app/desk/reading.py `HeldReading`). A cache of monday, never a second source of truth.

WHAT IS KEPT: a reading without an error - its times and kind, whether it was complete, its counts (`checks`), its cost,
and its body (`BODY_KEYS`): the charge rows and the rows they link to as monday shows them to the boards' members (names
and amounts), the linked leads' names and Owners, and what was asked. NEVER a column of NEVER_READ (app/monday/client.py:
an ID number, a refund's bank account, a company's business number, or a mirror of one): the reader cannot read one, and
`refuse_unsafe` checks the very text written, as app/recon/store.py does for the reconciliation's reading.

TWO INSTANCES, ONE ROW: a reading older than the row's never replaces it (`save` answers False). Errors are a
DeskStoreError with the SQLSTATE or the class, never the text (psycopg's can carry the URL). The deployment never creates
the table (CLAUDE.md rule 8): without it every load and save fails, is said in /health, and the desk reads monday as D1 did.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator, Optional

from ..monday.client import NEVER_READ
from .reading import BODY_KEYS, BODY_SCHEMA, UnsafeBody, body_of, instant

STORE_NAME = "desk"
TABLE = "desk_readings"
KINDS = ("full", "delta")
# a load answers an open of a cold instance and a save ends a refresh: both inside a request, so both are bounded
CONNECT_TIMEOUT_S = 5
STATEMENT_TIMEOUT_MS = 10_000
LOCK_TIMEOUT_MS = 5_000
# What the writes name. app/migrate.py checks the live schema against it.
DESK_TABLES = {
    TABLE: ("name", "read_at", "full_read_at", "kind", "complete", "body_schema", "checks", "body", "calls", "seconds",
            "saved_at"),
}
_SAVE = (
    f"INSERT INTO {TABLE} AS d (name, read_at, full_read_at, kind, complete, body_schema, checks, body, calls, seconds, "
    "saved_at) VALUES (%(name)s, %(read_at)s, %(full_read_at)s, %(kind)s, %(complete)s, %(body_schema)s, %(checks)s::jsonb, "
    "%(body)s::jsonb, %(calls)s, %(seconds)s, now()) "
    "ON CONFLICT (name) DO UPDATE SET read_at = EXCLUDED.read_at, full_read_at = EXCLUDED.full_read_at, "
    "kind = EXCLUDED.kind, complete = EXCLUDED.complete, body_schema = EXCLUDED.body_schema, checks = EXCLUDED.checks, "
    "body = EXCLUDED.body, calls = EXCLUDED.calls, seconds = EXCLUDED.seconds, saved_at = EXCLUDED.saved_at "
    "WHERE d.read_at <= EXCLUDED.read_at"
)
_LOAD = (f"SELECT read_at, full_read_at, kind, complete, body_schema, checks, body, calls, seconds FROM {TABLE} "
         "WHERE name = %s")


class DeskStoreError(Exception):
    """`detail` is the SQLSTATE or the exception's class: never its text."""

    def __init__(self, detail: str):
        self.detail = detail
        super().__init__(detail)


@contextmanager
def _guard() -> Iterator[None]:
    """psycopg's errors by their SQLSTATE, and a text that cannot be encoded to send (a lone surrogate: review of a32395f)
    by its class: a store failure either way, never a crash past the store."""
    import psycopg

    try:
        yield
    except psycopg.Error as exc:
        raise DeskStoreError(getattr(exc, "sqlstate", None) or type(exc).__name__) from None
    except UnicodeError as exc:
        raise DeskStoreError(type(exc).__name__) from None


def refuse_unsafe(text: str) -> None:
    """The last look before a text is written: no column of NEVER_READ anywhere in it, as a key or anywhere else. In
    compact JSON a column id is one word, never part of a name (analiza-metrics `refuse_unsafe`, the same look)."""
    for col in sorted(NEVER_READ):
        if col in text:
            raise UnsafeBody(col)


def _utc(t: datetime) -> str:
    """A time read back as it was written: UTC, to the microsecond (the page compares a reading's time as text)."""
    return t.astimezone(timezone.utc).isoformat(timespec="microseconds")


class DeskStore:
    """The desk's reading in the service's own Postgres. `url` never leaves this object."""

    kind = "postgres"

    def __init__(self, url: str, name: str = STORE_NAME):
        self._url, self.name = url, name

    def __repr__(self) -> str:
        return "DeskStore()"

    def _connect(self):
        """psycopg.connect, where a failure that is not psycopg's (a URL it cannot even parse) is a DeskStoreError too, by
        its class (review 19.11: those reach past a guard that catches psycopg's only)."""
        import psycopg

        try:
            return psycopg.connect(self._url, autocommit=True, connect_timeout=CONNECT_TIMEOUT_S)
        except psycopg.Error:
            raise
        except Exception as exc:  # noqa: BLE001 - the class only: the text can carry the URL
            raise DeskStoreError(type(exc).__name__) from None

    def save(self, reading: dict) -> bool:
        """The reading, unless a newer one is stored (another instance's): True when it was written. A reading with an
        error is never kept; a text that names a NEVER_READ column is refused before the database is asked (UnsafeBody)."""
        if reading.get("error"):
            raise ValueError("a reading that failed is never kept")
        if reading.get("kind") not in KINDS:
            raise ValueError("a reading of no known kind")
        read_at, full_read_at = instant(reading.get("read_at")), instant(reading.get("full_read_at"))
        if read_at is None or full_read_at is None:
            raise ValueError("a reading without its times")
        body = json.dumps(body_of(reading), ensure_ascii=False, separators=(",", ":"))
        checks = json.dumps(reading.get("checks") or {}, ensure_ascii=False, separators=(",", ":"))
        refuse_unsafe(body)
        refuse_unsafe(checks)
        seconds = reading.get("seconds")
        params = {"name": self.name, "read_at": read_at, "full_read_at": full_read_at, "kind": reading["kind"],
                  "complete": bool(reading.get("complete")), "body_schema": BODY_SCHEMA, "checks": checks, "body": body,
                  "calls": int(reading.get("calls") or 0), "seconds": None if seconds is None else float(seconds)}
        with _guard():
            with self._connect() as conn, conn.transaction():
                conn.execute(f"SET LOCAL statement_timeout = {STATEMENT_TIMEOUT_MS}")
                conn.execute(f"SET LOCAL lock_timeout = {LOCK_TIMEOUT_MS}")
                return conn.execute(_SAVE, params).rowcount == 1

    def load(self) -> Optional[dict]:
        """The stored reading in the shape `read_desk` gives, with the `body_schema` it was kept under; None when there is
        none."""
        with _guard():
            with self._connect() as conn, conn.transaction():
                conn.execute(f"SET LOCAL statement_timeout = {STATEMENT_TIMEOUT_MS}")
                row = conn.execute(_LOAD, (self.name,)).fetchone()
        if row is None:
            return None
        read_at, full_read_at, kind, complete, schema, checks, body, calls, seconds = row
        body = body if isinstance(body, dict) else {}
        out = {k: body.get(k) for k in BODY_KEYS}
        out.update({"read_at": _utc(read_at), "full_read_at": _utc(full_read_at), "kind": kind, "complete": bool(complete),
                    "error": None, "detail": None, "checks": checks if isinstance(checks, dict) else {},
                    "calls": calls, "seconds": None if seconds is None else float(seconds), "body_schema": schema})
        return out


def glance(cur, name: str = STORE_NAME) -> Optional[dict]:
    """For the public /health, on its cursor: when the stored reading was taken, which kind it is, whether it was complete,
    and when it was saved. No count and no row."""
    cur.execute(f"SELECT read_at, full_read_at, kind, complete, saved_at FROM {TABLE} WHERE name = %s", (name,))
    row = cur.fetchone()
    if row is None:
        return None
    read_at, full_read_at, kind, complete, saved_at = row
    return {"read_at": read_at.astimezone(timezone.utc).isoformat(timespec="seconds"),
            "full_read_at": full_read_at.astimezone(timezone.utc).isoformat(timespec="seconds"), "kind": kind,
            "complete": complete, "saved_at": saved_at.astimezone(timezone.utc).isoformat(timespec="seconds")}
