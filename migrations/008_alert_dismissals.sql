-- 008_alert_dismissals: "לא פער", the one thing the desk keeps of an alert (Analiza-Monday backlog 0301 R5; the spec
-- §6.1 item 4 and §2.4 `POST /api/alerts/not-a-gap`). CREATE only.
--
-- A person marks an alert of the books (a mark on a Student Charge row, a document in a list of documents with no charge,
-- a proposal of "רישום מחשבשבת") as not a gap. It stays hidden while its evidence is the same: the kind, the subject (the
-- row's item id, or the document's number) and `evidence`, a sha256 over a fixed set of fields per kind that the SERVER
-- computes from its own computation (app/desk/dismissals.py EVIDENCE), never a hash the page sent. When any of those
-- fields changes, the alert comes back.
--
-- One live dismissal per (company, kind, subject): the partial unique index below. A dismissal is never deleted and never
-- edited: undone, it keeps its row with `undone_at` and `undone_by` (only its own dismisser undoes it, the norm of
-- ADR-0190 §6); a dismissal whose alert came back with other evidence and was dismissed again is closed the same way,
-- with `replaced_by` naming the dismissal that took its place. `user_id` and `undone_by` are monday user ids from the
-- verified sessionToken. NEVER a name, an amount, a card key or an ID number: the evidence is one hash, and the subject is
-- digits only.
--
-- `kind` is checked for its shape, not against the list of kinds: a kind added later is code only, never a constraint
-- dropped in a publish (CLAUDE.md: any DROP in a Republish's SQL is Cancel). The closed list lives in the code.
--
-- Re-runnable: IF NOT EXISTS, and nothing here drops or rewrites anything. The development database first, from the Shell
-- (python3 -m app.migrate), then a Republish whose SQL is read (CLAUDE.md rule 8). The deployment never creates it.
CREATE TABLE IF NOT EXISTS alert_dismissals (
  id           bigserial   PRIMARY KEY,
  company      text        NOT NULL CHECK (company <> ''),
  kind         text        NOT NULL CHECK (kind ~ '^[a-z][a-z0-9_]{0,39}$'),
  subject      text        NOT NULL CHECK (subject ~ '^[0-9]{1,20}$'),
  evidence     text        NOT NULL CHECK (evidence ~ '^[0-9a-f]{64}$'),
  user_id      bigint      NOT NULL CHECK (user_id > 0),
  created_at   timestamptz NOT NULL DEFAULT now(),
  undone_at    timestamptz,
  undone_by    bigint      CHECK (undone_by > 0),
  replaced_by  bigint      REFERENCES alert_dismissals (id),
  CHECK ((undone_at IS NULL) = (undone_by IS NULL)),
  CHECK (replaced_by IS NULL OR undone_at IS NOT NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS alert_dismissals_live ON alert_dismissals (company, kind, subject) WHERE undone_at IS NULL;
