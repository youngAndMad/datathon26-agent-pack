"""T01: missing/wrong key → identical 401 before any body is read; no temp files/reservations."""

from __future__ import annotations

import asyncio
import sqlite3

import pytest

JOB = "00000000-0000-4000-8000-000000000001"
ROUTES = [
    ("POST", "/api/v1/analyze"),
    ("GET", f"/api/v1/jobs/{JOB}"),
    ("GET", f"/api/v1/jobs/{JOB}/report"),
    ("GET", f"/api/v1/jobs/{JOB}/media"),
    ("GET", f"/api/v1/jobs/{JOB}/evidence/e1-0.jpg"),
    ("GET", "/api/v1/unknown"),
]


def _state_snapshot(env):
    conn = sqlite3.connect(env.store.db_path)
    res = conn.execute("SELECT COUNT(*) FROM reservations").fetchone()[0]
    jobs = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    return res, jobs, sorted(p.name for p in env.store.tmp_dir.iterdir()), sorted(
        p.name for p in env.store.jobs_dir.iterdir())


@pytest.mark.parametrize("method,path", ROUTES)
@pytest.mark.parametrize("key", [None, "wrong", "x" * 64])
def test_unauthorized_identical_and_side_effect_free(env, mov_bytes, method, path, key):
    before = _state_snapshot(env)
    headers = {} if key is None else {"X-API-Key": key}
    if method == "POST":
        r = env.client.post(path, files={"file": ("a.mov", mov_bytes, "video/quicktime")}, headers=headers)
    else:
        r = env.client.get(path, headers=headers)
    assert r.status_code == 401
    body = r.json()
    assert body["error"] == {"code": "unauthorized", "message": "Missing or invalid API key"}
    assert len(body["request_id"]) == 32
    assert env.key not in r.text
    assert _state_snapshot(env) == before == (0, 0, [], [])


def test_key_not_accepted_from_query_or_lowercase_variants(env):
    r = env.client.get(f"/api/v1/jobs/{JOB}?api_key={env.key}")
    assert r.status_code == 401
    r = env.client.get(f"/api/v1/jobs/{JOB}", headers={"Authorization": f"Bearer {env.key}"})
    assert r.status_code == 401
    r = env.client.get(f"/api/v1/jobs/{JOB}", headers={"x-api-key": env.key})  # header names are case-insensitive
    assert r.status_code == 404


def test_gate_rejects_before_reading_body(env):
    """Drive the ASGI app directly: the 401 must be sent without a single receive() call."""
    calls = {"receive": 0}
    sent = []

    async def receive():
        calls["receive"] += 1
        return {"type": "http.request", "body": b"x" * 1024, "more_body": True}

    async def send(msg):
        sent.append(msg)

    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
             "scheme": "http", "path": "/api/v1/analyze", "raw_path": b"/api/v1/analyze", "query_string": b"",
             "root_path": "", "headers": [(b"content-type", b"multipart/form-data; boundary=x"),
                                          (b"content-length", b"999999999"), (b"x-api-key", b"bad")],
             "client": ("127.0.0.1", 1), "server": ("testserver", 80)}
    asyncio.run(env.app(scope, receive, send))
    assert calls["receive"] == 0
    assert sent[0]["status"] == 401


def test_health_endpoints_need_no_key(make_env):
    e = make_env()
    assert e.client.get("/healthz").json() == {"status": "ok"}
    r = e.client.get("/readyz")
    assert r.status_code == 503 and r.json() == {"status": "not_ready"}
    e.ready()
    r = e.client.get("/readyz")
    assert r.status_code == 200 and r.json() == {"status": "ready"}
