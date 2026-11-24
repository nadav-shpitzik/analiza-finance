-- 005_desk_events: how the desk is used (Analiza-Monday backlog 0301 D2). CREATE only.
--
-- analiza-metrics' migrations/004_desk_events.sql at da671b2, statement for statement: the same columns, types, defaults
-- and indexes, so D5 imports that history into this table row for row (the desk move plan §4.4). Plus the import's key, now
-- and not in a later ADD: `source` ('analiza-finance' for a row written here, 'analiza-metrics' for an imported one) and
-- `source_id` (the old row's id; NULL here), unique together, so an import run twice never doubles a row. Nadav, 16.11: "נתוני
-- שימוש שלה שם, כדי שנוכל לשפר את זה עם הזמן".
--
-- One row per thing a reader did on the desk's page - opened it, chose a tab, a state or a period, searched, opened a
-- row, ran an action, exported a file, read or copied an ID number - as the page sends it to POST /api/usage.
-- app/desk/usage.py checks every field against a closed list before a row is written. `user_id` is the reader's monday
-- user id, from the verified sessionToken, the one the audit line already names; `session` is a random id the page makes
-- when it opens, so one visit's rows read together; `page_ms` is how long the page had been open; `at` is the server's
-- clock.
--
-- What a row NEVER holds: a name, an amount, the search's text, an ID number, a bank account, an account or an item id.
-- A search is kept as its shape only (how many characters, digits or letters, how many rows it found). Every value in
-- `detail` is a number in a range, a yes/no, or a word that starts with a letter, so a page that tried to send a number
-- or a name could not have it written.
--
-- Nothing updates a row. Nothing reads the table but the operator's SQL (docs/usage-data.md). The deployment never
-- creates it: this file runs on the development database from the Shell (python3 -m app.migrate), and the Republish
-- that follows creates it in production (CLAUDE.md, "A change that touches migrations/").
CREATE TABLE IF NOT EXISTS desk_events (
  id        bigserial PRIMARY KEY,
  at        timestamptz NOT NULL DEFAULT now(),
  user_id   bigint NOT NULL,
  session   text NOT NULL,
  desk      text NOT NULL,
  kind      text NOT NULL,
  tab       text NOT NULL DEFAULT '',
  state     text NOT NULL DEFAULT '',
  page_ms   bigint NOT NULL DEFAULT 0,
  detail    jsonb NOT NULL DEFAULT '{}'::jsonb,
  source    text NOT NULL DEFAULT 'analiza-finance',
  source_id bigint
);
CREATE INDEX IF NOT EXISTS desk_events_at ON desk_events (at);
CREATE INDEX IF NOT EXISTS desk_events_user_at ON desk_events (user_id, at);
CREATE UNIQUE INDEX IF NOT EXISTS desk_events_source ON desk_events (source, source_id);
