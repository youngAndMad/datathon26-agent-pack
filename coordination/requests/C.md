# Requests from agent C (local inference)

## C-R1 Promote model manifest
* Change: copy `models/manifest.proposed.json` -> `models/manifest.json` (orchestrator-owned).
* Reason: `create_perception` in production mode refuses a model folder not listed in a manifest
  (falls back to the proposed file until then). Entries carry repo/revision, per-file size+sha256,
  license, MLX conversion provenance, library versions, hardware, revision cross-check.
* Consumers: worker startup / doctor (`cashwatch.inference.resolve_model_entry`, `check_model_files`).

## C-R2 Runtime profile does not fit the stage budgets at its current values (measured)
* Change: `configs/runtime-profile.json` sampling must be re-chosen from the measured table in
  `coordination/handoffs/C.md` (16 frames @640 px per window costs ~9-10 s prefill alone; 16 windows
  cannot fit 130 s; 24-frame verification @640 px cannot fit 70 s / 8 candidates).
* Proposed: `max_frames_per_observation` and `model_long_side_px` / window length per the handoff
  recommendation; `max_refinement_frames` <= 16 at 384 px and `verify_max_new_tokens` 384; and either fewer
  windows (longer windows at lower fps) or a lower `max_candidates_refined`.
* Consumers: pipeline sampling plan, media thumbnails (B), budget guards (O).

## C-R3 Optional settings for the adapter (no change required to run)
* Add to `Settings` (env CASHWATCH_...): `perception_label_mode: Literal["id","index"]="index"`,
  `perception_call_cap_sec: float=60`, `perception_max_pixels: int|None=None`.
* Today these are `AdapterConfig` defaults inside `src/cashwatch/inference/adapter.py`; the pipeline
  can pass `config=adapter_config_from_profile(settings.profile, ...)` to `create_perception`.
* Consumers: worker wiring only.

## C-R4 Pipeline handling of adapter refusals
* `PipelineError("internal_error", "...split the window")` is raised (never silent truncation) when a
  call exceeds `max_images_per_call` (32) or the visual-token cap; the pipeline should split windows
  so this never happens with the chosen profile.
* After any `model_unavailable` / `gpu_oom` / `deadline_exceeded` raised by observe/verify,
  `ready()` is False; the worker must stop accepting work and call `warmup(timeout_sec=...)` before
  the next job (do not retry inside the failed job unless the original deadline allows a full
  warmup + call).
