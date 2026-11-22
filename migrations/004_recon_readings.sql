-- 004_recon_readings: the reconciliation's reading of monday (Analiza-Monday backlog 0301 R0; spec §2.1). CREATE only.
--
-- One row per profile ('recon'), overwritten by every reading: a cache of monday, never a second source of truth.
-- checks: per board, its items_count, the rows read and the distinct ids (counts only). body: the boards' rows - never an
-- ID number or a bank account (app/recon/reading.py NEVER_READ; the store refuses a body that carries one). A reading that
-- failed or came short replaces the row too, so a monday-side rule never runs on a reading the last check did not confirm.

CREATE TABLE IF NOT EXISTS recon_readings (
  name      text        PRIMARY KEY,
  read_at   timestamptz NOT NULL,
  complete  boolean     NOT NULL,
  error     text,
  detail    text,
  checks    jsonb       NOT NULL,
  body      jsonb,
  calls     integer     NOT NULL DEFAULT 0,
  seconds   numeric
);
