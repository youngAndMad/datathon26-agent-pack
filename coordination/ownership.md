# File ownership (G0, frozen 2026-10-17)

One owner per path. Edit only your own paths. Shared changes: write `coordination/requests/<agent>.md`
(reason, exact change, affected consumers) and continue against the frozen interface.
Never reset/clean/overwrite others' files. Run Python via `uv run --frozen ...` or `.venv/bin/python`;
never `uv add`/`uv sync`/`pip install` (the venv and lockfile belong to the orchestrator).

| Owner | Paths |
|---|---|
| Orchestrator (O) | `pyproject.toml`, `uv.lock`, `Makefile`, `.gitignore`, `.env.example`, `src/cashwatch/__init__.py`, `src/cashwatch/{dto,ports,deadline,errors,settings,gpulock}.py`, `src/cashwatch/pipeline/`, `configs/`, `contracts/`, `models/manifest.json`, `coordination/*.md` (except requests/handoffs of others), `docs/adr/`, `tests/pipeline/`, `tests/fixtures/` (shared synthetic fixture builders), `README.md` (top-level) |
| A API & jobs | `src/cashwatch/api/`, `src/cashwatch/storage/`, `tests/api/`, `tests/storage/` |
| B media & evidence | `src/cashwatch/media/`, `tests/media/`, `scripts/make_synthetic_media.py` |
| C local inference | `src/cashwatch/inference/`, `tests/inference/`, `scripts/model_*.py`, `models/README.md`, `models/manifest.proposed.json` |
| D rules & data | `rules/` (registry.yaml, sources/), `src/cashwatch/rules/`, `src/cashwatch/reporting/`, `evaluation/annotations/`, `tests/rules/`, `tests/reporting/` |
| E frontend | `frontend/` (incl. its own `package.json`, `package-lock.json`, `node_modules/`) |
| F QA & deployment | `deploy/`, `tests/contracts/`, `tests/integration/`, `tests/offline/`, `evaluation/reports/`, `evaluation/manifest/` tooling, `scripts/qa_*`, `docs/runbook/`, `docs/limitations/`, `docs/submission/` |

Handoffs: `coordination/handoffs/<A|B|C|D|E|F>.md`. Requests: `coordination/requests/<A|B|C|D|E|F>.md`.

GPU/accelerator: real model runs only while holding `cashwatch.gpulock.gpu_reservation()` (file lock at
`data/.locks/gpu.lock`). No parallel model jobs.
