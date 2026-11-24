"""The desk's usage events in Postgres (`desk_events`, migrations/005; Analiza-Monday backlog 0301 D2).

The rows come from app/desk/usage.py `clean_batch`, every field already checked against the closed vocabulary; this module
only writes them: one batch, one transaction, all or none. A row whose fields are not exactly the table's is refused
before anything is written (analiza-metrics `usage_append`, the same rule). Nothing here reads the table: the operator's
SQL does (docs/usage-data.md).

The table is never created here (CLAUDE.md rule 8: no migration at startup, no CREATE ... IF NOT EXISTS at first use). A
database without it is `store_schema`, which POST /api/usage answers with 503 `usage_unavailable` and /health says twice:
`desk.usage.last_error`, and `store.missing_columns`, where app/migrate.py checks DESK_TABLES with every other table.

A connection of its own per batch, under a connect, a lock and a statement timeout: a batch is a few dozen short rows, the
page waits for none of it, and a table held by someone else costs one batch, never a hung request. Errors carry a kind
from USAGE_STORE_KINDS and the class or the SQLSTATE, never the text (psycopg's can carry the host, the user, or for a
malformed URL the password).
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Iterator

USAGE_TABLE = "desk_events"
# What the writes name (analiza-metrics app/store.py `_USAGE_COLUMNS`): app/migrate.py checks the live schema against it.
# `at` is the database's clock, `id` its sequence.
DESK_TABLES = {USAGE_TABLE: ("at", "user_id", "session", "desk", "kind", "tab", "state", "page_ms", "detail")}
ROW_FIELDS = frozenset(DESK_TABLES[USAGE_TABLE]) - {"at"}

CONNECT_TIMEOUT_S = 5
# the wait for a lock someone else holds on the table (a migration, a hand-run ALTER), and for any one statement
LOCK_TIMEOUT = "3s"
STATEMENT_TIMEOUT = "5s"

USAGE_STORE_KINDS = (
    "store_unreachable",  # no connection: the database is down, asleep too long, or the URL is wrong
    "store_schema",  # the table or a column is missing: python3 -m app.migrate has not run, or the Republish after it
    "store_lock_timeout",  # the table was held past LOCK_TIMEOUT
    "store_timeout",  # the write ran past STATEMENT_TIMEOUT
    "store_error",  # any other database error
)

_INSERT = ("INSERT INTO desk_events (user_id, session, desk, kind, tab, state, page_ms, detail) "
           "VALUES (%(user_id)s, %(session)s, %(desk)s, %(kind)s, %(tab)s, %(state)s, %(page_ms)s, %(detail)s::jsonb)")


class UsageStoreError(Exception):
    def __init__(self, kind: str, detail: str = ""):
        assert kind in USAGE_STORE_KINDS, kind
        self.kind = kind
        self.detail = detail
        super().__init__(kind + (f": {detail}" if detail else ""))


class UnknownRow(ValueError):
    """A row whose fields are not exactly the table's: nothing of its batch is written. Names the fields, never a value."""


@contextmanager
def _guard() -> Iterator[None]:
    """A database error as a UsageStoreError, by class (app/store.py's mapping, and the statement timeout apart)."""
    import psycopg
    from psycopg import errors as pgerr

    try:
        yield
    except (pgerr.UndefinedTable, pgerr.UndefinedColumn) as exc:
        raise UsageStoreError("store_schema", exc.sqlstate or type(exc).__name__) from None
    except pgerr.LockNotAvailable as exc:
        raise UsageStoreError("store_lock_timeout", exc.sqlstate or type(exc).__name__) from None
    except pgerr.QueryCanceled as exc:  # an OperationalError too: told apart from a connection that failed
        raise UsageStoreError("store_timeout", exc.sqlstate or type(exc).__name__) from None
    except psycopg.OperationalError as exc:
        raise UsageStoreError("store_unreachable", getattr(exc, "sqlstate", None) or type(exc).__name__) from None
    except psycopg.Error as exc:
        raise UsageStoreError("store_error", getattr(exc, "sqlstate", None) or type(exc).__name__) from None


def _connect(url: str):
    """psycopg.connect, autocommit (the write opens its own transaction). Any failure to connect is store_unreachable by
    its SQLSTATE or its class: psycopg's (down, asleep, a refused password; a malformed URL is its ProgrammingError) and
    what is not psycopg's (a %-escape that is not UTF-8, a host label over 63 characters; app/store.py's review 19.11)."""
    import psycopg

    try:
        return psycopg.connect(url, autocommit=True, connect_timeout=CONNECT_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001 - the class only: the text can carry the URL
        raise UsageStoreError("store_unreachable", getattr(exc, "sqlstate", None) or type(exc).__name__) from None


def append_events(url: str, rows: list) -> int:
    """The rows of one batch, all or none: how many were written. No rows, no connection."""
    if not rows:
        return 0
    for r in rows:
        if set(r) != ROW_FIELDS:
            raise UnknownRow(",".join(sorted(set(map(str, r)) ^ ROW_FIELDS)))
    params = [dict(r, detail=json.dumps(r["detail"], ensure_ascii=True)) for r in rows]
    with _guard(), _connect(url) as conn, conn.transaction():
        conn.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
        conn.execute(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT}'")
        with conn.cursor() as cur:
            cur.executemany(_INSERT, params)
    return len(rows)
