"""Apply migrations/ to the DEVELOPMENT database: `python3 -m app.migrate` in the Repl's Shell (also the
last step of the workflow Sync from GitHub). The deployment never runs it. Adapted on 2026-11-19 (slice S0
of backlog 0301) from analiza-metrics' app/migrate.py, which carries the history.

Why the deployment never changes its own schema. A publish aligns production's schema to the development
database's, DROP included (the runbook replit-postgres-provisioning in the docs repo). analiza-metrics also
applies its migrations at startup, as a safety net; the review of 19.11 showed the cost: whenever the
development database misses a step (a Pull in the Git pane, or the Sync's migration failing), the build
still ships main - the files match - and a startup run creates the tables in production only. Every
Republish after that offers to DROP them, with their data. So here production's schema comes from one
place only, the publish, and a missed step shows instead as `store.missing_columns` in /health and a
`store_schema` error on /check, with nothing to lose. The build's `--schema` guard (scripts/build.sh)
still holds main's migrations back until the workspace has them.

The ledger is not the schema (metrics, 2026-09-03): after the ledger pass, the live columns are checked
against what the writes name (`app.store.CHECKED_TABLES`), every file is re-run once if they disagree
(each is written to be re-runnable), and what is still missing is reported. The whole run is one
transaction under a transaction-scoped advisory lock: it applies entirely or not at all, and a pooled
connection cannot keep the lock (or a SET) past it.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"
LEDGER = "schema_migrations"
# pg_advisory_xact_lock key for a migration run: a fixed number, this service's own.
MIGRATE_LOCK_ID = 7_301_000_001
MIGRATE_LOCK_TIMEOUT = "60s"
CONNECT_TIMEOUT_S = 10


def checked_tables() -> dict:
    """The tables and columns the writes name, read from the stores: a copy here would drift."""
    from .books.store import BOOKS_TABLES
    from .desk.dismissals_store import DISMISSAL_TABLES
    from .desk.store import DESK_TABLES
    from .desk.usage_store import USAGE_TABLES
    from .recon.store import RECON_TABLES
    from .store import CHECKED_TABLES

    return {**CHECKED_TABLES, **BOOKS_TABLES, **RECON_TABLES, **DESK_TABLES, **USAGE_TABLES, **DISMISSAL_TABLES}


def _live_columns(cur, table: str) -> set:
    cur.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_schema = current_schema() AND table_name = %s",
        (table,),
    )
    return {row[0] for row in cur.fetchall()}


def _missing(cur) -> list:
    """An absent table by its name; a missing column as table.column."""
    out: list = []
    for table, columns in checked_tables().items():
        have = _live_columns(cur, table)
        if not have:
            out.append(table)
        else:
            out += [f"{table}.{c}" for c in columns if c not in have]
    return out


def apply_migrations(url: str) -> dict:
    """Apply the pending files, verify, repair once, in one transaction. Returns {"applied", "skipped",
    "repaired", "missing"}; raises on a connection or SQL failure (and then nothing was applied)."""
    import psycopg

    applied: list = []
    skipped: list = []
    repaired: list = []
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    with psycopg.connect(url, connect_timeout=CONNECT_TIMEOUT_S) as conn, conn.cursor() as cur:
        cur.execute(f"SET LOCAL lock_timeout = '{MIGRATE_LOCK_TIMEOUT}'")
        cur.execute("SELECT pg_advisory_xact_lock(%s)", (MIGRATE_LOCK_ID,))
        cur.execute(
            f"CREATE TABLE IF NOT EXISTS {LEDGER} ("
            "filename text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
        )
        for path in files:
            cur.execute(f"SELECT 1 FROM {LEDGER} WHERE filename = %s", (path.name,))
            if cur.fetchone():
                skipped.append(path.name)
                continue
            cur.execute(path.read_text(encoding="utf-8"))
            cur.execute(f"INSERT INTO {LEDGER} (filename) VALUES (%s) ON CONFLICT DO NOTHING", (path.name,))
            applied.append(path.name)
        missing = _missing(cur)
        if missing:
            for path in files:
                cur.execute(path.read_text(encoding="utf-8"))
                repaired.append(path.name)
            missing = _missing(cur)
    return {"applied": applied, "skipped": skipped, "repaired": repaired, "missing": missing}


def schema_state(cur) -> dict:
    """For /health, on the caller's cursor: which tables are there, what the writes need and the
    database lacks, and which files the ledger records. Reads only."""
    tables = {t: ("present" if _live_columns(cur, t) else "absent") for t in (*checked_tables(), LEDGER)}
    recorded: list = []
    if tables[LEDGER] == "present":
        cur.execute(f"SELECT filename FROM {LEDGER} ORDER BY filename")
        recorded = [row[0] for row in cur.fetchall()]
    return {"tables": tables, "missing_columns": _missing(cur), "migrations": recorded}


def _failure(exc: BaseException) -> str:
    """A database error as its class and SQLSTATE, never its text: psycopg's message can carry the host,
    the user and, for a malformed URL, the password itself (review 19.11). A missing module is named,
    with the step that installs it: it reads like a database problem otherwise."""
    if isinstance(exc, ImportError):
        return f"{type(exc).__name__}: no module {exc.name!r} - run pip install -r requirements.txt first"
    code = getattr(exc, "sqlstate", None)
    return type(exc).__name__ + (f" (SQLSTATE {code})" if code else "")


def _target(url: str) -> str:
    """host/database, so a person in a Shell sees WHICH database is about to change (the workspace's
    is `helium`; the deployment has its own). Never the user or the password."""
    try:
        import psycopg

        with psycopg.connect(url, autocommit=True, connect_timeout=CONNECT_TIMEOUT_S) as conn, conn.cursor() as cur:
            cur.execute("SELECT current_database()")
            db = cur.fetchone()[0]
            host = conn.info.host or "?"
        return f"{host}/{db}"
    except Exception as exc:  # noqa: BLE001 - the identity is a courtesy, not the job
        return f"unknown ({_failure(exc)})"


def main() -> int:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        print("DATABASE_URL is not set: create the Repl's Postgres first (the Database tool)", file=sys.stderr)
        return 2
    print(f"database: {_target(url)}")
    print("note: this is the database THIS SHELL sees (development, in the workspace). The deployment's")
    print("      schema follows it at the next Republish, whose SQL you read: CREATE / ADD only.")
    try:
        result = apply_migrations(url)
    except Exception as exc:  # noqa: BLE001 - said in one safe line, nothing applied
        print(f"FAILED: {_failure(exc)}. Nothing was applied (one transaction).", file=sys.stderr)
        return 1
    for name in result["skipped"]:
        print(f"skip     {name} (recorded)")
    for name in result["applied"]:
        print(f"applied  {name}")
    for name in result["repaired"]:
        print(f"repaired {name} (recorded as applied, but the schema disagreed)")
    if result["missing"]:
        print("MISSING after repair: " + ", ".join(result["missing"]), file=sys.stderr)
        print("The migrations do not add every column the writes need.", file=sys.stderr)
        return 1
    tables = list(checked_tables())
    print(f"ok: {', '.join(tables)} {'carries' if len(tables) == 1 else 'carry'} every column the writes need")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
