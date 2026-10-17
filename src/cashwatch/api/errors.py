"""Controlled API error bodies: {"error":{"code","message"},"request_id"}. Never stack traces,
paths, prompts or secrets."""

from __future__ import annotations

from starlette.responses import JSONResponse

GENERIC_MESSAGES: dict[str, str] = {
    "unauthorized": "Missing or invalid API key",
    "not_found": "Not found",
    "expired": "Job results have expired",
    "method_not_allowed": "Method not allowed",
    "payload_too_large": "Upload exceeds the maximum allowed size",
    "unsupported_container": "Unsupported container: a QuickTime/MOV file is expected",
    "invalid_media": "Uploaded file is empty or not a valid video container",
    "invalid_multipart": "Expected multipart/form-data with exactly one field named 'file'",
    "invalid_request": "Invalid request",
    "upload_timeout": "Upload took too long",
    "capacity_exceeded": "Analysis capacity is full, retry later",
    "not_ready": "Analysis worker is not ready",
    "storage_full": "Insufficient storage",
    "idempotency_conflict": "Idempotency-Key was already used with a different file",
    "range_not_satisfiable": "Requested range not satisfiable",
    "internal_error": "Internal error",
}


class ApiError(Exception):
    def __init__(self, status: int, code: str, headers: dict[str, str] | None = None) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.headers = headers or {}


def error_response(status: int, code: str, request_id: str | None,
                   headers: dict[str, str] | None = None) -> JSONResponse:
    body = {"error": {"code": code, "message": GENERIC_MESSAGES.get(code, "Error")},
            "request_id": request_id or ""}
    return JSONResponse(body, status_code=status, headers=headers)
