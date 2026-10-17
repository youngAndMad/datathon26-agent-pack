# Source identity integrity follow-up

Owned changes: `src/cashwatch/rules/registry.py`, `tests/rules/test_registry.py`.

The runtime source check now binds the official Adilet document URL to the
`document.group_number` in the already hashed offline API snapshot. A wrong
document, malformed document path or absent snapshot identity prevents activation.
Canonical Russian, Kazakh and English document paths are recognized; trailing
slashes, queries and fragments do not change the document identity. Existing
amendment snapshots remain independent sources.

No registry entries, legal text, approval statuses, model settings, shared DTOs or
dependencies changed. Tests mutate only in-memory copies of public registry data.

Verification (Windows existing Python 3.12 environment):

```
.venv-windows\Scripts\python.exe -m pytest tests/rules/test_registry.py::test_registry_rejects_wrong_document_url_even_with_valid_snapshot tests/rules/test_registry.py::test_registry_rejects_missing_snapshot_document_identity tests/rules/test_registry.py::test_registry_accepts_matching_document_identity tests/rules/test_registry.py::test_production_registry_has_zero_approved_rules_and_fails_readiness_honestly tests/rules/test_registry.py::test_snapshots_hash_match_and_excerpts_are_verbatim_official_slices tests/rules/test_registry.py::test_test_registry_refused_in_production_and_loaded_only_in_test_mode -q -p no:cacheprovider
```

Result: 10 passed. `git diff --check` on both changed code/test files passed.
No model benchmark or restricted footage inspection performed; no latency/memory
claim follows from these unit tests.

Remaining limitations: production rules still require actual local visual
validation and accountable applicability review. URL identity does not itself
verify a legal interpretation or current operative edition. Excerpt lists may
contain supporting amendment sources, so no blanket same-source restriction was
introduced. Future provenance work could require at least one excerpt from the
principal cited document after checking the intended multi-document design.

Integration: include these changes in the orchestrator's master commit; no push
or standalone commit performed by this lane.
