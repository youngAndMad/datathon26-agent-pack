# Delivery plan (orchestrator)

Status legend: pending / active / blocked / ready_for_integration / accepted.

## Facts at G0 (2026-10-17)
- Project: fresh copy of the implementation pack; no prior code. Local git initialized (no remote).
- Host: Apple M4 Pro (14 cores), 48 GB unified memory, macOS 26.6.2, ~78 GB free disk. No NVIDIA GPU.
- Toolchain: Python 3.12.13 (uv 0.10.9), Node 24.8, ffmpeg/ffprobe 8.0.1, Docker CLI present.
- Models: downloading `Qwen/Qwen3-VL-4B-Instruct@ebb281ec70b05090aa6165b016eac8ec08e71b17` and
  `mlx-community/Qwen3-VL-4B-Instruct-8bit@0943db6e15185b86be368d3cf0704aec740b142b` into `models/` (ignored by git).
- Dataset: organizer archive `open.zip` supplied 2026-10-17, extracted to git-ignored `data/dataset/open/` (42 MOV + `open_labels.xlsx`). Cloud agents saw metadata aggregates only: all 42 decode; H.264 464x848 portrait, ~29.8–30 fps, no audio, no rotation; duration 11.6–142.5 s (median 22.7, total 1444 s); longest samples in 1.1 s. Labels and footage NOT inspected by cloud agents (D004).
- Agents: orchestrator + 6 workers are cloud-hosted models ⇒ synthetic/public data only (decision D004).

## Blockers (external)
| ID | Blocker | Blocks | Needed from |
|---|---|---|---|
| X1 | ~~Dataset path~~ (resolved: data/dataset/open). Still needed: LOCAL human/agent to inspect it | D02, D04 (rule approval), F03 accuracy, G1 "longest authorized MOV", G2 real clip, G3 | Team / organizer |
| X2 | Local (non-cloud) reviewer for footage, annotations, error analysis | same as X1 | Team |
| X3 | Deployment host decision (this laptop vs other machine), public DNS/TLS, permitted ingress | G4 public HTTPS, T25 | Owner decision (no paid provisioning by agents) |
| X4 | Event deadline date/timezone, jury concurrency, retention allowance | `SUBMISSION_DEADLINE_ISO`, capacity, cleanup policy | Organizer |
| X5 | Official full-text snapshots of L01–L04 from adilet.zan.kz (retrieval inconsistent per research) | rule source verification | Agent D attempts; else blocked |

## Gates
See `coordination/release-gates.md`.

## Waves
- Wave 1 (now, parallel): A, B, C, D, E, F against frozen G0 contracts.
- Orchestrator in parallel: pipeline package (plan builder, coverage ledger, stage machine, validator, writer, worker loop) against ports with test doubles.
- Wave 2: integration (pipeline ↔ real media ↔ real MLX adapter ↔ rules ↔ storage ↔ API ↔ UI), synthetic end-to-end with real model in test registry mode, offline denied-egress check, latency on 145 s synthetic MOV.
