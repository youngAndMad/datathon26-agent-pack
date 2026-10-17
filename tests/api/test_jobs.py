"""Polling projection, UI report, evidence and media (T17 stable bytes, T19, T23)."""

from __future__ import annotations

import json
import uuid

import pytest
from jobfakes import FIXTURES, later, write_fake_result

from cashwatch.dto import StageProgress
from cashwatch.errors import public_error


def _submit(env, mov_bytes) -> str:
    r = env.upload(mov_bytes)
    assert r.status_code == 202
    return r.json()["job_id"]


def _finish(env, verdict="violation"):
    c = env.store.claim_job("fake-worker")
    out = write_fake_result(env.store, c, verdict=verdict)
    assert env.store.commit_result(c.job.job_id, c.job.lease_generation, out)
    return c


def test_done_returns_exact_stored_bytes_stably(env, mov_bytes, validate):
    jid = _submit(env, mov_bytes)
    _finish(env)
    stored = (env.store.jobs_dir / jid / "out-g1" / "jury.json").read_bytes()
    bodies = {env.client.get(f"/api/v1/jobs/{jid}", headers=env.h).content for _ in range(5)}
    assert bodies == {stored}
    r = env.client.get(f"/api/v1/jobs/{jid}", headers=env.h)
    assert r.headers["content-type"].startswith("application/json")
    body = r.json()
    validate("job-response.schema.json", body)
    assert body["status"] == "done" and body["verdict"] == "violation" and body["job_id"] == jid
    assert set(body) == {"job_id", "status", "verdict", "violations"}


def test_no_violation_done(env, mov_bytes, validate):
    jid = _submit(env, mov_bytes)
    _finish(env, verdict="no_violation")
    body = env.client.get(f"/api/v1/jobs/{jid}", headers=env.h).json()
    validate("job-response.schema.json", body)
    assert body == {"job_id": jid, "status": "done", "verdict": "no_violation", "violations": []}


def test_failed_projection_never_has_verdict(env, mov_bytes, validate):
    jid = _submit(env, mov_bytes)
    c = env.store.claim_job("w")
    env.store.fail_job(jid, c.job.lease_generation, public_error("decode_failed"))
    body = env.client.get(f"/api/v1/jobs/{jid}", headers=env.h).json()
    validate("job-response.schema.json", body)
    assert body == {"job_id": jid, "status": "failed",
                    "error": {"code": "decode_failed", "message": "Не удалось прочитать видеозапись"}}


def test_deadline_failure_visible_via_polling(env, mov_bytes, validate):
    """T19: no fabricated no_violation — a job past its deadline polls as failed."""
    jid = _submit(env, mov_bytes)
    env.store.claim_job("w")
    env.store.recover_expired_jobs(later(275))
    body = env.client.get(f"/api/v1/jobs/{jid}", headers=env.h).json()
    validate("job-response.schema.json", body)
    assert body["status"] == "failed" and body["error"]["code"] == "deadline_exceeded"


@pytest.mark.parametrize("jid", [str(uuid.uuid4()), "not-a-uuid", "..%2F..%2Fdb", "00000000-0000-1000-8000-000000000000"])
def test_unknown_and_malformed_ids_are_404(env, jid):
    for suffix in ("", "/report", "/media", "/evidence/e1-0.jpg"):
        r = env.client.get(f"/api/v1/jobs/{jid}{suffix}", headers=env.h)
        assert r.status_code == 404, (jid, suffix)
        assert r.json()["error"]["code"] == "not_found"


def test_expired_job_is_410(make_env, mov_bytes):
    env = make_env(retention_hours=1)
    env.ready()
    jid = _submit(env, mov_bytes)
    _finish(env)
    assert env.store.cleanup_retention(later(7200)) == 1
    for suffix in ("", "/report", "/media"):
        r = env.client.get(f"/api/v1/jobs/{jid}{suffix}", headers=env.h)
        assert r.status_code == 410 and r.json()["error"]["code"] == "expired"


