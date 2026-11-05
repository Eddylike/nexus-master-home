#!/usr/bin/env bash
# NEXUS portable start (Linux / Termux / macOS) — no PowerShell required.
# WoZiPC continues to use start_nexus.ps1; this does not replace it.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export NEXUS_ROOT="${NEXUS_ROOT:-$ROOT}"
cd "$ROOT"
PORT="${NEXUS_PORT:-8299}"
export NEXUS_HOST="${NEXUS_HOST:-0.0.0.0}"; HOST="$NEXUS_HOST"
mkdir -p "$ROOT/workspace/logs"
PIDFILE="$ROOT/workspace/logs/nexus.pid"
LOG="$ROOT/workspace/logs/nexus_start.log"
if [[ -f "$PIDFILE" ]]; then
  old="$(cat "$PIDFILE" 2>/dev/null || true)"
  if [[ -n "${old:-}" ]] && kill -0 "$old" 2>/dev/null; then
    echo "NEXUS already running pid=$old (use ./stop_nexus.sh)"
    exit 0
  fi
fi
export NEXUS_BRIDGE_PROCESS_LEVEL="${NEXUS_BRIDGE_PROCESS_LEVEL:-SAFE_WRITE}"
export NEXUS_KEEP_ALIVE="${NEXUS_KEEP_ALIVE:-0}"
nohup python3 "$ROOT/run_server.py" >>"$LOG" 2>&1 &
echo $! >"$PIDFILE"
echo "NEXUS started pid=$(cat "$PIDFILE") host=$HOST port=$PORT root=$ROOT"
echo "health: curl -s http://127.0.0.1:${PORT}/api/health"