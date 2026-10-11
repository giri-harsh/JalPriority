#!/usr/bin/env bash
# Clean demo state: wipe B's SQLite database and A's derived (disruption) scenarios. Stop the stack first.
set -eu
repo="$(cd "$(dirname "$0")/../.." && pwd)"
rm -f "$repo"/member-b/var/jp_b.sqlite3* && echo "removed B sqlite files"
rm -f "$repo"/member-a/data/derived/*.json && echo "removed A derived scenarios"
echo "demo reset done (docker users: docker compose down -v)"
