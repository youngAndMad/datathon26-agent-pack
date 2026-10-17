#!/usr/bin/env bash
# Stop the cashwatch API and worker started by deploy/start.sh (pid files under data/run/).
# Only signals PIDs whose command line still belongs to cashwatch (guards against PID reuse).
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${CASHWATCH_ENV_FILE:-$ROOT/deploy/cashwatch.env}"
if [[ -f "$ENV_FILE" ]]; then set -a; source "$ENV_FILE"; set +a; fi
DATA_DIR="${CASHWATCH_DATA_DIR:-$ROOT/data}"
RUN_DIR="$DATA_DIR/run"
GRACE="${CASHWATCH_STOP_GRACE_SEC:-20}"
rc=0

stop_one() {  # name
  local name="$1" pidf="$RUN_DIR/$1.pid" pid cmd
  [[ -f "$pidf" ]] || { echo "$name: not running (no pid file)"; return 0; }
  pid="$(cat "$pidf" 2>/dev/null || true)"
  if [[ -z "$pid" ]] || ! kill -0 "$pid" 2>/dev/null; then
    echo "$name: stale pid file removed"; rm -f "$pidf"; return 0
  fi
  cmd="$(ps -p "$pid" -o command= 2>/dev/null || true)"
  if [[ "$cmd" != *cashwatch* ]]; then
    echo "$name: pid $pid is not a cashwatch process; not signalling (pid file removed)" >&2
    rm -f "$pidf"; return 0
  fi
  kill -TERM "$pid" 2>/dev/null || true
  for ((i = 0; i < GRACE * 4; i++)); do
    kill -0 "$pid" 2>/dev/null || break
    sleep 0.25
  done
  if kill -0 "$pid" 2>/dev/null; then
    echo "$name: did not exit after ${GRACE}s, sending SIGKILL (including child processes)" >&2
    # The worker supervisor owns a model child; a SIGKILLed supervisor would orphan it.
    pkill -KILL -P "$pid" 2>/dev/null || true
    kill -KILL "$pid" 2>/dev/null || true
    sleep 0.5
  fi
  # Children that outlived a clean supervisor exit (should not happen) are reported, not ignored.
  if pgrep -P "$pid" >/dev/null 2>&1; then
    echo "$name: child processes of $pid still running: $(pgrep -P "$pid" | tr '\n' ' ')" >&2; rc=1
  fi
  if kill -0 "$pid" 2>/dev/null; then echo "$name: still alive (pid $pid)" >&2; rc=1; return; fi
  rm -f "$pidf"
  echo "$name: stopped"
}

# API first (stop admitting work), then the worker (its supervisor terminates the model child).
stop_one api
stop_one worker
exit $rc
