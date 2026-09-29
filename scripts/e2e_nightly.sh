#!/bin/sh
# Nightly real-harness check for gitvow, run by launchd (dev.wirevow.gitvow-e2e).
#
# It lives outside ~/Documents on purpose: macOS does not let a launchd job read ~/Documents without Full Disk
# Access, so the check runs against its own clone of main under ~/.gitvow/e2e/checkout, refreshed each night.
# That is also the right thing to test: main as pushed, not a working copy with uncommitted edits.
#
# Two checks, three agents: e2e_real.py drives Claude Code (two repositories, the card, a protected push);
# e2e_agents.py drives OpenCode (free model, no sign-in) and Gemini CLI (key in ~/.gemini/.env) through their own
# hook contracts. Results: $DAY.json (Claude Code), $DAY-agents.json (the others), latest.json with both.
set -u
OUT="$HOME/.gitvow/e2e"
CK="$OUT/checkout"
DAY="$(date +%Y-%m-%d)"
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
mkdir -p "$OUT"
if [ -d "$CK/.git" ]; then
  git -C "$CK" fetch -q origin main && git -C "$CK" reset -q --hard origin/main
else
  git clone -q https://github.com/wirevow/gitvow "$CK" || exit 2
fi
if [ ! -x "$CK/.venv/bin/python" ]; then
  uv venv -q --python 3.12 "$CK/.venv" || exit 2
fi
uv pip install -q -p "$CK/.venv/bin/python" -e "$CK" || exit 2
cd "$CK" || exit 2
"$CK/.venv/bin/python" "$CK/scripts/e2e_real.py" --json > "$OUT/$DAY.json" 2> "$OUT/$DAY.err"
rc=$?
"$CK/.venv/bin/python" "$CK/scripts/e2e_agents.py" --json > "$OUT/$DAY-agents.json" 2> "$OUT/$DAY-agents.err"
rc2=$?
python3 - "$OUT/$DAY.json" "$OUT/$DAY-agents.json" "$OUT/latest.json" <<'PY'
import json, sys
def load(p):
    try:
        return json.load(open(p))
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}
claude, agents = load(sys.argv[1]), load(sys.argv[2])
out = {"ok": bool(claude.get("ok")) and bool(agents.get("ok")), "claude_code": claude, "agents": agents.get("agents", agents)}
json.dump(out, open(sys.argv[3], "w"), indent=1)
PY
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) claude=$rc agents=$rc2 commit=$(git -C "$CK" rev-parse --short HEAD)" >> "$OUT/history.log"
find "$OUT" -name '20*.json' -mtime +21 -delete 2>/dev/null
find "$OUT" -name '20*.err' -mtime +21 -delete 2>/dev/null
[ "$rc" -eq 0 ] && [ "$rc2" -eq 0 ]
