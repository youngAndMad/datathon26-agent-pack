# Event deduplication hardening

Owned changes: `src/cashwatch/rules/engine.py`, `tests/rules/test_event_dedup.py`.

The merge stage now consumes all connected duplicate clusters, including later
windows bridging previously separate clusters. Same-rule events require overlapping
accepted intervals or shared decisive evidence; ordinary context frames and temporal
proximity alone never collapse events. Existing visible-reset checks remain active.
Different source hashes cannot merge, and mixed-source evidence is rejected.
The earliest verified decisive anchor and deterministic evidence selection remain
unchanged in meaning; no synthetic timestamp or confidence score is introduced.

Contracts consumed: `contracts/INTERNAL_CONTRACTS.md`, CandidateEvent, AcceptedEvent,
FrameRef, and the existing rule anchor convention. No public/internal schema changes.
No registry activation or source applicability claim was made.

Verification:

- `.venv-windows\Scripts\python.exe -m pytest tests\rules\test_event_dedup.py -q -p no:cacheprovider`: 7 passed.
- `.venv-windows\Scripts\python.exe -m pytest tests\rules\test_engine.py -q -p no:cacheprovider`: 19 passed; 5 fixture setup errors because the sandbox cannot access the existing AppData pytest temporary root. Re-run with the repository workspace-temp runner for integration.
- `git diff --check`: passed (only existing LF/CRLF warnings).

All fixtures are synthetic shapes/DTOs. No private media, GPU inference, model quality,
latency, or memory measurements were performed. No commit was created.

Integration: include the new test file in the orchestrator's focused suite. Existing
`decide` already prevents a proposed anchor from replacing missing verified decisive
frame IDs; two added tests lock this behavior down. Merge assumes accepted intervals
were produced by `decide`, not unverified proposal windows. This change cannot recover
events the model never detected.
