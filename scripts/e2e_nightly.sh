#!/bin/sh
# Nightly wrapper for the real-harness check. Runs from the gitvow checkout, keeps one JSON result per day under
# ~/.gitvow/e2e/ and the latest at latest.json, and exits with the check's own status. launchd runs this; a person
# reads the results with: tail -n 40 ~/.gitvow/e2e/latest.json
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$HOME/.gitvow/e2e"
mkdir -p "$OUT"
DAY="$(date +%Y-%m-%d)"
cd "$ROOT" || exit 2
"$ROOT/.venv/bin/python" "$ROOT/scripts/e2e_real.py" --json > "$OUT/$DAY.json" 2> "$OUT/$DAY.err"
rc=$?
cp "$OUT/$DAY.json" "$OUT/latest.json"
{ echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) rc=$rc"; } >> "$OUT/history.log"
# keep three weeks
find "$OUT" -name '20*.json' -mtime +21 -delete 2>/dev/null
find "$OUT" -name '20*.err' -mtime +21 -delete 2>/dev/null
exit $rc
