# Handoff — Agent C (local visual model)

All measurements below are on **synthetic generated frames only** (gray background, yellow zone,
red square inside/outside). No competition media, frames or real-scene model outputs were
read or produced. Synthetic sanity numbers show plumbing and simple-state perception only, **not**
cashier-domain accuracy (blocked by D004).

## Owned files changed (no commits; precise list)

* `src/cashwatch/inference/__init__.py` — `create_perception(settings, *, config=None, responder=None, stderr_path=None)`, `resolve_model_entry`, `check_model_files`, `adapter_config_from_profile`
* `src/cashwatch/inference/adapter.py` — `LocalPerception` (PerceptionPort), `AdapterConfig`, `CallStats`
* `src/cashwatch/inference/supervisor.py` — `ChildGenerator` (model child process, timeouts, kill/respawn), `child_env`
* `src/cashwatch/inference/child.py` — child entry `python -m cashwatch.inference.child`
* `src/cashwatch/inference/protocol.py` — length-prefixed pipe protocol, `GenRequest`/`GenResult`, `OFFLINE_ENV`
* `src/cashwatch/inference/backends/{__init__,mlx_backend,hf_backend,scripted}.py`
* `src/cashwatch/inference/prompts.py` — versioned templates + hashes
* `src/cashwatch/inference/validation.py` — strict parse/validate/neutralize
* `src/cashwatch/inference/vision_budget.py` — exact Qwen3-VL visual-token math
* `src/cashwatch/inference/fake.py` — explicit test-mode mock
* `src/cashwatch/inference/synthetic.py` — synthetic plumbing frames (tests/bench only)
* `tests/inference/{conftest,inference_canned,test_validation,test_adapter,test_supervisor,test_model_real}.py`
* `scripts/model_manifest.py`, `scripts/model_verify.py`, `scripts/model_bench.py`, `scripts/model_bench_report.py`, `scripts/model_offline_check.py`
* `models/README.md`, `models/manifest.proposed.json`
* `coordination/requests/C.md`
* Generated, git-ignored: `models/qwen3-vl-4b-instruct-mlx-4bit-local/` (3.1 GB, local offline 4-bit conversion; optional, may be deleted), `data/bench/*.json`, `data/logs/model-child.log`

## Functionality completed

* **Process boundary (D005).** The worker holds `LocalPerception`. The model runs in a child started with an argument list (no shell), its own session, and a scrubbed env (provider/API/HF tokens and proxies removed; `HF_HUB_OFFLINE`, `TRANSFORMERS_OFFLINE`, `HF_HUB_DISABLE_TELEMETRY`, `DO_NOT_TRACK` forced). The model loads once. Every call has a hard timeout of `min(deadline.remaining_sec(), per-call cap)`. On timeout, crash, OOM or a generation exception the child is SIGKILLed (process group) and `ready()` becomes False. `warmup()` respawns, loads, and runs one tiny generation; `ready()` is true only after that succeeds.
* **Error mapping.** Timeout bound by the job deadline → `deadline_exceeded`. Timeout from the per-call cap, child crash, load failure or generation failure → `model_unavailable`. Metal/MPS out-of-memory messages → `gpu_oom`. Two invalid outputs → `model_output_invalid`. Missing, escaping, or hash-mismatched thumbnail, an oversized call, or a malformed request → `internal_error` (a pipeline bug; never silently truncated).
* **MLX backend (primary).** Uses mlx-vlm `generate` with all frames in one call, interleaved in order as `"Frame <label> at <t>s:" <image>`. Decoding is greedy (temperature 0). `max_tokens` = `request.max_output_tokens`. Images come from bytes that the parent read inside `work_dir`, containment-checked (`resolve()` + `is_relative_to`, which also covers symlinks) and sha256-checked against `FrameRef.sha256`. The child never opens job paths and never sees URLs. Optional caps: `max_long_side_px` (aspect-preserving downscale, default 640) and `max_pixels` (processor). Before each call the visual-token cost is computed exactly (`vision_budget.py`); a call over `max_visual_tokens_per_call` is refused so the pipeline splits it. The child reports both expected and actual visual-token counts; they matched in every benchmark run.
* **Transformers backend (comparison).** `Qwen3VLForConditionalGeneration`, bf16, SDPA, MPS, `local_files_only=True`, `trust_remote_code=False`, `do_sample=False`. Same interface.
* **Fake backend.** `backend="fake"` gives `is_mock=True`, adapter `"fake"`. It is deterministic and uses only request ids: empty observations, all cues/predicates `unknown`, `interval_complete`/`visibility_sufficient` false. A `responder` hook exists for tests. `create_perception` raises `model_unavailable` when `mode == "production"`.
* **Prompts.** `observe-v1` / `verify-v1` / `repair-v1` follow agents/LOCAL_MODEL_PROMPTS.md, with additions:
  * a `cue_assessments` section (one entry per `ObservationRequest.cue_predicates`), an explicit JSON shape, and brevity limits;
  * an optional per-call integer frame-label mode (`index`, now the default), which has its own hashes.
  * `prompt_sha256` = sha256(system + `\n\x00\n` + user template + frame-label template [+ index note]); the hashes are in `prompts.PROMPT_REGISTRY`.
