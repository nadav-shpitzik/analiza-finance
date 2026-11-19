"""Apply migrations/ in order: `python3 -m app.migrate` in the Repl's Shell (the development database),
and at startup in the deployment (the production one). Adapted on 2026-11-19 (slice S0 of backlog 0301)
from analiza-metrics' app/migrate.py, which carries the history; the parts that matter here:

- A publish aligns production's schema to the development database's, DROP included (the runbook
  replit-postgres-provisioning in the docs repo). So a schema change reaches the development database
  first, from the Shell, and only then a Republish whose SQL is read: only CREATE / ADD goes ahead.
  The build refuses main's migrations until the workspace has them (`--schema` in scripts/build.sh),
  so the startup run never puts production ahead of development.
- The ledger is not the schema (metrics, 2026-09-03). After the ledger pass, the live columns are
  checked against what the writes name (`app.store.CHECKED_TABLES`), every file is re-run once if they
  disagree (each is written to be re-runnable), and what is still missing is reported, not raised, so
  the service starts and /health says what is wrong.
- Two autoscale cold starts at once run one after the other, under an advisory lock.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"
LEDGER = "schema_migrations"
# pg_advisory_lock key for a migration run: a fixed number, this service's own.
MIGRATE_LOCK_ID = 7_301_000_001
MIGRATE_LOCK_TIMEOUT = "60s"
CONNECT_TIMEOUT_S = 10


def checked_tables() -> dict:
    """The tables and columns the writes name, read from the store: a copy here would drift."""
    from .store import CHECKED_TABLES

    return CHECKED_TABLES


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
    """Apply the pending files, verify, repair once. Returns {"applied", "skipped", "repaired",
    "missing"}; raises only on a connection or SQL failure."""
    import psycopg

    applied: list = []
    skipped: list = []
    repaired: list = []
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    # The lock is the session's: closing the connection at the end of the block releases it, also
    # when a statement failed. Waiting longer than MIGRATE_LOCK_TIMEOUT raises, and the startup says so.
    with psycopg.connect(url, autocommit=True, connect_timeout=CONNECT_TIMEOUT_S) as conn, conn.cursor() as cur:
        cur.execute(f"SET lock_timeout = '{MIGRATE_LOCK_TIMEOUT}'")
        cur.execute("SELECT pg_advisory_lock(%s)", (MIGRATE_LOCK_ID,))
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


def schema_state(url: str) -> dict:
    """For /health: which tables are there, what the writes need and the database lacks, and which
    files the ledger records. Reads only. Raises on a connection failure."""
    import psycopg

    with psycopg.connect(url, autocommit=True, connect_timeout=CONNECT_TIMEOUT_S) as conn, conn.cursor() as cur:
        tables = {t: ("present" if _live_columns(cur, t) else "absent") for t in (*checked_tables(), LEDGER)}
        recorded: list = []
        if tables[LEDGER] == "present":
            cur.execute(f"SELECT filename FROM {LEDGER} ORDER BY filename")
            recorded = [row[0] for row in cur.fetchall()]
        missing = _missing(cur)
    return {"tables": tables, "missing_columns": missing, "migrations": recorded}


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
        return f"unknown ({type(exc).__name__})"


def main() -> int:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        print("DATABASE_URL is not set: create the Repl's Postgres first (the Database tool)", file=sys.stderr)
        return 2
    print(f"database: {_target(url)}")
    print("note: this is the database THIS SHELL sees. The deployment has its own and")
    print("      migrates it itself on startup, after a Republish.")
    result = apply_migrations(url)
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
