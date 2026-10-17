# Requests from agent A (API & jobs) to the orchestrator

All items have a working local workaround already in place; none blocks integration.

## R-A1 Settings fields for store/API tunables (settings.py)
- Reason: values are currently module constants in `cashwatch/storage/store.py` and `cashwatch/api/app.py`.
- Exact change (add to `Settings`):
  - `worker_fresh_sec: float = 10.0`        (store.WORKER_FRESH_SEC — readiness/admission freshness)
  - `retry_min_remaining_sec: float = 120.0` (store.RETRY_MIN_REMAINING_SEC)
  - `max_attempts: int = 2`                  (store.MAX_ATTEMPTS)
  - `orphan_grace_sec: float = 600.0`        (store.ORPHAN_GRACE_SEC)
  - `sweep_interval_sec: float = 5.0`        (app.SWEEP_INTERVAL_SEC)
  - `min_free_bytes: int = 536870912`        (store.MIN_FREE_BYTES — /readyz storage check)
- Consumers: storage, API, pipeline worker heartbeat cadence (must beat < worker_fresh_sec), F deploy docs.

## R-A2 `AnalysisReport.coverage_notice_ru: str | None = None` (dto.py)
- Reason: `UiReport.coverage_notice_ru` must come from `ReportingPort.coverage_notice_ru(...)` (agent D),
  but the API only has `report.json`; AnalysisReport has no such field (extra=forbid), and the API
  must not author legal/coverage text.
- Workaround now: the projection joins `visual_limitations_ru` + `coverage.visual_limitations`
  verbatim (None when empty). Once the field exists, projection will prefer it.
- Consumers: pipeline writer (sets it), API projection, frontend (E).

## R-A3 Shared test helper location
- Reason: `tests/storage/jobfakes.py` (test-only fake result writer + settings builder) is imported by
  `tests/api/conftest.py` via a `sys.path` insert. Cleaner home: `tests/fixtures/` (orchestrator-owned)
  or a root `tests/conftest.py`. No functional impact.
