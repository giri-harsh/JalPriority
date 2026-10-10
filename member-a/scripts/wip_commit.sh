#!/usr/bin/env bash
# Snapshot commit of everything under member-a/ so no work is ever left uncommitted.
# Usage: scripts/wip_commit.sh ["summary"]   (refuses to commit if anything outside member-a/ changed)
set -u
cd "$(git rev-parse --show-toplevel)" || exit 2

if ! bash member-a/scripts/check_ownership.sh >/dev/null 2>&1; then
  echo "wip_commit: ownership violation - not committing" >&2
  exit 1
fi

git add -A member-a
if git diff --cached --quiet; then
  echo "wip_commit: nothing to commit"
  exit 0
fi

git commit -q -m "wip: ${1:-snapshot}" && git log --oneline -1

# push only when a remote exists and auth works; never fail the snapshot because of it
if [ -n "$(git remote)" ]; then
  git push -q -u origin "$(git branch --show-current)" 2>/dev/null || echo "wip_commit: push failed (continuing)" >&2
fi
exit 0
