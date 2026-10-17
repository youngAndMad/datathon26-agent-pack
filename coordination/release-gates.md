# Release-gate checklist (only the orchestrator marks gates)

Updated 2026-10-17 ~15:00 (+05). Host: Apple M4 Pro, 48 GB, macOS 26.6.2, MLX 8-bit Qwen3-VL-4B @0943db6e.
All model runs below used SYNTHETIC media only (D004).

## G0 contract freeze — PASSED
- [x] Repository/instructions inspected; new project
- [x] Environment discovery recorded (plan.md)
- [x] Typed DTOs/ports frozen (`CONTRACT_VERSION = g0-1.1.0`; 1.1 adds AnalysisReport.coverage_notice_ru)
- [x] Dependencies pinned + `uv.lock`
- [x] Ownership + coordination files
- [x] UI-report JSON schema + synthetic fixtures

## G1 feasibility — PASSED except real-footage item
- [x] Real model loads locally (MLX), warm-up ~3–4 s from cache
- [ ] Longest *authorized* MOV decodes — BLOCKED (X1). Substitute: synthetic 145 s 1080p MOV decodes/samples in 2.5 s
- [x] Accelerator timing measured (coordination/handoffs/C.md; n=5 per row)
- [x] API + UI round trip with explicit test fixtures (E, A tests)
- [x] Rule sources captured from official adilet.zan.kz with hashes (D); clause text verified, applicability unconfirmed

## G2 real vertical slice — PASSED IN TEST MODE ONLY
- [x] Upload → local MLX model → test-approved rule TEST01 → real evidence JPEG → strict jury JSON (synthetic marker clip:
      violation at 20.000 s vs truth 20.000 s; negative twin → no_violation; validator + black-box client pass)
- [ ] Same with a production-approved rule — BLOCKED (zero approved rules until local visual validation, X1/X2)
- [ ] UI against the real API in a browser — pending manual check

## G3 quality/timing
- [ ] Grouped validation, dedup, negatives, angle stress on real data — BLOCKED (X1)
- [x] Synthetic 145 s 1080p latency: 80.7 s upload→done (n=1, no candidates); 40 s marker clips 164–173 s (n=2, 6–7 candidates verified)
- [x] Failure paths with fake backend: T17, T18, T19, T28 (F); T15 parse/repair (C); T21 missing artifacts (F)
- [x] Real-footage latency, organizer `open.zip` (42 clips, 11.6–142.5 s), TEST mode, real MLX model, sequential:
      42/42 done, 0 failed; upload→done p50 31.3 s, p90 86.6 s, max 101.7 s; none over 240 s.
      Observation stage p50 29.3 s / max 99.3 s. Cloud agents saw aggregates only.
- [ ] Worst case WITH verification on real footage — not measured: test registry produced 0 candidates on real clips,
      so verification (~14 s per candidate measured on synthetic) is not included above.

## G4 release
- [x] Offline cold restart with denied egress, real model (T20): 2/2 PASS, no egress attempts
- [ ] Public HTTPS check — BLOCKED (X3)
- [ ] Secrets/retention review — retention allowance unknown (X4)
- [ ] Documentation + submission package — drafted (docs/), final numbers pending real data
