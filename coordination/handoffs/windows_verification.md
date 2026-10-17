# Installed Windows environment verification

Checked 2026-10-17 in the managed agent environment. Runtime: Python 3.12.13, PyTorch 2.14.1+cpu. Original package manifests, locks and runtime configuration still match origin/master.

Passed:
- `scripts/model_verify.py --key qwen3-vl-4b-instruct`: 14 model files verified, OK.
- Frontend build exists; `npm run typecheck` passed.
- Bundled ffmpeg starts and includes libx264.
- Focused backend suite: 145 passed and 19 subtests passed. Includes contracts, rules, reporting and inference units; this is not an end-to-end inference result.

Incomplete/blocked:
- Same suite: 103 setup errors caused by denied pytest temporary directory access, 39 skipped (FFmpeg was not on that test process PATH). No assertion failures reported in that run. Cache was disabled after a denied cache-directory creation. API/storage/pipeline coverage is incomplete.
- `npm test -- --run`: Vite dependency loading fails with spawn EPERM. Alternative runner config loader also fails (require is not defined); no config or dependency changes were made to hide failures.
- Real service start: SQLite cannot open data/db/cashwatch.sqlite3; access to data/db is denied in this environment. API exited. Worker supervisor was stopped using PowerShell Stop-Process after taskkill was denied. Tracked launch records were cleaned by stop.ps1.
- CPU resources: 15.73 GiB physical RAM total, 3.21 GiB available at inspection, CUDA unavailable in installed torch. Current real backend uses FP32 on CPU; 4B weights need roughly 16 GB before processing overhead. No real model load or timing claim is made; current resources are insufficient for a practical run of this profile.

Fixed during verification:
- Windows startup now checks storage before spawning API/worker.
- Startup preserves original exception if cleanup also fails.
- Windows stop includes identity-checked PowerShell fallback when taskkill fails and reports unverified descendant cleanup honestly.
- Re-ran start after the change: storage preflight fails clearly before any service starts.

No competition recordings or derived content were inspected. No configuration versions changed. At this checkpoint there was no claim of macOS verification, production readiness, successful video detection, or a running local service.

## Subsequent Windows runtime check (2026-10-17)

- Added explicit test-only inherited-ACL runtime root `data/windows-local`, with production refusal and no edits to existing private ACLs.
- Added optional BF16 CPU/disk placement (2 GiB resident weight budget, not a total RAM limit); default backend behavior is preserved.
- Started `deploy/start.ps1 -Mode test -WindowsLocalTest -LowMemory -TimeoutSeconds 700`. UI 200, unauthenticated job request 401, readiness 200 with transformers/is_mock=false after actual model warmup.
- Blank synthetic MOV was correctly rejected as input_unusable; a 1-second testsrc2 MOV was accepted (202) but failed with model_unavailable after 63.765 seconds at the inference stage deadline. No successful end-to-end real inference claim. Worker rewarms following the failed generation; readiness is 503 during recovery.
- Windows targeted checks: 15 HF backend unittest tests, 3 storage unittest tests and 15 media path pytest tests passed; 1 POSIX-only path test skipped. The earlier broader suite/frontend test limitations still apply.
- Shared pyproject.toml, uv.lock, settings.py, configs and frontend package manifests have no diff against origin/master. macOS was not executed on this Windows machine.
- Local service remains available at http://127.0.0.1:8000; real inference performance is the outstanding blocker. Test results are in ignored data/windows-local/*smoke-summary.json. No competition media was used.
