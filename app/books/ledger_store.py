"""What is kept of the ledger report, in Postgres (backlog 0301 B3; ADR-0187; migrations/007_ledger_totals.sql).

A month is replaced whole, in one transaction (ADR-0187 §4): its lines are deleted and the new ones inserted, its row in
ledger_months is overwritten, and the sort codes' names and the account keys' sort codes it carried are upserted. So a
rerun never doubles a line, and a line that left the books leaves the store. What is written is what
app/books/ledger_totals.py computed: lines of two accounts or more, keys the copy's cleaning keeps whole - never a row.

A run of one company holds a transaction-scoped advisory lock on a connection of its own (its own lock id: a ledger run
never waits for a tick, nor a tick for it), so a second run finds it held and is skipped. Errors become LedgerStoreError
with the class or SQLSTATE only, never the text: psycopg's can carry the URL, and a unique violation quotes the key.
"""

from __future__ import annotations

import hashlib
from contextlib import contextmanager
from datetime import date
from typing import Iterator, Optional

CONNECT_TIMEOUT_S = 10
STATEMENT_TIMEOUT_MS = 15000
MIN_STATEMENT_MS = 500
# What the writes name, per table. app/migrate.py checks the live schema against it.
LEDGER_TABLES = {
    "ledger_runs": ("run_id", "company", "trigger", "started_at", "finished_at", "months", "ok", "reasons", "note"),
    "ledger_months": ("company", "month", "row_count", "sort_codes", "kept_lines", "single_account", "single_sides",
                      "masked_keys", "batch_9998_rows", "batch_9998_debit", "batch_9998_credit", "parts", "run_id",
                      "computed_at"),
    "ledger_totals": ("company", "month", "sort_code", "debit", "credit", "row_count", "account_count", "masked_keys",
                      "run_id"),
    "ledger_sort_codes": ("company", "sort_code", "name", "run_id", "seen_at"),
    "ledger_accounts": ("company", "account_key", "sort_code", "run_id", "seen_at"),
}
# The words replace_month answers when it keeps the month as it was.
EMPTIED = "emptied"  # the store holds rows for the month and the books answered none: never trusted as an emptied month


class LedgerStoreError(Exception):
    def __init__(self, detail: str):
        self.detail = detail
        super().__init__(detail)


@contextmanager
def _guard() -> Iterator[None]:
    import psycopg

    try:
        yield
    except psycopg.Error as exc:
        raise LedgerStoreError(getattr(exc, "sqlstate", None) or type(exc).__name__) from None


def _connect(url: str, autocommit: bool = True, connect_timeout_s: int = CONNECT_TIMEOUT_S):
    import psycopg

    try:
        return psycopg.connect(url, autocommit=autocommit, connect_timeout=connect_timeout_s)
    except psycopg.Error:
        raise
    except Exception as exc:  # noqa: BLE001 - the class only: the text can carry the URL
        raise LedgerStoreError(type(exc).__name__) from None


