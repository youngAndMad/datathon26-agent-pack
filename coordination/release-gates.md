# Release-gate checklist (only the orchestrator marks gates)

## G0 contract freeze
- [x] Repository/instructions inspected; new project
- [x] Environment discovery recorded (plan.md)
- [x] Typed DTOs/ports frozen (`CONTRACT_VERSION = g0-1.0.0`)
- [x] Dependencies pinned + `uv.lock`
- [x] Ownership + coordination files
- [x] UI-report JSON schema + synthetic fixtures generated (`contracts/ui-report.schema.json`, `contracts/fixtures/`)

## G1 feasibility
- [ ] Real model loads locally (MLX or Transformers), warm-up measured
- [ ] Longest *authorized* MOV decodes — BLOCKED (X1); substitute: synthetic 145 s MOV
- [ ] Accelerator timing measured per window
- [ ] API + UI round trip with explicit test fixtures
- [ ] ≥1 rule source verified against official snapshot

## G2 real vertical slice (mock disabled)
- [ ] Upload → local model → approved rule → real evidence → jury JSON → UI
  - approved rule requires local visual validation — BLOCKED (X1/X2)

## G3 quality/timing
- [ ] Grouped validation, dedup, negatives, angle stress — BLOCKED (X1)
- [ ] 145 s latency on actual host (synthetic allowed for timing only)
- [ ] Failure paths T15–T19

## G4 release
- [ ] Offline cold restart with denied egress
- [ ] Public HTTPS check — BLOCKED (X3)
- [ ] Secrets/retention review
- [ ] Documentation + submission package
