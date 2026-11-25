"""The books copy in Postgres (spec §3.2, backlog 0301 B1 and B2): saving documents, the run log, the series, the
weekly sweep's position, and a held document that went missing.

Every document is saved in its own transaction, as it is read, so a run that is cut loses nothing (spec
§2.3). A run of one company holds a transaction-scoped advisory lock on a connection of its own for its
whole length: a second run finds it held and is skipped, and a pooled connection keeps no lock past it.
Errors become BooksStoreError with the class or SQLSTATE only, never the text (it can carry the URL).
"""

from __future__ import annotations

import hashlib
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
from typing import Iterable, Iterator, Optional

from .doctypes import DRAFT_STATUS, INFERRED, VALID_STATUS, role
from .parse import DOCUMENT_COLUMNS, LINE_COLUMNS, line_key

CONNECT_TIMEOUT_S = 10
# the desk (D3) asks inside a person's request: a database that does not answer is said within seconds, not ten
DESK_CONNECT_TIMEOUT_S = 3
STATEMENT_TIMEOUT = "15s"
# What the writes name, per table. app/migrate.py checks the live schema against it.
BOOKS_TABLES = {
    "books_documents": ("company", "stock_id") + DOCUMENT_COLUMNS + (
        "content_hash", "fields_seen", "first_seen_at", "last_read_at", "last_changed_at", "gone_at", "gone_confirmed_by",
        "missing_since", "missing_seen_at"),
    "books_lines": ("company", "stock_id") + LINE_COLUMNS,
    "books_document_events": ("event_id", "company", "stock_id", "run_id", "kind", "fields", "at"),
    "books_retry": ("company", "stock_id", "kind", "attempts", "first_failed_at", "last_failed_at"),
    "books_sync_runs": ("run_id", "company", "trigger", "started_at", "finished_at", "from_id", "to_id", "probed", "found",
                        "added", "changed", "absent", "failed", "masked", "far_hits", "complete", "ok", "note", "reasons"),
    "books_sweep": ("company", "next_id", "pass_started_at", "last_pass_ended_at", "passes"),
}
HOLES_SAMPLE = 10
STUCK_SAMPLE = 20
# Ids put in books_retry before they were ever read: attempts 0, and a row already there keeps its kind.
_QUEUE = ("INSERT INTO books_retry (company, stock_id, kind, attempts) SELECT %s, unnest(%s::bigint[]), %s, 0 "
          "ON CONFLICT (company, stock_id) DO NOTHING")


