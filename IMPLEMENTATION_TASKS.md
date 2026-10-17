# Parallel implementation tickets

The orchestrator tracks each ticket as pending, active, blocked, ready_for_integration or accepted. “Accepted” requires its verification evidence. Dependency completion means the interface is available; mock providers may unblock consumers but do not satisfy real-inference gates.

| Ticket | Owner | Work | Dependencies | Acceptance |
|---|---|---|---|---|
| O00 | Lead | Inspect repository, environment and data boundaries | None | Risks and available resources recorded |
| O01 | Lead | Freeze DTOs/schemas, dependency versions and file ownership | O00 | Contract files parse; lanes agree |
| A01 | A | Auth gate, streamed upload, capacity reservation | O01 | Invalid key before body parsing; valid MOV persists |
| A02 | A | Durable jobs/leases, fencing, recovery sweeper | A01 | Crash/retry tests with original deadline |
| A03 | A | Exact polling projection and protected media/report | A02 | Schema tests and authorized playback |
| B01 | B | Probe and presentation timeline | O01 | VFR/rotation/offset fixtures |
| B02 | B | Sampling, manifest and real evidence extraction | B01 | Correct frame/time round-trip |
| C01 | C | Model/processor/license manifest and load spike | O00/O01 | Real local model loads on actual hardware |
| C02 | C | Observe/verify JSON adapters and bounds | C01 | Reference validation and controlled failure |
| C03 | C | Memory/time profile with denied egress | C02/B02 | Measured results, no cloud/network dependence |
| D01 | D | Official source capture and rule draft review | O01 | Exact source/edition/scope or explicit blocker |
| D02 | D | Local data audit/group split/type annotation | O00 | Reviewed labels and no split leakage |
| D03 | D | Predicates, exclusions, dedup and descriptions | D01 | Rule/counterexample tests |
| D04 | D | Activate validated rules | D02/D03 | Source and visual gates recorded |
| E01 | E | Upload/key/progress UI against synthetic contract | O01 | User can complete fixture flow |
| E02 | E | Evidence cards, source panel and timeline playback | E01 | Multiple events, failure, keyboard flow |
| F01 | F | Contract runner and synthetic media tests | O01 | Black-box API validator ready |
| F02 | F | Deployment/offline packaging and recovery harness | O01 | Build/run artifacts, no secrets/media bundled |
| O02 | Lead | Pipeline and stage-budget integration | A02/B02/C02/D03 | Typed vertical slice through real adapter |
| O03 | Lead | Real end-to-end acceptance | O02/D04/E02/A03 | Authorized clip → genuine validated result |
| F03 | F | Grouped local quality/angle/latency evaluation | O03/C03 | Honest metrics and retained failures |
| O04 | Lead | Select and freeze measured release profile | F03 | Approved model/rules/config versions |
| F04 | F | Offline restart, deadline/burst/public URL smoke | O04/F02 | Mandatory release checks pass or blocked |
| O05 | Lead | Final runbook, private credentials and handover | F04 | Complete submission checklist |

Shared changes go through the lead. GPU work is reserved separately from coding tasks. If a ticket is externally blocked, the agent completes other owned work and reports the exact dependency; no fabricated “pass” state is allowed.
