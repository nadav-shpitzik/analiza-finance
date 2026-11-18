"""Configuration from the environment. Only WIZ_PRIVATE_KEY is a secret.

The server and the two company databases are not secrets (Nadav, 2026-11-18):
the token reaches a Demo company and the real one, both on LB19. The service
starts on Demo and moves to the real company by changing WIZ_COMPANY, a
deliberate one-line act.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional

ROOT = Path(__file__).resolve().parent.parent

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
    return Config(server=server, company=company, db=db, private_key=key, problems=tuple(problems))


def build_info() -> dict:
    """What `scripts/write_build_info.py` recorded at build time.

    Same vocabulary as the other services' /health `build` field, so `/deploy`
    reads it the same way.
    """
    path = ROOT / "build_info.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"source": None, "reason": "no build_info.json"}
    except (OSError, ValueError) as exc:
        return {"source": None, "reason": f"build_info.json unreadable: {type(exc).__name__}"}
