"""Configuration from the environment. WIZ_PRIVATE_KEY and DATABASE_URL are secrets.

The server and the two company databases are not secrets (Nadav, 2026-11-18):
the token reaches a Demo company and the real one, both on LB19. The service
starts on Demo and moves to the real company by changing WIZ_COMPANY, a
deliberate one-line act. DATABASE_URL comes with the Repl's Postgres (the
workspace and the deployment each have their own); it carries a password, so
it is only ever reported as set or not.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Mapping, Optional

from .wizcloud import key_problem  # one rule for the key, here and in the client

COMPANIES = {
    "demo": "wizdb2245n4",  # "Demo"
    "analiza": "wizdb2245n5",  # "אנליזה מודיעין סייבר"
}
DEFAULT_SERVER = "lb19.wizcloud.co.il"
DEFAULT_COMPANY = "demo"
_SERVER_RE = re.compile(r"^lb\d+\.wizcloud\.co\.il$")


@dataclass(frozen=True)
class Config:
    server: str
    company: str
    db: Optional[str]
    private_key: str = field(repr=False)
    problems: tuple = ()
    database_url: str = field(default="", repr=False)
    # The key Make sends to run a tick of the books copy (spec §2.4), in a Make keychain, never inline.
    books_sync_key: str = field(default="", repr=False)
    # monday's API token (a Replit Secret): the same monday user as analiza-metrics' token (the desk move plan §2). The
    # reader only reads (app/monday/client.py refuses a mutation); reported as set or not, never shown.
    monday_token: str = field(default="", repr=False)
    # The monday app's Client Secret (a Replit Secret; Developer Center, Basic information, "Client Secret", not the
    # "Signing Secret"): monday signs the board view's sessionToken with it (app/desk/auth.py). The desk's view is a second
    # feature of analiza-metrics' app (the desk move plan §2, option A), so the value is the one analiza-metrics holds.
    # Reported as set or not, never shown.
    monday_client_secret: str = field(default="", repr=False)
    # The ledger report's External file (B3, ADR-0187 §6): the whole JSON of "כרטסת לאנליזה" as one Replit Secret, never in
    # git. Its `datafile` is the report itself. /health says set, missing or bad (app/books/ledger.py), never a field of it.
    ledger_report: str = field(default="", repr=False)

    @property
    def ok(self) -> bool:
        return not self.problems


def load(env: Optional[Mapping[str, str]] = None) -> Config:
    env = os.environ if env is None else env
    problems = []
    key = (env.get("WIZ_PRIVATE_KEY") or "").strip()
    if not key:
        problems.append("WIZ_PRIVATE_KEY is not set (a Replit Secret, or the repository secret for live-check)")
    elif key_problem(key):
        problems.append(f"WIZ_PRIVATE_KEY {key_problem(key)}")
    server = (env.get("WIZ_SERVER") or DEFAULT_SERVER).strip().lower()
    if not _SERVER_RE.match(server):
        problems.append(f"WIZ_SERVER {server!r} is not an lbN.wizcloud.co.il host")
    company = (env.get("WIZ_COMPANY") or DEFAULT_COMPANY).strip().lower()
    db = COMPANIES.get(company)
    if db is None:
        problems.append(f"WIZ_COMPANY {company!r} is not one of {sorted(COMPANIES)}")
    return Config(
        server=server,
        company=company,
        db=db,
        private_key=key,
        problems=tuple(problems),
        database_url=(env.get("DATABASE_URL") or "").strip(),
        books_sync_key=(env.get("BOOKS_SYNC_KEY") or "").strip(),
        monday_token=(env.get("MONDAY_TOKEN") or "").strip(),
        # trimmed: a newline pasted with a secret is a 401 on every open with nothing on screen to say why
        monday_client_secret=(env.get("MONDAY_CLIENT_SECRET") or "").strip(),
        ledger_report=(env.get("WIZ_LEDGER_REPORT") or "").strip(),
    )

