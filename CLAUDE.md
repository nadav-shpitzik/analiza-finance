# CLAUDE.md - analiza-finance

The home of finance at Analiza (renamed from `finance-sync` on 2026-11-19): the Hashavshevet (WizCloud) books, read only;
the reconciliation against monday as tested code; the gaps as items and the gated actions on monday; and Chava's finance
desk, moving here from analiza-metrics. Its docs, decisions and backlog live in the docs repo `nadav-shpitzik/Analiza-Monday`:
the service page `docs/services/analiza-finance/README.md`, ADR-0185 (this home) and ADR-0176 (read-only books), the spec
`docs/design/analiza-finance-spec.md` with its two companions (the reconciliation rules, the desk move plan), and the build
in slices `docs/backlog/0299-analiza-finance-build.md`. Boot from there for anything beyond this repo. Talk to Nadav in Hebrew.

**Until Nadav's go on the spec, nothing beyond v0 is built here.**

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
   rows. `.gitignore` blocks csv/xlsx. **An ID number is never stored server side** (ADR-0173 §1,
   ADR-0171 §1): text from the books is cleaned of digit runs before it is written (spec §3.4).
5. **A success shape is unverified until a live answer is seen.** Code that reads a response it has
   never seen says so in `/check` (what shape it found), never `except: pass`.
6. **A reconciliation rule exists only with its test** (the rules file, §0). A name is only ever a
   candidate; a match is made by a cited number or a link.
7. **Every write to monday: dry run, approval, write, read-back** (spec §7). The server writes only the
   four billing columns on Accounts; the charge boards are written by the person in the browser, or by
   a session through the monday connector after Nadav's go in chat.
8. **A Postgres schema change goes to the development database first, from the Shell**, then a
   Republish whose SQL is read (the norm in the docs repo's CLAUDE.md).

## Working here

- Tests: `pip install -r requirements-dev.txt && python -m pytest -q`. Every new read path gets a
  test that the write paths stay refused.
- `WIZ_PRIVATE_KEY` lives only in Replit Secrets (workspace and deployment) and in this repo's Actions secret
  for live-check. Locally, tests use a
  fake transport; a live local run takes a placeholder key and proves only the error path.
- Live check without Replit: dispatch `live-check.yml` (inputs `company`, `doc_ids`), read the job log. It
  prints shapes and non-personal values only; keep it that way (`SAFE_FIELDS` in `scripts/live_check.py`).
- Deploy: Replit Autoscale, no Repl yet (slice S0). Not wired to `/deploy` yet (no GitHub-main build step),
  so a publish is Pull in the Repl, then Republish. `/health` shows `build.sha`.
