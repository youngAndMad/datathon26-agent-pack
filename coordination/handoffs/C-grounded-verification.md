# Grounded verification follow-up

Changed files: `src/cashwatch/inference/validation.py`, `adapter.py`, `prompts.py`; `tests/inference/test_accuracy_contract.py`, `test_adapter.py`.

Compact verification requires explicit frame references for positive and negative answers. Bare negatives no longer acquire invented references to every frame. Partial fallback preserves independently valid rows using strict per-row grammar; malformed, duplicate and unreferenced answers remain unknown. Explicit unclear is a valid answer. JSON answers with overlapping support and contradiction are unknown as well.

The single repair attempt for compact verification now rechecks supplied images and original questions, bounded by the existing repair call timeout, total deadline and token caps. Text-only formatting cannot establish missing visual evidence. Coarse observation cues still propose whole windows, so downstream acceptance must use verified references, never coarse anchors alone.

Contracts consumed: `contracts/INTERNAL_CONTRACTS.md`, `VerificationRequest`, `PredicateAssessment`, `VerificationResult`. No DTO/API/dependency/profile changes.

Validation:

- `.venv-windows\Scripts\python.exe -m unittest tests.inference.test_accuracy_contract tests.inference.test_neutral_repair_budget`: 23 passed.
- `.venv-windows\Scripts\python.exe scripts\qa_accuracy_tests.py`: 96 passed, 1 Windows symlink privilege skip. This run preceded the final JSON conflict guard, which is covered by the focused unittest run above.
- Direct pytest initially encountered existing Windows temporary-folder ACL errors; the existing workspace-temp wrapper resolved those fixture errors.

No model benchmark or competition media accessed. Extra visual retry latency is unmeasured and bounded, not free. No accuracy improvement claimed. Integration: enforce verified source-frame anchoring downstream and measure full pipeline latency locally before release.
