#!/usr/bin/env bash
# Stop the Copilot Bridge daemon (index/tail/inject loops).
set -euo pipefail

STATE_DIR="${HOME}/.copilot-bridge"
PIDFILE="${STATE_DIR}/bridge.pids"

sweep_orphans() {
  # Sweep wrappers from older script versions that may have been orphaned under
  # PID 1 and are no longer represented in the current PID file.
  pkill -f 'python3 -m bridge (index|tail|inject|voice)' 2>/dev/null || true
}

if [[ ! -f "$PIDFILE" ]]; then
  echo "no PID file ($PIDFILE); bridge not running (or started elsewhere)."
  sweep_orphans
  exit 0
fi

# Kill the recorded wrapper PIDs and any child `python3 -m bridge` processes.
PIDS="$(cat "$PIDFILE")"
for pid in $PIDS; do
  pkill -P "$pid" 2>/dev/null || true
  kill "$pid" 2>/dev/null || true
done

sleep 1
# Force-kill if still alive
for pid in $PIDS; do
  pkill -9 -P "$pid" 2>/dev/null || true
  kill -9 "$pid" 2>/dev/null || true
done

sweep_orphans

rm -f "$PIDFILE"
echo "bridge stopped."