# A held document read again unchanged, in one message: its last read moves and its books_retry row goes - what the full
# save does for it - in one round trip instead of six (22.11 on the deployment: about 0.24 s a save, more than the read,
# so the steady tick's fixed phases filled the box and the weekly sweep never began). BEGIN, SET LOCAL statement_timeout,
# the statement and COMMIT travel as one Query, so every wait in it - a row or a table lock - ends at the same timeout
# as the full save's (adversarial review 22.11: NOWAIT alone left the retry row's lock and the table locks unbounded).
# Only a row that is neither missing nor gone and has the same hash; anything else matches no row and the full save
# runs. The values go in as literals: a Query with parameters cannot carry four statements.
_UNCHANGED = (
    "BEGIN; SET LOCAL statement_timeout = {timeout}; "
    "WITH upd AS (UPDATE books_documents SET last_read_at = now() WHERE company = {company} AND stock_id = {stock_id} "
    "AND content_hash = {hash} AND gone_at IS NULL AND missing_since IS NULL RETURNING stock_id), "
    "cleared AS (DELETE FROM books_retry r USING upd WHERE r.company = {company} AND r.stock_id = upd.stock_id) "
    "SELECT count(*) FROM upd; COMMIT")


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

    def _connect(self, autocommit: bool = True, connect_timeout_s: int = CONNECT_TIMEOUT_S):
        import psycopg

        try:
            return psycopg.connect(self._url, autocommit=autocommit, connect_timeout=connect_timeout_s)
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
    def start_run(self, company: str, trigger: str, from_id: Optional[int]) -> int:
        with _guard():
            return self._conn.execute(
                "INSERT INTO books_sync_runs (company, trigger, from_id) VALUES (%s, %s, %s) RETURNING run_id",
                (company, trigger, from_id),
            ).fetchone()[0]

    def finish_run(self, run_id: int, summary: dict) -> None:
        import json

        with _guard():
            self._conn.execute(
                "UPDATE books_sync_runs SET finished_at = now(), from_id = %s, to_id = %s, probed = %s, found = %s, added = %s, "
                "changed = %s, absent = %s, failed = %s, masked = %s, far_hits = %s, complete = %s, ok = %s, note = %s, "
                "reasons = %s WHERE run_id = %s",
                (summary["from_id"], summary["to_id"], summary["probed"], summary["found"], summary["added"], summary["changed"],
                 summary["absent"], json.dumps(summary["failed"]), summary["masked"], summary["far_hits"],
                 summary["complete"], summary["ok"], summary.get("note"), list(summary.get("reasons") or []), run_id),
            )

    def last_reasons(self, company: str) -> Optional[list]:
        """Why the last finished run of this company was not ok ([] when it was), or None when none finished."""
        with _guard():
            row = self._conn.execute(
                "SELECT reasons FROM books_sync_runs WHERE company = %s AND finished_at IS NOT NULL ORDER BY run_id DESC LIMIT 1",
                (company,)).fetchone()
        return None if row is None else list(row[0] or [])

    def ever_completed(self, company: str) -> bool:
        """Whether a run of this company ever reached the end: after that, a run starting with nothing
        held (Demo) uses the plain frontier, not the first run's wide one."""
        with _guard():
            return self._conn.execute(
                "SELECT EXISTS (SELECT 1 FROM books_sync_runs WHERE company = %s AND complete)", (company,)
            ).fetchone()[0]

    # ---- reads to try again ------------------------------------------------------------------------
    def retry_ids(self, company: str) -> list:
        with _guard():
            return [r[0] for r in self._conn.execute(
                "SELECT stock_id FROM books_retry WHERE company = %s ORDER BY stock_id", (company,)).fetchall()]

    def retry_rows(self, company: str) -> list:
        """(stock_id, kind, attempts, last_failed_at) of every id waiting in books_retry. `attempts` counts failed
        reads: an id a probe jumped over waits with 0 until it is read."""
        with _guard():
            return [tuple(r) for r in self._conn.execute(
                "SELECT stock_id, kind, attempts, last_failed_at FROM books_retry WHERE company = %s ORDER BY stock_id",
                (company,)).fetchall()]

    def retry_summary(self, company: str, limit: int = STUCK_SAMPLE) -> dict:
        """What /api/books/status says of books_retry: how many ids wait, by kind, and the ids that failed more
        than once (stuck), most attempts first (third review 22.11: three such ids froze the copy unseen)."""
        with _guard():
            by_kind = dict(self._conn.execute(
                "SELECT kind, count(*) FROM books_retry WHERE company = %s GROUP BY kind ORDER BY kind", (company,)).fetchall())
            stuck = self._conn.execute(
                "SELECT stock_id, kind, attempts, first_failed_at, last_failed_at FROM books_retry "
                "WHERE company = %s AND attempts >= 2 ORDER BY attempts DESC, stock_id LIMIT %s", (company, limit)).fetchall()
            stuck_count = self._conn.execute(
                "SELECT count(*) FROM books_retry WHERE company = %s AND attempts >= 2", (company,)).fetchone()[0]
        return {
            "waiting": sum(by_kind.values()),
            "by_kind": by_kind,
            "stuck_count": stuck_count,
            "stuck": [{"stock_id": r[0], "kind": r[1], "attempts": r[2],
                       "first_failed_at": r[3].isoformat(timespec="seconds"),
                       "last_failed_at": r[4].isoformat(timespec="seconds")} for r in stuck],
        }

    def mark_failed(self, company: str, stock_id: int, kind: str) -> None:
        """A failed read. An id queued as jumped (attempts 0) fails for the first time now: its first failure is now,
        not when it was queued (fourth review 22.11)."""
        with _guard():
            self._conn.execute(
                "INSERT INTO books_retry (company, stock_id, kind) VALUES (%s, %s, %s) ON CONFLICT (company, stock_id) "
                "DO UPDATE SET kind = EXCLUDED.kind, attempts = books_retry.attempts + 1, last_failed_at = now(), "
                "first_failed_at = CASE WHEN books_retry.attempts = 0 THEN now() ELSE books_retry.first_failed_at END",
                (company, stock_id, kind),
            )

    def clear_retry(self, company: str, stock_id: int) -> None:
        with _guard():
            self._conn.execute("DELETE FROM books_retry WHERE company = %s AND stock_id = %s", (company, stock_id))

    def held_ids(self, company: str) -> list:
        with _guard():
            return [r[0] for r in self._conn.execute(
                "SELECT stock_id FROM books_documents WHERE company = %s AND gone_at IS NULL ORDER BY stock_id",
                (company,)).fetchall()]

    def high_water(self, company: str) -> Optional[int]:
        with _guard():
            return self._conn.execute(
                "SELECT max(stock_id) FROM books_documents WHERE company = %s", (company,)
            ).fetchone()[0]

    # ---- one document -----------------------------------------------------------------------------
    def save(self, company: str, run_id: int, doc: dict, lines: list, jumped: Iterable[int] = (),
             held: bool = True) -> str:
        """'added', 'changed' or 'unchanged'. One transaction: the document, its lines, the event, its own
        books_retry row cleared, and `jumped` - the ids below it that the run has not read - put in books_retry,
        so no crash between the two leaves them unread for good (third review 22.11, finding 4). A held document
        read again unchanged is one message first (`_UNCHANGED`); `held=False` - a document the run does not hold -
        skips that try, which would only cost a round trip."""
        jumped = list(jumped)
        if held and not jumped and self._unchanged(company, doc):
            return "unchanged"
        cols = ("company",) + DOCUMENT_COLUMNS + ("content_hash", "fields_seen")
        values = [company] + [doc.get(c) for c in DOCUMENT_COLUMNS] + [doc["content_hash"], doc["fields_seen"]]
        with _guard(), self._conn.transaction():
            cur = self._conn.cursor()
            cur.execute(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT}'")
            if jumped:
                cur.execute(_QUEUE, (company, jumped, "jumped"))
            cur.execute("DELETE FROM books_retry WHERE company = %s AND stock_id = %s", (company, doc["stock_id"]))
            cur.execute(
                f"SELECT content_hash, gone_at, missing_since, {', '.join(DOCUMENT_COLUMNS)} FROM books_documents "
                "WHERE company = %s AND stock_id = %s FOR UPDATE",
                (company, doc["stock_id"]),
            )
            row = cur.fetchone()
            if row is not None and (row[1] is not None or row[2] is not None):  # read again: not missing, not gone
                cur.execute(
                    "UPDATE books_documents SET gone_at = NULL, gone_confirmed_by = NULL, missing_since = NULL, "
                    "missing_seen_at = NULL WHERE company = %s AND stock_id = %s", (company, doc["stock_id"]))
                if row[1] is not None:
                    cur.execute(
                        "INSERT INTO books_document_events (company, stock_id, run_id, kind) VALUES (%s, %s, %s, 'reappeared')",
                        (company, doc["stock_id"], run_id))
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
                old = dict(zip(DOCUMENT_COLUMNS, row[3:]))
                fields = sorted(c for c in DOCUMENT_COLUMNS if _differs(old.get(c), doc.get(c)))
                cur.execute(f"SELECT {', '.join(LINE_COLUMNS)} FROM books_lines WHERE company = %s AND stock_id = %s",
                            (company, doc["stock_id"]))
                old_lines = sorted(line_key(dict(zip(LINE_COLUMNS, r))) for r in cur.fetchall())
                if old_lines != sorted(line_key(line) for line in lines):
                    fields.append("lines")
                sets = ", ".join(f"{c} = %s" for c in cols[1:])
                cur.execute(
                    f"UPDATE books_documents SET {sets}, last_read_at = now(), last_changed_at = now() "
                    "WHERE company = %s AND stock_id = %s",
                    values[1:] + [company, doc["stock_id"]],
                )
                cur.execute("DELETE FROM books_lines WHERE company = %s AND stock_id = %s", (company, doc["stock_id"]))
                kind = "status_changed" if "status" in fields else "changed"
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

    def _unchanged(self, company: str, doc: dict) -> bool:
        """True when the one message found the row unchanged and saved it; False sends the full save. An error -
        the timeout included - is the full save's own error (BooksStoreError with the SQLSTATE), after the block it
        left open is rolled back."""
        from psycopg import pq, sql

        query = sql.SQL(_UNCHANGED).format(
            timeout=sql.Literal(STATEMENT_TIMEOUT), company=sql.Literal(company), stock_id=sql.Literal(doc["stock_id"]),
            hash=sql.Literal(doc["content_hash"]))
        try:
            with _guard():
                cur = self._conn.execute(query)
                found = 0
                while True:
                    if cur.description is not None:  # the SELECT; BEGIN, SET and COMMIT carry no columns
                        found = cur.fetchone()[0]
                    if not cur.nextset():
                        break
        except BooksStoreError:
            if self._conn.info.transaction_status != pq.TransactionStatus.IDLE:
                with _guard():
                    self._conn.rollback()  # the error stopped the message before its COMMIT
            raise
        return bool(found)

    # ---- the tick's lists (B2) ---------------------------------------------------------------------
    def held_status(self, company: str) -> dict:
        """stock_id -> status of every document held and not gone."""
        with _guard():
            return dict(self._conn.execute(
                "SELECT stock_id, status FROM books_documents WHERE company = %s AND gone_at IS NULL", (company,)).fetchall())

    def canaries(self, company: str, n: int) -> list:
        """The oldest valid documents held, not missing and not waiting in books_retry: what a tick reads first to know
        WizCloud answers for this company (spec §4.2.4). Leaving out what failed keeps an unreadable document from being
        a canary for good (fourth review 22.11)."""
        with _guard():
            return [r[0] for r in self._conn.execute(
                "SELECT d.stock_id FROM books_documents d WHERE d.company = %s AND d.gone_at IS NULL AND d.missing_since IS NULL "
                f"AND d.status = {VALID_STATUS} AND NOT EXISTS (SELECT 1 FROM books_retry r WHERE r.company = d.company "
                "AND r.stock_id = d.stock_id) ORDER BY d.stock_id LIMIT %s", (company, n)).fetchall()]

    def draft_ids(self, company: str) -> list:
        """Every draft held, the one read longest ago first."""
        with _guard():
            return [r[0] for r in self._conn.execute(
                f"SELECT stock_id FROM books_documents WHERE company = %s AND gone_at IS NULL AND status = {DRAFT_STATUS} "
                "ORDER BY last_read_at, stock_id", (company,)).fetchall()]

    def recent_ids(self, company: str, window: int, limit: int) -> list:
        """Of the `window` highest ids held, the `limit` read longest ago: each is read again within window/limit
        ticks."""
        with _guard():
            return [r[0] for r in self._conn.execute(
                "SELECT stock_id FROM (SELECT stock_id, last_read_at FROM books_documents WHERE company = %s AND gone_at IS NULL "
                "ORDER BY stock_id DESC LIMIT %s) recent ORDER BY last_read_at, stock_id LIMIT %s",
                (company, window, limit)).fetchall()]

    def missing_due(self, company: str, after_s: float, limit: int) -> list:
        """Held documents that answered 'not found' and whose last miss is at least `after_s` old: read again to
        confirm (spec §4.2.5)."""
        with _guard():
            return [r[0] for r in self._conn.execute(
                "SELECT stock_id FROM books_documents WHERE company = %s AND gone_at IS NULL AND missing_since IS NOT NULL "
                "AND missing_seen_at <= now() - make_interval(secs => %s) ORDER BY missing_since, stock_id LIMIT %s",
                (company, after_s, limit)).fetchall()]

    def mark_missing(self, company: str, stock_id: int) -> Optional[tuple]:
        """A held document answered 'not found': (its status, seconds since its first miss), or None when it is not
        held (or gone already)."""
        with _guard():
            row = self._conn.execute(
                "UPDATE books_documents SET missing_since = coalesce(missing_since, now()), missing_seen_at = now() "
                "WHERE company = %s AND stock_id = %s AND gone_at IS NULL "
                "RETURNING status, extract(epoch FROM now() - missing_since)", (company, stock_id)).fetchone()
        return None if row is None else (row[0], float(row[1]))

    def mark_gone(self, company: str, stock_id: int, run_id: int, by: str) -> None:
        with _guard(), self._conn.transaction():
            cur = self._conn.cursor()
            cur.execute("UPDATE books_documents SET gone_at = now(), gone_confirmed_by = %s "
                        "WHERE company = %s AND stock_id = %s AND gone_at IS NULL", (by, company, stock_id))
            if cur.rowcount:
                cur.execute("INSERT INTO books_document_events (company, stock_id, run_id, kind) VALUES (%s, %s, %s, 'gone')",
                            (company, stock_id, run_id))

    def missing_summary(self, company: str, confirm_s: float, limit: int = STUCK_SAMPLE) -> dict:
        """Held documents that answered 'not found': missed once, and valid ones missed twice at least
        `confirm_s` apart, which stay in every figure until a person confirms them gone."""
        with _guard():
            rows = self._conn.execute(
                "SELECT stock_id, status, missing_seen_at - missing_since >= make_interval(secs => %s) FROM books_documents "
                "WHERE company = %s AND gone_at IS NULL AND missing_since IS NOT NULL ORDER BY stock_id",
                (confirm_s, company)).fetchall()
        confirmed = [r[0] for r in rows if r[2] and r[1] != DRAFT_STATUS]
        return {"missing": len(rows), "valid_confirmed": len(confirmed), "valid_confirmed_ids": confirmed[:limit]}

    def stuck_since(self, company: str, older_s: float) -> int:
        """Ids that failed again and whose first failure is at least `older_s` old."""
        with _guard():
            return self._conn.execute(
                "SELECT count(*) FROM books_retry WHERE company = %s AND attempts >= 2 "
                "AND first_failed_at <= now() - make_interval(secs => %s)", (company, older_s)).fetchone()[0]

    # ---- the weekly sweep's position ------------------------------------------------------------------
    def sweep_state(self, company: str) -> Optional[dict]:
        """Where the sweep stands. `ended`: the pass that began at pass_started_at has ended (fourth review 22.11: an
        id above the place where a pass ended is a new document, read by the frontier - not more of that pass)."""
        with _guard():
            row = self._conn.execute(
                "SELECT next_id, extract(epoch FROM now() - pass_started_at), last_pass_ended_at, passes, "
                "coalesce(last_pass_ended_at >= pass_started_at, false) FROM books_sweep WHERE company = %s",
                (company,)).fetchone()
        if row is None:
            return None
        return {"next_id": row[0], "pass_age_s": float(row[1]), "last_pass_ended_at": row[2], "passes": row[3],
                "ended": row[4]}

    def save_sweep(self, company: str, next_id: int, *, new_pass: bool, ended: bool) -> None:
        with _guard():
            self._conn.execute(
                "INSERT INTO books_sweep (company, next_id, last_pass_ended_at, passes) "
                "VALUES (%s, %s, CASE WHEN %s THEN now() END, CASE WHEN %s THEN 1 ELSE 0 END) "
                "ON CONFLICT (company) DO UPDATE SET next_id = EXCLUDED.next_id, "
                "pass_started_at = CASE WHEN %s THEN now() ELSE books_sweep.pass_started_at END, "
                "last_pass_ended_at = CASE WHEN %s THEN now() ELSE books_sweep.last_pass_ended_at END, "
                "passes = books_sweep.passes + CASE WHEN %s THEN 1 ELSE 0 END",
                (company, next_id, ended, ended, new_pass, ended, ended))

    def sweep_summary(self, company: str) -> Optional[dict]:
        state = self.sweep_state(company)
        if state is None:
            return None
        ended = state["last_pass_ended_at"]
        return {"next_id": state["next_id"], "pass_age_h": round(state["pass_age_s"] / 3600, 1), "passes": state["passes"],
                "pass_ended": state["ended"], "last_pass_ended_at": ended.isoformat(timespec="seconds") if ended else None}

    # ---- what /health and the status route say ---------------------------------------------------
    def last_run(self, company: str) -> Optional[dict]:
        with _guard():
            row = self._conn.execute(
                "SELECT run_id, trigger, started_at, finished_at, from_id, to_id, probed, found, added, changed, absent, "
                "failed, masked, far_hits, complete, ok, note, reasons FROM books_sync_runs WHERE company = %s "
                "ORDER BY run_id DESC LIMIT 1",
                (company,),
            ).fetchone()
        if row is None:
            return None
        keys = ("run_id", "trigger", "started_at", "finished_at", "from_id", "to_id", "probed", "found", "added",
                "changed", "absent", "failed", "masked", "far_hits", "complete", "ok", "note", "reasons")
        out = dict(zip(keys, row))
        for k in ("started_at", "finished_at"):
            out[k] = out[k].isoformat(timespec="seconds") if out[k] else None
        out["far_hits"] = list(out["far_hits"] or [])
        return out

    def matching_snapshot(self, company: str) -> dict:
        """What a rule that matches monday to the books reads (backlog 0301 A1), in ONE repeatable-read transaction, so a
        tick that saves between two statements cannot leave a hole in what is matched (review 23.11): every held
        document's number, type, status, day and total, and whether it answered 'not found'; whether a run ever
        completed, and whether the last finished one did and was ok (a run in progress is not judged); and how many
        ids wait in books_retry. Never a name, a card or a text field."""
        return _snapshot(self._conn, company, wide=False)

    def desk_snapshot(self, company: str, connect_timeout_s: int = DESK_CONNECT_TIMEOUT_S) -> dict:
        """What the desk shows of the books (backlog 0301 D3): matching_snapshot's documents and conditions, in the same ONE
        repeatable-read transaction, and with each document also its card (`account_key`, `account_name` - cleaned of ID
        numbers when it was written, spec §3.4) and its `total_excl`; and the last finished run's id and time, which the
        desk names its copy by. On a connection of its own, outside a run's lock: the desk reads, it never waits for a
        tick. Nothing of it is logged or returned anywhere but the desk's gated answer (CLAUDE.md rule 4)."""
        with _guard():
            conn = self._connect(autocommit=True, connect_timeout_s=connect_timeout_s)
            try:
                return _snapshot(conn, company, wide=True)
            finally:
                conn.close()

    def last_finished_run(self, company: str, connect_timeout_s: int = DESK_CONNECT_TIMEOUT_S) -> Optional[tuple]:
        """(run_id, finished_at) of the last finished run of this company, or None when none finished: the cheap question
        the desk asks before it reads the copy again (D3). On a connection of its own, like desk_snapshot, and bounded as
        desk_snapshot's statements are: a person's request waits for it, inside the desk's one lock (the review of dfa967b:
        unbounded, a query waiting behind a lock on books_sync_runs - a publish's schema change takes one - held the
        request and the lock for as long as the lock was held)."""
        with _guard():
            conn = self._connect(autocommit=True, connect_timeout_s=connect_timeout_s)
            try:
                with conn.transaction():
                    conn.execute(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT}'")
                    row = conn.execute(
                        "SELECT run_id, finished_at FROM books_sync_runs WHERE company = %s AND finished_at IS NOT NULL "
                        "ORDER BY run_id DESC LIMIT 1", (company,)).fetchone()
            finally:
                conn.close()
        return None if row is None else (row[0], row[1])

    def series(self, company: str) -> list:
        """Per document type: valid documents held, first and last number, and the numbers missing
        between them (spec §4.2.6: Israeli tax documents are numbered without gaps)."""
        with _guard():
            rows = self._conn.execute(
                "SELECT doc_type, doc_number, status FROM books_documents WHERE company = %s AND gone_at IS NULL",
                (company,),
            ).fetchall()
        return summarize_series(rows)


