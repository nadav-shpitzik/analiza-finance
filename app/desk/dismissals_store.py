""""לא פער" in Postgres (`alert_dismissals`, migrations/008; Analiza-Monday backlog 0301 R5, the spec §6.1 item 4).

What is kept, per dismissal: the books company, the alert's kind and subject (a Student Charge item id or a document's
number), the evidence (one sha256 the server computed, app/desk/dismissals.py), who dismissed it (the sessionToken's monday
user id) and when; and once it ends, when and by whom. Never a name, an amount, a card key or an ID number.

THE RULES, each held by the table as well as here:
1. One live dismissal per (company, kind, subject) - the partial unique index `alert_dismissals_live`. Two presses at once
   are one dismissal: the second is `already_hidden`, by the index, whatever this code read first.
2. Nothing is deleted or edited. An undo sets `undone_at` and `undone_by` on its row, and only its own dismisser may
   (ADR-0190 §6, the same norm: a check made by mistake is undone only by whoever made it; the repo has no admin).
3. An alert that came back with other evidence ("חזר": its live dismissal no longer matches) may be dismissed again, by
   anyone who may dismiss it: the old row is closed (`undone_at`, `undone_by` the new dismisser) and `replaced_by` names the
   new one, in one transaction, so the history says it was replaced and not undone.

A connection of its own per call, autocommit with an explicit transaction, under a connect, a lock and a statement
timeout: the live read runs inside a person's request (app/desk/books.py DeskBooks, at most every RUN_CHECK_S), and a
press waits for its write. Errors are a DismissalStoreError of a kind from STORE_KINDS with the SQLSTATE or the class,
never the text (psycopg's can carry the host, the user, or for a malformed URL the password); a refusal by the rules is a
DismissalRefused with its word. The deployment never creates the table (CLAUDE.md rule 8): without it every call is
`store_schema`, which /health says (`desk.books.dismissals`, and `store.missing_columns`).
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator, Optional

TABLE = "alert_dismissals"
# What the writes and the reads name. app/migrate.py checks the live schema against it.
DISMISSAL_TABLES = {TABLE: ("id", "company", "kind", "subject", "evidence", "user_id", "created_at", "undone_at",
                            "undone_by", "replaced_by")}

CONNECT_TIMEOUT_S = 5
LOCK_TIMEOUT = "3s"
STATEMENT_TIMEOUT = "5s"

STORE_KINDS = (
    "store_unreachable",   # no connection: the database is down, asleep too long, or the URL is wrong
    "store_schema",        # the table or a column is missing: python3 -m app.migrate has not run, or the Republish after it
    "store_lock_timeout",  # the table was held past LOCK_TIMEOUT
    "store_timeout",       # a statement ran past STATEMENT_TIMEOUT
    "store_error",         # any other database error
)
# What the rules refuse (the routes answer each by its word)
REFUSALS = ("already_hidden", "not_live", "not_yours")

_LIVE = (f"SELECT id, kind, subject, evidence, user_id, created_at FROM {TABLE} "
         "WHERE company = %s AND undone_at IS NULL ORDER BY id")
_LIVE_ONE = (f"SELECT id, evidence, user_id FROM {TABLE} WHERE company = %s AND kind = %s AND subject = %s "
             "AND undone_at IS NULL FOR UPDATE")
_INSERT = (f"INSERT INTO {TABLE} (company, kind, subject, evidence, user_id) VALUES (%s, %s, %s, %s, %s) "
           "RETURNING id, created_at")
_CLOSE = f"UPDATE {TABLE} SET undone_at = now(), undone_by = %s WHERE id = %s AND undone_at IS NULL"
_REPLACED = f"UPDATE {TABLE} SET replaced_by = %s WHERE id = %s"
_ONE = (f"SELECT user_id FROM {TABLE} WHERE id = %s AND company = %s AND kind = %s AND subject = %s "
        "AND undone_at IS NULL FOR UPDATE")


class DismissalStoreError(Exception):
    """`kind` is one of STORE_KINDS; `detail` the SQLSTATE or the exception's class, never its text."""

    def __init__(self, kind: str, detail: str = ""):
        assert kind in STORE_KINDS, kind
        self.kind, self.detail = kind, detail
        super().__init__(kind + (f": {detail}" if detail else ""))


class DismissalRefused(Exception):
    """The rules refused the call: `code` is one of REFUSALS. Nothing was written."""

    def __init__(self, code: str):
        assert code in REFUSALS, code
        self.code = code
        super().__init__(code)