* **Validation.** Strips code fences and prose, then validates into the dto types.
  * Rejected (→ one repair): unknown frame ids/labels/ranges, unknown or duplicate predicate ids, unknown enums, non-bool booleans, `window_id`/`candidate_id` mismatch, oversized arrays, over-long text, and actor refs that are not short anonymous labels.
  * Ignored: extra keys (including any model timestamps).
  * Downgraded to `unknown` (never strengthened): `supported`/`contradicted` without frame ids.
  * Missing predicates are filled as `unknown`, with a limitation note.
  * Neutralized to `[removed]`, counted in limitations: legal citations (en/ru), verdict/accusation/authorization words, URLs/adilet, identity claims, monetary amounts and discrepancies.
  * Observation ids are reassigned as `<window_id>.o<n>`. Frame lists are in presentation order. `interval_start/end_sec` come from the request FrameRefs (min/max of supporting∪contradicting).
* **Repair.** At most one repair call. It is text-only (no images) and gets the validation error plus the allowed ids; if the first output hit the length limit, the repair is told to compress. Its budget is `min(max_output_tokens, 384)`. A second failure raises `model_output_invalid`.

## Contracts consumed

`cashwatch.ports.PerceptionPort`; dto `ObservationRequest/ObservationBatch/VerificationRequest/VerificationResult/PredicateAssessment/Observation/ModelInfo/FrameRef`; `Deadline`; `PipelineError` codes; `Settings` (`mode`, `backend`, `model_path`, `model_manifest`, `device`, `data_dir`, `profile`); `gpulock.gpu_reservation`. No shared file was modified.

## Exact model and libraries

* Primary: `mlx-community/Qwen3-VL-4B-Instruct-8bit` @ `0943db6e15185b86be368d3cf0704aec740b142b`. `model.safetensors` is 5,104,903,807 B, sha256 `49313ba7eb04191cb38fff8486ff87436d5fb6dcbb3eb9a32df250b0bef7f60d`. It is a third-party conversion (mlx-vlm 0.3.4, affine 8-bit, group 64); the base revision was not recorded by the converter, and parity with bf16 is not established.
* Reference: `Qwen/Qwen3-VL-4B-Instruct` @ `ebb281ec70b05090aa6165b016eac8ec08e71b17` (2 bf16 shards, about 8.9 GB).
* License: Apache-2.0 for both, from the model-card front matter (neither snapshot ships a LICENSE file).
* Revision check: the commit hash in each folder's HF download metadata matches, and LFS etag = sha256.
* All files with sha256 and sizes: `models/manifest.proposed.json`. `scripts/model_verify.py` reports OK.
* Libraries: mlx 0.32.4, mlx-metal 0.32.4, mlx-vlm 0.7.8, transformers 5.20.1, torch 2.14.1, torchvision 0.29.1, accelerate 1.15.0, tokenizers 0.23.3, safetensors 0.8.0, huggingface-hub 2.2.0, pillow 13.0.0, numpy 2.5.4, Python 3.12.13.
* Processor: mlx-vlm `Qwen3VLImageProcessor` (patch 16, merge 2, min_pixels 65536, max_pixels 16777216 from the shipped preprocessor_config). It gives the same token counts as transformers' `Qwen2VLImageProcessorFast`: identical prompt tokens 4665 for the 16-frame call.
* Hardware: Apple M4 Pro, 48 GB unified memory, macOS 26.6.2, Metal (no CUDA).

Load command (what the child does): `mlx_vlm.load("<repo>/models/qwen3-vl-4b-instruct-mlx-8bit")`, after a folder check (config, safetensors and tokenizer present; refused if any `*.py` file exists).

## Verification commands and outcomes

* `.venv/bin/python -m pytest tests/inference -m "not model"` → **52 passed** (validation, repair/failure codes, index labels/ranges, neutralization, containment/hash, request checks, token budget, production refuses fake, and the real-subprocess supervisor: hang→kill within the cap, deadline-bound timeout, crash, OOM, load failure, warmup timeout/recovery, env scrubbing).
* `.venv/bin/python -m pytest tests/inference -m model` → **1 passed** (real MLX warmup + observe on synthetic frames, 220 visual tokens/frame, revision in `ModelInfo`).
* `.venv/bin/python scripts/model_offline_check.py --backend mlx` → exit 0. It ran under `/usr/bin/sandbox-exec -p '(version 1)(allow default)(deny network-outbound)(allow network-outbound (remote unix-socket))'` with `HF_HUB_OFFLINE=1`; `tcp 1.1.1.1:443` and `huggingface.co:443` were **blocked (EPERM)**. Real warmup + observe (8 synthetic frames) succeeded: parse ok, 158 output tokens, 14.8 s including spawn/load. The model child inherits the sandbox.
* `.venv/bin/python scripts/model_verify.py` → OK (all 3 folders).
* Benchmarks: `scripts/model_bench.py` (see table), JSON in `data/bench/`, tables via `scripts/model_bench_report.py data/bench/*.json`.

