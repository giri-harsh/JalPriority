#!/usr/bin/env bash
# Ownership guard (CLAUDE.md / MEMBER_A.md section 7): every path changed since tag t0 must live under member-a/.
# Checks (1) committed changes `git diff --name-only t0...HEAD`, (2) staged/unstaged/untracked paths.
set -u
cd "$(git rev-parse --show-toplevel)" || exit 2

if ! git rev-parse -q --verify refs/tags/t0 >/dev/null; then
  echo "check_ownership: tag t0 not found" >&2
  exit 2
fi

bad=0

committed_leaks=$(git diff --name-only t0...HEAD | grep -v '^member-a/' || true)
if [ -n "$committed_leaks" ]; then
  echo "OWNERSHIP VIOLATION (committed paths outside member-a/):" >&2
  echo "$committed_leaks" | sed 's/^/  /' >&2
  bad=1
fi

# porcelain: "XY path" (renames "XY old -> new"); strip the status columns
dirty_leaks=$(git status --porcelain --untracked-files=all | sed -E 's/^.{3}//; s/^.* -> //; s/^"//; s/"$//' | grep -v '^member-a/' || true)
if [ -n "$dirty_leaks" ]; then
  echo "OWNERSHIP VIOLATION (working-tree paths outside member-a/):" >&2
  echo "$dirty_leaks" | sed 's/^/  /' >&2
  bad=1
fi

if [ "$bad" -eq 0 ]; then
  echo "check_ownership: OK (only member-a/** changed since t0)"
fi
exit "$bad"
