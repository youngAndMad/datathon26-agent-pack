# Tickets

| Ticket | Owner | Status | Notes |
|---|---|---|---|
| O00 inspect repo/env/data boundaries | O | accepted | plan.md facts; dataset absent (X1) |
| O01 freeze DTOs/schemas/deps/ownership | O | accepted | `src/cashwatch/{dto,ports,deadline,errors,settings}.py`, `uv.lock`, ownership.md |
| A01 auth gate, streamed upload, reservation | A | active | |
| A02 durable jobs/leases/fencing/sweeper | A | active | |
| A03 polling projection, report/media/evidence routes | A | active | |
| B01 probe + presentation timeline | B | active | |
| B02 sampling, manifest, evidence | B | active | |
| C01 model manifest + load spike | C | active | MLX 8-bit and HF reference downloading |
| C02 observe/verify adapters + bounds | C | active | |
| C03 memory/time profile, denied egress | C | active | synthetic only; domain timing blocked by X1 |
| D01 official source capture + rule draft review | D | active | X5 risk |
| D02 local data audit/split/annotation | D | blocked | X1, X2, D004 |
| D03 predicates, exclusions, dedup, descriptions | D | active | |
| D04 activate validated rules | D | blocked | needs D02 |
| E01 upload/key/progress UI | E | active | |
| E02 evidence cards, source panel, timeline | E | active | |
| F01 contract runner + synthetic media tests | F | active | |
| F02 deployment/offline packaging + recovery harness | F | active | |
| O02 pipeline + stage budgets | O | active | |
| O03 real end-to-end acceptance | O | pending | needs approved rule (D04) for real-domain; synthetic e2e first |
| F03 grouped quality/angle/latency evaluation | F | blocked | X1 |
| O04 freeze release profile | O | pending | |
| F04 offline restart, deadline/burst/public smoke | F | pending | public part blocked by X3 |
| O05 runbook, credentials, handover | O | pending | |
