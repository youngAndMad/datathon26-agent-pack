# Windows integration with upstream de3253f

Merged upstream into windows-support. Resolved both add/add conflicts:
- Kept upstream model_fetch.py CLI and pinned MLX/HF revisions, including --only hf.
- Combined Transformers backend: upstream MPS/Unix auto selection and image processing plus Windows CPU default, CUDA FP16, offline/token validation and bounded generation.

Windows scripts use upstream models/manifest.json. Explicit `start.ps1 -Mode test` uses the synthetic registry and real model by default; fake is available only with an explicit test flag and is rejected in production. Dependency installation checks module availability without requiring production rule approvals. Original package versions, lock files and runtime settings match upstream.

Windows checkout was corrupting source snapshot hashes through LF-to-CRLF conversion. `.gitattributes` preserves source snapshot bytes; local snapshots were restored from index bytes without changing their content in Git. Hash enforcement remains enabled.

Checks: 13 mocked HF backend tests passed (agent), 5 actual Windows process/lock tests passed, 2 merged registry/reporting tests passed, PowerShell parse and production/fake rejection checks passed. Tests ran using existing Python 3.13 dependencies, not the intended Python 3.12 runtime. No real model or full service inference has been verified here.

Attempted setup with -DownloadModel -DownloadFfmpeg: pip returned no available distributions for uv==0.10.12 under the managed network restrictions. Attempted start -Mode test: correctly refused missing .venv-windows. Server is not running; dependencies/model were not downloaded. Missing reporting/rules code is no longer a blocker after the upstream merge; production rule approval still requires local human validation.
