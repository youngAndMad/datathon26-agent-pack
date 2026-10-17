# Accuracy improvements integration

Scope: public code and synthetic fixtures only. No competition media, private annotations or per-clip results inspected. No shared configuration, dependency lock, Python version or macOS startup changes. No additional bank rules approved.

## Implemented

- Full compact verification conditions/counterexamples; valid complete uncertainty is accepted, malformed output receives one bounded repair, contradictory evidence stays unresolved.
- Targeted rechecks of unknown exclusions only.
- Explicit internal `VerificationRequest.requires_complete_interval` supplied by the registry-driven rules engine; structured JSON route preserves model completeness/visibility answers. Existing endpoint, gap and opportunity-completion gates remain necessary.
- Aggregate pipeline diagnostics hook covering coarse cues, proposals, verification decisions/errors and emitted events; no frame identifiers or scene text in snapshots.
- Order-sensitive stage prompt digests include earlier windows instead of retaining only the last call. Adapter observation digest includes cue prompt.
- Evaluation summary v2 separates completed classification from reliability and all-submission correctness; legacy penalized fields labelled, empty selections rejected, recovery failures retained.
- Separate ignored sampling profiles and one-rule-at-a-time plan, metadata split validator and aggregate comparison tool.

## Verification

`.venv-windows\Scripts\python.exe -m unittest tests.inference.test_accuracy_contract tests.inference.test_neutral_repair_budget tests.test_dev_eval_metrics tests.test_accuracy_experiments tests.pipeline.test_accuracy_diagnostics tests.rules.test_omission_routing tests.pipeline.test_tz_regressions tests.inference.test_hf_backend tests.inference.test_cuda_nf4`

71 passed. All synthetic/mock tests, not real inference accuracy.

`.venv-windows\Scripts\python.exe -m pytest tests/contracts -q -p no:cacheprovider`

77 passed. Jury contract unchanged.

`.venv-windows\Scripts\python.exe scripts\qa_accuracy_tests.py`

94 passed, 1 skipped (Windows symlink creation privilege unavailable). Path traversal and hash-integrity tests still run separately. Runner uses normal inherited-ACL temporary folders in the workspace; no filesystem permission settings changed.

Experiment profiles prepared under ignored `data/accuracy-experiments/accuracy-improvements-v1`; no inference run. Detailed commands and interpretation in `docs/runbook/accuracy-experiments.md`.

## Limits and next local steps

- Production metrics on 41 previously tuned clips do not establish independent holdout accuracy. New unseen scenes/angles and reviewed local labels are needed.
- Per-rule emitted counts do not measure per-rule recall/precision without event-type ground truth.
- Windows NVIDIA driver now reports 617.42. GPU environment contains CUDA torch2.14.1 but the probe fails importing `typing_extensions`; preparation may still be in progress in the user's terminal. Do not modify a concurrent installation.
- No real-model latency, memory reduction, accuracy improvement or training result measured in this work.
- Keep all real-data evaluation inside the local authorized workflow described by AGENTS.md. All proposed sampling variants are unmeasured and must respect the existing five-minute limit.