@contextmanager
def _guard() -> Iterator[None]:
    """A database error as a DismissalStoreError by its class (app/desk/usage_store.py's mapping); a second live
    dismissal of one subject, which only the unique index can see when two presses meet, as `already_hidden`."""
    import psycopg
    from psycopg import errors as pgerr

    try:
        yield
    except pgerr.UniqueViolation:
        raise DismissalRefused("already_hidden") from None
    except (pgerr.UndefinedTable, pgerr.UndefinedColumn) as exc:
        raise DismissalStoreError("store_schema", exc.sqlstate or type(exc).__name__) from None
    except pgerr.LockNotAvailable as exc:
        raise DismissalStoreError("store_lock_timeout", exc.sqlstate or type(exc).__name__) from None
    except pgerr.QueryCanceled as exc:  # an OperationalError too: told apart from a connection that failed
        raise DismissalStoreError("store_timeout", exc.sqlstate or type(exc).__name__) from None
    except psycopg.OperationalError as exc:
        raise DismissalStoreError("store_unreachable", getattr(exc, "sqlstate", None) or type(exc).__name__) from None
    except psycopg.Error as exc:
        raise DismissalStoreError("store_error", getattr(exc, "sqlstate", None) or type(exc).__name__) from None
    except UnicodeError as exc:  # a text that cannot be encoded to send: a store failure, never a crash past the store
        raise DismissalStoreError("store_error", type(exc).__name__) from None


def _utc(t: Optional[datetime]) -> Optional[str]:
    return None if t is None else t.astimezone(timezone.utc).isoformat(timespec="seconds")


class DismissalStore:
    """The dismissals in the service's own Postgres. `url` never leaves this object."""

    kind = "postgres"

    def __init__(self, url: str):
        self._url = url

    def __repr__(self) -> str:
        return "DismissalStore()"

    def _connect(self):
        """psycopg.connect, autocommit (each call opens its own transaction). Any failure to connect is store_unreachable
        by its SQLSTATE or its class, psycopg's or not (a URL it cannot even parse; review 19.11)."""
        import psycopg

        try:
            return psycopg.connect(self._url, autocommit=True, connect_timeout=CONNECT_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 - the class only: the text can carry the URL
            raise DismissalStoreError("store_unreachable", getattr(exc, "sqlstate", None) or type(exc).__name__) from None

    @contextmanager
    def _transaction(self):
        with _guard(), self._connect() as conn, conn.transaction():
            conn.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
            conn.execute(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT}'")
            yield conn

    def live(self, company: str) -> list:
        """Every live dismissal of the company, oldest first: {"id", "kind", "subject", "evidence", "by", "at"}."""
        with self._transaction() as conn:
            rows = conn.execute(_LIVE, (company,)).fetchall()
        return [{"id": int(i), "kind": k, "subject": s, "evidence": e, "by": int(u), "at": _utc(at)}
                for i, k, s, e, u, at in rows]

    def dismiss(self, company: str, kind: str, subject: str, evidence: str, user_id: int) -> dict:
        """Keep one dismissal: {"id", "at", "replaced" (the id of a live one with other evidence it closed, or None)}.
        DismissalRefused("already_hidden") when a live one with this very evidence is there (nothing is written)."""
        with self._transaction() as conn:
            old = conn.execute(_LIVE_ONE, (company, kind, subject)).fetchone()
            if old is not None and old[1] == evidence:
                raise DismissalRefused("already_hidden")
            if old is not None:   # it came back with other evidence: the old one is closed, and names the new
                conn.execute(_CLOSE, (int(user_id), old[0]))
            new_id, at = conn.execute(_INSERT, (company, kind, subject, evidence, int(user_id))).fetchone()
            if old is not None:
                conn.execute(_REPLACED, (new_id, old[0]))
        return {"id": int(new_id), "at": _utc(at), "replaced": None if old is None else int(old[0])}

    def undo(self, company: str, kind: str, subject: str, dismissal_id: int, user_id: int) -> None:
        """End one live dismissal, by its own dismisser only. DismissalRefused("not_live") when it is not the live one of
        that kind and subject (undone, replaced, or never), ("not_yours") when someone else dismissed it."""
        with self._transaction() as conn:
            row = conn.execute(_ONE, (int(dismissal_id), company, kind, subject)).fetchone()
            if row is None:
                raise DismissalRefused("not_live")
            if int(row[0]) != int(user_id):
                raise DismissalRefused("not_yours")
            conn.execute(_CLOSE, (int(user_id), int(dismissal_id)))
