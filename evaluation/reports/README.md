# Evaluation reports

`scripts/qa_benchmark.py` (make benchmark) writes **private** outputs only:

* `evaluation/reports/private/<run>.json` — per-clip rows (opaque clip ids, labels, predicted
  verdict, timecodes, latency, error codes, contract errors) + summary. Git-ignored.
* `evaluation/reports/private/<run>.summary.json` — aggregates only.

No media, frames, base64, descriptions or scene text are stored. Aggregate metrics may be
quoted externally only if the organizer permits; per-clip rows stay local.

## What the numbers mean

| Metric | Definition |
|---|---|
| `video_level` (headline) | Confusion matrix, positive = violation. **Failed / unprocessed / contract-invalid jobs count as wrong answers.** |
| `video_level_processed_only` | Same, excluding failures — always reported next to the headline, never instead of it. |
| `temporal.by_tolerance` | One-to-one Hungarian matching of predicted timecodes to organizer timestamps: maximize matches within tolerance, then minimize total absolute error. Tolerances 0.5/1/2/5 s are internal diagnostics, not organizer thresholds. Types are used only when every labeled clip has reviewed `label_types`. |
| `abs_error_at_5s` | Anchor error of matched pairs at the 5 s tolerance; p90 only when n ≥ 10. |
| latency | upload→terminal and accepted→terminal; nearest-rank p50/p95/max with `n`; `p95_reliable` is false for n < 20. |

`evaluation_level` is `local_real_model` only for a production-mode service with a non-mock
adapter. `test_mode_plumbing` runs (`--allow-test-mode`) are not detection evaluations. Mock
runs are refused.

Status: **BLOCKED** (no authorized dataset; X1/X2). No accuracy numbers exist yet.
