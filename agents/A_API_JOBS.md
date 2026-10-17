# Agent A — API and durable jobs

Read AGENTS.md and implementation sections 4–6, 12–14. Own src/cashwatch/api/, src/cashwatch/storage/, tests/api/ and storage tests. Shared contracts and dependencies remain with the integrator.

Implement exact multipart upload and polling APIs, X-API-Key authentication, streamed private storage, capacity reservations, durable state, lease claims with fencing, immutable terminal results and controlled errors. Request a key from environment/secret file without logging it. Return 202 with job_id after persistence; never run inference inline. Implement protected report/media routes including byte ranges or an agreed object-URL approach. Use constant-time key comparison, no URL keys, path containment and no user-controlled filesystem layout.

Consume JobInput, AnalysisReport and JuryDoneResult from frozen contracts. Provide claim_job, heartbeat, commit_result and fail_job interfaces to the pipeline. Queued/running map to processing; success to done; errors to failed. Status reads must remain responsive during GPU work.

Provide SQLite migrations, a recovery plan for abandoned uploads and stale leases, and safe job cleanup. Test crash windows around file rename/database commit, two concurrent claims, stale writer fencing, duplicate requests, payload limits, wrong keys and stable completed responses. Implement optional idempotency only after the basic contract works.

Use an explicit fake pipeline only for plumbing tests; ensure production rejects it. Do not infer rules or generate evidence in API code. Handoff includes example curl commands, database state transitions and T01–T04, T17–T19, T23/T27 results. Coordinate error-code/stage names with the orchestrator and UI agent.
