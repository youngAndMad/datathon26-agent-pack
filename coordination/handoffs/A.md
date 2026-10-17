# Handoff — agent A (API & durable jobs)

Status: ready_for_integration (not a release-gate claim). Synthetic data only (D004); no real media opened.

## Owned files changed (no commits made; orchestrator commits)
- `src/cashwatch/storage/__init__.py` — `create_store(settings) -> JobStore`, exports
- `src/cashwatch/storage/schema.py` — SQLite WAL connection + forward-only migrations (`schema_migrations`)
- `src/cashwatch/storage/store.py` — `JobStore` (JobStorePort + API helpers), sweeper, retention, orphan cleanup
- `src/cashwatch/api/__init__.py` — exports `create_app`
- `src/cashwatch/api/app.py` — `create_app(settings, store=None, *, sweep_interval_sec=5.0)`; routes; Range media; static SPA; lifespan sweeper
- `src/cashwatch/api/ingress.py` — pure ASGI gate: auth before body, trusted request start, request id, security headers, logs
- `src/cashwatch/api/upload.py` — streaming multipart → bounded temp file + SHA-256; QuickTime/ISO-BMFF atom sniff
- `src/cashwatch/api/projection.py` — AnalysisReport/job row → `dto.UiReport`
- `src/cashwatch/api/errors.py` — `{"error":{"code","message"},"request_id"}` bodies
- `src/cashwatch/api/main.py` — `run()` (uvicorn, 1 process); refuses start on `production_errors()` or bad key
- `tests/storage/{conftest.py,jobfakes.py,test_store.py}`, `tests/api/{conftest.py,test_auth.py,test_upload.py,test_jobs.py,test_app.py}`
- `coordination/requests/A.md`, `coordination/handoffs/A.md`

## Contracts consumed
dto.py (JobInput, StageProgress, ErrorInfo, ERROR_MESSAGES_RU, Stage, AnalysisReport, ReportEvent,
JuryDoneResult, UiReport…), ports.JobStorePort/ClaimedJob, deadline.iso_utc/parse_utc, settings.Settings,
contracts/{job-response,jury-result,submit-response,ui-report}.schema.json, contracts/fixtures (tests only), D006, D008, D010.

## Interface for the pipeline worker (orchestrator)
```python
store = cashwatch.storage.create_store(settings)
store.worker_heartbeat(worker_id, ready=<model warmed & registry ok>, info={...})  # every < 10 s
c = store.claim_job(worker_id)            # None if nothing queued or active_jobs already running
c.job (JobInput; source_path relative to data_dir), c.work_dir (abs Path jobs/<id>/work)
store.source_path(c.job)                  # containment-checked abs path to source.mov
store.heartbeat(job_id, gen, stage=..., progress=StageProgress|None) -> bool  # every < lease_sec (20 s); False => stop, don't commit
out = store.output_dir(c.job)             # jobs/<id>/out-g<gen>.tmp ; write jury.json, report.json, evidence/<evidence_id>.jpg (jpeg_path relative to out), optional preview.mp4
store.commit_result(job_id, gen, out) -> bool   # validates jury.json (JuryDoneResult, job_id, verdict invariant), fsync, rename -> out-g<gen>, CAS
store.fail_job(job_id, gen, ErrorInfo) -> bool  # stores code; public message always ERROR_MESSAGES_RU[code]
store.recover_expired_jobs(now_iso=None) -> int
```
Notes: claim_job never claims past-deadline jobs (fails them `deadline_exceeded`). heartbeat re-extends a lease
that expired but was not yet recovered (fencing is by generation, not wall-clock). commit_result raises
ValueError on a broken jury.json or a foreign dir (job stays running → worker should then fail_job).

## DB state transitions
```
(reservation row) --upload ok--> jobs.queued (attempt=1, generation=0, deadline_at=request_start+270s)
queued --claim (gen+=1, lease)--> running
running --commit_result CAS(state=running, gen)--> succeeded (result_path=jobs/<id>/out-g<gen>, stage=complete)
running --fail_job CAS--> failed(error_code)
running --lease expired, attempt<2 and deadline-now>=120s--> queued (attempt=2; next claim gen+1)
running --lease expired otherwise--> failed(worker_lost)
queued|running|receiving --now>=deadline_at--> failed(deadline_exceeded)       [sweeper and claim]
succeeded|failed --finished_at older than retention_hours (if >0)--> expired (files deleted; GET -> 410)
```
External: receiving/queued/running → `processing`; succeeded → stored jury.json bytes; failed → `failed`+error; expired → 410.
Tables: jobs, leases, reservations, idempotency, worker_status, schema_migrations (data_dir/db/cashwatch.sqlite3, WAL, synchronous=FULL).

## Functionality completed
- Auth gate (pure ASGI, before body/route): X-API-Key only, hmac.compare_digest, identical 401 for missing/wrong;
  /healthz, /readyz, static UI exempt; no key in logs/errors; principal = single `jury`.
- POST /api/v1/analyze: Content-Length early 413; exactly one `file` part (else 422 invalid_multipart);
  streamed via python-multipart `MultipartParser` to `tmp/<reservation>.part` (O_EXCL, 0600) with SHA-256;
  max_file_bytes (413) and max_upload_sec (400 upload_timeout); atom sniff: non-QuickTime/ISO → 415,
  empty or no reachable moov/moof → 422; MIME/extension ignored; readiness 503 (+Retry-After 10) and
  capacity 429 (+Retry-After = time to earliest active deadline) checked before reading the body;
  fsync + atomic rename to `jobs/<uuid4>/source.mov` + single-transaction job insert; 202 {"job_id"}.
  Idempotency-Key: principal-scoped sha256 key hash + content hash; same → same job (also when capacity full), different → 409.
