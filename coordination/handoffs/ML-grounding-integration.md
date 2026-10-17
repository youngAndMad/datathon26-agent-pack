# Grounding and detail integration

Implemented parser grounding, visual bounded retry, safe partial salvage, source-safe
event deduplication, stricter service/item conditions, disabled CW14 experiment,
optional anchor-focused sampling, optional native desk crops, and evaluation code hashes.
See `docs/ML_IMPROVEMENTS.md` for behavior, limitations and exact evaluation command.

Contracts: existing DTOs, RulesPort, MediaPort and INTERNAL_CONTRACTS.md. No public
API schema, shared config, dependencies or Python version changed. CUDA 4 GiB profile
is isolated; dense sampling is enabled there, desk ROI requires explicit local calibration.
No new bank rule activated. Approved-rule historical counts are not current validation.

Verification on Windows:

- `.venv-windows\Scripts\python.exe scripts\qa_accuracy_tests.py`: 149 passed, 1 skipped (Windows symlink capability).
- `.venv-windows\Scripts\python.exe -m unittest tests.media.test_roi tests.pipeline.test_desk_detail tests.pipeline.test_tz_regressions -q`: 13 passed.
- `.venv-windows\Scripts\python.exe -m unittest discover -s tests -p test_dev_eval_metrics.py -q`: 7 passed.
- `git diff --check`: passed, only line-ending warnings.
- Real synthetic CUDA benchmark, Qwen3-VL-2B NF4, RTX3050 Laptop 4 GiB, 8 frames384px: observe42.469s (repaired), verify7.875s (ok), n=1 per stage. No domain-quality inference.

Local development evaluation started at `data/dev_eval/cuda-grounded-v2/`, 40 unique
clips with CW11/CW12 and isolated Windows v2 profile. Final metrics pending at handoff.
Private labels, media and per-clip outputs are not included here. No timestamp-quality
claim because the current local manifest contains no reviewed reference timestamps.

Remaining: camera-specific ROI calibration and actual model ROI evaluation, separate CW01/CW14
candidate experiments, normative exception review, suitable host for 8B comparison,
independent permitted new-angle recordings, public API/queue test. No automatic deployment.
