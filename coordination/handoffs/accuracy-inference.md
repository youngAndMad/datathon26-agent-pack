# Accuracy inference implementation

Owned changes: `src/cashwatch/inference/{adapter,prompts,validation}.py`,
`tests/inference/test_accuracy_contract.py`, `tests/inference/test_adapter.py`.

## Completed

- Compact prompts preserve every supplied scope condition, boundary condition and
  predicate counterexample; only exact duplicate text is removed.
- Strict compact verification distinguishes complete explicit `unclear` responses
  from malformed/missing statements. Missing statements trigger bounded repair;
  foreign frame references and duplicate question numbers cannot count as evidence.
- Conflicting yes/no evidence for the same frame stays unknown. Changes over time
  with disjoint frame sets retain both supporting and contradicting references.
- Exclusion rechecks run only for unresolved exclusions, never overwrite an already
  known exclusion or erase conflicting evidence. Malformed rechecks stay unresolved.
- Observation provenance combines rendered neutral and cue prompts. Compact
  verification provenance includes rendered verification, repair and actual
  exclusion prompts in call order; public DTO schemas are unchanged.
- Complete-interval requests use existing full JSON verification. Reported
  completeness, visibility, alternatives and limitations are preserved; compact
  responses still cannot establish completeness. Root integration wires the
  internal request flag from the curated rule. Source interval boundaries and
  maximum sampling gap remain independent rules-engine acceptance gates.
- Legacy JSON fallback remains supported for ordinary compact verification.

## Contracts and checks

Consumed `contracts/INTERNAL_CONTRACTS.md`, typed `FrameRef`, `VerificationRequest`,
`VerificationResult`, `ObservationBatch`, and supervised generator contracts.
No dependency, shared runtime configuration, macOS or jury API changes.

Executed:

```text
.venv-windows\Scripts\python.exe -m unittest tests.inference.test_accuracy_contract tests.inference.test_neutral_repair_budget
19 tests passed (12 new synthetic tests plus 7 existing sequencing tests).
git diff --check
Passed.
.venv-windows\Scripts\python.exe -m pytest tests/inference/test_validation.py -q -p no:cacheprovider
2 passed, 25 setup errors: Windows denied access to AppData/Local/Temp/pytest-of-berik.
```

The pytest fixture errors do not establish that its blocked assertions passed.
Follow-up integration used the root's workspace temporary-fixture runner:
`.venv-windows\Scripts\python.exe .tmp\run_accuracy_tests.py` completed with
94 passed and 1 skipped (Windows user lacks symbolic-link creation privilege,
WinError 1314). Ordinary path traversal and hash-tampering checks now run in a
separate test and passed; symlink containment still runs wherever supported.
Updated existing adapter tests assert the full rendered combined prompt hashes
and exactly one bounded repair before malformed evidence fails.
No real model/video benchmark ran: latency, memory and accuracy effects are
unmeasured. Model replies in these tests are scripted synthetic strings. No
competition media, annotations, scene descriptions or private reports were read.
More complete prompts may increase token processing time; compare on the actual
local target hardware before claiming a performance or accuracy improvement.

Integration: retain root's internal `requires_complete_interval` flag wiring and
independent completeness acceptance tests. No new rules are approved here.
