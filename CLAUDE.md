# CLAUDE.md - analiza-finance

The home of finance at Analiza (renamed from `finance-sync` on 2026-11-19): the Hashavshevet (WizCloud) books, read only;
the reconciliation against monday as tested code; the gaps as items and the gated actions on monday; and Chava's finance
desk, moving here from analiza-metrics. Its docs, decisions and backlog live in the docs repo `nadav-shpitzik/Analiza-Monday`:
the service page `docs/services/analiza-finance/README.md`, ADR-0185 (this home) and ADR-0176 (read-only books), the spec
`docs/design/analiza-finance-spec.md` with its two companions (the reconciliation rules, the desk move plan), and the build
in slices `docs/backlog/0301-analiza-finance-build.md`. Boot from there for anything beyond this repo. Talk to Nadav in Hebrew.

**Nadav's go on the spec: 19.11. The build follows the slices of backlog 0301.** S0 (the home: the name, the build from
GitHub `main`, Postgres, the saved session, the test of every documented path) is in the code; its acceptance is read on
the deployment's `/health` and `/check` (0301 says where it stands).

## Hard rules

1. **Read-only toward the books.** `READ_PATHS` in `app/wizcloud.py` is the only way out, matched
   exactly. Never add a path that creates, issues, deletes or imports anything, and never register,
   change or delete a webhook (`TriggersApi/setURL|updateURL|deleteURL`) from code. Any of those is a
   new ADR in the docs repo plus Nadav's explicit go, and is tried on the Demo company first.
2. **The private key travels in the createSession URL.** Never log, print, return or put in an
   exception a URL or a request object. The `httpx` and `httpcore` loggers stay at WARNING. Errors
   carry a `kind` from the closed `KINDS` list plus a redacted, truncated message. **A session string
   carries all of Chava's rights for 24 hours:** it is saved only sealed (`app/store.py`, AES-GCM under a
   key derived from the private key), never printed or exported, and `wiz_sessions` is never granted to
   another service's role. `DATABASE_URL` carries a password: reported as set or not, errors by class.
3. **Demo first.** `WIZ_COMPANY=demo` (`wizdb2245n4`) is the default. A new call or report runs on
   Demo before `analiza` (`wizdb2245n5`).
4. **No data from the books in git or in chat.** Report output carries names, ID numbers and
   salaries. Write it under `exports/` (ignored) or to the database, show shapes and counts, not
   rows. `.gitignore` blocks csv/xlsx. **An ID number is never stored server side** (ADR-0173 §1,
   ADR-0171 §1): text from the books is cleaned of digit runs before it is written (spec §3.4).
5. **A success shape is unverified until a live answer is seen.** Code that reads a response it has
   never seen says so in `/check` (what shape it found), never `except: pass`.
6. **A reconciliation rule exists only with its test** (the rules file, §0). A name is only ever a
   candidate; a match is made by a cited number or a link.
7. **Every write to monday: dry run, approval, write, read-back** (spec §7). The server writes only the
   four billing columns on Accounts; the charge boards are written by the person in the browser, or by
   a session through the monday connector after Nadav's go in chat.
8. **A Postgres schema change goes to the development database first, from the Shell**, then a
   Republish whose SQL is read (the norm in the docs repo's CLAUDE.md). **The deployment never changes its own
   schema** - no migration at startup, no `CREATE ... IF NOT EXISTS` at first use - so production's schema comes
   only from the publish and can never run ahead of development (`app/migrate.py` says why; review 19.11).

## Working here

- Tests: `pip install -r requirements-dev.txt && python -m pytest -q`; the store's SQL runs against a real,
  empty Postgres when `TEST_DATABASE_URL` is set (CI: `tests.yml`, Postgres 16, every push to `main`). Every path
  the vendor documents is in `DOCUMENTED_PATHS` (`tests/test_wizcloud.py`: 60 as the vendor writes them, read 19.11
  from both docs versions with their JS and screenshots, five SwaggerHub specs and 16 npm client versions) and refused
  unless it is in `READ_PATHS`; a path the vendor adds goes there. Read the docs with broad patterns: the vendor
  writes `docsapi/...`, `cancelKabala/cancel` and `inv_issue/restoreDoc` too (an `...Api/` regex missed them).
- `WIZ_PRIVATE_KEY` lives only in Replit Secrets (workspace and deployment) and in this repo's Actions secret
  for live-check. Locally, tests use a
  fake transport; a live local run takes a placeholder key and proves only the error path.
- Live check without Replit: dispatch `live-check.yml` (inputs `company`, `doc_ids`, `session_test`), read the job
  log. It prints shapes and non-personal values only; keep it that way (`SAFE_FIELDS` in `scripts/live_check.py`).
- Deploy: Replit Autoscale. The build ships GitHub `main` (`scripts/build.sh` runs `scripts/deploy_source.py`,
  a byte-identical copy of the generic one: change it in every repo or none); `/health` shows `build.source`
  and `build.sha`. A code-only landing: `main`, then Republish (`/deploy` once the Repl is in its table).
- **A change that touches `migrations/` or `app/migrate.py`** ships in this order, each step said to Nadav as its
  own step, with the Shell command itself (the norm in analiza-metrics' CLAUDE.md, the same trap):
  1. Land on `main`.
  2. In the Repl's **Shell** (hand Nadav the command, never only the workflow's name, Nadav 19.11):
     `bash scripts/sync-from-github.sh && pip install -q -r requirements.txt && python3 -m app.migrate` - the
     same as the workflow Sync from GitHub. Expect `Now at: <main sha7>`, then `database: helium/...`
     (development), and last the `ok:` line.
  3. **Republish in the Replit UI**, reading the SQL it shows: only `CREATE` / `ADD`; any `DROP` is Cancel.
     A publish from the chat refuses any schema difference.
  4. `/health`: `build.sha` is main's, the new table is `"present"` in `store.tables`, and `store.missing_columns`
     is `[]` (read at most every 15 s). In production `store.migrations` stays empty: the publish copies the schema,
     not the ledger's rows.
