#!/usr/bin/env bash
# Denied-egress verification (T20) + missing-artifact failure (T21) — `make verify-offline`.
#
# Runs the worker and API as fresh processes under `sandbox-exec -f tests/offline/deny-egress.sb`
# (outbound network denied except loopback/unix sockets; DNS resolver socket denied) with
# HF_HUB_OFFLINE=1, performs a real local-inference job on a synthetic clip, restarts both
# processes cold and repeats, and scans the unified log for sandbox network denials.
#
# Usage: bash tests/offline/verify_offline.sh [--backend mlx|fake] [--skip-model] [--json-out FILE]
#   --backend fake  plumbing only; reported as NOT a T20 pass
#   --skip-model    only sandbox self-test + T21 missing-artifact checks
# Exit: 0 all PASS; 1 any FAIL; 2 only BLOCKED/PENDING items.
#
# What this proves / does not prove: see docs/runbook/RUNBOOK.md "Offline verification".
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "BLOCKED: sandbox-exec is macOS-only; on Linux use a network namespace (unshare -n) or a container with network none" >&2
  exit 2
fi
command -v sandbox-exec >/dev/null || { echo "BLOCKED: sandbox-exec not found" >&2; exit 2; }
PY="$ROOT/.venv/bin/python"
[[ -x "$PY" ]] || { echo "BLOCKED: $PY missing" >&2; exit 2; }
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_DATASETS_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1 DO_NOT_TRACK=1
unset HF_TOKEN HUGGING_FACE_HUB_TOKEN OPENAI_API_KEY ANTHROPIC_API_KEY 2>/dev/null || true
exec "$PY" "$ROOT/tests/offline/run_offline_check.py" --profile "$ROOT/tests/offline/deny-egress.sb" "$@"
