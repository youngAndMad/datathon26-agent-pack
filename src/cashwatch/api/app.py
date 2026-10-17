"""FastAPI application: exact jury API + protected UI report/evidence/media + static UI.

The API process never invokes a model, rules or evidence generation; it persists uploads,
admits durable jobs and serves stored results.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import os
import re
import uuid
from collections.abc import Iterator
from pathlib import Path

import anyio
from fastapi import FastAPI, Request
from pydantic import ValidationError
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import FileResponse, JSONResponse, Response, StreamingResponse

from cashwatch.api.errors import ApiError, error_response
from cashwatch.api.ingress import PRINCIPAL_ID, IngressMiddleware, is_api_path
from cashwatch.api.projection import build_ui_report
from cashwatch.api.upload import MULTIPART_OVERHEAD_BYTES, receive_file, sniff_container
from cashwatch.deadline import iso_utc, utc_now
from cashwatch.dto import ExportBundle, JobListResponse, JuryDoneResult, ReviewRequest
from cashwatch.settings import Settings
from cashwatch.storage import (
    MISSED_EVENT_ID,
    CapacityFull,
    IdempotencyConflict,
    JobNotDone,
    JobStore,
    JobView,
    create_store,
)

log = logging.getLogger("cashwatch.api")

SWEEP_INTERVAL_SEC = 5.0
MEDIA_CHUNK = 256 * 1024
_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")
_IDEM_RE = re.compile(r"^[\x21-\x7e]{1,200}$")
REVIEW_BODY_MAX_BYTES = 16 * 1024
LIST_DEFAULT_LIMIT = 50
LIST_MAX_LIMIT = 200
LIST_STATUSES = ("processing", "done", "failed")


def _rid(request: Request) -> str:
    return getattr(request.state, "request_id", "") or ""


def create_app(settings: Settings, store: JobStore | None = None, *,
               sweep_interval_sec: float = SWEEP_INTERVAL_SEC) -> FastAPI:
    store = store or create_store(settings)
    api_key = settings.read_api_key()

    def sweep_once() -> None:
        try:
            n = store.recover_expired_jobs()
            m = store.cleanup_retention()
            if n or m:
                log.info("sweeper recovered=%d expired=%d", n, m)
        except Exception:
            log.exception("sweeper iteration failed")

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI):
        await anyio.to_thread.run_sync(sweep_once)
        task = None
        if sweep_interval_sec > 0:
            async def loop() -> None:
                while True:
                    await asyncio.sleep(sweep_interval_sec)
                    await anyio.to_thread.run_sync(sweep_once)
            task = asyncio.create_task(loop())
        try:
            yield
        finally:
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    app = FastAPI(title="cashwatch", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.store = store
    app.state.settings = settings

    # ------------------------------------------------------------------ error handlers

    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError):
        return error_response(exc.status, exc.code, _rid(request), exc.headers)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException):
        code = {404: "not_found", 405: "method_not_allowed", 413: "payload_too_large"}.get(
            exc.status_code, "invalid_request" if exc.status_code < 500 else "internal_error")
        return error_response(exc.status_code, code, _rid(request))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError):
        return error_response(422, "invalid_request", _rid(request))

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        log.error("request_id=%s unhandled %s", _rid(request), type(exc).__name__)
        return error_response(500, "internal_error", _rid(request))

    # ------------------------------------------------------------------ health

    @app.get("/healthz", include_in_schema=False)
    def healthz():
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False)
    def readyz():
        ok = store.worker_ready() and store.storage_writable()
        return JSONResponse({"status": "ready" if ok else "not_ready", **store.worker_identity()},
                            status_code=200 if ok else 503)

    # ------------------------------------------------------------------ upload

    @app.post("/api/v1/analyze", status_code=202)
    async def analyze(request: Request):
        state = request.state
        principal = getattr(state, "principal_id", PRINCIPAL_ID)
        cl = request.headers.get("content-length")
        if cl is not None:
            try:
                if int(cl) > settings.max_file_bytes + MULTIPART_OVERHEAD_BYTES:
                    raise ApiError(413, "payload_too_large")
            except ValueError:
                raise ApiError(400, "invalid_request") from None
        if "multipart/form-data" not in request.headers.get("content-type", "").lower():
            raise ApiError(422, "invalid_multipart")
        idem = request.headers.get("idempotency-key")
        key_hash = None
        existing = None
        if idem is not None:
            if not _IDEM_RE.match(idem):
                raise ApiError(400, "invalid_request")
            key_hash = hashlib.sha256(f"{principal}\x00{idem}".encode()).hexdigest()
            existing = await anyio.to_thread.run_sync(store.idempotency_lookup, principal, key_hash)

        reservation: str | None = None
        if existing is None:
            if not await anyio.to_thread.run_sync(store.worker_ready):
                raise ApiError(503, "not_ready", {"Retry-After": "10"})
            try:
                reservation = await anyio.to_thread.run_sync(store.reserve_capacity, principal)
            except CapacityFull as e:
                raise ApiError(429, "capacity_exceeded", {"Retry-After": str(e.retry_after_sec)}) from None
        tmp_id = reservation or uuid.uuid4().hex
        tmp_path = store.tmp_upload_path(tmp_id)
        job_id: str | None = None
        try:
            received = await receive_file(
                request, tmp_path, max_file_bytes=settings.max_file_bytes,
                max_upload_sec=settings.max_upload_sec, started_mono=state.started_mono)
            verdict = await anyio.to_thread.run_sync(sniff_container, tmp_path)
            if verdict == "unsupported":
                raise ApiError(415, "unsupported_container")
            if verdict != "ok":
                raise ApiError(422, "invalid_media")
            if existing is not None:
                if existing[0] == received.sha256:
                    return JSONResponse({"job_id": existing[1]}, status_code=202)
                raise ApiError(409, "idempotency_conflict")
            job_id = str(uuid.uuid4())
            rel = await anyio.to_thread.run_sync(store.persist_upload, tmp_id, job_id)
            try:
                final_id, created = await anyio.to_thread.run_sync(
                    lambda: store.enqueue_job(
                        job_id=job_id, principal_id=principal, source_rel=rel, source_sha256=received.sha256,
                        size_bytes=received.size, request_started_at=state.started_utc,
                        reservation_id=reservation, idempotency_key_hash=key_hash))
            except IdempotencyConflict:
                await anyio.to_thread.run_sync(store.discard_job_dir, job_id)
                raise ApiError(409, "idempotency_conflict") from None
            except BaseException:
                await anyio.to_thread.run_sync(store.discard_job_dir, job_id)
                raise
            if not created:
                await anyio.to_thread.run_sync(store.discard_job_dir, job_id)
            log.info("request_id=%s accepted job_id=%s size=%d created=%s", _rid(request), final_id,
                     received.size, created)
            return JSONResponse({"job_id": final_id}, status_code=202)
        finally:
            with contextlib.suppress(OSError):
                tmp_path.unlink(missing_ok=True)
            if reservation is not None:
                await anyio.to_thread.run_sync(store.release_reservation, reservation)

    # ------------------------------------------------------------------ job reads

    def _view(job_id: str) -> JobView:
        view = store.get_job(job_id)  # None for unknown and malformed ids alike
        if view is None or view.principal_id != PRINCIPAL_ID:
            raise ApiError(404, "not_found")
        if view.state == "expired":
            raise ApiError(410, "expired")
        return view

    @app.get("/api/v1/jobs")
    def list_jobs(request: Request):
        q = request.query_params
        limit = LIST_DEFAULT_LIMIT
        raw_limit = q.get("limit")
        if raw_limit is not None:
            if not raw_limit.isdigit() or not 1 <= int(raw_limit) <= LIST_MAX_LIMIT:
                raise ApiError(422, "invalid_request")
            limit = int(raw_limit)
        status = q.get("status") or None
        if status is not None and status not in LIST_STATUSES:
            raise ApiError(422, "invalid_request")
        principal = getattr(request.state, "principal_id", PRINCIPAL_ID)
        jobs = store.list_jobs(principal, limit=limit, status=status)
        return JSONResponse(JobListResponse(jobs=jobs).model_dump(mode="json"))

    @app.get("/api/v1/jobs/{job_id}")
    def get_job(job_id: str):
        view = _view(job_id)
        if view.state == "succeeded":
            data = store.read_jury_bytes(view)
            if data is None:
                raise ApiError(500, "internal_error")
            return Response(content=data, media_type="application/json")
        if view.state == "failed":
            return JSONResponse({"job_id": view.id, "status": "failed", "error": view.error().model_dump()})
        return JSONResponse({"job_id": view.id, "status": "processing"})

    def _ui_report(view: JobView):
        report = store.read_report(view) if view.state == "succeeded" else None
        reviews = store.list_reviews(view.id) if view.state == "succeeded" else []
        ui = build_ui_report(view, report, now=utc_now(), default_mode=settings.mode,
                             media_available=store.media_file(view) is not None, reviews=reviews)
        return ui, report

    @app.get("/api/v1/jobs/{job_id}/report")
    def get_report(job_id: str):
        ui, _ = _ui_report(_view(job_id))
        return JSONResponse(ui.model_dump(mode="json"))

    # ------------------------------------------------------------------ reviewer decisions / export

    async def _read_small_body(request: Request, cap: int) -> bytes:
        cl = request.headers.get("content-length")
        if cl is not None:
            if not cl.strip().isdigit():
                raise ApiError(400, "invalid_request")
            if int(cl) > cap:
                raise ApiError(413, "payload_too_large")
        buf = bytearray()
        async for chunk in request.stream():
            buf.extend(chunk)
            if len(buf) > cap:
                raise ApiError(413, "payload_too_large")
        return bytes(buf)

    @app.post("/api/v1/jobs/{job_id}/events/{event_id}/review", status_code=201)
    async def post_review(job_id: str, event_id: str, request: Request):
        view = await anyio.to_thread.run_sync(_view, job_id)
        if view.state != "succeeded":
            raise ApiError(409, "job_not_done")
        report = await anyio.to_thread.run_sync(store.read_report, view)
        if report is None:
            raise ApiError(500, "internal_error")
        known = {e.event.event_id for e in report.events}
        if event_id != MISSED_EVENT_ID and event_id not in known:
            raise ApiError(404, "not_found")
        body = await _read_small_body(request, REVIEW_BODY_MAX_BYTES)
        try:
            req = ReviewRequest.model_validate_json(body)
        except ValidationError:
            raise ApiError(422, "invalid_request") from None
        missed = event_id == MISSED_EVENT_ID
        if (req.decision == "missed") != missed:
            raise ApiError(422, "invalid_review")
        if missed and req.corrected_timecode_sec is None:
            raise ApiError(422, "invalid_review")
        if req.corrected_timecode_sec is not None:
            duration = report.source.duration_sec if report.source is not None else None
            if duration is None or req.corrected_timecode_sec > duration:
                raise ApiError(422, "invalid_review")
        try:
            review = await anyio.to_thread.run_sync(store.add_review, view.id, event_id, req)
        except JobNotDone:
            raise ApiError(409, "job_not_done") from None
        log.info("request_id=%s review job_id=%s decision=%s", _rid(request), view.id, review.decision)
        return JSONResponse(review.model_dump(mode="json"), status_code=201)

    @app.get("/api/v1/jobs/{job_id}/reviews")
    def get_reviews(job_id: str):
        view = _view(job_id)
        return JSONResponse([r.model_dump(mode="json") for r in store.list_reviews(view.id)])

    @app.get("/api/v1/jobs/{job_id}/export")
    def get_export(job_id: str):
        view = _view(job_id)
        if view.state != "succeeded":
            raise ApiError(409, "job_not_done")
        ui, report = _ui_report(view)
        jury_bytes = store.read_jury_bytes(view)
        if report is None or jury_bytes is None:
            raise ApiError(500, "internal_error")
        bundle = ExportBundle(
            job_id=view.id, exported_at=iso_utc(utc_now()), report=ui, versions=report.versions,
            coverage=report.coverage, original_jury_result=JuryDoneResult.model_validate_json(jury_bytes))
        return JSONResponse(bundle.model_dump(mode="json"), headers={
            "Content-Disposition": f'attachment; filename="cashwatch-{view.id}.json"'})

    @app.get("/api/v1/jobs/{job_id}/evidence/{name}")
    def get_evidence(job_id: str, name: str):
        view = _view(job_id)
        if not name.endswith(".jpg"):
            raise ApiError(404, "not_found")
        path = store.evidence_file(view, name[:-4])
        if path is None:
            raise ApiError(404, "not_found")
        return FileResponse(path, media_type="image/jpeg")

    @app.get("/api/v1/jobs/{job_id}/media")
    def get_media(job_id: str, request: Request):
        view = _view(job_id)
        found = store.media_file(view)
        if found is None:
            raise ApiError(404, "not_found")
        path, ctype = found
        size = path.stat().st_size
        rng = _parse_range(request.headers.get("range"), size)
        if rng == "unsatisfiable":
            raise ApiError(416, "range_not_satisfiable", {"Content-Range": f"bytes */{size}"})
        if rng is None:
            return StreamingResponse(_iter_file(path, 0, size), media_type=ctype, headers={
                "Accept-Ranges": "bytes", "Content-Length": str(size)})
        start, end = rng
        return StreamingResponse(_iter_file(path, start, end - start + 1), status_code=206, media_type=ctype,
                                 headers={"Accept-Ranges": "bytes", "Content-Length": str(end - start + 1),
                                          "Content-Range": f"bytes {start}-{end}/{size}"})

    # ------------------------------------------------------------------ static UI

    dist = Path(settings.frontend_dist)
    if dist.is_dir() and (dist / "index.html").is_file():
        dist_root = dist.resolve()

        @app.get("/{full_path:path}", include_in_schema=False)
        def spa(full_path: str):
            if is_api_path("/" + full_path):
                raise ApiError(404, "not_found")
            candidate = (dist_root / full_path).resolve() if full_path else dist_root / "index.html"
            if (full_path and candidate.is_relative_to(dist_root) and candidate.is_file()
                    and not (dist_root / full_path).is_symlink()):
                return FileResponse(candidate)
            return FileResponse(dist_root / "index.html", media_type="text/html")

    app.add_middleware(IngressMiddleware, api_key=api_key)
    return app


def _parse_range(header: str | None, size: int) -> tuple[int, int] | str | None:
    """Single byte range per RFC 9110. Malformed/multi-range headers are ignored (full 200)."""
    if not header:
        return None
    m = _RANGE_RE.match(header.strip())
    if not m:
        return None
    a, b = m.groups()
    if a == "" and b == "":
        return None
    if a == "":
        n = int(b)
        if n == 0 or size == 0:
            return "unsatisfiable"
        return max(0, size - n), size - 1
    start = int(a)
    end = int(b) if b else size - 1
    if start >= size or (b and end < start):
        return "unsatisfiable"
    return start, min(end, size - 1)


def _iter_file(path: Path, start: int, length: int) -> Iterator[bytes]:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.lseek(fd, start, os.SEEK_SET)
        left = length
        while left > 0:
            chunk = os.read(fd, min(MEDIA_CHUNK, left))
            if not chunk:
                break
            left -= len(chunk)
            yield chunk
    finally:
        os.close(fd)
