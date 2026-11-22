"""The reconciliation's reading in Postgres (`recon_readings`; Analiza-Monday backlog 0301 R0, spec §2.1).

One row per profile, overwritten by every reading: a cache of monday, never a second source of truth. A reading that
failed or came short replaces the row too, so the rules never run on an older reading that the last check did not
confirm (the rules' contract §0.6: a monday-side rule runs only on a reading whose LAST check was complete).

The body holds the boards' rows (item names among them: students' names on a private board), so it is behind the sync
key like everything with a name; /health says only when the last reading was and whether it was complete. Errors are a
class or a SQLSTATE, never the text (psycopg's can carry the URL).
"""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from typing import Iterator, Optional

from .reading import NEVER_READ, PROFILE_NAME

CONNECT_TIMEOUT_S = 10
STATEMENT_TIMEOUT = "15s"
# What the writes name. app/migrate.py checks the live schema against it.
RECON_TABLES = {
    "recon_readings": ("name", "read_at", "complete", "error", "detail", "checks", "body", "calls", "seconds"),
}


class ReconStoreError(Exception):
    def __init__(self, detail: str):
        self.detail = detail
        super().__init__(detail)


class UnsafeBody(Exception):
    """A row carries a column that is never read (an ID number, a bank account): nothing is written."""


@contextmanager
def _guard() -> Iterator[None]:
    import psycopg

    try:
        yield
    except psycopg.Error as exc:
        raise ReconStoreError(getattr(exc, "sqlstate", None) or type(exc).__name__) from None


def _connect(url: str, autocommit: bool = True):
    import psycopg

    try:
        return psycopg.connect(url, autocommit=autocommit, connect_timeout=CONNECT_TIMEOUT_S)
    except psycopg.Error:
        raise
    except Exception as exc:  # noqa: BLE001 - the class only: the text can carry the URL
        raise ReconStoreError(type(exc).__name__) from None


def _lock_id(name: str) -> int:
    digest = hashlib.sha256(f"analiza-finance:recon-reading:{name}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


@contextmanager
def reading_lock(url: str, name: str = PROFILE_NAME) -> Iterator[bool]:
    """True while this request holds the reading (a transaction-scoped advisory lock on a connection of its own, so a
    pooled connection keeps nothing); False when another reading of the profile is running.

    If that connection drops mid-reading the lock goes with it, and a second reading may start: both read every board
    whole and the later save wins, so nothing is wrong but the cost (review 22.11, judged harmless). Letting go of a
    dropped connection is not an error: the reading was read and kept (review 22.11: it answered `store`)."""
    with _guard():
        lock = _connect(url, autocommit=False)
    try:
        with _guard():
            got = lock.execute("SELECT pg_try_advisory_xact_lock(%s)", (_lock_id(name),)).fetchone()[0]
        yield bool(got)
    finally:
        for step in (lock.rollback, lock.close):
            try:
                step()
            except Exception:  # noqa: BLE001 - a connection that dropped holds nothing to let go of
                pass


def refuse_unsafe(boards: dict) -> None:
    for rows in boards.values():
        for row in rows:
            bad = NEVER_READ.intersection(row) | NEVER_READ.intersection(k.rstrip("#") for k in row)
            if bad:
                raise UnsafeBody(f"a row carries {sorted(bad)}")


def save_reading(url: str, reading: dict, budget_s: Optional[float] = None) -> None:
    """The reading replaces the profile's row, in one transaction under the statement timeout - and within `budget_s`
    when given, the time the request has left (review 22.11: 10 s to connect and 15 s to write, against a 4 s reserve)."""
    refuse_unsafe(reading.get("boards") or {})
    body = json.dumps(reading["boards"], ensure_ascii=False) if reading.get("boards") else None
    connect_s, statement_ms = CONNECT_TIMEOUT_S, 15000
    if budget_s is not None:
        connect_s = max(1, min(CONNECT_TIMEOUT_S, int(budget_s)))
        statement_ms = max(500, min(statement_ms, int(budget_s * 1000)))
    import psycopg

    with _guard(), psycopg.connect(url, autocommit=True, connect_timeout=connect_s) as conn, conn.transaction():
        conn.execute(f"SET LOCAL statement_timeout = {int(statement_ms)}")
        conn.execute(
            "INSERT INTO recon_readings (name, read_at, complete, error, detail, checks, body, calls, seconds) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (name) DO UPDATE SET read_at = EXCLUDED.read_at, "
            "complete = EXCLUDED.complete, error = EXCLUDED.error, detail = EXCLUDED.detail, checks = EXCLUDED.checks, "
            "body = EXCLUDED.body, calls = EXCLUDED.calls, seconds = EXCLUDED.seconds",
            (reading["profile"], reading["read_at"], reading["complete"], reading.get("error"), reading.get("detail"),
             json.dumps(reading["checks"]), body, reading.get("calls") or 0, reading.get("seconds")))


def load_reading(url: str, name: str = PROFILE_NAME, body: bool = True) -> Optional[dict]:
    """The profile's last reading, its rows with it when `body`; None when there is none."""
    cols = "name, read_at, complete, error, detail, checks, calls, seconds" + (", body" if body else "")
    with _guard(), _connect(url) as conn:
        row = conn.execute(f"SELECT {cols} FROM recon_readings WHERE name = %s", (name,)).fetchone()
    if row is None:
        return None
    out = {"profile": row[0], "read_at": row[1].isoformat(timespec="seconds") if row[1] else None, "complete": row[2],
           "error": row[3], "detail": row[4], "checks": row[5] or {}, "calls": row[6],
           "seconds": float(row[7]) if row[7] is not None else None}
    if body:
        out["boards"] = row[8] or {}
    return out


def reading_glance(cur, name: str = PROFILE_NAME) -> Optional[dict]:
    """For the public /health, on its cursor: when the last reading was, whether it was complete, and the closed code of
    one that failed. No count, no board's rows."""
    cur.execute("SELECT read_at, complete, error FROM recon_readings WHERE name = %s", (name,))
    row = cur.fetchone()
    if row is None:
        return None
    return {"read_at": row[0].isoformat(timespec="seconds") if row[0] else None, "complete": row[1], "error": row[2]}
