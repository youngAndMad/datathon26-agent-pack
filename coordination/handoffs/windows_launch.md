# Windows launch extension handoff

Owned additions: deploy/setup.ps1, deploy/start.ps1, deploy/stop.ps1, scripts/model_fetch.py, docs/runbook/windows.md.

Setup bootstraps pinned uv in .tools using existing Python3.11, installs managed Python3.12, uses separate .venv-windows and uv sync --frozen --extra torch. No existing manifests, source config, .venv or system Python/Node/driver modifications. Explicit -DownloadModel -DownloadFfmpeg downloads immutable Qwen snapshot and checksum-verified Gyan archive. Model SHA256 manifest is separate models/manifest.windows.json. Secrets generated into file with restricted Windows ACL, never printed. Existing key preserved.

Start is production only, CPU by default (CUDA opt-in), loopback, offline flags and no downloads. Preflight imports production modules and validates approved registry. Processes hidden with separate logs and PID/start-time records. Bounded /readyz polling and failure cleanup. Stop checks PID reuse and terminates child process tree. Frozen jury contract unchanged.

Verification: .venv\Scripts\python.exe -m py_compile scripts\model_fetch.py PASS; .venv\Scripts\python.exe scripts\model_fetch.py --help PASS. PowerShell 5.1 Language.Parser.ParseFile across deploy/*.ps1 via temporary -File script PASS (temporary script removed). Inline powershell -Command and python -c through tool shell were unreliable (quotes/variables stripped); their output is not counted as successful validation. No real download or inference performed by this lane. Root performs actual setup attempt. CPU/GPU latency and memory not measured, sample count zero.

Remaining blockers: managed network denies dependency/model downloads; reporting module and rules factories unavailable in current checkout; production registry not approved. No fabricated readiness, metrics or rules. GPU 4GB not qualified for 4B model; CPU deadline not qualified. FFmpeg checksum uses provider trust at preparation, mutable release URL pinned by stored archive SHA rather than repository-pinned upstream version. Root should inspect setup failure and communicate limitations; this extension cannot repair missing domain modules within authorized scope.