## Measured latency / memory (synthetic, M4 Pro 48 GB, GPU reservation held, n = runs per row)

Cold start (spawn + load + warmup, OS file cache warm): **3.0–3.5 s** (load 2.5–2.9 s, warmup 0.4–0.5 s), n=10 across runs. A first load after reboot was not measured. Transformers cold start: 12.9 s.

Speeds, MLX 8-bit: prefill about 430–590 tok/s (the vision tower alone takes ~2.2 s for 16×640 px), decode about 35–41 tok/s. MLX 4-bit decodes at 50–66 tok/s.

Visual tokens/frame (expected = actual in every run): 640×360 → **220**, 480×270 → 120, 384×216 → 84. The system prompts are 674 (observe) / 485 (verify) tokens per call.

Main table: MLX 8-bit, index labels (`data/bench/C-bench-mlx-2-index.json`). Latency includes any repair call; output tokens include the repair.

| call | px | frames | max_new | n | p50 s | max s | prompt tok | median out tok | repaired | failed | MLX peak GB | child RSS GB |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| observe | 640 | 16 | 512 | 5 | 20.1 | 34.6 | 4665 | 460 | 0 | 1 | 6.7 | 5.7 |
| observe | 480 | 16 | 384 | 5 | 13.9 | 26.7 | 3065 | 275 | 2 | 0 | 6.4 | 5.6 |
| observe | 384 | 16 | 384 | 5 | 19.3 | 21.7 | 2489 | 528 | 3 | 0 | 6.3 | 5.5 |
| observe | 384 | 12 | 256 | 5 | 14.6 | 17.3 | 2085 | 405 | 3 | 1 | 6.3 | 5.5 |
| verify | 640 | 24 | 384 | 5 | 23.3 | 24.3 | 6373 | 329 | 0 | 0 | 7.0 | 5.9 |
| verify | 384 | 16 | 384 | 5 | 10.2 | 15.9 | 2309 | 226 | 2 | 0 | 6.3 | 5.5 |
| verify | 384 | 12 | 256 | 5 | 14.3 | 15.0 | 1909 | 398 | 4 | 0 | 6.3 | 5.5 |

The same calls with real frame ids instead of index labels (`C-bench-mlx-1.json`, 640/16/512 observe): p50 21.5 s on 3 successful runs, with 2 of 5 runs failing on truncation. Every 384/480-px and 24-frame-verify profile failed on 3–5 of 5 runs at max_new 256/384. Index labels plus range strings ("3-9") were the most effective change.

MLX 4-bit (local conversion, `C-bench-mlx-3-4bit-index.json`): observe 640/16/512 p50 23.2 s; 480/16/384 failed 2/5 runs; verify 640/24 p50 24.5 s. Peak memory is 4.3–5.0 GB. Decode is faster, but outputs were about 2× longer and less format-compliant, so there is **no net gain**. Rejected.

Transformers bf16 / MPS / SDPA (`C-bench-hf-index.json`, n=3): observe 640/16/512 p50 36.2 s (max 37.7); verify 640/24/384 p50 35.4 s (max 39.0). MPS driver memory is 11.4–12.5 GB. About 1.7× slower than MLX. Rejected for runtime.

Synthetic plumbing sanity (MLX 8-bit, 640 px, when the output fit): the zone cue was in the expected state on 4/4 runs; 28 supporting frame ids were all correct and 32 contradicting ids were all correct. At lower resolutions there were some wrong contradicting ids (for example 4 at 480 px). The second synthetic cue ("whole rectangle visible") was often judged wrong, which shows over-interpretation of a simple image. This is **not** domain accuracy.

## Budget for a 145 s clip — does NOT fit with the current profile

* **Coarse pass, 16 windows vs 130 s.** 640/16/512: 16 × 20.1 = 322 s at p50. Best measured, 480/16/384: 16 × 13.9 = **222 s at p50**, 427 s at max. Neither fits.
  * To fit 130 s at about 14 s per call, the plan needs ≤ 9 calls. For example, ~9 windows of 16 frames at 480 px covering 145 s (~1 fps, little overlap) gives about 125 s at p50. The tail is still over budget: a repaired call takes about 27 s.
