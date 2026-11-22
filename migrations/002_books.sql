-- 002_books: the read-only copy of the books (Analiza-Monday backlog 0301, slice B1; spec §3.2).
--
-- `company` (the wizdb) is part of every key: stockIDs overlap between Demo and Analiza.
-- Text from the books is cleaned before it is written (app/books/clean.py, spec §3.4): there is no
-- column for an address, a phone, a full email or a tax id, ever, and a run of six digits or more in any
-- stored text is masked. The tax-api number is kept as present or not until spec §10 settles what it is.
-- The books stay the source of truth: nothing here is ever written back.

CREATE TABLE IF NOT EXISTS books_documents (
  company                 text        NOT NULL,
  stock_id                bigint      NOT NULL,
  doc_type                integer,
  doc_number              text,
  status                  integer,
  value_date              date,
  issue_date              date,
  due_date                date,
  pay_date                date,
  account_key             text,
  account_name            text,
  total_incl              numeric,
  total_excl              numeric,
  total_vat_free          numeric,
  vat_pct                 numeric,
  currency                text,
  reference               text,
  refs_in_text            text[]      NOT NULL DEFAULT '{}',
  remarks                 text,
  details                 text,
  email_domain            text,
  tax_api_number_present  boolean,
  batch                   text,
  content_hash            text        NOT NULL,
  fields_seen             text[]      NOT NULL DEFAULT '{}',
  first_seen_at           timestamptz NOT NULL DEFAULT now(),
  last_read_at            timestamptz NOT NULL DEFAULT now(),
  last_changed_at         timestamptz NOT NULL DEFAULT now(),
  gone_at                 timestamptz,
  gone_confirmed_by       text,
  PRIMARY KEY (company, stock_id)
);
CREATE INDEX IF NOT EXISTS books_documents_series ON books_documents (company, doc_type, status);

CREATE TABLE IF NOT EXISTS books_lines (
  company        text     NOT NULL,
  move_id        bigint   NOT NULL,
  stock_id       bigint   NOT NULL,
  base_move_id   bigint,
  base_quantity  numeric,
  item_key       text,
  item_name      text,
  details        text,
  quantity       numeric,
  price          numeric,
  total_excl     numeric,
  vat_exempt     boolean,
  currency_code  text,
  status         integer,
  PRIMARY KEY (company, move_id)
);
CREATE INDEX IF NOT EXISTS books_lines_document ON books_lines (company, stock_id);
CREATE INDEX IF NOT EXISTS books_lines_base ON books_lines (company, base_move_id) WHERE base_move_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS books_document_events (
  event_id  bigserial   PRIMARY KEY,
  company   text        NOT NULL,
  stock_id  bigint      NOT NULL,
  run_id    bigint,
  kind      text        NOT NULL CHECK (kind IN ('first_seen', 'changed', 'status_changed', 'gone', 'reappeared')),
  fields    text[]      NOT NULL DEFAULT '{}',
  at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS books_document_events_document ON books_document_events (company, stock_id);

CREATE TABLE IF NOT EXISTS books_sync_runs (
  run_id       bigserial   PRIMARY KEY,
  company      text        NOT NULL,
  trigger      text        NOT NULL,
  started_at   timestamptz NOT NULL DEFAULT now(),
  finished_at  timestamptz,
  from_id      bigint,
  to_id        bigint,
  probed       integer     NOT NULL DEFAULT 0,
  found        integer     NOT NULL DEFAULT 0,
  added        integer     NOT NULL DEFAULT 0,
  changed      integer     NOT NULL DEFAULT 0,
  absent       integer     NOT NULL DEFAULT 0,
  failed       jsonb       NOT NULL DEFAULT '{}'::jsonb,
  masked       integer     NOT NULL DEFAULT 0,
  far_hits     bigint[]    NOT NULL DEFAULT '{}',
  complete     boolean,
  ok           boolean,
  note         text
);
CREATE INDEX IF NOT EXISTS books_sync_runs_company ON books_sync_runs (company, run_id DESC);
