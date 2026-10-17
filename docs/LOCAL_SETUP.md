# Running cashwatch locally (team guide)

This guide gets the full service — API, worker with the local vision model, and the Russian UI —
running on your own laptop. Everything runs locally; no cloud AI is involved.

> **Data rule (read first).** Competition videos, frames, model outputs on them, scene descriptions
> and annotations stay on your machine. Never commit them, never paste them into ChatGPT/Claude/
> Copilot chat or any other cloud tool. Keep them under `data/` or another git-ignored folder.

## 1. Requirements

| | Needed |
|---|---|
| Real model (MLX) | Apple Silicon Mac (M1–M4), ≥16 GB RAM (model peak ≈7 GB), macOS 14+ |
| Real model (NVIDIA) | `transformers` backend exists but is **untested on CUDA** — expect to debug |
| Plumbing only | Any OS: test mode with the `fake` backend (no detection, clearly labeled) |
| Tools | `git`, [`uv`](https://docs.astral.sh/uv/) ≥0.10, Node ≥24.8 + npm, `ffmpeg` + `ffprobe` (with libx264) |
| Disk | ~6 GB (model 4.6 GB + venv) |

macOS: `brew install uv node ffmpeg`.

## 2. One-time setup (needs internet)

```bash
git clone https://github.com/youngAndMad/datathon26-agent-pack
cd datathon26-agent-pack

# Python 3.12 env from the lockfile (exact pinned versions)
uv sync --frozen --python 3.12 --extra mlx          # NVIDIA/Linux: --extra torch instead

# Frontend build (served by the API at /)
(cd frontend && npm ci && npm run build)

# Model weights, pinned revision (~4.6 GB). NVIDIA: --only hf
HF_HUB_OFFLINE=0 .venv/bin/python scripts/model_fetch.py --only mlx
.venv/bin/python scripts/model_verify.py --key qwen3-vl-4b-instruct-mlx-8bit   # sha256 check (NVIDIA: --key qwen3-vl-4b-instruct)

# Synthetic test clips (~45 s, generated, no real footage)
.venv/bin/python scripts/make_synthetic_media.py

# Your local API key (written to secrets/jury_api_key, mode 0600, never printed)
bash deploy/gen_api_key.sh
```

Check the machine:

```bash
make doctor     # OK/WARN/FAIL/BLOCKED per item; "rule registry approved=0" FAIL is expected (see §6)
make test       # unit/contract tests, no model needed (~1 min)
```

After setup everything runs offline.

## 3. Run the service (test mode, real model)

Production mode refuses to start until at least one rule is approved (§6). For local work use
**test mode** with the synthetic test rule — every result is labeled "ТЕСТОВЫЙ РЕЖИМ":

```bash
make start-test            # = CASHWATCH_MODE=test CASHWATCH_REGISTRY_PATH=rules/registry.test.yaml deploy/start.sh
```

It returns once `/readyz` is ready (≈5 s after the first load) and prints the URL, normally
`http://127.0.0.1:8000`. Stop with `make stop`. Logs: `data/logs/`.

Other modes:

```bash
# plumbing only, any OS, no model (results are meaningless, labeled test)
CASHWATCH_MODE=test CASHWATCH_BACKEND=fake CASHWATCH_REGISTRY_PATH=rules/registry.test.yaml bash deploy/start.sh

# NVIDIA (untested)
CASHWATCH_MODE=test CASHWATCH_BACKEND=transformers CASHWATCH_MODEL_PATH=models/qwen3-vl-4b-instruct \
  CASHWATCH_REGISTRY_PATH=rules/registry.test.yaml bash deploy/start.sh
```

Port in use? `CASHWATCH_PORT=8010 make start-test`.

## 4. Try it

**Browser:** open `http://127.0.0.1:8000`, paste the key (`cat secrets/jury_api_key` in your own
terminal — the UI keeps it in memory only), choose
`tests/fixtures/generated/marker_violation_40s.mov`, press «Анализировать». Expect «Обнаружено
возможное нарушение» at ~00:20 with evidence frames, after ~3 minutes on an M4 Pro.
`marker_negative_40s.mov` should give «Нарушение не обнаружено».

**Jury API (curl):**

```bash
KEY=$(cat secrets/jury_api_key)
curl -s -H "X-API-Key: $KEY" -F "file=@tests/fixtures/generated/marker_violation_40s.mov" \
     http://127.0.0.1:8000/api/v1/analyze                      # -> {"job_id":"..."}
curl -s -H "X-API-Key: $KEY" http://127.0.0.1:8000/api/v1/jobs/<job_id>   # processing | done | failed
```

**Black-box validator** (schema + evidence + timing checks):

```bash
.venv/bin/python scripts/qa_jury_client.py --base-url http://127.0.0.1:8000 \
  --key-file secrets/jury_api_key --file tests/fixtures/generated/marker_violation_40s.mov
```

Measured on M4 Pro / 48 GB (synthetic, small n): 40 s marker clips 164–173 s; 145 s 1080p clip 81 s.
Your laptop may be slower; one analysis at a time is allowed (a second upload gets 429).

## 5. Running your own clips

You can upload any MOV through the UI or curl. In test mode only the synthetic rule TEST01 exists,
so real footage will normally come back `no_violation` — that says nothing about real rules.
Results and uploaded files are stored under `data/jobs/` (git-ignored). Delete them when done:
`make stop && rm -rf data/jobs data/db`.

## 6. Why production mode is "not ready", and how to unblock it

`rules/registry.yaml` contains nine candidate rules (CW01–CW09) whose official clause text is
already captured and hash-verified, but **none is approved**: approval requires local review of real
clips. The cloud coding agents are not allowed to look at the footage, so this is team work:

1. Put the dataset somewhere outside the repo (or under `data/`) and set `export CASHWATCH_DATASET_DIR=...`.
2. Annotate locally following `evaluation/annotations/README.md` (neutral actions first, then rule
   links; keep organizer labels separate from your annotations; group related takes in the split).
3. For the shortlisted rules (CW01 departure during cash receipt, CW02 previous client's cash left
   out, CW04 personal items) confirm applicability — open questions are in `coordination/handoffs/D.md`.
4. Record validation counts and owner in the rule's `approval` block, set `state: approved`.
   The loader re-checks source hashes and refuses incomplete approvals.
5. `make doctor` should then show approved ≥1; `make start` (production) becomes ready.
6. Evaluate: `scripts/qa_benchmark.py` over a private manifest (`evaluation/manifest/README.md`);
   reports go to `evaluation/reports/private/` (git-ignored). Share only aggregate numbers.

## 7. Working together

- Branch per task (`rules-cw01`, `eval-split`, `ui-fixes`), open PRs; don't push to `master` directly.
- Shared files — `src/cashwatch/{dto,ports,settings}.py`, `pyproject.toml`, `uv.lock`, `Makefile`,
  `configs/` — change only after agreeing in the team (ownership map: `coordination/ownership.md`).
- Benchmark timing on one machine at a time; record hardware and sample count with every number.
- Useful commands: `make test`, `make test-integration`, `make test-offline-quick`, `make verify-offline`.

## 8. Troubleshooting

| Symptom | Fix |
|---|---|
| `start.sh` times out | `tail data/logs/worker.log`; usually missing model (`scripts/model_fetch.py`) or registry error |
| Upload returns 503 | worker not ready yet / crashed — check `curl -s 127.0.0.1:8000/readyz` |
| Upload returns 429 | an analysis is already running; wait for it |
| Video won't play in UI | MOV/HEVC; evidence frames still work, preview MP4 needs ffmpeg with libx264 |
| `make doctor` FAIL rule registry | expected until a rule is approved (§6); use `make start-test` |
| Want a clean slate | `make stop && rm -rf data/` (removes all local jobs, db and logs) |

More detail: `docs/runbook/RUNBOOK.md`, known limits: `docs/limitations/LIMITATIONS.md`.
