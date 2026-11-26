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
7. **Every write to monday: dry run, approval, write, read-back** (spec §7). **The server writes nothing to monday**
   (ADR-0188, since D2): the desk's writes - the charge boards' allowlisted columns and a customer's four billing
   details - are the person's own, in the browser through `monday.api` (`mondayWrite`, behind `WRITABLE`, held identical
   to the server's `desk_writes`); a session writes through the monday connector after Nadav's go in chat.
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
- **The ledger report (B3, 26.11; ADR-0187):** `app/books/ledger.py`. The definition is the External file of "כרטסת
  לאנליזה", the whole JSON as the Replit Secret `WIZ_LEDGER_REPORT`, never in git; `load_definition` finds its four parameters
  by name and `opOrigin`, never by position, and refuses in a closed word (`REASONS`). `month_parameters` sends the file's
  entries whole with only `defVal` changed, the shape the vendor documents (wizcloud-api's docx, all 16 versions): a sort code
  bare, by default the whole range; a date as `'YYYY-MM-DD'`, the file's own form, **a hypothesis the first live answer
  settles**. `describe` says an answer's shape only: keys and types, the row count and `hit_limit`, the columns carried and
  missing, distinct sort codes and months, rows outside the month asked, whether debit and credit are numeric. A key that is
  neither the definition's nor a plain field name is counted, never said, and `tests/test_ledger.py` scans every output for
  every fixture value (and proves the scan sees a leak). The probe is `POST /api/books/ledger/probe` behind the sync key
  (checked before the body is read), `{"month": "YYYY-MM", "company": ...}`: always 200 after the key, with an `error` from
  `PROBE_ERRORS`, boxed like a tick, nothing of the answer kept or logged. **The deployment reaches only `WIZ_COMPANY`**
  (`other_company`; one session, the tick's), so Demo is probed from the Repl's Shell on a local server (the README has
  both commands). `/health` `books.ledger_report`: `set`, `missing` or `bad`, never a field. Nothing is kept yet; no schema
  change.
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
- **FR-C2, the invoice number (D3a, 26.11):** `app/recon/ref_fill.py`, the kind `b2c_ref_fill` on the same two routes. A row
  whose status says money came in (A1's MONEY_IN), with an empty "אסמכתת חשבונית", a readable amount and a day in 2026 (its
  "תאריך תקבול", else the day it was made, Israel's time) is proposed one number: the one uncited invoice-receipt of 2026
  (type 2 only; a tax invoice is counted, `tax_invoice_waits`) on a students' card (D3's side logic) whose card's name
  equals the row's as a set of words (the row's before its first "|", `person_name`: the helper's balance payment
  "<student> | השלמת תשלום" is the student's; a card's name is whole), whose total is within 1 ₪ and whose date is within
  30 days of the row's day (`FILL_WINDOW_DAYS`; farther is `date_far`: course prices repeat, so a same-name, same-price pair
  a season apart is the likeliest false link), held by no other row, its number carried by no other valid document. Any
  active row with an empty cell, the same name and amount and within the 30 days holds the document, whatever its status or
  year (a lagging "לא שולם" may be the real row): only a money-in row of 2026 receives a number, but any holder blocks. The
  cards, names and `place_of` are `app/recon/cards.py`, the desk's moved there, never copied (a test holds the desk's list
  and the plan in agreement). It reads `matching_snapshot(wide=True)` (the cards, one transaction); A1's stays narrow. The
  plan never says a name or an amount; `verify` asks the plan's question again, the window too, and says a number only when
  the books hold it.
