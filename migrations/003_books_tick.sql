-- 003_books_tick: the tick of slice B2 (Analiza-Monday backlog 0301; spec §4.2). CREATE and ADD only.
--
-- books_sweep: where the weekly sweep stands, per company, so a tick cut by its box loses nothing (spec §2.3).
-- books_documents.missing_since / missing_seen_at: a held document that answered "Stock not found" - the first and
-- the latest miss. Gone needs two misses at least an hour apart (spec §4.2.5): a draft is then gone at once, a valid
-- document stays in every figure until a person confirms it.
-- books_sync_runs.reasons: why a tick was not ok, so the next tick knows whether the state changed (Make posts to
-- Discord only on a change).

CREATE TABLE IF NOT EXISTS books_sweep (
  company             text        PRIMARY KEY,
  next_id             bigint      NOT NULL,
  pass_started_at     timestamptz NOT NULL DEFAULT now(),
  last_pass_ended_at  timestamptz,
  passes              integer     NOT NULL DEFAULT 0
);

ALTER TABLE books_documents ADD COLUMN IF NOT EXISTS missing_since timestamptz;
ALTER TABLE books_documents ADD COLUMN IF NOT EXISTS missing_seen_at timestamptz;
ALTER TABLE books_sync_runs ADD COLUMN IF NOT EXISTS reasons text[] NOT NULL DEFAULT '{}';
