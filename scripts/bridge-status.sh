#!/usr/bin/env bash
# Show Copilot Bridge daemon status.
set -euo pipefail

STATE_DIR="${HOME}/.copilot-bridge"
PIDFILE="${STATE_DIR}/bridge.pids"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ -f "$ROOT/.env.local" ]]; then
  set -a
  # shellcheck disable=SC1091
  . "$ROOT/.env.local"
  set +a
fi

if [[ ! -f "$PIDFILE" ]]; then
  echo "bridge: not running (no PID file)."
  echo "  start with: ./scripts/bridge-start.sh"
  exit 0
fi

names=("index" "tail" "inject")
i=0
echo "bridge daemon:"
while IFS= read -r pid; do
  if kill -0 "$pid" 2>/dev/null; then
    echo "  ${names[$i]}  PID $pid  ✅ running"
  else
    echo "  ${names[$i]}  PID $pid  ❌ dead"
  fi
  i=$((i+1))
done < "$PIDFILE"

echo
echo "recent events in Miaoda:"
python3 - <<'PY' 2>/dev/null || true
from bridge import lark_db

rows = lark_db.query("SELECT COUNT(*) AS n FROM sessions")
if rows:
    print(f"  {rows[0]['n']} sessions")
PY
