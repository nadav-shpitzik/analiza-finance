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

    @property
    def ok(self) -> bool:
        return not self.problems


def load(env: Optional[Mapping[str, str]] = None) -> Config:
    env = os.environ if env is None else env
    problems = []
    key = (env.get("WIZ_PRIVATE_KEY") or "").strip()
    if not key:
        problems.append("WIZ_PRIVATE_KEY is not set (a Replit Secret, or the repository secret for live-check)")
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
    )

