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
- **The books copy (B1) and its tick (B2):** `app/books/` (clean, parse, store, sync). A tick is `POST /api/books/tick`
  with `Authorization: Bearer <BOOKS_SYNC_KEY>` (Replit Secret; Make's keychain), boxed at 60 s from the request's
  arrival, always 200 with `ok`, `reasons`, `alert`, a Hebrew `message` and the `email` Make sends to Nadav (ADR-0178's
  shape; `?alert_test=1` is a test alert that reads and writes nothing); the phases are in
  `app/books/sync.py`'s docstring. The detail is `GET /api/books/status` behind the same key. The public `/health`
  says only whether the last run worked and why not, how many numbers are missing and whether the key is `ok` - no
  count and no number of documents (Nadav 19.11: the deployment is public). `books-check.yml` (inputs `company`,
  `box_s`) runs a copy, a full re-read and a steady tick into a throwaway Postgres inside the job and prints counts
  only (`scripts/books_check.py`): series, what the cleaning cost, id gaps, what a tick reads in its box.
  **Demo holds no documents** (every id probed, 22.11). The cleaning judges a number whole (`app/books/clean.py`'s
  docstring is the rule); a new text field goes through it, and a new number shape comes with its test.
- **The reconciliation's reading of monday (R0):** `app/monday/client.py` reads only (a query that does not start with
  `query` is refused, and so is a job that asks for a `NEVER_READ` column: ID numbers, a sole proprietor's business number,
  bank details; moved there from `app/recon/reading.py` in D1); `app/recon/reading.py` holds the profile and `monday_side`, the
  guard every rule that matches the books to monday goes through.
  `MONDAY_TOKEN` is a Replit Secret, reported as set or not.
- **The actions (A1):** `app/recon/receipt_dates.py`, and `POST /api/actions/plan` / `POST /api/actions/verify` behind the sync
  key. They are the dry run and the read-back of a class-A fill (spec §7). The service never writes the charge boards: a session
  writes through the monday connector after Nadav's go, and then calls verify. A plan runs only on a whole reading (R0) and on a
  whole copy (`BooksStore.matching_snapshot`, one repeatable-read snapshot): the last finished run complete and ok, nothing in
  `books_retry`, and no hole in what is matched. A rule's bucket order is `BUCKETS`.
- **The desk (D1, 24.11):** `app/desk/` and `frontend/`. The page `/desk/` is the Board View "כספים", a second feature of
  analiza-metrics' monday app (the desk move plan §2, option A), so `MONDAY_CLIENT_SECRET` (a Replit Secret, reported as set
  or not) is the value analiza-metrics holds. `/api/desk` verifies the sessionToken (`app/desk/auth.py`; Analiza's account
  is pinned and nothing unpins it), refuses a guest, and gates each tab by membership of its charge board, fail closed
  (`app/desk/gate.py`). It answers counts only. The desk's reading (`app/desk/reading.py`) has the old `read()`'s shape for
  D2; the reader refuses every `NEVER_READ` column for every caller (an ID number, a bank account, an account's business
  number, and every mirror of one: three, read live 24.11), keeps only the cells it asked for, and a linked row answered
  from another board is a stranger, counted and dropped. The SDK is analiza-metrics' copy (its sha256 is pinned by a test). uvicorn's access line never carries a sessionToken (`NoTokenInAccessLog`).
- **The desk's page (D2):** `frontend/finance.js` and `finance.css` are analiza-metrics' (da671b2), ported faithfully (what
  changed is listed at the top of `finance.js`); `frontend/desk.js` mounts them (the SDK, the sessionToken, `monday` whenever
  monday supplied a context, `board` only for the two charge boards), and `/desk/?check=1` draws D1's self-check
  (`frontend/check.js`). The page calls `/api/desk` paths only, never one under `/api/finance`, and every monday request goes
  through finance.js's one door (`mondayCall`; its one mutation in `mondayWrite`, behind `WRITABLE`). What the server never
  reads (an ID number, a refund's bank account, a company's ח.פ) is read there as the person, only when shown, kept in the
  page's memory and sent nowhere. Tests: `tests/test_desk_page.py` (node) and `tests/test_desk_browser.py` (Playwright).
- **The desk's usage (D2):** `POST /api/usage` keeps what the page sends in `desk_events` (`app/desk/store.py`; `migrations/005`
  is analiza-metrics' 004 statement for statement, so D5 imports that history row for row): closed words and counts, checked
  by `app/desk/usage.py` (analiza-metrics' vocabulary, ported faithfully), never a name, an amount, a search's text or an ID
  number. The sessionToken only, under the token's user id, with a limit of its own apart from the desk's budget. The table
  is never made at runtime: a missing one is `store_schema`, 503 `usage_unavailable`, said in `/health` `desk.usage`. A kind
  or a detail changes in the page and the vocabulary together (`tests/test_desk_usage.py`); the queries: `docs/usage-data.md`.
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
     is `[]` (read at most every 15 s). Read the tables, not `store.migrations`: what the publish does with the
     ledger's rows is not settled (after the first publish, 19.11, production held 001's row).
- **Replit Agent edits `.replit` and pushes it to `main`** (19.11, `c37bf9b`, on import: Python 3.12, nix 25_05).
  The Repl runs Python 3.12; CI and live-check follow it.
