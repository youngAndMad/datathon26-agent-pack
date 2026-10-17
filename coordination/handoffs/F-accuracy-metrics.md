# Accuracy metrics implementation

Owned files: `scripts/dev_eval.py`, `tests/test_dev_eval_metrics.py`.

The development evaluator now reports completed-only classifier metrics separately
from service accuracy over all submitted clips (failed jobs are incorrect). Existing
top-level confusion/precision/recall/F1 fields remain failure-penalized for compatibility
and carry an explicit legacy-semantics warning. New consumers should use
`completed_classification`, `accuracy_all_submissions`, `completion_rate` and
`failure_counts`. Undefined denominators are null; defined zero F1 is zero.

Pipeline callback snapshots are recorded on success and failure and summed without
inventing failure causes. `per_rule_emitted_events` counts outputs, not per-rule
accuracy. `pipeline_diagnostics` counts observed proposals/decisions. A missing
callback is distinguishable through `rows_with_diagnostics`. The existing nearest
timestamp statistic is explicitly labelled as not one-to-one event matching.

Empty selections and unrecognized labels are rejected before model startup. A
recovery warmup exception no longer loses the already failed job from the denominator.
Fingerprints cover the selected profile, registry, model manifest/configuration,
evaluator and prompt/adapter code. They do not attest model weight verification.
Evaluation scope always states development/non-independent evaluation; selecting a
split called holdout does not falsely certify untouched data.

Contracts: `contracts/INTERNAL_CONTRACTS.md`; root-owned
`PipelineContext.diagnostics` callback receives phase/counters/per_rule snapshots.
No jury schema, dependencies, runtime profiles, rule approvals or model changed.

Verification:

```text
.venv-windows\Scripts\python.exe -m unittest tests.test_dev_eval_metrics
```

Seven synthetic unit tests passed. No private dataset or stored evaluation output
was read, and no real-model benchmark was run. No latency/memory or accuracy gain
is claimed. Root integration should run the callback integration tests; a local
human must execute real-data evaluation and review any per-rule error attribution.
