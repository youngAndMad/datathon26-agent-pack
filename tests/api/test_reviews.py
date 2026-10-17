"""Reviewer decisions, history list and export endpoints (synthetic fixtures only)."""

from __future__ import annotations

import asyncio
import hashlib
import json

import pytest
from jobfakes import finish

from cashwatch.dto import ExportBundle, JobListResponse, QualitySummary, RuleCoverage
from cashwatch.errors import public_error

JOB = "00000000-0000-4000-8000-000000000001"
NEW_ROUTES = [
    ("POST", f"/api/v1/jobs/{JOB}/events/e1/review"),
    ("GET", f"/api/v1/jobs/{JOB}/reviews"),
    ("GET", "/api/v1/jobs"),
    ("GET", "/api/v1/jobs?limit=5&status=done"),
    ("GET", f"/api/v1/jobs/{JOB}/export"),
]


def _submit(env, mov_bytes) -> str:
    r = env.upload(mov_bytes)
    assert r.status_code == 202
    return r.json()["job_id"]


def _done(env, mov_bytes, **kw) -> str:
    jid = _submit(env, mov_bytes)
    assert finish(env.store, **kw) == jid
    return jid


def _review(env, jid, event_id, **body):
    return env.client.post(f"/api/v1/jobs/{jid}/events/{event_id}/review", json=body, headers=env.h)


def _jury_sha(env, jid):
    return hashlib.sha256((env.store.jobs_dir / jid / "out-g1" / "jury.json").read_bytes()).hexdigest()


# ------------------------------------------------------------------ auth


@pytest.mark.parametrize("method,path", NEW_ROUTES)
@pytest.mark.parametrize("key", [None, "wrong"])
def test_new_routes_require_key(env, method, path, key):
    headers = {} if key is None else {"X-API-Key": key}
    r = env.client.request(method, path, headers=headers, json={"decision": "confirmed"} if method == "POST" else None)
    assert r.status_code == 401
    assert r.json()["error"] == {"code": "unauthorized", "message": "Missing or invalid API key"}


def test_review_post_401_without_reading_body(env):
    calls = {"receive": 0}
    sent = []

    async def receive():
        calls["receive"] += 1
        return {"type": "http.request", "body": b"{}", "more_body": False}

    async def send(msg):
        sent.append(msg)

    path = f"/api/v1/jobs/{JOB}/events/e1/review"
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST", "scheme": "http",
             "path": path, "raw_path": path.encode(), "query_string": b"", "root_path": "",
             "headers": [(b"content-type", b"application/json"), (b"content-length", b"2")],
             "client": ("127.0.0.1", 1), "server": ("testserver", 80)}
    asyncio.run(env.app(scope, receive, send))
    assert calls["receive"] == 0 and sent[0]["status"] == 401


# ------------------------------------------------------------------ review validation


def test_review_happy_path_and_results_unchanged(env, mov_bytes, validate):
    jid = _done(env, mov_bytes)
    sha = _jury_sha(env, jid)
    jury_before = env.client.get(f"/api/v1/jobs/{jid}", headers=env.h).content
    r = _review(env, jid, "e1", decision="confirmed", comment="ок", reviewer="тест")
    assert r.status_code == 201
    body = r.json()
    assert body["event_id"] == "e1" and body["decision"] == "confirmed" and body["reviewer"] == "тест"
    assert len(body["review_id"]) == 36 and body["reviewed_at"].endswith("+00:00")
    r2 = _review(env, jid, "e2", decision="rejected", corrected_type="другое", corrected_timecode_sec=47.5)
    assert r2.status_code == 201
    r3 = _review(env, jid, "missed", decision="missed", corrected_timecode_sec=59.9, comment="пропущено")
    assert r3.status_code == 201
    r4 = _review(env, jid, "e1", decision="needs_review")
    assert r4.status_code == 201

    assert _jury_sha(env, jid) == sha
    assert env.client.get(f"/api/v1/jobs/{jid}", headers=env.h).content == jury_before

    lst = env.client.get(f"/api/v1/jobs/{jid}/reviews", headers=env.h)
    assert lst.status_code == 200
    assert [x["review_id"] for x in lst.json()] == [x.json()["review_id"] for x in (r, r2, r3, r4)]

    rep = env.client.get(f"/api/v1/jobs/{jid}/report", headers=env.h).json()
    validate("ui-report.schema.json", rep)
    assert rep["reviews"] == lst.json()
    assert rep["verdict"] == "violation" and [e["event_id"] for e in rep["events"]] == ["e1", "e2"]


