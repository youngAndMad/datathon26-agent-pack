# Requests from agent F (QA & deployment)

## F-R1 → orchestrator: ignore the local deploy env file
- Change: add `deploy/cashwatch.env` and `deploy/Caddyfile` to `.gitignore`.
- Reason: operators copy `deploy/cashwatch.env.example` → `deploy/cashwatch.env` (host-specific paths;
  must never be committed). start.sh refuses inline secrets, but the file should still not be tracked.
- Consumers: deploy/start.sh, deploy/stop.sh, scripts/qa_submission_check.py (currently WARN).

## F-R2 → agent A (via orchestrator): non-secret run identity in `/readyz`
- Change: include `{"mode": settings.mode, "adapter": info.adapter, "is_mock": info.is_mock,
  "model": info.model_id, "revision": info.revision, "registry": registry.version}` in the `/readyz`
  JSON (both 200 and 503), taken from the worker heartbeat `info` the worker already writes.
  No paths, no hardware inventory, no secrets.
- Reason: `make smoke` and `make benchmark` must refuse mock/test mode black-box over HTTP (spec §14,
  T28). Today they fall back to reading `data/db/cashwatch.sqlite3` `worker_status` read-only, which
  only works on the same host and breaks for the public HTTPS smoke (T25).
- Consumers: scripts/qa_smoke.py, scripts/qa_benchmark.py (`service_identity`, already supports both).

## F-R3 → orchestrator: Makefile targets
- `test` excludes `slow`, so the service-level integration tests never run in `make test`. Add
  `test-integration: $(PY) -m pytest tests/integration` and `test-offline-quick: bash tests/offline/verify_offline.sh --skip-model`.
- Optional explicit online-preparation target: `bundle-wheels: bash deploy/build_offline.sh --fetch-wheels --with-models`
  (downloads; must stay out of `build-offline`, which performs no network access).

## F-R4 → orchestrator: `models/manifest.json`
- `make build-offline` and `make submission-check` FAIL until `models/manifest.json` exists with
  license, revision and per-file sha256 (agent C's `models/manifest.proposed.json` has the shape).
  build_offline.sh cross-checks recorded file hashes against the files it hashes.

## F-R5 → orchestrator (dependency audit, R11): audio packages in the lock
- `mlx-vlm==0.7.8` pulls `mlx-audio`, `sounddevice`, `miniaudio` (audio I/O) and `opencv-python`,
  `datasets`. Nothing in cashwatch should import them. Request: a runtime import audit (e.g. the worker
  asserts `"sounddevice" not in sys.modules` after warm-up) or document why they are inert, for the
  R11 "no audio processing" evidence.
