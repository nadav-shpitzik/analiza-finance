-- 007_ledger_totals: what is kept of the ledger report (Analiza-Monday backlog 0301, slice B3; ADR-0187; spec §3.5.3).
-- CREATE only.
--
-- The report's rows are never kept (ADR-0187 §3): a run reads whole months, sums them in memory and drops them
-- (app/books/ledger_totals.py). Only these, per company (the wizdb):
-- ledger_totals: per month (by "תאריך", the reference's date) and sort code, a line: the debit and credit totals, the rows,
--   the distinct accounts and how many of their keys the copy's cleaning masks. Only a line of three accounts or more whose
--   debit and credit are each filled by three or more (or by none) stands alone; the salary codes (LEDGER_SALARY_CODES) and
--   every other line go into the month's ONE merged line, sort_code -1, kept only on the same terms (account_count >= 3
--   below). The owner's decision of 26.11: one person's money is never a line of its own.
-- ledger_sort_codes: each sort code's name, cleaned like any text from the books (spec §3.4).
-- ledger_accounts: each account key's sort code, only for a key the cleaning keeps whole and that holds fewer than six
--   digits. Student cards are keyed by ID numbers (ADR-0173 §1): such a key is never stored, hashed or kept in any form,
--   only counted (ledger_totals.masked_keys). The CHECK below refuses a key that slipped past the code.
-- ledger_months: per month, the counts of the run that last replaced it - how many codes went into the merged line, and
--   whether it was kept, withheld or not needed - and batch 9998 (the year- or quarter-end transfer of income and expenses)
--   apart: its rows and totals, never in ledger_totals' sums (ADR-0187 §5).
-- ledger_runs: the run log, counts and closed words only.
-- No column holds an account's name, details, a reference, a counter-account, a cost code or one account's amount.
--
-- Re-runnable: IF NOT EXISTS, and nothing here drops or rewrites anything. The development database first, from the Shell
-- (python3 -m app.migrate), then a Republish whose SQL is read (CLAUDE.md rule 8). The deployment never runs it.

CREATE TABLE IF NOT EXISTS ledger_runs (
  run_id       bigserial   PRIMARY KEY,
  company      text        NOT NULL,
  trigger      text        NOT NULL,
  started_at   timestamptz NOT NULL DEFAULT now(),
  finished_at  timestamptz,
  months       text[]      NOT NULL DEFAULT '{}',
  ok           boolean,
  reasons      text[]      NOT NULL DEFAULT '{}',
  note         text
);
CREATE INDEX IF NOT EXISTS ledger_runs_company ON ledger_runs (company, run_id DESC);

CREATE TABLE IF NOT EXISTS ledger_months (
  company            text        NOT NULL,
  month              date        NOT NULL CHECK (extract(day FROM month) = 1),
  row_count          integer     NOT NULL,
  sort_codes         integer     NOT NULL,
  kept_lines         integer     NOT NULL,
  merged_codes       integer     NOT NULL,
  merged_state       text        NOT NULL CHECK (merged_state IN ('none', 'kept', 'withheld')),
  masked_keys        integer     NOT NULL,
  batch_9998_rows    integer     NOT NULL,
  batch_9998_debit   numeric     NOT NULL,
  batch_9998_credit  numeric     NOT NULL,
  parts              integer     NOT NULL,
  run_id             bigint      NOT NULL,
  computed_at        timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (company, month)
);

CREATE TABLE IF NOT EXISTS ledger_totals (
  company        text     NOT NULL,
  month          date     NOT NULL CHECK (extract(day FROM month) = 1),
  sort_code      integer  NOT NULL,
  debit          numeric  NOT NULL,
  credit         numeric  NOT NULL,
  row_count      integer  NOT NULL,
  account_count  integer  NOT NULL CHECK (account_count >= 3),
  masked_keys    integer  NOT NULL,
  run_id         bigint   NOT NULL,
  PRIMARY KEY (company, month, sort_code)
);

CREATE TABLE IF NOT EXISTS ledger_sort_codes (
  company    text        NOT NULL,
  sort_code  integer     NOT NULL,
  name       text,
  run_id     bigint      NOT NULL,
  seen_at    timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (company, sort_code)
);

CREATE TABLE IF NOT EXISTS ledger_accounts (
  company      text        NOT NULL,
  account_key  text        NOT NULL CHECK (account_key <> '' AND char_length(account_key) <= 64
                                           AND strpos(account_key, '#') = 0 AND account_key !~ '([^0-9]*[0-9]){6}'),
  sort_code    integer     NOT NULL,
  run_id       bigint      NOT NULL,
  seen_at      timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (company, account_key)
);