* **Refinement, 8 verifies vs 70 s.** 640/24: 8 × 23.3 = 187 s. 384/16: 8 × 10.2 = **82 s at p50** (127 s at max). Does not fit; about 4–5 verifications at 384/16 fit in 70 s at p50.
* **Ladder tried in order.** Smaller output: helps only with terse prompts and index labels; a tight `max_new` (256) *hurts*, because truncation triggers a repair that doubles latency. Fewer visual tokens: 480 px helps (−1600 prompt tokens per 16 frames), 384 px gave no gain because outputs got longer. Fewer frames per window: 12 frames gave no gain. 4-bit: no net gain. The binding cost is decode at ~36 tok/s × 250–500 output tokens, plus ~0.5 ms per prompt token.
* **Recommendation for O04 (to be validated end-to-end; numbers are synthetic):**
  * 8-bit MLX, index labels;
  * `model_long_side_px` 480, ≤16 frames per call, `observe_max_new_tokens` 512 as a ceiling (actual output is about 250–300 tokens);
  * ~9 coarse windows per 145 s;
  * verify at 384 px with ≤16 frames, `verify_max_new_tokens` 384, and `max_candidates_refined` ≤ 4;
  * keep cue lists short, since each cue adds output tokens.
* There is **no five-minute guarantee**: real-scene outputs may be longer than synthetic ones, and the end-to-end run has not been measured (blocked by D004).

## Failure behaviour summary

| situation | result | ready() after |
|---|---|---|
| deadline already expired | `deadline_exceeded` (no call) | unchanged |
| call exceeds remaining deadline | child killed → `deadline_exceeded` | False |
| call exceeds per-call cap (60 s observe/verify, 30 s repair) | child killed → `model_unavailable` | False |
| child crash / exit / pipe broken | `model_unavailable` | False |
| Metal/MPS OOM message | child killed → `gpu_oom` | False |
| model folder missing/unlisted (production)/size mismatch | `create_perception` raises `model_unavailable` | — |
| load failure or warmup timeout | `warmup` raises `model_unavailable` | False |
| invalid output twice | `model_output_invalid` | unchanged (child healthy) |
| bad thumbnail (escape/missing/hash), >32 frames, over visual-token cap, unordered/duplicate ids, frame outside window ±1 s | `internal_error` | unchanged |
| production + backend fake | `create_perception` raises `model_unavailable` | — |

## How the worker should use it

```python
from cashwatch.inference import create_perception, adapter_config_from_profile
perception = create_perception(settings, config=adapter_config_from_profile(settings.profile))  # no load yet
perception.warmup(timeout_sec=180)        # spawn child + load once + tiny generation; raises PipelineError
assert perception.ready(); info = perception.info()   # ModelInfo (is_mock False for mlx/transformers)
batch = perception.observe(req, work_dir, deadline)   # work_dir = job work dir containing FrameRef.local_path
result = perception.verify(vreq, work_dir, deadline)
# after any model_unavailable / gpu_oom / deadline_exceeded: ready() is False -> report not ready,
# finish the job as failed, then perception.warmup(timeout_sec=180) before claiming the next job.
perception.close()                        # graceful shutdown, then SIGKILL fallback
```

* Hold `gpu_reservation(...)` around the worker's model lifetime if other agents benchmark on the same host.
* The child's stderr goes to `data/logs/model-child.log` (library warnings and tracebacks only; no prompts or outputs).
* `AdapterConfig.keep_raw_text` must stay False for real media.

## Remaining assumptions and limitations

* Domain accuracy, real-scene output length and real end-to-end latency are unmeasured (D004). Synthetic sanity is not evidence of accuracy.
* The 8-bit conversion's parity with bf16 is unknown, and the converter did not record the base revision.
* Repair is text-only: it cannot add visual facts, only reformat or compress.
* Model self-assessment is not calibrated, and agreement between observe and verify is not independent evidence.
* Range strings and integer labels are application-mapped; the real `frame_id`s never come from the model in index mode.
* Neutralization is pattern-based (en/ru). It removes words such as "authorized"/"permission" even in harmless contexts, which is intentionally conservative.
* MLX OOM on macOS may appear as a jetsam SIGKILL; that maps to `model_unavailable`, not `gpu_oom`.
* Cold-after-reboot load time is not measured; observed loads were 2.5–2.9 s with the file cache warm and up to ~13 s for Transformers.
* No `RLIMIT` memory cap is set on the child (not meaningful for Metal allocations).

## Requested integration actions

See `coordination/requests/C.md`:

* C-R1: promote the manifest.
* C-R2: re-choose the runtime profile from the table above; the current one does not fit.
* C-R3: optional Settings fields.
* C-R4: the pipeline must split oversized calls and re-warm after failures.

The orchestrator marks gates.