- **The desk (D1, 24.11):** `app/desk/` and `frontend/`. The page `/desk/` is the Board View "כספים", a second feature of
  analiza-metrics' monday app (the desk move plan §2, option A), so `MONDAY_CLIENT_SECRET` (a Replit Secret, reported as set
  or not) is the value analiza-metrics holds. `/api/desk` verifies the sessionToken (`app/desk/auth.py`; Analiza's account
  is pinned and nothing unpins it), refuses a guest, and gates each tab by membership of its charge board, fail closed
  (`app/desk/gate.py`). It answers counts only. The desk's reading (`app/desk/reading.py`) has the old `read()`'s shape for
  D2; the reader refuses every `NEVER_READ` column for every caller (an ID number, a bank account, an account's business
  number, and every mirror of one: three, read live 24.11), keeps only the cells it asked for, and a linked row answered
  from another board is a stranger, counted and dropped. An ID number typed into a name or a free-text cell is masked as
  the desk reads it (`mask_ids`: 8+ digits, judged whole by the books' cleaning, become `#`; shorter references stay, and a whole date or month is cut out first, QA 24.11; in the two invoice columns a cell A1 reads as a
  list of numbers is kept as typed and only a number of 7+ digits in it is masked, `mask_refs`, D3's review), so
  memory, `desk_readings`, the page and the Excel file agree (Nadav 24.11). The SDK is analiza-metrics' copy (its sha256 is pinned by a test). uvicorn's access line never carries a query string (`NoQueryInAccessLog`: a sessionToken, a search, a signed link).
- **The desk's page (D2):** `frontend/finance.js` and `finance.css` are analiza-metrics' (da671b2), ported faithfully (what
  changed is listed at the top of `finance.js`); `frontend/desk.js` mounts them (the SDK, the sessionToken, `monday` whenever
  monday supplied a context, `board` only for the two charge boards), and `/desk/?check=1` draws D1's self-check
  (`frontend/check.js`). The page calls `/api/desk` paths only, never one under `/api/finance`, and every monday request goes
  through finance.js's one door (`mondayCall`; its writes in `mondayWrite`, behind `WRITABLE`: a column change, and a billing edit's Update only when the app has `updates:write`). What the server never
  reads (an ID number, a refund's bank account, a company's ח.פ) is read there as the person, only when shown, kept in the
  page's memory and sent nowhere. A failed "רענון" keeps the desk and what was typed, and says so beside the reading's time
  until the next reading (`REFRESH_FAILED`, D4's review). A row's action in its details floats beside its control, as a batch
  does (`actPop`), so no row moves. One flash shows at a time: a new one takes the place of the one on the screen. Tests:
  `tests/test_desk_page.py` (node) and `tests/test_desk_browser.py` (Playwright).
- **The desk's usage (D2):** `POST /api/usage` keeps what the page sends in `desk_events` (`app/desk/usage_store.py`; `migrations/005`
  is analiza-metrics' 004 statement for statement, so D5 imports that history row for row): closed words and counts, checked
  by `app/desk/usage.py` (analiza-metrics' vocabulary, ported faithfully), never a name, an amount, a search's text or an ID
  number. The sessionToken only, under the token's user id, with a limit of its own apart from the desk's budget. The table
  is never made at runtime: a missing one is `store_schema`, 503 `usage_unavailable`, said in `/health` `desk.usage`. A kind
  or a detail changes in the page and the vocabulary together (`tests/test_desk_usage.py`); the queries: `docs/usage-data.md`.
- **The desk's stored reading and its refresh (D2, 24.11; analiza-metrics round 3 slice 2 at da671b2):** `HeldReading`
  (`app/desk/reading.py`) keeps the last reading in `desk_readings` too (migrations/006, one row, `app/desk/store.py`: never
  a `NEVER_READ` column, and the store refuses a text that names one). A plain `GET /api/desk` past the span answers the
  held reading, or on a cold instance the stored one, at once with `refreshing: true`; `?fresh=1` and `POST /api/desk/refresh`
  read a refresh (`read_delta`: each board's items updated since the held reading, read again by id; the deciding leads every
  time; the two charge boards' ids paged, so a charge that left unseen is dropped and one that came unseen is read by id),
  whole instead with nothing held, after `FULL_AFTER` (36 h), on ids that cannot be reconciled or a failed refresh (at most
  once per `FALLBACK_GAP`, never on `NO_FALLBACK` or a rate limit). A request, never a thread. `/health`: `desk.last_reading.kind`,
  `desk.refresh` (codes), with a database `desk.stored` (its age); never a count of rows. Tests: `tests/test_desk_mirror.py`.
- **What the books say in the desk (D3, 25.11), read only:** `app/desk/books.py`. A1's `analyse` (reused, never forked; it now
  also says `refused` and `group`) judges every Student Charge row of the desk's reading, and three buckets are marks on the row
  (`books_ref_invalid`, `books_amount_differs`, `books_receipt_unpaid`): a red chip with its words filled on the server, under
  the row's state and in "מה לבדוק" (no column added: QA 4's widths), a sentence in the details, the chip in the files. A
  cited number just past the end of its series (`not_yet_read`: at most 20 past the last of the series nearest below, held by no
  document of the copy) is not yet in the copy (read hourly): no mark, a note in the details; one further on is "לא נמצא", and a
  number the copy holds as a draft is "טיוטה".
  `books_receipt_unpaid` is read off the row itself, never off A1's buckets (`receipt_unpaid_facts`): "לא שולם", "יש בעיה" or an
  empty status (never "מלגת אנליזה" or an unknown label), every number cited a valid invoice-receipt of the window, and its amount
  empty (A1's `amount_unreadable`, the usual unpaid row) or its group's sums within the tolerance. A mark moves no row and changes no sum; a marked row from 25.10.2026 is in "לטיפול"
  too (`books_task`: filter_b2c and the page's scFilter alike). Under "לטיפול", the documents of the era no charge row cites:
  the students' (a card with an invoice-receipt and no proforma, no Account's key; candidates by name, the row's before its
  first "|" and shown whole, never a match), the
  companies' (an Account's `מפתח חשבשבת`, the whole board read by `AccountKeys`, never `text_mm3y3gm7`), card 10091 counted
  apart, the unclear ones to a member of both charge boards only (`visible_part`). A card key that is masked ("#": the copy's
  cleaning masks a key of six digits or more) or empty names no card (`card_of`): its side is judged by the card's name
  (`side_card`: NFKC, case folded, spaces collapsed; evidence only, never matched to an Account or a row), so a proforma beside
  a company's masked invoice-receipts makes them unclear (FR-C4, 18.11); with neither a key nor a name, alone, by its type. Computed per reading and per books run
  (`DeskBooks`), only on a whole reading and a whole copy (`BooksStore.desk_snapshot`, one repeatable-read snapshot, judged by
  `not_ready`, A1's rule, now in app/books/store.py); else the last whole result `held`, or `none`, with closed reasons; a whole copy whose last run finished more
  than 3 h ago is `held` too (`copy_stale`, `COPY_STALE_S`: the marks never pose as current on a stopped sync); past Student Charge's window (2026,
  the desk's year) a part notes `student_window_ended` (NOTES: said after the reasons, by a current part too); `off`
  on Demo or without a database; an answer at once never reads monday for the keys (`pending`) and never waits for another request's computation. `/health` `desk.books`: state,
  as_of, reasons, never a count. No schema change. Tests: `tests/test_desk_books.py`, `tests/test_desk_books_page.py`.
- **What the books propose to fill (D4, 26.11, "רישום מחשבשבת"):** `app/desk/books.py` `proposals`, in the same computation.
  Never a rule of its own: FR-C2's `ref_fill.analyse` on the desk's own reading (Student Charge, B2B Charge, the Accounts'
  keys) gives the number, and A1's `analyse` on the rows as they would be with those numbers written gives the date (its
  `fill` bucket: an empty "תאריך תקבול" only; a row that already cites a valid invoice-receipt gets the date alone). Only a
  row the desk dates from ERA_FROM on, with a document from it on (older is C1's cleanup). A proposal and a mark never meet
  on a row; a proposal leaves the server only from an `ok` part computed on the reading shown (`merge`), never a held one.
  The chips `books_ref_fill` / `books_date_fill` are the legend's. The action `books_fill` (DESK_ACTIONS) writes them as the
  person: one step, the number (text) and the date (date), each only the value proposed and only into an empty cell
  (`if_empty`); `date_mm8yh4bn` is on `desk_writes` and WRITABLE as a date. `merge` lays on the row its chip (a flag, never
  `books_task`), `books_fill` {number?, date?} and the action; on the document `proposal`. The page (finance.js D4): the
  chip, the sentence and "רישום מחשבשבת" in the details, "הצעה: לרשום בשורה של ..." and its button in the list, "לרשום את כל
  ההצעות (N)" above it - all the batch of D2's 1.1 (one read, one confirmation, each row read again before its write; a row
  whose cells are not what the desk read is skipped and said, never written over: `fillMoved`), floating beside what was
  pressed - its report too: beside the document's line (`data-books-at`, kept once written), and where it was on the page
  once a reading took the document off (`POP_AT`); a batch's box is its one report, a flash only while the students' list
  is off the screen (QA 26.11). A proposal is always the reading's on the screen (D4's review): taken off the current rows when the confirmation
  is made, held to a newer reading that lands while it is open (`recheckBatch`: one withdrawn or changed leaves with its closed
  reason, `FILL_GONE` / `FILL_CHANGED`; with none left it closes, said) and checked again right before each row's write. Both
  reads take what the proposal rests on too (the action's `rests_on`: "סטטוס תשלום" by index, "סכום ששולם"), held to the
  reading (`restsMoved`), and right before a number is written one `items_page_by_column_values` read as the person
  (`READ_CITING`, `citedElsewhere`) finds a row of Student Charge that already cites it; a check that cannot be made fails
  the row. The legend says exactly what is checked. A date written here files its row under that date's month at once
  (`applyWritten`). Usage kind `books_fill` (`from`, `rows`). Tests: `tests/test_desk_books_fill.py` (the desk's proposals equal
  `ref_fill.plan`'s and A1's plan's on the same rows; the gate), `tests/test_desk_books_fill_page.py` (node, Playwright).
- **The students by cohort (D4, 26.11; backlog 0313), read only:** `app/desk/cohorts.py`, in the desk's answer as
  `b2c.cohorts` (Student Charge's members only). Only cohorts on the voucher track (Registration Flow `color_mm31k62f` index
  3, `on_track`): one on another track is shown nowhere, priced or not. Per such cohort with one clean "מחיר" (`text_mm5fhydb`,
  `price_of`, an invisible mark at its edge a space as A1's `_clean` reads it; else listed with why and no money, and a
  cohort "בוטל" too): its students from Enrollments, never from the payments (Status נרשם,
  פעיל, At Risk or סיים by index, the helper's IN set; one per person), what each paid (their payments' `net`, the desk's own
  money; a payment filed by its lead with no enrollment is the student's whose enrollment links that lead, `EN["lead"]`
  "קישור לליד", or who is its lead's person, `LD["person"]`, when exactly one student is, `_lead_payer`; else it is said on
  the cohort's line, `unlinked`, and in no balance), and each balance in one part by the voucher status' INDEX (`PARTS`: "אושר - ממתין לתשלום" first, then 0, 8/9,
  17, a 4 with a balance left, and "בלי סטטוס שובר"); 2/3 waived, 6/7/10 out: listed, in no balance, and what they paid is
  in "שולם בפועל", everything that came in (Chava's "כמה כבר התקבל בסך הכול"), and is their expected. A line is its students'
  sums (`checks.ties`), and ties to what the list files under its cohort, summed from the payments apart from the students
  (`filed` = paid + `unlinked` + `shared`, in money and in payments; one filed under none because its enrollments name two
  cohorts, under each). The reading reads every cohort on the track, paid for or not: one items_page with monday's status
  filter (`any_of` [3] on Registration Flow, `TRACK`, `app/monday/client.py status_rule`) in a request of its own right
  after the columns check, and a refresh's right after its windows (`filtered_ids`: one request more, a quiet refresh four,
  and one per page of 500 after the first) - never an alias of them: monday refuses a request whole when one field is bad
  (the review of D4) - and they join the payments' cohorts. A track that cannot be read (refused, another shape, no board,
  the request failed) never fails a reading or a refresh: step 5 is skipped and `cohorts_read` says why (`COHORTS_UNREAD`),
  which the view shows in place of its money (`state` "unread", `UNREAD_SAID`) and /health as `desk.last_reading.cohorts`;
  a step 5 that fails is said the same way (`students_failed`), the reading kept as it was before it. An exception in the
  module stays in its part (`state` "error", `CRASHED_SAID`, logged by class: view.py `_cohorts_of`), never `view_error`.
  The enrollments of those with a price through `board_relation_mm1hetrb` (step 5, `roster_ids`); a refresh reads
  those cohorts again every time (`BODY_SCHEMA` 4). Its totals and its Excel
  file are the list `b2c_cohorts` of `/api/desk/totals` and the export link (`app/desk/cohorts_export.py`: a line per
  cohort, every student under the same money columns so they sum to their line, the cohorts without a price). The page:
  "רשימה | לפי מחזור" in the students' list head (frontend/finance.js, its block "D4"): the figures, the table and the file
  in one order (`COLUMNS`, QA 26.11): "צפי תשלום", "שולם בפועל", "יתרה לגבייה", then the parts, "אושר - ממתין לתשלום" the
  first of them and the one tinted; a cohort row opening to its students, the price and the count under the cohort's name
  and agorot only where an amount has them, the figures above the table as the table writes them (the table fits 1280 and
  1400), its legend part the server's; its clicks on
  `document`, inside the desk only. Tests: `tests/test_desk_cohorts.py`, `tests/test_desk_cohorts_page.py` (node, Playwright).
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