def test_review_unknown_event_404(env, mov_bytes):
    jid = _done(env, mov_bytes)
    for ev in ("e9", "E1", "missed2"):
        r = _review(env, jid, ev, decision="confirmed")
        assert r.status_code == 404 and r.json()["error"]["code"] == "not_found", ev
    assert _review(env, "00000000-0000-4000-8000-00000000abcd", "e1", decision="confirmed").status_code == 404


def test_review_on_processing_or_failed_job_409(env, mov_bytes):
    jid = _submit(env, mov_bytes)
    r = _review(env, jid, "e1", decision="confirmed")
    assert r.status_code == 409 and r.json()["error"]["code"] == "job_not_done"
    c = env.store.claim_job("w")
    assert _review(env, jid, "e1", decision="confirmed").status_code == 409
    env.store.fail_job(jid, c.job.lease_generation, public_error("decode_failed"))
    assert _review(env, jid, "missed", decision="missed", corrected_timecode_sec=1).status_code == 409
    assert env.client.get(f"/api/v1/jobs/{jid}/reviews", headers=env.h).json() == []


@pytest.mark.parametrize("event_id,body", [
    ("missed", {"decision": "missed"}),  # missing timecode
    ("missed", {"decision": "confirmed", "corrected_timecode_sec": 3}),  # missed slot needs decision missed
    ("e1", {"decision": "missed", "corrected_timecode_sec": 3}),  # decision missed needs event "missed"
    ("missed", {"decision": "missed", "corrected_timecode_sec": 60.001}),  # beyond duration
    ("e1", {"decision": "rejected", "corrected_timecode_sec": 1000}),  # beyond duration
])
def test_review_semantic_rejections_422(env, mov_bytes, event_id, body):
    jid = _done(env, mov_bytes, duration_sec=60.0)
    r = _review(env, jid, event_id, **body)
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_review"
    assert env.client.get(f"/api/v1/jobs/{jid}/reviews", headers=env.h).json() == []