def _snapshot(conn, company: str, wide: bool) -> dict:
    """matching_snapshot's one repeatable-read transaction on `conn`. `wide` (the desk, D3) adds each document's card and
    total before VAT, and the last finished run's id and time; without it the answer is A1's, key for key."""
    cols = "stock_id, doc_type, doc_number, status, value_date, total_incl, missing_since IS NOT NULL"
    if wide:
        cols += ", total_excl, account_key, account_name"
    with _guard(), conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        if wide:  # a person's request waits for it: bounded, as a save is
            conn.execute(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT}'")
        rows = conn.execute(
            f"SELECT {cols} FROM books_documents WHERE company = %s AND gone_at IS NULL ORDER BY stock_id",
            (company,)).fetchall()
        waiting = conn.execute("SELECT count(*) FROM books_retry WHERE company = %s", (company,)).fetchone()[0]
        last = conn.execute(
            "SELECT complete, ok, reasons, run_id, finished_at FROM books_sync_runs WHERE company = %s "
            "AND finished_at IS NOT NULL ORDER BY run_id DESC LIMIT 1", (company,)).fetchone()
        ever = conn.execute(
            "SELECT EXISTS (SELECT 1 FROM books_sync_runs WHERE company = %s AND complete)", (company,)).fetchone()[0]
    documents = []
    for r in rows:
        doc = {"stock_id": r[0], "doc_type": r[1], "doc_number": r[2], "status": r[3], "value_date": r[4],
               "total_incl": r[5], "missing": bool(r[6])}
        if wide:
            doc.update(total_excl=r[7], account_key=r[8], account_name=r[9])
        documents.append(doc)
    out = {"documents": documents, "retry_waiting": waiting, "last_run_complete": bool(last and last[0]),
           "last_run_ok": bool(last and last[1]), "last_run_reasons": list(last[2] or []) if last else [],
           "ever_completed": bool(ever)}
    if wide:
        out.update(last_run_id=last[3] if last else None, last_run_finished_at=last[4] if last else None)
    return out


