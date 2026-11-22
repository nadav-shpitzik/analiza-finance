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


# A held document read again unchanged, in one statement: its last read moves and its books_retry row goes - what the
# full save does for it, in one round trip instead of six (22.11 on the deployment: about 0.24 s a save, more than the
# read, so the steady tick's fixed phases filled the box and the weekly sweep never began). Only a row that is neither
# missing nor gone and has the same hash; and its lock is taken NOWAIT, so a row held elsewhere falls to the full save,
# which waits within its statement timeout - this one has none (autocommit, no SET LOCAL).
_UNCHANGED = (
    "WITH hit AS (SELECT stock_id FROM books_documents WHERE company = %(company)s AND stock_id = %(stock_id)s "
    "AND content_hash = %(hash)s AND gone_at IS NULL AND missing_since IS NULL FOR UPDATE NOWAIT), "
    "upd AS (UPDATE books_documents d SET last_read_at = now() FROM hit "
    "WHERE d.company = %(company)s AND d.stock_id = hit.stock_id RETURNING d.stock_id), "
    "cleared AS (DELETE FROM books_retry r USING upd WHERE r.company = %(company)s AND r.stock_id = upd.stock_id) "
    "SELECT count(*) FROM upd")


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
        read again unchanged is one statement first (`_UNCHANGED`); `held=False` - a document the run does not hold -
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
        """True when the one statement found the row unchanged and did the save; False sends the full save."""
        import psycopg.errors

        with _guard():
            try:
                row = self._conn.execute(
                    _UNCHANGED, {"company": company, "stock_id": doc["stock_id"], "hash": doc["content_hash"]}).fetchone()
            except psycopg.errors.LockNotAvailable:
                return False  # held elsewhere: the full save waits for it, within its statement timeout
        return bool(row[0])

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

    def series(self, company: str) -> list:
        """Per document type: valid documents held, first and last number, and the numbers missing
        between them (spec §4.2.6: Israeli tax documents are numbered without gaps)."""
        with _guard():
            rows = self._conn.execute(
                "SELECT doc_type, doc_number, status FROM books_documents WHERE company = %s AND gone_at IS NULL",
                (company,),
            ).fetchall()
        return summarize_series(rows)


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
