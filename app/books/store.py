"""The books copy in Postgres (spec §3.2, backlog 0301 B1): saving documents, the run log, the series.

Every document is saved in its own transaction, as it is read, so a run that is cut loses nothing (spec
§2.3). A run of one company holds a transaction-scoped advisory lock on a connection of its own for its
whole length: a second run finds it held and is skipped, and a pooled connection keeps no lock past it.
Errors become BooksStoreError with the class or SQLSTATE only, never the text (it can carry the URL).
"""

from __future__ import annotations

import hashlib
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
from typing import Iterator, Optional

from .doctypes import INFERRED, VALID_STATUS, role
from .parse import DOCUMENT_COLUMNS, LINE_COLUMNS

CONNECT_TIMEOUT_S = 10
STATEMENT_TIMEOUT = "15s"
# What the writes name, per table. app/migrate.py checks the live schema against it.
BOOKS_TABLES = {
    "books_documents": ("company", "stock_id") + DOCUMENT_COLUMNS + (
        "content_hash", "fields_seen", "first_seen_at", "last_read_at", "last_changed_at", "gone_at", "gone_confirmed_by"),
    "books_lines": ("company", "stock_id") + LINE_COLUMNS,
    "books_document_events": ("event_id", "company", "stock_id", "run_id", "kind", "fields", "at"),
    "books_sync_runs": ("run_id", "company", "trigger", "started_at", "finished_at", "from_id", "to_id", "probed", "found",
                        "added", "changed", "absent", "failed", "masked", "far_hits", "complete", "ok", "note"),
}
HOLES_SAMPLE = 10


class BooksStoreError(Exception):
    def __init__(self, detail: str):
        self.detail = detail
        super().__init__(detail)


