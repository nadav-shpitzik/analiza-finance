# CLAUDE.md - finance-sync

The Hashavshevet (WizCloud) integration service. Its docs, decisions and backlog live in the docs
repo `nadav-shpitzik/Analiza-Monday` (`docs/services/finance-sync/README.md`, ADR-0176); boot from
there for anything beyond this repo. Talk to Nadav in Hebrew.

## Hard rules

1. **Read-only toward the books.** `READ_PATHS` in `app/wizcloud.py` is the only way out, matched
   exactly. Never add a path that creates, issues, deletes or imports anything, and never register,
   change or delete a webhook (`TriggersApi/setURL|updateURL|deleteURL`) from code. Any of those is a
   new ADR in the docs repo plus Nadav's explicit go, and is tried on the Demo company first.
2. **The private key travels in the createSession URL.** Never log, print, return or put in an
   exception a URL or a request object. The `httpx` and `httpcore` loggers stay at WARNING. Errors
   carry a `kind` from the closed `KINDS` list plus a redacted, truncated message.
3. **Demo first.** `WIZ_COMPANY=demo` (`wizdb2245n4`) is the default. A new call or report runs on
   Demo before `analiza` (`wizdb2245n5`).
4. **No data from the books in git or in chat.** Report output carries names, ID numbers and
   salaries. Write it under `exports/` (ignored) or to the database, show shapes and counts, not
   rows. `.gitignore` blocks csv/xlsx.
5. **A success shape is unverified until a live answer is seen.** Code that reads a response it has
   never seen says so in `/check` (what shape it found), never `except: pass`.

## Working here

- Tests: `pip install -r requirements-dev.txt && python -m pytest -q`. Every new read path gets a
  test that the write paths stay refused.
- `WIZ_PRIVATE_KEY` lives only in Replit Secrets (workspace and deployment). Locally, tests use a
  fake transport; a live local run takes a placeholder key and proves only the error path.
- Deploy: Replit Autoscale. Not wired to `/deploy` yet (no GitHub-main build step), so a publish is
  Pull in the Repl, then Republish. `/health` shows `build.sha`.
