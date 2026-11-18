"""Record which commit this build is, for /health `build` (run by the deployment build step).

Vocabulary shared with the other services: `source` is "workspace" here (this
service does not fetch GitHub `main` at build time yet), `sha` the commit, and
`reason` when something is missing.
"""

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def main() -> None:
    sha = git("rev-parse", "HEAD")
    info = {
        "source": "workspace",
        "sha": sha or None,
        "branch": git("rev-parse", "--abbrev-ref", "HEAD") or None,
        "dirty": bool(git("status", "--porcelain")),
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if not sha:
        info["reason"] = "git not available at build time"
    (ROOT / "build_info.json").write_text(json.dumps(info, indent=1), encoding="utf-8")
    print("build_info.json:", info)


if __name__ == "__main__":
    main()
