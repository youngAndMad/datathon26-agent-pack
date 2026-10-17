# Handoff — agent F (independent verification & deployment)

Cloud-hosted agent: synthetic, non-sensitive media only. No real videos or real-media outputs
were touched. No commits made by F (the orchestrator committed part of this work already;
the remaining changes are listed below). No GPU/model runs by F.

## Owned files changed

- `scripts/qa_jury_client.py` — black-box jury client + validator (schema via `referencing` registry; §5.3 semantics; latency; stable-bytes re-poll; exit codes 0/1/2/3/4/5)
- `scripts/qa_smoke.py` — real-inference smoke; refuses mock, refuses test mode unless `--allow-test-mode`, refuses if model identity unknown
- `scripts/qa_benchmark.py` — private-manifest benchmark: confusion matrix (failures counted as errors + labeled processed-only), Hungarian one-to-one matching at 0.5/1/2/5 s, latency p50/p95/max with n; refuses mock; BLOCKED without manifest
- `scripts/qa_submission_check.py` — secret/media/weights scan (tracked + untracked-unignored), gitignore, mock config, frontend offline/no key, model manifest, registry approved-count + snapshot hashes, docs, key perms, deadline
- `evaluation/manifest/build_manifest.py`, `evaluation/manifest/README.md`, `evaluation/reports/README.md`
- `deploy/start.sh`, `deploy/stop.sh`, `deploy/gen_api_key.sh`, `deploy/build_offline.sh`, `deploy/cashwatch.env.example`, `deploy/Caddyfile.example`, `deploy/launchd/com.cashwatch.{api,worker,caffeinate}.plist`
- `tests/contracts/{__init__,conftest,test_schemas,test_semantic_validator,test_dto_schema_consistency}.py`
- `tests/integration/{__init__,conftest,harness,test_service_e2e,test_qa_tooling,test_deploy_scripts}.py`
- `tests/offline/{__init__.py,deny-egress.sb,verify_offline.sh,run_offline_check.py,audit/sitecustomize.py}`
- `docs/runbook/RUNBOOK.md`, `docs/limitations/LIMITATIONS.md`, `docs/submission/CHECKLIST.md`, `docs/submission/LICENSES.md`
- `coordination/requests/F.md`, this file

Uncommitted at handoff time (per `git status`): `deploy/stop.sh` (kills supervisor children on SIGKILL escalation), `tests/integration/test_qa_tooling.py`, `tests/offline/run_offline_check.py`, new `tests/offline/audit/`, `docs/`, `coordination/requests/F.md`, `coordination/handoffs/F.md`.

## Contracts consumed

`contracts/*.schema.json`, `contracts/fixtures/*`, `contracts/openapi.json`, `cashwatch.dto` (JuryDoneResult, UiReport, ErrorCode, TIME_DECIMALS), `cashwatch.settings.Settings` env names, entry points `cashwatch.api.main:run` / `cashwatch.pipeline.worker:run`, `python -m cashwatch.pipeline.doctor`, `cashwatch.gpulock.gpu_reservation`, worker_status row in `<data>/db/cashwatch.sqlite3` (read-only fallback for run identity), agent B fixture names from `scripts/make_synthetic_media.py`.

## Verification commands and outcomes (host: Apple M4 Pro, 48 GB, macOS 26.6; 2026-10-17)

