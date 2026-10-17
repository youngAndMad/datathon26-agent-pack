# Stable runbook commands (spec §14). Owned by the orchestrator.
# No target downloads anything except `bootstrap` and `fetch-models` (explicit online preparation).

SHELL := /bin/bash
PY := .venv/bin/python
UVRUN := uv run --frozen --offline
export HF_HUB_OFFLINE := 1
export HF_HUB_DISABLE_TELEMETRY := 1
export TRANSFORMERS_OFFLINE := 1
export DO_NOT_TRACK := 1

.PHONY: doctor bootstrap fetch-models fixtures test-contracts test benchmark build-offline start smoke verify-offline submission-check stop frontend

doctor:
	$(PY) -m cashwatch.pipeline.doctor

bootstrap:
	HF_HUB_OFFLINE=0 uv sync --frozen --extra mlx
	cd frontend && npm ci --no-audit --no-fund

fetch-models:
	HF_HUB_OFFLINE=0 $(PY) scripts/model_fetch.py

fixtures:
	$(PY) tests/fixtures/build_contract_fixtures.py
	$(PY) scripts/make_synthetic_media.py

test-contracts:
	$(PY) -m pytest tests/contracts

test:
	$(PY) -m pytest -m "not model and not slow"

frontend:
	cd frontend && npm run build

benchmark:
	$(PY) scripts/qa_benchmark.py

build-offline:
	bash deploy/build_offline.sh

start:
	bash deploy/start.sh

smoke:
	$(PY) scripts/qa_smoke.py

verify-offline:
	bash tests/offline/verify_offline.sh

submission-check:
	$(PY) scripts/qa_submission_check.py

stop:
	bash deploy/stop.sh
