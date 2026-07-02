#!/usr/bin/env bash
# Show Copilot Bridge daemon status.
set -euo pipefail

STATE_DIR="${HOME}/.copilot-bridge"
PIDFILE="${STATE_DIR}/bridge.pids"

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
python3 -m bridge ls 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'  {len(d)} sessions')" 2>/dev/null || true