@pytest.mark.parametrize("raw", [
    b"", b"not json", b"[]", b'{"decision":"maybe"}', b'{"decision":"confirmed","extra":1}',
    b'{"decision":"missed","corrected_timecode_sec":-1}', json.dumps({"decision": "confirmed", "comment": "x" * 1001}).encode(),
])
def test_review_malformed_bodies_422(env, mov_bytes, raw):
    jid = _done(env, mov_bytes)
    r = env.client.post(f"/api/v1/jobs/{jid}/events/e1/review", content=raw,
                        headers={**env.h, "Content-Type": "application/json"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_request"
    assert "Traceback" not in r.text and str(env.store.root) not in r.text


def test_review_timecode_at_bounds_accepted(env, mov_bytes):
    jid = _done(env, mov_bytes, duration_sec=60.0)
    assert _review(env, jid, "missed", decision="missed", corrected_timecode_sec=0).status_code == 201
    assert _review(env, jid, "missed", decision="missed", corrected_timecode_sec=60.0).status_code == 201


def test_review_timecode_rejected_when_duration_unknown(env, mov_bytes):
    jid = _done(env, mov_bytes, duration_sec=None)  # report.source is None
    assert _review(env, jid, "missed", decision="missed", corrected_timecode_sec=1).status_code == 422
    assert _review(env, jid, "e1", decision="confirmed").status_code == 201


def test_review_body_cap_413(env, mov_bytes):
    jid = _done(env, mov_bytes)
    big = json.dumps({"decision": "confirmed", "comment": "x" * 20000}).encode()
    r = env.client.post(f"/api/v1/jobs/{jid}/events/e1/review", content=big,
                        headers={**env.h, "Content-Type": "application/json"})
    assert r.status_code == 413 and r.json()["error"]["code"] == "payload_too_large"

    def chunks():  # no Content-Length: streamed cap
        for _ in range(20):
            yield b"x" * 1024

    r = env.client.post(f"/api/v1/jobs/{jid}/events/e1/review", content=chunks(),
                        headers={**env.h, "Content-Type": "application/json"})
    assert r.status_code == 413


def test_review_on_expired_job_410_and_reviews_removed(make_env, mov_bytes):
    from jobfakes import later

    env = make_env(retention_hours=1)
    env.ready()
    jid = _done(env, mov_bytes)
    assert _review(env, jid, "e1", decision="confirmed").status_code == 201
    env.store.cleanup_retention(later(7200))
    assert _review(env, jid, "e1", decision="confirmed").status_code == 410
    assert env.client.get(f"/api/v1/jobs/{jid}/reviews", headers=env.h).status_code == 410
    assert env.store.list_reviews(jid) == []


# ------------------------------------------------------------------ report projection


def test_report_projects_rule_coverage_quality_review_required(env, mov_bytes, validate):
    cov = [RuleCoverage(rule_id="FIXTURE-A", type_ru="Тестовый тип FIXTURE-A", status="checked"),
           RuleCoverage(rule_id="FIXTURE-B", type_ru="Тестовый тип FIXTURE-B", status="partially_checked",
                        unverifiable_intervals=[{"start_sec": 1.0, "end_sec": 2.0}], note_ru="перекрыто")]
    q = QualitySummary(sampled_frames=10, dark_frames=2, low_detail_frames=0, notes_ru=["Тёмные кадры: 2 из 10"])
    jid = _done(env, mov_bytes, rule_coverage=cov, quality=q, review_required=True)
    rep = env.client.get(f"/api/v1/jobs/{jid}/report", headers=env.h).json()
    validate("ui-report.schema.json", rep)
    assert rep["rule_coverage"] == [c.model_dump(mode="json") for c in cov]
    assert rep["quality_notes_ru"] == ["Тёмные кадры: 2 из 10"]
    assert rep["review_required"] is True and rep["reviews"] == []


def test_old_report_without_new_fields_still_projects(env, mov_bytes, validate):
    jid = _done(env, mov_bytes, drop=("rule_coverage", "quality", "review_required", "coverage_notice_ru"))
    raw = json.loads((env.store.jobs_dir / jid / "out-g1" / "report.json").read_text(encoding="utf-8"))
    assert "rule_coverage" not in raw and "quality" not in raw and "review_required" not in raw
    rep = env.client.get(f"/api/v1/jobs/{jid}/report", headers=env.h)
    assert rep.status_code == 200
    body = rep.json()
    validate("ui-report.schema.json", body)
    assert body["rule_coverage"] == [] and body["quality_notes_ru"] == [] and body["review_required"] is False
    assert env.client.get(f"/api/v1/jobs/{jid}/export", headers=env.h).status_code == 200
    assert env.client.get("/api/v1/jobs", headers=env.h).json()["jobs"][0]["review_required"] is False


# ------------------------------------------------------------------ history


def test_jobs_list_order_filter_limit(make_env, mov_bytes):
    env = make_env(active_jobs=1, max_queue_length=10)
    env.ready()
    a = _done(env, mov_bytes, review_required=True)
    b = _submit(env, mov_bytes)
    c = env.store.claim_job("w")
    env.store.fail_job(b, c.job.lease_generation, public_error("deadline_exceeded"))
    d = _submit(env, mov_bytes)
    assert _review(env, a, "e1", decision="confirmed").status_code == 201
    assert _review(env, a, "e1", decision="rejected").status_code == 201
    assert _review(env, a, "missed", decision="missed", corrected_timecode_sec=5).status_code == 201

    r = env.client.get("/api/v1/jobs", headers=env.h)
    assert r.status_code == 200
    body = JobListResponse.model_validate(r.json())
    assert [j.job_id for j in body.jobs] == [d, b, a]
    ja = body.jobs[2]
    assert (ja.status, ja.verdict, ja.event_count, ja.reviewed_event_count, ja.review_required, ja.mode) == (
        "done", "violation", 2, 1, True, "test")
    assert body.jobs[1].status == "failed" and body.jobs[1].error_code == "deadline_exceeded"
    assert body.jobs[0].status == "processing" and body.jobs[0].verdict is None

    def ids(q):
        resp = env.client.get(f"/api/v1/jobs{q}", headers=env.h)
        assert resp.status_code == 200, q
        return [j["job_id"] for j in resp.json()["jobs"]]

    assert ids("?status=done") == [a]
    assert ids("?status=failed") == [b]
    assert ids("?status=processing") == [d]
    assert ids("?limit=1") == [d]
    assert ids("?limit=200&status=") == [d, b, a]
    for bad in ("?limit=0", "?limit=201", "?limit=-1", "?limit=abc", "?status=expired", "?status=DONE"):
        resp = env.client.get(f"/api/v1/jobs{bad}", headers=env.h)
        assert resp.status_code == 422 and resp.json()["error"]["code"] == "invalid_request", bad


def test_jobs_list_does_not_shadow_job_routes(env, mov_bytes):
    jid = _submit(env, mov_bytes)
    assert env.client.get(f"/api/v1/jobs/{jid}", headers=env.h).json() == {"job_id": jid, "status": "processing"}
    assert [j["job_id"] for j in env.client.get("/api/v1/jobs", headers=env.h).json()["jobs"]] == [jid]


def test_jobs_list_many_jobs_fast(make_env, mov_bytes):
    """Hundreds of committed jobs: list reads only the DB (cached summaries), no result files."""
    import time

    from unittest import mock

    from jobfakes import enqueue

    env = make_env(active_jobs=1, max_queue_length=400)
    env.ready()
    for _ in range(300):
        enqueue(env.store)
        finish(env.store)

    with mock.patch.object(env.store, "_result_summary", side_effect=AssertionError("file read")):
        t = time.perf_counter()
        r = env.client.get("/api/v1/jobs?limit=200", headers=env.h)
        dt = time.perf_counter() - t
    assert r.status_code == 200 and len(r.json()["jobs"]) == 200
    assert dt < 1.0


# ------------------------------------------------------------------ export


def test_export_bundle(env, mov_bytes):
    jid = _done(env, mov_bytes)
    assert _review(env, jid, "e1", decision="confirmed").status_code == 201
    r = env.client.get(f"/api/v1/jobs/{jid}/export", headers=env.h)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    assert r.headers["content-disposition"] == f'attachment; filename="cashwatch-{jid}.json"'
    assert r.headers["cache-control"] == "no-store, private"
    bundle = ExportBundle.model_validate(r.json())
    stored = json.loads((env.store.jobs_dir / jid / "out-g1" / "jury.json").read_bytes())
    assert r.json()["original_jury_result"] == stored
    assert bundle.job_id == jid and bundle.report.status == "done"
    assert [x.decision for x in bundle.report.reviews] == ["confirmed"]
    assert bundle.versions is not None and bundle.versions.registry_version == "fixture"
    text = r.text
    assert str(env.store.root) not in text and "jpeg_path" not in text


def test_export_not_done_409(env, mov_bytes):
    jid = _submit(env, mov_bytes)
    r = env.client.get(f"/api/v1/jobs/{jid}/export", headers=env.h)
    assert r.status_code == 409 and r.json()["error"]["code"] == "job_not_done"
    assert env.client.get("/api/v1/jobs/not-a-uuid/export", headers=env.h).status_code == 404
