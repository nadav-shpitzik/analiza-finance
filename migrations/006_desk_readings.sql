-- 006_desk_readings: the finance desk's stored reading (Analiza-Monday backlog 0301 D2; ported 24.11 from analiza-metrics'
-- migrations/003_finance_readings.sql at da671b2). CREATE only.
--
-- One row, name 'desk', overwritten: the reading an instance that woke from zero answers with at once (marked
-- refreshing), and the base the next refresh reads only what changed since `read_at` on top of. `full_read_at` is when the
-- last full reading of its line was taken: after 36 hours the next refresh reads whole (app/desk/reading.py FULL_AFTER).
-- A reading older than the row's never replaces it (two instances, one database). A cache of monday, never a second
-- source of truth; only a reading without an error is kept.
--
-- `body` holds the charge rows and the rows they link to as monday shows them to the boards' members (names and amounts).
-- It NEVER holds a column of NEVER_READ (app/monday/client.py): an ID number, a refund's bank account, a company's
-- business number, or a mirror of one. The reader never reads them, and app/desk/store.py refuses to write a text that
-- names one. `checks` holds counts only.
--
-- Re-runnable: IF NOT EXISTS, and nothing here drops or rewrites anything. The development database first, from the Shell
-- (python3 -m app.migrate), then a Republish whose SQL is read (CLAUDE.md rule 8).
CREATE TABLE IF NOT EXISTS desk_readings (
  name          text        PRIMARY KEY,
  read_at       timestamptz NOT NULL,
  full_read_at  timestamptz NOT NULL,
  kind          text        NOT NULL CHECK (kind IN ('full', 'delta')),
  complete      boolean     NOT NULL,
  body_schema   text        NOT NULL,
  checks        jsonb       NOT NULL,
  body          jsonb       NOT NULL,
  calls         integer     NOT NULL DEFAULT 0,
  seconds       numeric,
  saved_at      timestamptz NOT NULL DEFAULT now()
);
