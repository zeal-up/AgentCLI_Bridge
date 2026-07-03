#!/usr/bin/env bash
# Copilot Bridge daemon orchestrator.
# Launches: sessions indexer (periodic), events tailer (all sessions, live),
# and commands injector (poll). PIDs recorded to ~/.copilot-bridge/bridge.pids.
#
# Usage: ./scripts/bridge-start.sh [--once]
#   --once: run index + tail --all --once + inject --once, then exit (no daemon).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# Load local instance values (APP_ID, profile, allowlist) if present.
# See .env.example. .env.local is gitignored.
if [[ -f "$ROOT/.env.local" ]]; then
  set -a
  # shellcheck disable=SC1091
  . "$ROOT/.env.local"
  set +a
fi

STATE_DIR="${HOME}/.copilot-bridge"
PIDFILE="${STATE_DIR}/bridge.pids"
LOGDIR="${STATE_DIR}/logs"
mkdir -p "$STATE_DIR" "$LOGDIR"

ONCE=0
[[ "${1:-}" == "--once" ]] && ONCE=1

# Don't double-start
if [[ -f "$PIDFILE" && "$ONCE" -eq 0 ]]; then
  if kill -0 "$(head -1 "$PIDFILE")" 2>/dev/null; then
    echo "bridge already running (PID $(head -1 "$PIDFILE")). Use bridge-stop.sh first." >&2
    exit 1
  fi
  rm -f "$PIDFILE"
fi

run_index_loop() {
  # Refresh session index every 60s (or once)
  python3 -m bridge index >>"$LOGDIR/index.log" 2>&1
  [[ "$ONCE" -eq 1 ]] && return 0
  while true; do
    sleep 60
    python3 -m bridge index >>"$LOGDIR/index.log" 2>&1 || true
  done
}

run_tail() {
  if [[ "$ONCE" -eq 1 ]]; then
    python3 -m bridge tail --all --once >>"$LOGDIR/tail.log" 2>&1
  else
    while true; do
      python3 -m bridge tail --all >>"$LOGDIR/tail.log" 2>&1
      rc=$?
      echo "$(date -Is) tail exited rc=$rc; restarting in 5s" >>"$LOGDIR/tail.log"
      sleep 5
    done
  fi
}

run_inject() {
  if [[ "$ONCE" -eq 1 ]]; then
    python3 -m bridge inject --once >>"$LOGDIR/inject.log" 2>&1
  else
    python3 -m bridge inject >>"$LOGDIR/inject.log" 2>&1
  fi
}

start_daemon() {
  local script="$1"
  setsid nohup bash -c "$script" bash "$ROOT" "$LOGDIR" >/dev/null 2>&1 </dev/null &
  echo "$!"
}

if [[ "$ONCE" -eq 1 ]]; then
  echo "bridge --once: running index + tail + inject once..."
  run_index_loop
  run_tail
  run_inject
  echo "bridge --once done."
  exit 0
fi

# Daemon mode: launch three independent wrapper processes. The wrappers are
# nohup'ed so they survive the non-interactive shell that started this script.
INDEX_PID=$(start_daemon '
cd "$1"
if [[ -f "$1/.env.local" ]]; then
  set -a
  . "$1/.env.local"
  set +a
fi
while true; do
  python3 -m bridge index >>"$2/index.log" 2>&1 || true
  sleep 60
done
')
TAIL_PID=$(start_daemon '
cd "$1"
if [[ -f "$1/.env.local" ]]; then
  set -a
  . "$1/.env.local"
  set +a
fi
while true; do
  python3 -m bridge tail --all >>"$2/tail.log" 2>&1
  rc=$?
  echo "$(date -Is) tail exited rc=$rc; restarting in 5s" >>"$2/tail.log"
  sleep 5
done
')
INJECT_PID=$(start_daemon '
cd "$1"
if [[ -f "$1/.env.local" ]]; then
  set -a
  . "$1/.env.local"
  set +a
fi
while true; do
  python3 -m bridge inject >>"$2/inject.log" 2>&1
  rc=$?
  echo "$(date -Is) inject exited rc=$rc; restarting in 5s" >>"$2/inject.log"
  sleep 5
done
')

echo "$INDEX_PID" > "$PIDFILE"
echo "$TAIL_PID" >> "$PIDFILE"
echo "$INJECT_PID" >> "$PIDFILE"

echo "bridge started (daemon):"
echo "  index  PID $INDEX_PID  (logs $LOGDIR/index.log)"
echo "  tail   PID $TAIL_PID   (logs $LOGDIR/tail.log)"
echo "  inject PID $INJECT_PID (logs $LOGDIR/inject.log)"
echo "stop with: ./scripts/bridge-stop.sh"
