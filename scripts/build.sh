#!/bin/bash
# The deployment build (.replit [deployment] build). Copied on 2026-11-19 from
# analiza-metrics (Analiza-Monday backlog 0214; slice S0 of backlog 0301), the
# same two passes; only the arguments below are this service's.
#
# Ship GitHub `main`, not whatever the workspace held at Republish, so a
# code-only deploy needs no pull first. deploy_source.py writes main's copy of
# the repo over the build's snapshot and records the commit in
# build_info.json, which /health serves under `build` (source, sha, reason,
# commit_url). Without GITHUB_DEPLOY_TOKEN (the account secret, linked to this
# app), with work in the Repl that is not on GitHub, or on any fetch error, it
# leaves the workspace files alone and build_info says why. A failure while
# writing files exits 1: the build fails and Replit keeps the running deployment.
#
# SCHEMA (--schema migrations,app/migrate.py): the service applies migrations/
# at startup (app/migrate.py), and the workspace and the deployment have
# different databases; a publish aligns production to the development one,
# DROP included. So when main's migrations differ from the workspace's, this
# builds from the workspace and says why. Such a change ships as CLAUDE.md
# says: Sync from GitHub in the Repl, `python3 -m app.migrate` in its Shell,
# then Republish in the UI, where the migration SQL is read.
#
# The rest of the build then runs from main's copy of this script, in case main
# changed it; DEPLOY_SOURCE_APPLIED stops that second pass from fetching again.
# .replit itself is never taken from main: Replit read it before this ran. The
# same holds for the first pass of this script, so a change to the call below
# reaches the build only after one Sync from GitHub in the Repl.
set -euo pipefail

cd "$(dirname "$0")/.."

if [ -z "${DEPLOY_SOURCE_APPLIED:-}" ]; then
    python3 scripts/deploy_source.py \
        --repo nadav-shpitzik/analiza-finance \
        --require app/main.py,app/wizcloud.py,app/migrate.py,requirements.txt \
        --generated replit.md \
        --schema migrations,app/migrate.py
    export DEPLOY_SOURCE_APPLIED=1
    exec bash scripts/build.sh
fi

# main's requirements.txt (pinned), so a dependency added on main is installed.
pip install --quiet -r requirements.txt
