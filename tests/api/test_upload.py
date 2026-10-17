"""Upload: T02, T03, T04, T18 (capacity), T27 (idempotency), readiness 503."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
import uuid

import pytest

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 200


def _db(env, sql):
    return sqlite3.connect(env.store.db_path).execute(sql).fetchall()


def _no_leftovers(env):
    assert list(env.store.tmp_dir.iterdir()) == []
    assert _db(env, "SELECT COUNT(*) FROM reservations")[0][0] == 0


def test_t02_octet_stream_mov_accepted_and_persisted(env, mov_bytes, validate):
    r = env.upload(mov_bytes, ctype="application/octet-stream")
    assert r.status_code == 202, r.text
    validate("submit-response.schema.json", r.json())
    jid = r.json()["job_id"]
    assert uuid.UUID(jid).version == 4 and set(r.json()) == {"job_id"}
    v = env.store.get_job(jid)
    assert v.state == "queued" and v.source_path == f"jobs/{jid}/source.mov"
    src = env.store.jobs_dir / jid / "source.mov"
    assert src.read_bytes() == mov_bytes
    row = _db(env, f"SELECT source_hash, size_bytes FROM jobs WHERE id='{jid}'")[0]
    assert row == (hashlib.sha256(mov_bytes).hexdigest(), len(mov_bytes))
    assert oct(src.stat().st_mode & 0o777) == "0o600"
    _no_leftovers(env)
    status = env.client.get(f"/api/v1/jobs/{jid}", headers=env.h)
    assert status.json() == {"job_id": jid, "status": "processing"}
    validate("job-response.schema.json", status.json())


def test_t03_renamed_bin_accepted(env, mov_bytes):
    r = env.upload(mov_bytes, name="video.bin", ctype="application/x-whatever")
    assert r.status_code == 202


@pytest.mark.parametrize("data,status,code", [
    (PNG, 415, "unsupported_container"),
    (os.urandom(4096), 415, "unsupported_container"),
    (b"", 422, "invalid_media"),
    (b"\x00\x00\x00\x14ftypqt  \x00\x00\x00\x00qt  ", 422, "invalid_media"),  # header only, no movie
], ids=["png", "random", "empty", "ftyp-only"])
def test_t03_rejects_unsupported_corrupt_empty(env, data, status, code):
    r = env.upload(data, name="clip.mov", ctype="video/quicktime")
    assert r.status_code == status
    assert r.json()["error"]["code"] == code and "request_id" in r.json()
    assert str(env.store.root) not in r.text and "Traceback" not in r.text  # no paths/stack traces
    _no_leftovers(env)
    assert list(env.store.jobs_dir.iterdir()) == []


def test_t03_truncated_mov_with_moov_is_left_to_worker(env, mov_bytes):
    # A cheap sniff cannot decide decodability; deep validation happens in the worker.
    assert env.upload(mov_bytes[: len(mov_bytes) - 100]).status_code == 202


@pytest.mark.parametrize("files", [
    {"video": ("a.mov", b"x", "video/quicktime")},
    [("file", ("a.mov", b"\x00\x00\x00\x08wide", "video/quicktime")),
     ("file", ("b.mov", b"\x00\x00\x00\x08wide", "video/quicktime"))],
])
def test_exactly_one_file_field(env, files):
    r = env.client.post("/api/v1/analyze", files=files, headers=env.h)
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_multipart"
    _no_leftovers(env)


def test_non_multipart_rejected(env, mov_bytes):
    r = env.client.post("/api/v1/analyze", content=mov_bytes,
                        headers={**env.h, "Content-Type": "video/quicktime"})
    assert r.status_code == 422
    _no_leftovers(env)


def test_t04_oversize_streamed_and_content_length(make_env, mov_bytes):
    env = make_env(max_file_bytes=len(mov_bytes) - 1)
    env.ready()
    r = env.upload(mov_bytes)  # streamed check (Content-Length within multipart allowance)
    assert r.status_code == 413 and r.json()["error"]["code"] == "payload_too_large"
    _no_leftovers(env)
    r = env.client.post("/api/v1/analyze", headers={**env.h, "Content-Type": "multipart/form-data; boundary=b",
                                                     "Content-Length": str(10 ** 10)}, content=b"--b--")
    assert r.status_code == 413
    _no_leftovers(env)
    assert env.store.counts() == {}


def test_upload_time_limit(make_env, mov_bytes):
    env = make_env(max_upload_sec=0.0)
    env.ready()
    r = env.upload(mov_bytes)
    assert r.status_code == 400 and r.json()["error"]["code"] == "upload_timeout"
    _no_leftovers(env)


def test_503_when_worker_not_ready(make_env, mov_bytes):
    env = make_env()
    r = env.upload(mov_bytes)
    assert r.status_code == 503 and r.json()["error"]["code"] == "not_ready"
    _no_leftovers(env)
    env.store.worker_heartbeat("w", ready=False, info={})  # loading/warming up
    assert env.upload(mov_bytes).status_code == 503


def _assert_429(r):
    assert r.status_code == 429 and r.json()["error"]["code"] == "capacity_exceeded"
    assert 1 <= int(r.headers["Retry-After"]) <= 300


def test_t18_capacity_429_with_retry_after_queue0(make_env, mov_bytes):
    env = make_env(active_jobs=1, max_queue_length=0)
    env.ready()
    assert env.upload(mov_bytes).status_code == 202
    _assert_429(env.upload(mov_bytes))
    _no_leftovers(env)
    assert env.store.counts() == {"queued": 1}
    # repeated polling during a full queue stays cheap and stable
    for _ in range(20):
        assert env.client.get(f"/api/v1/jobs/{uuid.uuid4()}", headers=env.h).status_code == 404


def test_t18_capacity_queue1_second_queued_third_429(make_env, mov_bytes):
    env = make_env(active_jobs=1, max_queue_length=1)
    env.ready()
    a = env.upload(mov_bytes)
    assert a.status_code == 202
    assert env.store.claim_job("w").job.job_id == a.json()["job_id"]  # first one running
    b = env.upload(mov_bytes)
    assert b.status_code == 202  # admitted to the single queue slot
    _assert_429(env.upload(mov_bytes))
    _no_leftovers(env)
    assert env.store.counts() == {"running": 1, "queued": 1}
    assert env.client.get(f"/api/v1/jobs/{b.json()['job_id']}", headers=env.h).json()["status"] == "processing"


def test_t18_queued_job_keeps_original_deadline(make_env, mov_bytes):
    """A queued job claimed later keeps deadline_at = trusted request start + internal_terminal_sec,
    and is failed deadline_exceeded instead of claimed once that has passed."""
    from jobfakes import later, write_fake_result

    from cashwatch.deadline import parse_utc
    from cashwatch.errors import public_error

    env = make_env(active_jobs=1, max_queue_length=1)
    env.ready()
    a = env.upload(mov_bytes).json()["job_id"]
    b = env.upload(mov_bytes).json()["job_id"]
    ca = env.store.claim_job("w")
    assert ca.job.job_id == a
    vb = env.store.get_job(b)
    delta = (parse_utc(vb.deadline_at) - parse_utc(vb.received_at)).total_seconds()
    assert delta == env.settings.internal_terminal_sec
    # A finishes; B claimed "later" (still in time) keeps its original deadline
    assert env.store.commit_result(a, 1, write_fake_result(env.store, ca))
    cb = env.store.claim_job("w", now_utc=later(100))
    assert cb.job.job_id == b and cb.job.deadline_at == vb.deadline_at
    assert cb.job.request_started_at == vb.received_at

    # Second scenario: the queued job's original deadline passes while waiting -> failed, not claimed
    env2 = make_env(active_jobs=1, max_queue_length=1)
    env2.ready()
    x = env2.upload(mov_bytes).json()["job_id"]
    y = env2.upload(mov_bytes).json()["job_id"]
    cx = env2.store.claim_job("w")
    assert cx.job.job_id == x
    env2.store.fail_job(x, 1, public_error("decode_failed"))
    deadline_y = env2.store.get_job(y).deadline_at
    assert env2.store.claim_job("w", now_utc=later(env2.settings.internal_terminal_sec + 1)) is None
    vy = env2.store.get_job(y)
    assert vy.state == "failed" and vy.error_code == "deadline_exceeded" and vy.deadline_at == deadline_y
    body = env2.client.get(f"/api/v1/jobs/{y}", headers=env2.h).json()
    assert body["status"] == "failed" and body["error"]["code"] == "deadline_exceeded"


def test_t18_concurrent_burst_bounded(make_env, mov_bytes):
    env = make_env(active_jobs=1, max_queue_length=1)
    env.ready()
    codes, barrier = [], threading.Barrier(4)

    def go():
        barrier.wait()
        codes.append(env.upload(mov_bytes).status_code)

    ts = [threading.Thread(target=go) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(codes) == [202, 202, 429, 429]
    assert env.store.counts() == {"queued": 2}
    _no_leftovers(env)


def test_t27_idempotency_same_and_different(make_env, mov_bytes):
    env = make_env(active_jobs=1, max_queue_length=3)
    env.ready()
    k = {"Idempotency-Key": "retry-123"}
    a = env.upload(mov_bytes, headers=k)
    b = env.upload(mov_bytes, headers=k)
    assert a.status_code == b.status_code == 202
    assert a.json() == b.json()
    other = mov_bytes + b"\x00\x00\x00\x08free"
    c = env.upload(other, headers=k)
    assert c.status_code == 409 and c.json()["error"]["code"] == "idempotency_conflict"
    assert a.json()["job_id"] not in c.text  # no leakage of the bound job
    d = env.upload(mov_bytes, headers={"Idempotency-Key": "another"})
    assert d.status_code == 202 and d.json() != a.json()
    assert env.store.counts() == {"queued": 2}
    assert len(list(env.store.jobs_dir.iterdir())) == 2
    _no_leftovers(env)


def test_t27_idempotent_replay_bypasses_full_capacity(make_env, mov_bytes):
    env = make_env(active_jobs=1, max_queue_length=0)
    env.ready()
    k = {"Idempotency-Key": "k1"}
    a = env.upload(mov_bytes, headers=k)
    assert env.upload(mov_bytes).status_code == 429  # capacity is full now
    b = env.upload(mov_bytes, headers=k)  # the jury retrying the same request still gets its job
    assert b.status_code == 202 and b.json() == a.json()
