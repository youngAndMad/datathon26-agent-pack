# Windows compatibility integration

Scope: additive Windows support; existing model settings, runtime profiles, Python requirements, package versions and dependency locks remain unchanged.

Changes coordinated with the inference and deployment lanes:
- Local Transformers backend implementing the existing GenRequest/GenResult interface.
- Windows binary subprocess IPC, bounded calls, process cleanup and accelerator reservation.
- Portable decoder memory accounting and Windows child environment; optional POSIX core-dump setting.
- Separate Windows setup/start/stop scripts and model snapshot preparation.

Root-owned files: `.gitignore`, `src/cashwatch/media/{decode,_worker,service,tools,platform_support}.py`, `tests/test_platform_support.py`, this handoff. Lane-owned paths and checks are described in the other Windows handoffs.

Verification:
- `.venv\Scripts\python.exe -m unittest discover -s tests -p test_platform_support.py -v`: 1 passed on Windows Python 3.11.
- Inference lane: 9 mocked HF backend tests passed on existing Python 3.13; no real model weights loaded.
- Runtime lane: 5 Windows tests passed on existing Python 3.13, including actual subprocess timeout/restart and lock exclusion with an explicit scripted test backend.
- `.venv\Scripts\python.exe -m compileall -q src/cashwatch/inference src/cashwatch/media src/cashwatch/gpulock.py scripts/model_fetch.py`: passed.
- `git diff -- pyproject.toml uv.lock src/cashwatch/settings.py configs config frontend/package.json frontend/package-lock.json`: empty.
- Executed `powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy\setup.ps1 -DownloadModel -DownloadFfmpeg`: stopped at bootstrap because pip could not access any distribution for uv==0.10.12. Dependencies and weights were not downloaded; no service started.

Unverified: real Transformers model load, decoder integration with PyAV/FFmpeg, full application startup, offline real inference and five-minute throughput. Available NVIDIA device has 4096 MiB VRAM and driver 527.54; no inference latency or model memory measurement was performed.

Preparation blockers: shell network traffic is blocked by the managed proxy at 127.0.0.1:9; pip has no usable index and npm requires cached artifacts. Existing repository also lacks `cashwatch.reporting` and exports `create_rules`/`load_registry` from `cashwatch.rules`; rule approval is not supplied. Windows changes do not bypass these production gates or supply fabricated rules/results. Existing Python installations are not replaced; runtime setup targets its separate `.venv-windows` environment.