def test_report_processing_done_failed_match_ui_schema(env, mov_bytes, validate):
    jid = _submit(env, mov_bytes)
    r = env.client.get(f"/api/v1/jobs/{jid}/report", headers=env.h).json()
    validate("ui-report.schema.json", r)
    assert r["status"] == "processing" and r["stage"] == "receiving" and r["media_available"] is True
    c = env.store.claim_job("w")
    env.store.heartbeat(jid, 1, stage="observing", progress=StageProgress(completed=3, total=8, unit="windows"))
    r = env.client.get(f"/api/v1/jobs/{jid}/report", headers=env.h).json()
    validate("ui-report.schema.json", r)
    assert r["stage"] == "observing" and r["progress"] == {"completed": 3, "total": 8, "unit": "windows"}
    out = write_fake_result(env.store, c)
    env.store.commit_result(jid, 1, out)
    r = env.client.get(f"/api/v1/jobs/{jid}/report", headers=env.h).json()
    validate("ui-report.schema.json", r)
    assert r["status"] == "done" and r["stage"] == "complete" and r["verdict"] == "violation"
    assert r["mode"] == "test" and r["elapsed_sec"] == 1.5
    assert [e["event_id"] for e in r["events"]] == ["e1", "e2"]
    e1 = r["events"][0]
    assert e1["timecode_sec"] == 12.345 and e1["evidence"] == [
        {"timestamp_sec": 12.345, "image_url": f"/api/v1/jobs/{jid}/evidence/e1-0.jpg"}]
    assert e1["source"]["official_url"].startswith("https://adilet.zan.kz/")
    jury = env.client.get(f"/api/v1/jobs/{jid}", headers=env.h).json()
    assert [v["description"] for v in jury["violations"]] == [e["description"] for e in r["events"]]
    text = json.dumps(r)
    assert str(env.store.root) not in text and "jpeg_path" not in text and "prompt" not in text


def test_report_failed(env, mov_bytes, validate):
    jid = _submit(env, mov_bytes)
    c = env.store.claim_job("w")
    env.store.heartbeat(jid, 1, stage="observing")
    env.store.fail_job(jid, c.job.lease_generation, public_error("gpu_oom"))
    r = env.client.get(f"/api/v1/jobs/{jid}/report", headers=env.h).json()
    validate("ui-report.schema.json", r)
    assert r["status"] == "failed" and r["stage"] == "observing" and r["error"]["code"] == "gpu_oom"
    assert r["verdict"] is None and r["events"] == []


def test_t23_evidence_served_and_contained(env, mov_bytes):
    jid = _submit(env, mov_bytes)
    _finish(env)
    r = env.client.get(f"/api/v1/jobs/{jid}/evidence/e1-0.jpg", headers=env.h)
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert r.content == (FIXTURES / "e1.jpg").read_bytes()
    assert r.headers["cache-control"] == "no-store, private"
    for bad in ("e9-0.jpg", "e1-0.png", "..%2Fjury.json", "%2E%2E%2Freport.json.jpg", "e1-0"):
        assert env.client.get(f"/api/v1/jobs/{jid}/evidence/{bad}", headers=env.h).status_code == 404, bad


def test_t23_media_ranges(env, mov_bytes):
    jid = _submit(env, mov_bytes)
    url = f"/api/v1/jobs/{jid}/media"
    size = len(mov_bytes)
    full = env.client.get(url, headers=env.h)
    assert full.status_code == 200 and full.content == mov_bytes
    assert full.headers["accept-ranges"] == "bytes" and full.headers["content-type"] == "video/quicktime"
    r = env.client.get(url, headers={**env.h, "Range": "bytes=10-99"})
    assert r.status_code == 206 and r.content == mov_bytes[10:100]
    assert r.headers["content-range"] == f"bytes 10-99/{size}" and r.headers["content-length"] == "90"
    r = env.client.get(url, headers={**env.h, "Range": "bytes=-16"})
    assert r.status_code == 206 and r.content == mov_bytes[-16:]
    r = env.client.get(url, headers={**env.h, "Range": f"bytes={size - 5}-"})
    assert r.content == mov_bytes[-5:]
    r = env.client.get(url, headers={**env.h, "Range": f"bytes={size}-"})
    assert r.status_code == 416 and r.headers["content-range"] == f"bytes */{size}"
    r = env.client.get(url, headers={**env.h, "Range": "bytes=0-1,5-9"})  # multi-range ignored → full
    assert r.status_code == 200 and len(r.content) == size


def test_t23_media_prefers_preview(env, mov_bytes):
    jid = _submit(env, mov_bytes)
    c = env.store.claim_job("w")
    out = write_fake_result(env.store, c)
    (out / "preview.mp4").write_bytes(b"\x00\x00\x00\x08free" * 4)
    env.store.commit_result(jid, 1, out)
    r = env.client.get(f"/api/v1/jobs/{jid}/media", headers=env.h)
    assert r.headers["content-type"] == "video/mp4" and r.content == b"\x00\x00\x00\x08free" * 4
