# Rule condition refinement

Owned files: `rules/registry.yaml`, `tests/rules/test_service_item_conditions.py`, this handoff.

## Implemented

- CW11 v0.3.0: client arrival alone is insufficient; actual new service must be visible. The decisive question requires the earlier received cash to be followed into the next service, rather than treating any initial pile as previous cash. Added an explicit visible-storage exclusion. Unknown storage remains unknown and cannot supply missing positive cash continuity or visibility.
- CW12 v0.4.0: requires a visible cashier-item action, same-item association and a specific evidence frame. Proximity, another person's presence, leaving the desk, bag color and client-item transfer do not establish personal ownership.
- CW11 intentionally uses visible cash/document exchange as a conservative operational service cue; it may miss legitimate service that begins through other visible actions. This narrowing is not claimed as the statutory definition of service and must be measured in local recall evaluation.
- Registry version bumped. Existing authorization and source statuses retained as requested. Historical validation counts explicitly identify old versions and do not validate changed prompts/gates. No new rule activated, no normative source or legal meaning changed.
- Ten synthetic tests exercise positive gates, unknown/contradicted service or item association, visible storage rejection and unchanged approved rule IDs. These tests do not establish model accuracy.
- Follow-up: CW14 v0.3.0 remains disabled. Receipt of the same batch, continuing counting after actual departure and visibility of the customer service area are explicit. Standing is not departure; an empty chair is not proof of absence. Added visible completed-count exclusion and clarified that an unseen handover is not proof of internal counting.

Contracts consumed: `AGENTS.md`, `agents/D_RULES_DATA.md`, `RULES_CATALOG.md`, `contracts/INTERNAL_CONTRACTS.md`, `src/cashwatch/rules/schema.py`, existing tri-state engine behavior.

Verification: `.venv-windows\Scripts\python.exe -m pytest tests\rules\test_service_item_conditions.py tests\rules\test_candidate_mode.py -q -p no:cacheprovider` — 20 passed (17 condition tests and 3 existing candidate-mode tests). No model runs; no latency/memory claims. No competition videos, annotations or logs inspected.

## Remaining research and evaluation work

CW10 currently combines three different patterns with OR: cash left, client present, safe open. Do not enable it as-is. The client-service departure branch already exists as disabled CW01 with clause 22, explicit service-in-progress and service-ended exclusion. Evaluate that existing candidate instead of duplicating rule IDs. The stored-cash branch must establish earlier accepted cash; departure, a bag or an open safe alone is insufficient under the existing public mapping for clause 20 part 2. CW02/CW11 are narrower existing candidates. There is no justified generic open-safe or unattended-bag rule in these verified mappings. New branch approval still needs local validation and applicability review.

CW14's safer wording is implemented but not visually validated. It remains disabled pending candidate evaluation. No legal source, applicability status or approval was changed; there are no historical validation counts to migrate. Do not simply weaken its trigger to any counting or any client movement to improve recall.

CW12 has a pre-existing normative exception gap: the registry cites permitted medical items and communications devices without photo/video functionality, but the current question list does not explicitly evaluate these exceptions. Do not interpret every unidentified handheld device as a prohibited phone; camera capability cannot be assumed from holding it. Further exception gates need careful local and normative review rather than an invented visual shortcut.

Integration: re-evaluate changed versions separately against the old registry with an unchanged model/profile/dataset. Report false negatives, false positives, unknowns and failures, not accuracy alone. No winner-takes-all suppression between independent rules was added. The revised registry retains old owner authorization operationally; this is not a fresh empirical approval.
