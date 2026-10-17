"""Pure ASGI ingress gate.

Runs before routing and before any request body is received:
* records trusted request-start time (UTC + monotonic) and a server-generated request id in
  ``scope["state"]`` (client timing / request-id headers are ignored);
* authenticates ``X-API-Key`` for every ``/api`` path with a constant-time comparison; a
  missing and a wrong key yield the identical 401 body and the body is never read;
* adds security headers (CSP, nosniff, no-referrer, frame deny; no CORS headers) and
  ``Cache-Control: no-store`` for API responses;
* logs request id, method, status and duration only (no keys, filenames or query strings).
"""

from __future__ import annotations

import hmac
import json
import logging
import time
import uuid

from cashwatch.deadline import iso_utc, utc_now

log = logging.getLogger("cashwatch.api")

PRINCIPAL_ID = "jury"  # single jury principal behind the one configured key

CSP = ("default-src 'self'; img-src 'self' blob: data:; media-src 'self' blob:; "
       "object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
SECURITY_HEADERS: list[tuple[bytes, bytes]] = [
    (b"content-security-policy", CSP.encode()),
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
    (b"x-frame-options", b"DENY"),
    (b"cross-origin-resource-policy", b"same-origin"),
]


def is_api_path(path: str) -> bool:
    return path == "/api" or path.startswith("/api/")


class IngressMiddleware:
    def __init__(self, app, *, api_key: str) -> None:
        if len(api_key) < 32:
            raise RuntimeError("API key too weak")
        self.app = app
        self._key = api_key.encode("utf-8")

    def _authorized(self, headers: list[tuple[bytes, bytes]]) -> bool:
        provided = b""
        for name, value in headers:
            if name == b"x-api-key":
                provided = value.strip()
                break
        # compare_digest on both branches; a missing key compares against an empty value.
        return hmac.compare_digest(provided, self._key) and bool(provided)

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started_mono = time.monotonic()
        rid = uuid.uuid4().hex
        state = scope.setdefault("state", {})
        state["request_id"] = rid
        state["started_utc"] = iso_utc(utc_now())
        state["started_mono"] = started_mono
        path = scope.get("path", "")
        api = is_api_path(path)
        status_holder = {"status": 0}

        async def send_wrapper(message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                headers = [(k, v) for k, v in message.get("headers", [])
                           if k.lower() not in (b"access-control-allow-origin", b"server")]
                headers.extend(SECURITY_HEADERS)
                headers.append((b"x-request-id", rid.encode()))
                if api:
                    headers.append((b"cache-control", b"no-store, private"))
                message = {**message, "headers": headers}
            await send(message)

        try:
            if api and not self._authorized(scope.get("headers", [])):
                body = json.dumps({"error": {"code": "unauthorized", "message": "Missing or invalid API key"},
                                   "request_id": rid}).encode()
                await send_wrapper({"type": "http.response.start", "status": 401, "headers": [
                    (b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
                    (b"connection", b"close")]})
                await send_wrapper({"type": "http.response.body", "body": body})
                return
            if api:
                state["principal_id"] = PRINCIPAL_ID
            await self.app(scope, receive, send_wrapper)
        finally:
            log.info("request_id=%s method=%s api=%s status=%s duration_ms=%.1f", rid, scope.get("method"),
                     api, status_holder["status"], (time.monotonic() - started_mono) * 1000)
