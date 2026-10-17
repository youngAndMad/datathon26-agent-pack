# Instructions for every coding agent

Build the ForteBank Datathon 26 service described in IMPLEMENTATION_SPEC.md. This file applies to the entire implementation repository. Read your role file under agents/ and the shared contracts before editing.

## Working rules

1. The orchestrator must delegate independent lanes to subagents when supported. Start after G0 contract freeze; do not create six incompatible skeletons. Use at most one orchestrator plus six workers and reduce concurrency if local memory is insufficient.
2. This is a local video-processing system. No cloud AI calls, remote embeddings, remote OCR, inference gateways or externally hosted multimodal calls. No automatic cloud fallback.
3. Do not upload or paste competition videos, frames, scene descriptions, embeddings, annotations or sensitive logs into cloud coding agents. Only local agents/humans may inspect restricted data. If your current inference backend is cloud-hosted, work only on public specification/code/synthetic fixtures and report the local-data step as blocked.
4. No audio analysis, face recognition, face embeddings, personal identification or cross-clip person tracking. Anonymous roles and short within-clip tracks may be used when visually supported.
5. Use the exact jury API schemas. Two public verdicts only. Processing failures are failed, never no_violation. Every reported event needs actual source JPEGs, source-derived timestamps and a verified approved rule.
6. Never invent norms, labels, metrics, licenses, visual facts, timestamps or tests. Model confidence is not calibrated probability. Synthetic/mock success is not real inference success.
7. Work only in assigned paths. Shared schemas, root manifests, dependency locks, pipeline wiring and top-level config belong to the orchestrator. Request changes through coordination/requests/<agent>.md with reason and affected consumers.
8. Preserve existing repository work. Use isolated worktrees or disjoint owned paths; do not reset, clean or overwrite others' changes. No git publish, paid provisioning or external submission unless separately authorized.
9. Run focused meaningful tests for your risk area, then stop redundant testing. Keep expensive GPU use behind a shared reservation so parallel agents do not invalidate benchmarks or cause OOM.
10. Keep dependencies small and pinned. Prefer a real vertical slice over optional frameworks, fine-tuning or UI polish. No runtime internet dependency after preparation.
11. Follow the pipeline contract: neutral perception → tri-state predicates → curated rules → real evidence → validated result. A model must not freely generate legal sources or final jury objects.
12. No production fixture adapter or hard-coded result lookup by filename/hash. Production startup refuses mock mode. Test mode is explicit and visibly labeled.
13. Report blockers with the narrow missing requirement and continue independent work. Do not stop for routine implementation choices already covered by the specification.

## Handoff format

Write coordination/handoffs/<agent>.md containing: owned files changed; functionality completed; contracts consumed; exact verification commands and outcomes; measured latency/memory with hardware and sample count; remaining assumptions/limitations; requested integration action. Omit sensitive media/scene content from any handoff that leaves the local environment. Return a commit hash if using worktrees, otherwise a precise changed-file list.

No claim of completion until the appropriate tests ran. The orchestrator alone marks release gates passed.
