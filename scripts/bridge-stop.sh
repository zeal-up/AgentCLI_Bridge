#!/usr/bin/env bash
# Stop the Copilot Bridge daemon (index/tail/inject loops).
set -euo pipefail

STATE_DIR="${HOME}/.copilot-bridge"
PIDFILE="${STATE_DIR}/bridge.pids"

if [[ ! -f "$PIDFILE" ]]; then
  echo "no PID file ($PIDFILE); bridge not running (or started elsewhere)."
  exit 0
fi

# Kill the recorded PIDs and any child `python3 -m bridge` processes.
PIDS="$(cat "$PIDFILE")"
for pid in $PIDS; do
  kill "$pid" 2>/dev/null || true
done
# Also sweep any stragglers (child processes)
pkill -P "$(head -1 "$PIDFILE")" 2>/dev/null || true

sleep 1
# Force-kill if still alive
for pid in $PIDS; do
  kill -9 "$pid" 2>/dev/null || true
done

rm -f "$PIDFILE"
echo "bridge stopped."
