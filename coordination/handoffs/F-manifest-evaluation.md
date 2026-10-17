# Local evaluation manifest input

Changed: `scripts/dev_eval.py`, `tests/test_dev_eval_manifest.py`.

The development evaluator accepts the version 1 manifest emitted by
`evaluation/manifest/build_manifest.py` through `--manifest`. The legacy index
remains the default. `--expected-clips` checks the final selection count;
`--check-selection` validates input and prints aggregate counts without loading
the model. Manifest split selection uses manifest metadata, not the legacy split
file. Invalid labels, absent source files, duplicate IDs/hashes and unsafe output
components are rejected before inference. Existing run directories cannot be
overwritten. Full timestamp lists are retained in rows; the existing temporal
summary still scores only the first label timestamp and explicitly states this.
The summary fingerprints the selection source.

Consumed: shared internal pipeline contracts (organizer labels remain outside
perception inputs), existing manifest v1 and evaluator report conventions.

Verification:

```
.venv-windows\Scripts\python.exe -m unittest tests.test_dev_eval_manifest
```

10 tests passed, using only synthetic dictionaries and mocked file metadata.
`git diff --check` passed. No competition data was read, no model inference was
run, and no real latency, memory or accuracy measurements were collected.

Limitations: manifest SHA values are checked for format and uniqueness; input
file content is hashed by the existing evaluator when running each job, not by
the lightweight selection audit. Manifest labels must be prepared and checked
locally by an authorized human. Split names do not prove independent holdout
status. Evaluation reports remain private and git-ignored.

Integration: orchestrator wires these arguments into the Windows CUDA evaluation
wrapper; no dependency or shared configuration changes required. No commit made.
