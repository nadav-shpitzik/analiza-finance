#!/bin/bash
# "Sync from GitHub" - the workflow button that brings this Repl's workspace
# up to GitHub main. analiza-finance: copied from analiza-metrics on 2026-11-19, with
# the last line changed for a Python-only Repl (review 19.11). There:
# Copied from Analiza-Deal-Breakdown unchanged on 2026-10-20
# (Analiza-Monday backlog 0214); that copy came from Monday-API-Extension
# (2026-09-25), plus the lockfile rule below. It replaces pulling in the Git pane, which
# merged on some days and rebased on others, and pushed Replit's own
# "Published your App" commits back to GitHub for every session to merge.
#
# It only ever fast-forwards. Replit's "Published your App" commits change no
# files, so it drops them. Anything else, it stops, changes nothing, and says
# what it found:
#   - uncommitted edits to tracked files
#   - local commits with real changes that are not on GitHub yet
#   - a branch other than main
#
# package-lock.json is the exception: the Start workflow runs `npm install`,
# which can rewrite it (Replit's package-firewall URLs, see .github/workflows/
# ci.yml), and that is machine output, not work. When it is the only edit, it
# is put back to the committed copy and the sync goes on.
#
# Deploying does not need this. Republish builds from GitHub main by itself
# (scripts/deploy_source.py). This is for working in the Repl.
set -uo pipefail

stop() {
    printf '\nSTOPPED: %s\nNothing was changed.\n' "$1"
    exit 1
}

cd "$(git rev-parse --show-toplevel 2>/dev/null)" || stop "this is not a git checkout."

branch=$(git symbolic-ref --quiet --short HEAD) || stop "the workspace is not on a branch."
[ "$branch" = "main" ] || stop "the workspace is on branch '$branch', not main."

dirty=$(git status --porcelain --untracked-files=no)
if [ "$(git diff --name-only HEAD)" = "package-lock.json" ]; then
    echo "package-lock.json was rewritten by npm install: restoring the committed copy."
    git checkout --quiet HEAD -- package-lock.json || stop "could not restore package-lock.json."
    dirty=$(git status --porcelain --untracked-files=no)
fi
if [ -n "$dirty" ]; then
    printf 'Uncommitted changes:\n%s\n' "$dirty"
    stop "commit or discard these in the Git pane first, or ask Claude."
fi

echo "Fetching GitHub main..."
git fetch --quiet origin main || stop "could not fetch from GitHub. Is the Repl still connected to GitHub?"

real=""
for c in $(git rev-list origin/main..HEAD); do
    if [ "$(git log -1 --format=%s "$c")" = "Published your App" ] &&
        [ -z "$(git diff-tree --no-commit-id --name-only -r "$c")" ]; then
        continue # Replit's publish marker: no files in it, safe to drop
    fi
    real="$real $c"
done
if [ -n "$real" ]; then
    echo "Local commits that are not on GitHub:"
    for c in $real; do git log -1 --format='  %h %an: %s' "$c"; done
    stop "push these first (Git pane, Push), then press Sync again."
fi

before=$(git rev-parse HEAD)
incoming=$(git rev-list --count HEAD..origin/main)
# --keep refuses, rather than overwrites, if a local file is in the way.
git reset --quiet --keep origin/main || stop "git refused to move to GitHub main (the reason is above)."

echo
if [ "$incoming" -eq 0 ]; then
    echo "Already up to date with GitHub main."
else
    echo "Updated with $incoming commit(s) from GitHub:"
    git log --format='  %h %s' "$before..HEAD" | head -20
fi
echo "Now at: $(git log -1 --format='%h %s')"
echo "Press Stop, then Run, to run the new code here (Run installs requirements.txt first)."
