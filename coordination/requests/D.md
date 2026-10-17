# Requests from agent D (rules and reporting) to the orchestrator

## R-D1: test-mode registry path (settings/config, owned by O)
- Change: synthetic end-to-end runs need `CASHWATCH_MODE=test` and
  `CASHWATCH_REGISTRY_PATH=rules/registry.test.yaml`. Please document this in the runbook/Makefile.
  Production keeps the default `rules/registry.yaml`.
- Reason: `load_registry(..., allow_test=False)` raises `PipelineError(rule_registry_invalid)` for a
  `test_only` registry. So if production is pointed at the test registry, the worker fails at
  startup. That is intended.
- Affected: pipeline/worker.py, doctor.py (both already pass `allow_test=settings.mode == "test"`),
  F (runbook, offline tests).

## R-D2: one shared forbidden-terms list (pipeline/validate.py, owned by O)
- Change (optional): replace `FORBIDDEN_TERMS` in `pipeline/validate.py` with
  `cashwatch.rules.schema.FORBIDDEN_RE`, or keep both. Mine is a superset (adds хищение,
  несанкционированн*, неуполномоченн*, умысел, steal, criminal, intent). The registry lint and
  `describe()` already enforce it.
- Affected: validator only.

## R-D3: capture tool is preparation-only (F offline audit)
- `cashwatch.rules.capture` does network I/O (urllib to adilet.zan.kz) and is never imported at
  runtime. Please add it to F's denied-egress/offline audit allow-list as a prep-only module, and
  check that the worker and API never import it.

## R-D4: commit legal snapshots
- `rules/sources/*` contains public official legal texts (about 1.9 MB) plus sha256-bound excerpts.
  The registry loader re-hashes them at startup, so they must be committed and must not be
  git-ignored.

## R-D5 (BLOCKER, needs a local human or local model; D02/D04)
- No rule can be approved until a LOCAL reviewer annotates authorized clips
  (`evaluation/annotations/README.md`), confirms applicability (for example whether the filmed
  teller position is part of a кассовый узел, p.5, for CW04), and records validation counts.
  Until then production readiness correctly reports "no approved rules".
