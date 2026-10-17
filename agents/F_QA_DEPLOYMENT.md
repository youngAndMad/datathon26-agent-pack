# Agent F — Independent verification and deployment

Read AGENTS.md and implementation sections 12–16. Own deploy/, tests/contracts/, tests/integration/, tests/offline/, evaluation/reports/ tooling, scripts/qa_* and docs/runbook/limitations/submission. Request root Makefile/dependency changes from the integrator.

Build a black-box jury client and schema validator before implementation completes. Test valid MOV upload, correct header, polling, all terminal states, strict done JSON, real JPEG decoding, time bounds, evidence provenance and multiple events. Keep failed/unprocessed jobs in benchmark counts.

Prepare a one-machine deployment with resident worker, persistent local data, secured ingress and offline model files. No paid provisioning or external publication without owner authorization. Produce a concrete runbook and readiness checks. Pre-stage all artifacts; no automatic runtime download. Do not rely on a tunnel/CDN that sends restricted data to an unapproved third party.

Implement denied-egress cold-start inference test, dependency/secret scan, missing-artifact failure, deadline watchdog test, two-upload burst, disk/worker crash cases and public HTTPS smoke procedure. Use synthetic non-sensitive media for remote smoke unless the real dataset path is explicitly authorized. Do not send restricted outputs to cloud dashboards or agents.

Measure confusion matrices, type metrics only with reviewed labels, one-to-one temporal matching, legal grounding and latency on the actual host. Distinguish training/validation/holdout and mock/real results. Never call a mock test a completed detection evaluation. Release must fail if mock adapter is enabled.

Handoff includes an acceptance matrix with pass/fail/blocked and exact evidence, local run commands, deploy prerequisites, measured capacity, offline artifacts/license inventory, retention cleanup and a submission checklist. Review other lanes for material risks without silently editing their files.