# Why a copy cannot be matched against now (spec §6.1.5: only a whole copy changes what is shown), in this order. A1's plan
# (app/main.py) and the desk (app/desk/books.py, D3) judge by this one rule.
NOT_READY = ("never_completed", "last_run_incomplete", "last_run_not_ok", "retry_waiting", "holes")


def not_ready(snapshot: dict) -> Optional[str]:
    """Why the books copy cannot be matched against now, or None, judged on the very snapshot the rule matches
    (matching_snapshot, desk_snapshot): no run ever completed; the last finished run did not (cut short, crashed, or a read
    failed), or was not ok (with `canary` no "not found" was trusted, so a deleted document would still be matched - second
    review 23.11); ids wait in books_retry; or a series of the documents read has a hole (review 23.11: 'ever completed'
    let a copy cut short since then be matched)."""
    if not snapshot["ever_completed"]:
        return "never_completed"
    if not snapshot["last_run_complete"]:
        return "last_run_incomplete"
    if not snapshot["last_run_ok"]:
        return "last_run_not_ok"
    if snapshot["retry_waiting"]:
        return "retry_waiting"
    held = [(d["doc_type"], d["doc_number"], d["status"]) for d in snapshot["documents"]]
    if any(x["holes"] for x in summarize_series(held)):
        return "holes"
    return None