| Command | Outcome |
|---|---|
| `.venv/bin/python -m pytest tests/contracts` | 77 passed |
| `.venv/bin/python -m pytest tests/contracts tests/integration -o addopts=""` | **161 passed, 2 skipped** (skips: labeled-marker expectation needs the real model) in 42.5 s, fake backend, TEST mode |
| `bash -n` on deploy/*.sh, tests/offline/*.sh | all OK (also a test) |
| `plutil -lint deploy/launchd/*.plist` | OK |
| `bash tests/offline/verify_offline.sh --backend fake` | 8 PASS, 1 BLOCKED (fake backend ≠ T20); exit 2 |
| `bash tests/offline/verify_offline.sh --skip-model` | sandbox self-test + T21 PASS |
| start.sh/stop.sh test-mode round trip (temp data dir, port 18765) | ready in 2 s; smoke correctly REFUSED fake adapter; clean stop; key absent from logs |
| `bash deploy/build_offline.sh` | bundle built (164 files, 1.7 MB, model hashing ~7 s); exit 1 because `models/manifest.json` missing |
| `.venv/bin/python scripts/qa_submission_check.py` | 10 PASS, 1 WARN, 1 FAIL (models/manifest.json), 5 BLOCKED |
| `.venv/bin/python scripts/qa_benchmark.py` | BLOCKED (no private manifest), exit 2 |

Fake-backend timings below are plumbing only (n=1 each), not detection or production latency:
40 s synthetic clip upload→done ≈ 2.0–2.1 s; T17 kill at 0.41 s → retried job done at 22.6 s (≈ lease 20 s); T19 with 1 s internal deadline → failed before 60 s bound (3.1 s test).

## Acceptance matrix (spec §13.2)

| ID | Status | Evidence |
|---|---|---|
| T01 auth all endpoints | **PASS** | `test_t01_auth_required_everywhere[api_only,service]`: 401 on analyze/status/report/media/evidence for missing, wrong and query-string key; identical error code; key not echoed; no files created in data dir by rejected uploads |
| T02 octet-stream MIME | **PASS** (fake) | jury client uploads `application/octet-stream`; all valid synthetic clips accepted + done |
| T03 renamed / unsupported / corrupt | **PASS** (fake) | `mov_renamed_bin` accepted+done; `empty`, `png_renamed`, `mkv_renamed`, `truncated_nomoov` rejected 4xx or failed with media error code, never done |
| T04 oversize/huge/decode bomb | pending (B/A tests) | not in F harness |
| T05/T06 timelines | partial (fake) | cfr_10s_30, bframes_10s, rotated_cw90_10s end-to-end valid incl. anchor==evidence and time-in-duration checks; timing accuracy is B's suite |
| T07 evidence JPEG/provenance | **PASS (validator)** / pending real | validator decodes JPEG (PIL), rejects data:/non-JPEG/PNG/bad base64, 1–3 frames, anchor equality — unit tests; real evidence needs approved rule |
| T08 negative/positive clip | pending real model | `test_labeled_synthetic_marker_with_real_model` ready (`CASHWATCH_IT_BACKEND=mlx`, holds GPU lock) |
| T09–T16, T22 | not F-owned | rules/inference/media lanes |
| T17 kill worker mid-job | **PASS** (fake) | SIGKILL of worker process group after claim → restart → terminal (retried to done at 22.6 s); never stuck processing |
| T18 two concurrent uploads | **PASS** (fake) | codes [202, 429]; 429 carries numeric Retry-After; accepted job valid; repeated polling stable bytes |
| T19 deadline mid-stage | **PASS** (fake) | internal deadline 1 s → failed `deadline_exceeded`/`worker_lost`, no fabricated done |
| T20 denied egress + cold restart | **BLOCKED (real model)** / plumbing PASS | harness ready: sandbox self-test, audit-hook control, two sandboxed cold starts, no egress attempts — passes with fake; real `--backend mlx` run left to orchestrator integration step (GPU lock) |
| T21 missing artifact | **PASS** | doctor FAIL lines for missing model and missing registry (each with a positive control); service never ready with missing model dir |
| T23 UI key handling | pending (E) | submission check: no key/VITE_*KEY in dist, no external URLs |
| T24 new angle | BLOCKED (X1) | |
| T25 public HTTPS | BLOCKED (X3) | Caddyfile + procedure in RUNBOOK §6 |
| T26 retention/cleanup | documented | RUNBOOK §9; API sweeper calls `cleanup_retention`; retention allowance unconfirmed (X4) |
| T27 idempotency | pending (A) | not in F harness |
| T28 production + mock | **PASS** | worker and API exit non-zero (process test); `Settings.production_errors`; start.sh refuses before spawning; smoke/benchmark refuse fake adapter |

## Deploy prerequisites

`.venv` (make bootstrap), model dir + `models/manifest.json`, `frontend/dist`, ffmpeg/ffprobe on PATH, `secrets/jury_api_key` (gen_api_key.sh, 0600), `deploy/cashwatch.env` from the example, ≥1 approved rule for production readiness (D011), Caddy + DNS/TLS on a team-controlled host (X3), sleep prevention (caffeinate plist / pmset), FileVault on, `data/` excluded from backups.

Capacity: one active job, no queue (second concurrent upload → 429). Real-model capacity/latency not measured by F.

## License inventory (verified from .venv metadata / model cards / ffmpeg -version)

See `docs/submission/LICENSES.md`. Highlights: Qwen3-VL-4B-Instruct and the mlx-community 8-bit conversion declare Apache-2.0 (model card front matter only; no LICENSE file in snapshots). **Homebrew FFmpeg 8.0.1 is a GPL-3.0 build** (`--enable-gpl --enable-version3`); not bundled. PyAV 19.0.1 BSD-3; its bundled FFmpeg reports LGPL-3.0+ but the wheel also ships libx264/libx265 (GPL-2.0+ upstream) — review before redistribution. MLX/mlx-vlm MIT; transformers Apache-2.0; React MIT. Regenerate the table with:
`.venv/bin/python -c "import importlib.metadata as m;[print(d.metadata['Name'],d.version,d.metadata.get('License-Expression') or d.metadata.get('License')) for d in m.distributions()]"`

## Open risks found in other lanes (reported, not edited)

1. **R11 dependency audit:** `mlx-vlm` pulls `mlx-audio`, `sounddevice`, `miniaudio` (audio I/O), `opencv-python`, `datasets` into the lock. Unused by cashwatch but present; needs a runtime import audit (F-R5).
2. **`/readyz` exposes no run identity** (mode/adapter/is_mock). Smoke/benchmark fall back to reading the local SQLite worker_status, which fails for remote HTTPS smoke (F-R2).
3. **Production cannot become ready** while the production registry has 0 approved rules — intended (D011), but means `make start`/`make smoke` in production fail until local validation is done.
4. `contracts/fixtures/jury-violation.json` description is `Источник: FIXTURE…` with no adilet URL — schema-valid, fails the strict provenance check (fine as a placeholder; tests document it). Production `RuleRef.official_url` pattern enforces adilet.zan.kz.
5. Worker supervisor: if it is SIGKILLed, its core/model child is orphaned (keeps the model and may claim jobs). stop.sh now kills children on escalation; launchd `KeepAlive` restarts could still leave an orphan after a hard kill — consider `start_new_session` + killpg or a parent-death check in the core.
6. Sweeper runs only in the API process; with the API down, abandoned running jobs are reconciled only when the API restarts (acceptable since status is served by the API, noted for ops).
7. `make test` excludes `slow`, so service-level integration tests do not run in `make test` (F-R3).
8. `deploy/cashwatch.env` is not git-ignored (F-R1).
9. Agent C was running `scripts/model_bench.py` (MLX child process) during F's work; F did not touch it.

## Remaining assumptions / limitations

- Caddyfile not machine-validated (caddy not installed on this host).
- `--fetch-wheels` path (pip download with `--require-hashes` for macOS arm64 cp312) not executed (would need network; explicit online preparation).
- sandbox-exec limits: see RUNBOOK §7 (XPC/mach networking daemons not denied; OS page cache not cold; macOS only).
- All fake-backend results are plumbing evidence only, never detection/accuracy evidence.

## Requested integration actions

1. Orchestrator integration step with the real model: `CASHWATCH_IT_BACKEND=mlx .venv/bin/python -m pytest tests/integration` and `bash tests/offline/verify_offline.sh` (default `--backend mlx`; both take the GPU reservation) → fills T08 synthetic / T20.
2. F-R1…F-R5 in `coordination/requests/F.md` (gitignore, `/readyz` identity, Makefile targets, `models/manifest.json`, audio-dependency audit).
3. Commit the uncommitted F files listed above.