- GET /api/v1/jobs/{id}: processing / exact stored jury.json bytes / failed+RU message; malformed and unknown → 404; expired → 410.
- GET …/report (UiReport), …/evidence/{evidence_id}.jpg (resolved only via report.json evidence ids, containment
  + symlink checks), …/media (preview.mp4 else source.mov; single-range 206/416; multi-range → 200 full).
- /healthz liveness; /readyz = fresh ready worker heartbeat (<10 s) AND storage writable with ≥512 MiB free; generic body.
- Static SPA from settings.frontend_dist (index fallback, containment); CSP `default-src 'self'; img-src 'self' blob: data:;
  media-src 'self' blob:; object-src 'none'; frame-ancestors 'none'…`, nosniff, no-referrer, DENY, no CORS headers;
  API responses `Cache-Control: no-store, private`; /docs and /openapi.json disabled.
- Sweeper at startup + every 5 s (thread offload): recover_expired_jobs + cleanup_retention (+ orphan tmp/job-dir cleanup after 10 min grace).

## Verification (Apple M4 Pro, macOS 26.6, Python 3.12.13)
- `.venv/bin/python -m pytest tests/api tests/storage` → **76 passed in 0.89 s**.
- Manual uvicorn smoke (`python -m cashwatch.api.main`, test mode, port 8765, n=1): no-key upload 401;
  209,728,192-byte MOV (2 s testsrc + 200 MiB `free` atom) → 202 in 0.17 s; server RSS 64.8 → 67.9 MB peak
  (sampled every 0.2 s) — body not held in memory; second upload 429; tmp dir empty; key absent from logs.

T-ids covered (plumbing level, synthetic MOV from `ffmpeg -f lavfi testsrc`):
- T01 `test_auth.py`: 6 protected routes × {missing, wrong, wrong-64-char} → identical 401, no tmp files/reservations/jobs;
  direct ASGI drive proves zero `receive()` calls before the 401; query-string/Bearer keys rejected.
- T02 octet-stream MOV accepted, bytes/sha/size persisted, 0600 perms, processing response schema-valid.
- T03 `.bin` accepted; PNG/random → 415; empty and ftyp-only → 422; truncated MOV with moov → accepted (worker decides); 2 file parts / wrong field → 422.
- T04 streamed oversize 413 and Content-Length 413, no leftovers. (Duration/resolution/decode-bomb limits are worker/media-side.)
- T17 rename-then-crash then commit completes idempotently; crash-before-rename → sweeper retry gen 2; stale gen-1
  heartbeat/commit/fail all False; only gen-2 bytes published; duplicate commit fenced; 8 concurrent claimers → 1 winner;
  broken jury.json never committed; repeated polls return byte-identical jury.json.
- T18 429 + Retry-After; 4-way concurrent burst with capacity 2 → exactly [202,202,429,429]; 503 when worker not ready.
- T19 past-deadline queued job failed by claim/sweeper, never claimed; running job past deadline failed and late commit fenced; polling shows failed deadline_exceeded.
- T23 report/media/evidence need key (T01) ; evidence bytes exact, traversal names 404; Range 206/suffix/open-ended/416; preview preferred.
- T27 same key+same file → same job; different file → 409 without leaking job id; replay works while capacity full.
- Also: UiReport for processing/done/failed validates against ui-report.schema.json; job responses validate against
  job-response.schema.json (referencing Registry for jury-result $ref); production mode with fake backend → `run()` exits 2; weak key → exits 2, key not printed.

## Example curl (placeholder key)
```bash
K='<jury-api-key>'
curl -s -H "X-API-Key: $K" -F "file=@clip.mov;type=application/octet-stream" http://127.0.0.1:8000/api/v1/analyze
curl -s -H "X-API-Key: $K" -H "Idempotency-Key: run-1" -F "file=@clip.mov" http://127.0.0.1:8000/api/v1/analyze
curl -s -H "X-API-Key: $K" http://127.0.0.1:8000/api/v1/jobs/<job_id>
curl -s -H "X-API-Key: $K" http://127.0.0.1:8000/api/v1/jobs/<job_id>/report
curl -s -H "X-API-Key: $K" -o e.jpg http://127.0.0.1:8000/api/v1/jobs/<job_id>/evidence/<evidence_id>.jpg
curl -s -H "X-API-Key: $K" -H "Range: bytes=0-1023" -o part.bin http://127.0.0.1:8000/api/v1/jobs/<job_id>/media
curl -s http://127.0.0.1:8000/healthz ; curl -s http://127.0.0.1:8000/readyz
```

## Assumptions / limitations
- Single principal (`jury`) for the one configured key; principal check is in place for future multi-key.
- Upload chunks are written synchronously inside the event loop (64 KiB writes; measured fast); DB calls are thread-offloaded.
- Upload timeout returns 400 `upload_timeout` (openapi lists no 408).
- `coverage_notice_ru` is a verbatim join of stored limitations until R-A2 lands.
- Retry-After is a heuristic (seconds to earliest active deadline, clamped 1..300), not a measured queue estimate.
- Retention deletes via unlink/rmtree; this is not secure erasure on SSD.
- No latency claims for real inference; API numbers above are n=1 local loopback.

## Requested integration actions
1. Worker loop: call `worker_heartbeat(ready=...)` every ≤5 s (API returns 503/readyz not ready otherwise), heartbeat leases
   at stage boundaries and ≤ every ~10 s, write results only under `output_dir(job)`, then `commit_result`.
2. `make start`: launch `cashwatch-api` (pyproject script already points at `cashwatch.api.main:run`) and the worker; both share `CASHWATCH_DATA_DIR`.
3. Consider R-A1..R-A3 in `coordination/requests/A.md`.