def record_crash(url: str, company: str, trigger: str, note: str, connect_timeout_s: int = 3) -> Optional[bool]:
    """Best effort, on a connection of its own, for a tick that crashed before its run's row was written: a finished
    row that says so, so the next tick knows to say the way back (fourth review 22.11). Returns whether the last
    finished run said otherwise (an alert), or None when the database could not be reached."""
    import psycopg

    try:
        with psycopg.connect(url, autocommit=True, connect_timeout=connect_timeout_s) as conn:
            row = conn.execute(
                "SELECT reasons FROM books_sync_runs WHERE company = %s AND finished_at IS NOT NULL ORDER BY run_id DESC LIMIT 1",
                (company,)).fetchone()
            conn.execute(
                "INSERT INTO books_sync_runs (company, trigger, finished_at, complete, ok, note, reasons) "
                "VALUES (%s, %s, now(), false, false, %s, %s)", (company, trigger, note, ["crashed"]))
        return row is None or list(row[0] or []) != ["crashed"]
    except Exception:  # noqa: BLE001 - best effort: the caller keeps the crash in memory instead
        return None


def summarize_series(rows) -> list:
    by_type: dict = {}
    for doc_type, number, status in rows:
        entry = by_type.setdefault(doc_type, {"valid": [], "drafts": 0, "other_status": 0, "not_numeric": 0})
        if status == VALID_STATUS:
            text = str(number) if number is not None else ""
            if text.isascii() and text.isdigit():
                entry["valid"].append(int(text))
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
        # Holes as the gaps between consecutive numbers: no set of the whole range (review 22.11 - one
        # mistyped number made it gigabytes on a public URL).
        hole_count, holes = 0, []
        for a, b in zip(unique, unique[1:]):
            gap = b - a - 1
            if gap > 0:
                hole_count += gap
                room = HOLES_SAMPLE - len(holes)
                if room > 0:
                    holes.extend(range(a + 1, a + 1 + min(gap, room)))
        out.append({
            "doc_type": doc_type,
            "role": role(doc_type),
            "name": INFERRED.get(doc_type, (None, None))[1] if isinstance(doc_type, int) else None,
            "valid": len(numbers),
            "first": unique[0] if unique else None,
            "last": unique[-1] if unique else None,
            "holes": hole_count,
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
