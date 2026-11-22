"""A manual, read-only check of the books copy against WizCloud (backlog 0301, B1's acceptance), run by
.github/workflows/books-check.yml into a throwaway Postgres that lives only inside the job.

Two runs: the first copies the company's documents; the second re-reads every document held and must find
each one unchanged, add nothing, and fail no read (review 22.11: a second run that reads nothing proves
nothing). Printed:
the runs' counts, the series (document type, valid documents, first and last number, holes and up to ten
missing numbers), how many text fields were masked and by which column, what masking cost (numbers masked,
references lost, by reason), the gaps between held ids (to judge the frontier's window of 50), the longest
text kept per column, and the field names the vendor sent. Never a name, a text, an amount, a date or a card.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config, migrate  # noqa: E402
from app.books import sync  # noqa: E402
from app.books.store import BooksStore  # noqa: E402
from app.wizcloud import WizClient  # noqa: E402

RUN_KEYS = ("from_id", "to_id", "probed", "found", "added", "changed", "unchanged", "absent", "failed", "masked",
            "far_hits", "retried", "reread", "held_absent", "queued", "stuck", "complete", "ok", "note", "elapsed_s",
            "masked_fields", "clean")
TEXT_COLUMNS = ("account_name", "account_key", "reference", "remarks", "details", "currency", "batch")


def main() -> int:
    cfg = config.load()
    if not cfg.ok or not cfg.database_url:
        print(json.dumps({"ok": False, "problems": list(cfg.problems) or ["DATABASE_URL is not set"]}))
        return 2
    migrate.apply_migrations(cfg.database_url)
    box_s = float(os.environ.get("BOX_S") or 2400)
    client = WizClient(cfg.server, cfg.db, cfg.private_key)
    store = BooksStore(cfg.database_url)
    runs = []
    for n in (1, 2):
        with store.session(cfg.db) as s:
            runs.append(sync.run_once(client, s, cfg.db, trigger=f"books-check-{n}", box_s=box_s, reread_held=n == 2))
    with store.session(cfg.db + ":status") as s:
        conn = s._conn
        series = s.series(cfg.db)
        retry = s.retry_summary(cfg.db, limit=0)
        counts = conn.execute(
            "SELECT count(*), count(*) FILTER (WHERE status = 0), count(*) FILTER (WHERE cardinality(refs_in_text) > 0), "
            "count(*) FILTER (WHERE email_domain IS NOT NULL), count(*) FILTER (WHERE tax_api_number_present) "
            "FROM books_documents WHERE company = %s", (cfg.db,)).fetchone()
        lines = conn.execute("SELECT count(*), count(base_move_id) FROM books_lines WHERE company = %s", (cfg.db,)).fetchone()
        fields = sorted(r[0] for r in conn.execute(
            "SELECT DISTINCT unnest(fields_seen) FROM books_documents WHERE company = %s", (cfg.db,)).fetchall())
        ids = [r[0] for r in conn.execute(
            "SELECT stock_id FROM books_documents WHERE company = %s ORDER BY stock_id", (cfg.db,)).fetchall()]
        longest = conn.execute(
            "SELECT " + ", ".join(f"coalesce(max(length({c})), 0)" for c in TEXT_COLUMNS)
            + ", count(*) FILTER (WHERE account_key LIKE '%%#%%') FROM books_documents WHERE company = %s",
            (cfg.db,)).fetchone()
        line_longest = conn.execute(
            "SELECT coalesce(max(length(details)), 0), coalesce(max(length(item_name)), 0), "
            "count(*) FILTER (WHERE item_key LIKE '%%#%%') FROM books_lines WHERE company = %s", (cfg.db,)).fetchone()
    first, second = runs
    accepted = bool(
        first["ok"] and first["complete"] and not first["failed"]
        and second["ok"] and second["complete"] and not second["failed"]
        and second["added"] == 0 and second["changed"] == 0
        and second["reread"] == counts[0] and second["unchanged"] == counts[0] and second["held_absent"] == 0
    )
    out = {
        "code_sha": os.environ.get("GITHUB_SHA"),
        "company": cfg.company,
        "accepted": accepted,
        "runs": [{k: r.get(k) for k in RUN_KEYS} for r in runs],
        "documents": {"held": counts[0], "drafts": counts[1], "citing_documents_in_text": counts[2],
                      "with_email_domain": counts[3], "with_tax_api_number": counts[4]},
        "lines": {"held": lines[0], "with_base_move_id": lines[1]},
        "series": series,
        "retry": {k: retry[k] for k in ("waiting", "by_kind", "stuck_count")},
        "id_gaps": _gaps(ids),
        "longest_text": dict(zip(TEXT_COLUMNS, longest[:-1]), **{"line.details": line_longest[0],
                                                                  "line.item_name": line_longest[1]}),
        "keys_masked": {"account_key": longest[-1], "line.item_key": line_longest[2]},
        "fields_seen": fields,
    }
    print(json.dumps(out, ensure_ascii=False, indent=1, default=str))
    return 0 if accepted else 1


def _gaps(ids: list) -> dict:
    """Runs of empty ids between held documents: the largest five and how many reach 10, 25 and 50."""
    gaps = sorted((b - a - 1 for a, b in zip(ids, ids[1:]) if b - a > 1), reverse=True)
    return {"first_id": ids[0] if ids else None, "largest": gaps[:5],
            **{f"at_least_{n}": sum(1 for g in gaps if g >= n) for n in (10, 25, 50)}}


if __name__ == "__main__":
    sys.exit(main())
