# cashwatch — ForteBank Datathon 26

Local cashier-video analysis service (API + local VLM worker + Russian UI).
**To run it on your laptop, see [docs/LOCAL_SETUP.md](docs/LOCAL_SETUP.md).** Status and gates: `coordination/release-gates.md`.

---

# Datathon 26 implementation pack

This is a ready-to-use implementation brief for a local coding-agent team, not an already implemented application. Engineering instructions are in English; the required UI and explanations default to Russian.

## Start

1. Extract this package into the target project or its documentation directory. Preserve existing repository instructions; reconcile conflicts explicitly.
2. Give your local lead coding agent `ORCHESTRATOR_PROMPT.md` and access to the whole pack. It should read `AGENTS.md` and `IMPLEMENTATION_SPEC.md` first.
3. Supply the local training-dataset path and available hardware when known. Keep the videos and all derived descriptions inside the permitted local environment.
4. The lead agent freezes contracts and launches six independent lanes, with shared-file ownership and integration gates. It then integrates and tests the real service.

## Contents

| File or directory | Purpose |
|---|---|
| IMPLEMENTATION_SPEC.md | Full product, inference, API, runtime, evaluation and deployment specification |
| ORCHESTRATOR_PROMPT.md | Pasteable execution instruction for the lead coding agent |
| AGENTS.md | Rules for all agents and conflict-free parallel work |
| agents/ | Six specialist agent briefs |
| contracts/ | Strict jury result schema, job/submit schemas, OpenAPI and internal boundaries |
| RULES_CATALOG.md | Nine legal candidates with scopes and activation gates |
| SOURCES.md | Official model/documentation and legal source URLs |
| config/ | Provisional runtime profile and disabled rule seeds |

No weights, private videos, keys, fake benchmarks or working application are included. Rule candidates are disabled until official source snapshots, applicability and local visual tests are verified. The selected model and sampling budgets require measurement on your hardware.

If a cloud coding assistant is used for public code/specification work, it must not receive the competition footage, frames, descriptions, derived annotations or logs. Actual video analysis and data-bearing agent work remain local.
