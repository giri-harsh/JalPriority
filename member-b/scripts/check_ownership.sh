#!/bin/bash
# scripts/check_ownership.sh
set -e

# Compare HEAD with t0. If t0 doesn't exist, this script will fail.
CHANGES=$(git diff --name-only t0...HEAD)

LEAKS=0
for FILE in $CHANGES; do
  if [[ ! "$FILE" == member-b/* ]]; then
    echo "ERROR: File modified outside member-b/: $FILE"
    LEAKS=$((LEAKS+1))
  fi
done

if [ "$LEAKS" -gt 0 ]; then
  exit 1
fi

echo "Ownership check passed."
exit 0
