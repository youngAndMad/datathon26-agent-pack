# Submission checklist (spec §16)

Automated part: `make submission-check` (scripts/qa_submission_check.py) prints PASS/FAIL/BLOCKED.
Only the orchestrator marks release gates; this list records evidence, not approval.

| # | Item | Evidence / command | Status |
|---|---|---|---|
| 1 | UI URL, API base URL, jury API key delivered privately | public host (X3); `deploy/gen_api_key.sh`; organizer's private channel | BLOCKED (X3) |
| 2 | One-page launch/use instructions + offline runbook + hardware | docs/runbook/RUNBOOK.md; hardware in plan.md | draft |
| 3 | Architecture, exact model revisions/licenses, design reasons | README/ADR (orchestrator), models/manifest.json, docs/submission/LICENSES.md | manifest pending |
| 4 | Approved detected types, observable conditions, RK sources/clauses | rules/registry.yaml (agent D) | BLOCKED: 0 approved (D011) |
| 5 | Local evaluation summary: split, counts, FP/misses, localization, timing | `make benchmark` → evaluation/reports/private/*.summary.json | BLOCKED (X1/X2) |
| 6 | Known limits | docs/limitations/LIMITATIONS.md | draft |
| 7 | Reproducible source/build, dependency/license manifest, cleanup procedure; no videos/secrets | `make build-offline` (MANIFEST.sha256), LICENSES.md, RUNBOOK §9, `make submission-check` secret/media scan | partial |
| 8 | Optional 5-minute pitch | team | — |

## Release preconditions (must all hold; spec §16)

- [ ] Mock mode disabled: `CASHWATCH_MODE=production`, backend `mlx`; start.sh/worker/API refuse fake (T28 test passes)
- [ ] Real authorized clip end-to-end (G2) — BLOCKED (X1)
- [ ] Rules + evidence validate (`qa_jury_client` 0 errors on every run)
- [ ] Measured envelope on the actual host: 145 s synthetic MOV p95/max with n (G3)
- [ ] `make verify-offline` exit 0 with the real backend (T20/T21)
- [ ] Public HTTPS smoke from another network (T25) — BLOCKED (X3)
- [ ] `make submission-check` has no FAIL
- [ ] Retention confirmed with organizer; backups of `data/` disabled; cleanup procedure rehearsed (T26)
- [ ] `SUBMISSION_DEADLINE_ISO` set after confirming date + timezone (X4)
