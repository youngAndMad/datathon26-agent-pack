#!/usr/bin/env bash
# Start the cashwatch worker and API from local artifacts only (no installation, no downloads).
# Returns 0 only after GET /readyz is 200; otherwise prints a useful error and exits non-zero.
#
# Env: deploy/cashwatch.env (if present; override with CASHWATCH_ENV_FILE) then the environment.
#   CASHWATCH_MODE           production (default) | test   (test mode is visibly labeled)
#   CASHWATCH_BACKEND        mlx (default) | transformers | fake   (fake refused in production)
#   CASHWATCH_START_TIMEOUT  seconds to wait for /readyz (default 240: model load + warm-up)
#   CASHWATCH_KEEP_ON_FAIL   1 = leave processes running for diagnosis when readiness fails
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
ENV_FILE="${CASHWATCH_ENV_FILE:-$ROOT/deploy/cashwatch.env}"
if [[ -f "$ENV_FILE" ]]; then
  if grep -Ev '^[[:space:]]*#' "$ENV_FILE" | grep -E '(API_KEY|TOKEN|SECRET|PASSWORD)[A-Z_]*=' \
       | grep -Ev '_FILE=' | grep -q .; then
    echo "refusing: $ENV_FILE appears to contain an inline secret; use CASHWATCH_API_KEY_FILE" >&2
    exit 2
  fi
  set -a; source "$ENV_FILE"; set +a
fi

export CASHWATCH_MODE="${CASHWATCH_MODE:-production}"
export CASHWATCH_BACKEND="${CASHWATCH_BACKEND:-mlx}"
# Offline/telemetry flags. These are not proof of no egress (see tests/offline/verify_offline.sh).
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_DATASETS_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1 \
       DO_NOT_TRACK=1 TOKENIZERS_PARALLELISM=false PYTHONUNBUFFERED=1
# Never hand cloud-provider credentials to the service processes.
unset OPENAI_API_KEY ANTHROPIC_API_KEY GOOGLE_API_KEY GEMINI_API_KEY HF_TOKEN HUGGING_FACE_HUB_TOKEN \
      AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AZURE_OPENAI_API_KEY 2>/dev/null || true

case "$CASHWATCH_MODE" in production|test) ;; *)
  echo "refusing: CASHWATCH_MODE must be production or test (got '$CASHWATCH_MODE')" >&2; exit 2;; esac
case "$CASHWATCH_BACKEND" in mlx|transformers|fake) ;; *)
  echo "refusing: unknown CASHWATCH_BACKEND '$CASHWATCH_BACKEND'" >&2; exit 2;; esac
if [[ "$CASHWATCH_MODE" == "production" && "$CASHWATCH_BACKEND" == "fake" ]]; then
  echo "refusing: mock/fake perception adapter is forbidden in production mode (T28)" >&2
  exit 2
fi
if [[ "$CASHWATCH_MODE" == "test" ]]; then
  echo "WARNING: starting in TEST mode — results are labeled test and are not a release configuration" >&2
fi

PY="$ROOT/.venv/bin/python"
[[ -x "$PY" ]] || { echo "missing $PY — run 'make bootstrap' during online preparation (or install the offline bundle)" >&2; exit 2; }
HOST="${CASHWATCH_HOST:-127.0.0.1}"
PORT="${CASHWATCH_PORT:-8000}"
DATA_DIR="${CASHWATCH_DATA_DIR:-$ROOT/data}"
KEY_FILE="${CASHWATCH_API_KEY_FILE:-$ROOT/secrets/jury_api_key}"
RUN_DIR="$DATA_DIR/run"; LOG_DIR="$DATA_DIR/logs"
TIMEOUT="${CASHWATCH_START_TIMEOUT:-240}"
export CASHWATCH_DATA_DIR="$DATA_DIR" CASHWATCH_API_KEY_FILE="$KEY_FILE" CASHWATCH_HOST="$HOST" CASHWATCH_PORT="$PORT"

if [[ ! -f "$KEY_FILE" ]]; then
  echo "missing API key file $KEY_FILE — run: bash deploy/gen_api_key.sh" >&2; exit 2
fi
perm="$(stat -f '%Lp' "$KEY_FILE" 2>/dev/null || stat -c '%a' "$KEY_FILE")"
if [[ "$perm" != "600" && "$perm" != "400" ]]; then
  echo "refusing: $KEY_FILE has mode $perm; run: chmod 600 '$KEY_FILE'" >&2; exit 2
fi
if [[ "$HOST" != "127.0.0.1" && "$HOST" != "localhost" && "$HOST" != "::1" ]]; then
  echo "WARNING: API binds $HOST; public access should go through the TLS reverse proxy only" >&2
fi

umask 077
mkdir -p "$RUN_DIR" "$LOG_DIR"
chmod 700 "$DATA_DIR" "$RUN_DIR" "$LOG_DIR" 2>/dev/null || true

alive() { local f="$RUN_DIR/$1.pid"; [[ -f "$f" ]] && kill -0 "$(cat "$f")" 2>/dev/null; }
for n in worker api; do
  if alive "$n"; then echo "refusing: $n already running (pid $(cat "$RUN_DIR/$n.pid")); run make stop" >&2; exit 2; fi
  rm -f "$RUN_DIR/$n.pid"
done
if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "refusing: TCP port $PORT is already in use" >&2; exit 2
fi

fail() {
  echo "start failed: $1" >&2
  for n in worker api; do
    if [[ -f "$LOG_DIR/$n.log" ]]; then echo "--- last lines of $LOG_DIR/$n.log ---" >&2; tail -n 15 "$LOG_DIR/$n.log" >&2; fi
  done
  if [[ "${CASHWATCH_KEEP_ON_FAIL:-0}" != "1" ]]; then bash "$ROOT/deploy/stop.sh" >&2 || true; fi
  exit 1
}

ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }
echo "[$(ts)] starting worker (mode=$CASHWATCH_MODE backend=$CASHWATCH_BACKEND)" >> "$LOG_DIR/worker.log"
nohup "$PY" -c 'from cashwatch.pipeline.worker import run; run()' >> "$LOG_DIR/worker.log" 2>&1 < /dev/null &
echo $! > "$RUN_DIR/worker.pid"
sleep 1
alive worker || fail "worker exited immediately"

echo "[$(ts)] starting api on $HOST:$PORT" >> "$LOG_DIR/api.log"
nohup "$PY" -c 'from cashwatch.api.main import run; run()' >> "$LOG_DIR/api.log" 2>&1 < /dev/null &
echo $! > "$RUN_DIR/api.pid"

URL="http://$HOST:$PORT"
[[ "$HOST" == *:* ]] && URL="http://[$HOST]:$PORT"
start_s=$(date +%s)
code=000
while :; do
  alive worker || fail "worker process exited (see log)"
  alive api || fail "api process exited (see log)"
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 --noproxy '*' "$URL/readyz" || true)"
  [[ "$code" == "200" ]] && break
  if (( $(date +%s) - start_s >= TIMEOUT )); then
    body="$(curl -s --max-time 3 --noproxy '*' "$URL/readyz" | head -c 600 || true)"
    fail "not ready after ${TIMEOUT}s (last /readyz HTTP $code): $body"
  fi
  sleep 2
done
echo "cashwatch ready in $(( $(date +%s) - start_s ))s: $URL  (mode=$CASHWATCH_MODE, backend=$CASHWATCH_BACKEND)"
echo "logs: $LOG_DIR  pids: $RUN_DIR  stop: make stop"