def _lock_id(company: str) -> int:
    digest = hashlib.sha256(f"analiza-finance:books-run:{company}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


@contextmanager
def _guard() -> Iterator[None]:
    import psycopg

    try:
        yield
    except psycopg.Error as exc:
        raise BooksStoreError(getattr(exc, "sqlstate", None) or type(exc).__name__) from None


class BooksStore:
    def __init__(self, url: str):
        self._url = url
        self._conn = None

    def __repr__(self) -> str:
        return "BooksStore()"

    def _connect(self, autocommit: bool = True):
        import psycopg

        try:
            return psycopg.connect(self._url, autocommit=autocommit, connect_timeout=CONNECT_TIMEOUT_S)
        except psycopg.Error:
            raise
        except Exception as exc:  # noqa: BLE001 - the class only: the text can carry the URL
            raise BooksStoreError(type(exc).__name__) from None

    @contextmanager
    def session(self, company: str) -> Iterator[Optional["BooksStore"]]:
        """The store for one run, or None when another run of this company holds the lock (skipped)."""
        with _guard():
            lock = self._connect(autocommit=False)
            try:
                got = lock.execute("SELECT pg_try_advisory_xact_lock(%s)", (_lock_id(company),)).fetchone()[0]
                if not got:
                    yield None
                    return
                self._conn = self._connect(autocommit=True)
                try:
                    yield self
                finally:
                    self._conn.close()
                    self._conn = None
            finally:
                lock.rollback()  # ends the transaction, and with it the lock
                lock.close()

    # ---- the run log ------------------------------------------------------------------------------
    def start_run(self, company: str, trigger: str, from_id: int) -> int:
        with _guard():
            return self._conn.execute(
                "INSERT INTO books_sync_runs (company, trigger, from_id) VALUES (%s, %s, %s) RETURNING run_id",
                (company, trigger, from_id),
            ).fetchone()[0]

    def finish_run(self, run_id: int, summary: dict) -> None:
        import json

        with _guard():
            self._conn.execute(
                "UPDATE books_sync_runs SET finished_at = now(), to_id = %s, probed = %s, found = %s, added = %s, "
                "changed = %s, absent = %s, failed = %s, masked = %s, far_hits = %s, complete = %s, ok = %s, note = %s "
                "WHERE run_id = %s",
                (summary["to_id"], summary["probed"], summary["found"], summary["added"], summary["changed"],
                 summary["absent"], json.dumps(summary["failed"]), summary["masked"], summary["far_hits"],
                 summary["complete"], summary["ok"], summary.get("note"), run_id),
            )

    def high_water(self, company: str) -> Optional[int]:
        with _guard():
            return self._conn.execute(
                "SELECT max(stock_id) FROM books_documents WHERE company = %s", (company,)
            ).fetchone()[0]

    # ---- one document -----------------------------------------------------------------------------
    def save(self, company: str, run_id: int, doc: dict, lines: list) -> str:
        """'added', 'changed' or 'unchanged'. One transaction: the document, its lines and the event."""
        cols = ("company",) + DOCUMENT_COLUMNS + ("content_hash", "fields_seen")
        values = [company] + [doc.get(c) for c in DOCUMENT_COLUMNS] + [doc["content_hash"], doc["fields_seen"]]
        with _guard(), self._conn.transaction():
            cur = self._conn.cursor()
            cur.execute(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT}'")
            cur.execute(
                f"SELECT content_hash, {', '.join(DOCUMENT_COLUMNS)} FROM books_documents "
                "WHERE company = %s AND stock_id = %s FOR UPDATE",
                (company, doc["stock_id"]),
            )
            row = cur.fetchone()
            if row is not None and row[0] == doc["content_hash"]:
                cur.execute(
                    "UPDATE books_documents SET last_read_at = now() WHERE company = %s AND stock_id = %s",
                    (company, doc["stock_id"]),
                )
                return "unchanged"
            if row is None:
                cur.execute(
                    f"INSERT INTO books_documents ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})", values
                )
                kind, fields = "first_seen", []
            else:
                old = dict(zip(DOCUMENT_COLUMNS, row[1:]))
                fields = sorted(c for c in DOCUMENT_COLUMNS if _differs(old.get(c), doc.get(c)))
                sets = ", ".join(f"{c} = %s" for c in cols[1:])
                cur.execute(
                    f"UPDATE books_documents SET {sets}, last_read_at = now(), last_changed_at = now() "
                    "WHERE company = %s AND stock_id = %s",
                    values[1:] + [company, doc["stock_id"]],
                )
                cur.execute("DELETE FROM books_lines WHERE company = %s AND stock_id = %s", (company, doc["stock_id"]))
                kind = "status_changed" if "status" in fields else "changed"
                if not fields:
                    fields = ["lines"]
            for line in lines:
                lcols = ("company", "stock_id") + LINE_COLUMNS
                cur.execute(
                    f"INSERT INTO books_lines ({', '.join(lcols)}) VALUES ({', '.join(['%s'] * len(lcols))}) "
                    f"ON CONFLICT (company, move_id) DO UPDATE SET "
                    + ", ".join(f"{c} = EXCLUDED.{c}" for c in ("stock_id",) + LINE_COLUMNS[1:]),
                    [company, doc["stock_id"]] + [line.get(c) for c in LINE_COLUMNS],
                )
            cur.execute(
                "INSERT INTO books_document_events (company, stock_id, run_id, kind, fields) VALUES (%s, %s, %s, %s, %s)",
                (company, doc["stock_id"], run_id, kind, fields),
            )
            return "added" if row is None else "changed"

    # ---- what /health and the status route say ---------------------------------------------------
    def last_run(self, company: str) -> Optional[dict]:
        with _guard():
            row = self._conn.execute(
                "SELECT run_id, trigger, started_at, finished_at, from_id, to_id, probed, found, added, changed, absent, "
                "failed, masked, far_hits, complete, ok, note FROM books_sync_runs WHERE company = %s "
                "ORDER BY run_id DESC LIMIT 1",
                (company,),
            ).fetchone()
        if row is None:
            return None
        keys = ("run_id", "trigger", "started_at", "finished_at", "from_id", "to_id", "probed", "found", "added",
                "changed", "absent", "failed", "masked", "far_hits", "complete", "ok", "note")
        out = dict(zip(keys, row))
        for k in ("started_at", "finished_at"):
            out[k] = out[k].isoformat(timespec="seconds") if out[k] else None
        out["far_hits"] = list(out["far_hits"] or [])
        return out

    def series(self, company: str) -> list:
        """Per document type: valid documents held, first and last number, and the numbers missing
        between them (spec §4.2.6: Israeli tax documents are numbered without gaps)."""
        with _guard():
            rows = self._conn.execute(
                "SELECT doc_type, doc_number, status FROM books_documents WHERE company = %s AND gone_at IS NULL",
                (company,),
            ).fetchall()
        return summarize_series(rows)


def summarize_series(rows) -> list:
    by_type: dict = {}
    for doc_type, number, status in rows:
        entry = by_type.setdefault(doc_type, {"valid": [], "drafts": 0, "other_status": 0, "not_numeric": 0})
        if status == VALID_STATUS:
            if number is not None and str(number).isdigit():
                entry["valid"].append(int(number))
            else:
                entry["not_numeric"] += 1
        elif status == 0:
            entry["drafts"] += 1
        else:
            entry["other_status"] += 1
    out = []
    for doc_type in sorted(by_type, key=lambda t: (t is None, t)):
        e = by_type[doc_type]
        numbers = sorted(e["valid"])
        unique = sorted(set(numbers))
        holes = sorted(set(range(unique[0], unique[-1] + 1)) - set(unique)) if unique else []
        out.append({
            "doc_type": doc_type,
            "role": role(doc_type),
            "name": INFERRED.get(doc_type, (None, None))[1] if isinstance(doc_type, int) else None,
            "valid": len(numbers),
            "first": unique[0] if unique else None,
            "last": unique[-1] if unique else None,
            "holes": len(holes),
            "holes_sample": holes[:HOLES_SAMPLE],
            "duplicates": len(numbers) - len(unique),
            "drafts": e["drafts"],
            "other_status": e["other_status"],
            "not_numeric": e["not_numeric"],
        })
    return out


def _differs(old, new) -> bool:
    if old is None or new is None:
        return old is not new
    if isinstance(old, (list, tuple)) or isinstance(new, (list, tuple)):
        return list(old) != list(new)
    if isinstance(old, Decimal) or isinstance(new, Decimal):
        try:
            return Decimal(str(old)) != Decimal(str(new))
        except InvalidOperation:
            return True
    return old != new if type(old) is type(new) else str(old) != str(new)
