-- 001_wiz_sessions: the saved WizCloud session (Analiza-Monday backlog 0301, slice S0; spec §2.4-§2.5).
--
-- One row per server and company, shared by every instance of the service, so a cold start reuses
-- the day's session instead of opening a new one (app/store.py says why).
--
-- `sealed` is never the session string itself: it is a random nonce followed by AES-GCM ciphertext,
-- under a key derived from WIZ_PRIVATE_KEY (which lives only in Replit Secrets) and bound to the
-- server and the company. A session string carries all of Chava's rights in the books for 24 hours.
-- Never grant this table to another service's role: the metrics reader of slice M1 gets `facts` only.
CREATE TABLE IF NOT EXISTS wiz_sessions (
  server     text        NOT NULL,
  db         text        NOT NULL,
  sealed     bytea       NOT NULL,
  shape      text        NOT NULL,
  issued_at  timestamptz NOT NULL,
  saved_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (server, db)
);