def _lock_id(company: str) -> int:
    digest = hashlib.sha256(f"analiza-finance:ledger-run:{company}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


def first_day(month: str) -> date:
    """"YYYY-MM" as the first day of the month, the form every table keys a month by."""
    year, mon = month.split("-")
    return date(int(year), int(mon), 1)


def _statement_ms(budget_s: Optional[float]) -> int:
    if budget_s is None:
        return STATEMENT_TIMEOUT_MS
    return max(MIN_STATEMENT_MS, min(STATEMENT_TIMEOUT_MS, int(budget_s * 1000)))


class LedgerStore:
    def __init__(self, url: str):
        self._url = url
        self._conn = None

    def __repr__(self) -> str:
        return "LedgerStore()"

    @contextmanager
    def session(self, company: str) -> Iterator[Optional["LedgerStore"]]:
        """The store for one run, or None when another run of this company holds the lock (skipped)."""
        with _guard():
            lock = _connect(self._url, autocommit=False)
            try:
                got = lock.execute("SELECT pg_try_advisory_xact_lock(%s)", (_lock_id(company),)).fetchone()[0]
                if not got:
                    yield None
                    return
                self._conn = _connect(self._url, autocommit=True)
                try:
                    yield self
                finally:
                    self._conn.close()
                    self._conn = None
            finally:
                lock.rollback()  # ends the transaction, and with it the lock
                lock.close()

    # ---- the run log ------------------------------------------------------------------------------
    def start_run(self, company: str, trigger: str, months: list) -> int:
        with _guard():
            return self._conn.execute(
                "INSERT INTO ledger_runs (company, trigger, months) VALUES (%s, %s, %s) RETURNING run_id",
                (company, trigger, list(months))).fetchone()[0]

    def finish_run(self, run_id: int, ok: bool, reasons: list, note: Optional[str] = None) -> None:
        with _guard():
            self._conn.execute(
                "UPDATE ledger_runs SET finished_at = now(), ok = %s, reasons = %s, note = %s WHERE run_id = %s",
                (ok, list(reasons), note, run_id))

    # ---- one month, replaced whole ----------------------------------------------------------------
    def replace_month(self, company: str, run_id: int, month, budget_s: Optional[float] = None) -> Optional[str]:
        """Replace one month with `month` (ledger_totals.MonthResult, computed whole), in one transaction. None when it was
        replaced; EMPTIED when it was kept as it was: the store holds rows for the month and the answer had none - a
        whole month does not leave the books, so an empty answer then is a reading to doubt, not a fact (the canary's
        lesson, spec §4.2.4)."""
        day = first_day(month.month)
        lines = month.lines
        names = sorted(month.names.items())
        keys = sorted(month.keys.items())
        with _guard(), self._conn.transaction():
            cur = self._conn.cursor()
            cur.execute(f"SET LOCAL statement_timeout = {_statement_ms(budget_s)}")
            cur.execute("SELECT row_count FROM ledger_months WHERE company = %s AND month = %s FOR UPDATE", (company, day))
            held = cur.fetchone()
            if month.rows == 0 and held is not None and held[0] > 0:
                return EMPTIED
            cur.execute("DELETE FROM ledger_totals WHERE company = %s AND month = %s", (company, day))
            if lines:
                cur.execute(
                    "INSERT INTO ledger_totals (company, month, sort_code, debit, credit, row_count, account_count, "
                    "masked_keys, run_id) SELECT %s, %s, t.sort_code, t.debit, t.credit, t.row_count, t.account_count, "
                    "t.masked_keys, %s FROM unnest(%s::integer[], %s::numeric[], %s::numeric[], %s::integer[], "
                    "%s::integer[], %s::integer[]) AS t(sort_code, debit, credit, row_count, account_count, masked_keys)",
                    (company, day, run_id, [x.sort_code for x in lines], [x.debit for x in lines],
                     [x.credit for x in lines], [x.rows for x in lines], [x.accounts for x in lines],
                     [x.masked_keys for x in lines]))
            batch = month.batch
            cur.execute(
                "INSERT INTO ledger_months (company, month, row_count, sort_codes, kept_lines, single_account, single_sides, "
                "masked_keys, batch_9998_rows, batch_9998_debit, batch_9998_credit, parts, run_id) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (company, month) DO UPDATE SET "
                "row_count = EXCLUDED.row_count, sort_codes = EXCLUDED.sort_codes, kept_lines = EXCLUDED.kept_lines, "
                "single_account = EXCLUDED.single_account, single_sides = EXCLUDED.single_sides, "
                "masked_keys = EXCLUDED.masked_keys, batch_9998_rows = EXCLUDED.batch_9998_rows, "
                "batch_9998_debit = EXCLUDED.batch_9998_debit, batch_9998_credit = EXCLUDED.batch_9998_credit, "
                "parts = EXCLUDED.parts, run_id = EXCLUDED.run_id, computed_at = now()",
                (company, day, month.rows, month.sort_codes, len(lines), month.single_account, month.single_sides,
                 month.masked_keys, batch.rows, batch.debit, batch.credit, month.parts, run_id))
            if names:
                cur.execute(
                    "INSERT INTO ledger_sort_codes (company, sort_code, name, run_id) SELECT %s, t.sort_code, t.name, %s "
                    "FROM unnest(%s::integer[], %s::text[]) AS t(sort_code, name) ON CONFLICT (company, sort_code) DO UPDATE "
                    "SET name = COALESCE(EXCLUDED.name, ledger_sort_codes.name), run_id = EXCLUDED.run_id, seen_at = now()",
                    (company, run_id, [c for c, _ in names], [n for _, n in names]))
            if keys:
                cur.execute(
                    "INSERT INTO ledger_accounts (company, account_key, sort_code, run_id) SELECT %s, t.account_key, "
                    "t.sort_code, %s FROM unnest(%s::text[], %s::integer[]) AS t(account_key, sort_code) "
                    "ON CONFLICT (company, account_key) DO UPDATE SET sort_code = EXCLUDED.sort_code, "
                    "run_id = EXCLUDED.run_id, seen_at = now()",
                    (company, run_id, [k for k, _ in keys], [c for _, c in keys]))
        return None


def _money(value) -> Optional[str]:
    """A total as exact text ("48213.77"), never a float (spec §2.1); None stays None (a side withheld)."""
    return None if value is None else format(value, "f")


def read_totals(url: str, company: str, first: str, last: str, connect_timeout_s: int = CONNECT_TIMEOUT_S) -> dict:
    """The kept lines of the months from `first` to `last` ("YYYY-MM"), each with its sort code's name, and each month's
    counts and batch 9998 - in ONE repeatable-read transaction, on a connection of its own (a read never waits for a run's
    lock). Category totals only: a line of one account was never stored, and a side one account alone fills is None."""
    with _guard():
        conn = _connect(url, autocommit=True, connect_timeout_s=connect_timeout_s)
        try:
            with conn.transaction():
                conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                conn.execute(f"SET LOCAL statement_timeout = {STATEMENT_TIMEOUT_MS}")
                lines = conn.execute(
                    "SELECT to_char(t.month, 'YYYY-MM'), t.sort_code, s.name, t.debit, t.credit, t.row_count, "
                    "t.account_count, t.masked_keys FROM ledger_totals t LEFT JOIN ledger_sort_codes s "
                    "ON s.company = t.company AND s.sort_code = t.sort_code WHERE t.company = %s AND t.month BETWEEN %s AND %s "
                    "ORDER BY t.month, t.sort_code", (company, first_day(first), first_day(last))).fetchall()
                months = conn.execute(
                    "SELECT to_char(month, 'YYYY-MM'), row_count, sort_codes, kept_lines, single_account, single_sides, "
                    "masked_keys, batch_9998_rows, batch_9998_debit, batch_9998_credit, parts, run_id, computed_at "
                    "FROM ledger_months WHERE company = %s AND month BETWEEN %s AND %s ORDER BY month",
                    (company, first_day(first), first_day(last))).fetchall()
        finally:
            conn.close()
    return {
        "lines": [{"month": m, "sort_code": code, "name": name, "debit": _money(debit), "credit": _money(credit),
                   "rows": rows, "accounts": accounts, "masked_keys": masked}
                  for m, code, name, debit, credit, rows, accounts, masked in lines],
        "months": {m: {"rows": rows, "sort_codes": codes, "kept_lines": kept, "single_account": single,
                       "single_sides": sides, "masked_keys": masked, "batch_9998_rows": b_rows,
                       "batch_9998_debit": _money(b_debit), "batch_9998_credit": _money(b_credit), "parts": parts,
                       "run_id": run_id, "computed_at": at.isoformat(timespec="seconds") if at else None}
                   for m, rows, codes, kept, single, sides, masked, b_rows, b_debit, b_credit, parts, run_id, at in months},
    }


def glance(cur, company: str) -> dict:
    """For the public /health, on its cursor: the last run's times, whether it was ok and why not (closed words), and the
    months held. Never a total, never a count of rows or lines."""
    cur.execute("SELECT started_at, finished_at, ok, reasons FROM ledger_runs WHERE company = %s ORDER BY run_id DESC LIMIT 1",
                (company,))
    row = cur.fetchone()
    cur.execute("SELECT to_char(month, 'YYYY-MM') FROM ledger_months WHERE company = %s ORDER BY month", (company,))
    months = [r[0] for r in cur.fetchall()]
    last = None if row is None else {
        "started_at": row[0].isoformat(timespec="seconds") if row[0] else None,
        "finished_at": row[1].isoformat(timespec="seconds") if row[1] else None, "ok": row[2], "reasons": list(row[3] or [])}
    return {"last_run": last, "months": months}
