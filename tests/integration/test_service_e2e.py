"""End-to-end black-box tests against real API + worker processes (TEST mode, synthetic media).

With the default fake backend these prove plumbing/contract behaviour only, never detection
quality. Acceptance IDs refer to IMPLEMENTATION_SPEC §13.2."""

from __future__ import annotations

import os
import signal
import threading
import time
import uuid

import httpx
import pytest

from .conftest import IT_BACKEND

pytestmark = [pytest.mark.slow] + ([pytest.mark.model] if IT_BACKEND != "fake" else [])
UNKNOWN_JOB = str(uuid.uuid4())
TIMEOUT = 300.0


def client():
    return httpx.Client(timeout=30.0, trust_env=False)


# --------------------------------------------------------------------------- T01

def _endpoints(job_id: str):
    return [("GET", f"/api/v1/jobs/{job_id}"), ("GET", f"/api/v1/jobs/{job_id}/report"),
            ("GET", f"/api/v1/jobs/{job_id}/media"), ("GET", f"/api/v1/jobs/{job_id}/evidence/e1-0.jpg")]


@pytest.fixture(params=["api_only", "service"])
def auth_target(request):
    return request.getfixturevalue(request.param)


def test_t01_auth_required_everywhere(auth_target, clip):
    service = auth_target
    mov = clip("tiny")
    before = service.files_snapshot()
    bodies = {}
    with client() as c:
        for label, hdrs in (("missing", {}), ("wrong", {"X-API-Key": "x" * 64}),
                            ("query-only", {})):
            url = f"{service.base_url}/api/v1/analyze" + ("?api_key=" + service.key if label == "query-only" else "")
            with open(mov, "rb") as fh:
                r = c.post(url, headers=hdrs, files={"file": ("a.mov", fh, "video/quicktime")})
            assert r.status_code == 401, (label, r.status_code)
            bodies[label] = r.json().get("error", {}).get("code")
            for method, path in _endpoints(UNKNOWN_JOB):
                r = c.request(method, service.base_url + path, headers=hdrs)
                assert r.status_code == 401, (label, path, r.status_code)
        # Same generic response for missing vs wrong key; key never echoed.
        assert bodies["missing"] == bodies["wrong"]
        r = c.post(f"{service.base_url}/api/v1/analyze", headers={"X-API-Key": "bad"},
                   files={"file": ("a.mov", b"x", "video/quicktime")})
        assert service.key not in r.text
    # No temp files / job dirs created by rejected uploads (auth before body consumption).
    assert service.files_snapshot() == before


def test_t01_valid_key_unknown_job_is_404(service):
    with client() as c:
        r = c.get(f"{service.base_url}/api/v1/jobs/{UNKNOWN_JOB}", headers={"X-API-Key": service.key})
    assert r.status_code in (404, 422)


def test_healthz_is_public_and_minimal(service):
    with client() as c:
        r = c.get(f"{service.base_url}/healthz")
    assert r.status_code == 200
    assert service.key not in r.text and str(service.data_dir) not in r.text


# --------------------------------------------------------------------------- happy path / T02 / T03

@pytest.mark.parametrize("name", ["cfr_10s_30", "bframes_10s", "rotated_cw90_10s", "marker_negative_40s",
                                  "marker_violation_40s"])
def test_synthetic_clip_end_to_end_valid(service, clip, qa, name):
    mov = clip(name)
    res = qa.run_job(service.base_url, service.key, mov, timeout_sec=TIMEOUT)
    assert res.submit_http == 202, res.errors
    assert res.errors == [], res.errors
    # A valid synthetic MOV must complete; a technical failure is a defect, never a clean result.
    assert res.terminal_status == "done", (res.error_code, service.log_tail())
    assert res.stable_on_repoll is True
    assert res.upload_to_terminal_sec is not None and res.upload_to_terminal_sec < 300


@pytest.mark.model
@pytest.mark.skipif(IT_BACKEND == "fake", reason="labeled synthetic expectation needs the real model")
@pytest.mark.parametrize("name,expected", [("marker_violation_40s", "violation"),
                                           ("marker_negative_40s", "no_violation")])
def test_labeled_synthetic_marker_with_real_model(service, clip, qa, name, expected):
    """Test-registry synthetic rule (red square leaves yellow zone at t=20 s). Plumbing + model
    smoke on synthetic geometry only — not a cashier-domain accuracy result."""
    res = qa.run_job(service.base_url, service.key, clip(name), timeout_sec=TIMEOUT)
    assert res.errors == [] and res.terminal_status == "done"
    assert res.verdict == expected
    if expected == "violation":
        assert any(abs(t - 20.0) <= 2.0 for t in res.timecodes_sec), res.timecodes_sec


def test_t03_renamed_valid_mov_accepted(service, clip, qa):
    mov = clip("mov_renamed_bin")
    res = qa.run_job(service.base_url, service.key, mov, timeout_sec=TIMEOUT)
    assert res.submit_http == 202 and res.errors == [] and res.terminal_status == "done"


@pytest.mark.parametrize("name", ["empty", "png_renamed", "mkv_renamed", "truncated_nomoov"])
def test_t03_invalid_inputs_fail_safely(service, clip, qa, name):
    res = qa.run_job(service.base_url, service.key, clip(name), timeout_sec=TIMEOUT)
    if res.submit_http == 202:
        assert res.errors == [], res.errors
        assert res.terminal_status == "failed", "invalid media must never yield a done result"
        assert res.error_code in {"invalid_media", "unsupported_container", "decode_failed",
                                  "timeline_unsupported", "input_unusable", "limits_exceeded"}
    else:
        assert res.submit_http in (400, 413, 415, 422), res.submit_http


# --------------------------------------------------------------------------- T18

def test_t18_two_concurrent_uploads_bounded(service, clip, qa):
    mov = clip("marker_negative_40s")
    results = [None, None]

    def go(i):
        results[i] = qa.run_job(service.base_url, service.key, mov, timeout_sec=TIMEOUT)
    ts = [threading.Thread(target=go, args=(i,)) for i in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    codes = [r.submit_http for r in results]
    assert all(c in (202, 429) for c in codes), codes
    assert 202 in codes
    for r in results:
        if r.submit_http == 429:
            assert r.retry_after and r.retry_after.isdigit(), "429 needs a numeric Retry-After"
        else:
            assert r.errors == [] and r.terminal_status == "done", (r.errors, r.error_code)
    print(f"T18 submit codes={codes} latencies={[r.upload_to_terminal_sec for r in results]}")


# --------------------------------------------------------------------------- T19

def test_t19_deadline_mid_stage_fails_before_external_timeout(make_service, clip, qa):
    internal = 1.0
    svc = make_service(extra_env={"CASHWATCH_INTERNAL_TERMINAL_SEC": str(internal)})
    svc.start(timeout=240 if IT_BACKEND != "fake" else 90)
    res = qa.run_job(svc.base_url, svc.key, clip("marker_violation_40s"), timeout_sec=120)
    assert res.errors == [], res.errors
    assert res.terminal_status is not None, "job stuck in processing past its deadline"
    if res.terminal_status == "done":
        if res.upload_to_terminal_sec > internal + 1.0:
            pytest.fail(f"done after {res.upload_to_terminal_sec}s although hard deadline was {internal}s")
        pytest.skip("pipeline finished inside the 1 s deadline; T19 not exercised")
    assert res.error_code in ("deadline_exceeded", "worker_lost"), res.error_code
    # watchdog grace (8 s) + lease (20 s) + sweeper slack: still far below the jury's 300 s
    assert res.upload_to_terminal_sec < 60, res.upload_to_terminal_sec


# --------------------------------------------------------------------------- T17

def test_t17_kill_worker_mid_job_never_stuck(make_service, clip, qa):
    svc = make_service()
    svc.start(timeout=240 if IT_BACKEND != "fake" else 90)
    hdr = {"X-API-Key": svc.key}
    mov = clip("marker_violation_40s")
    with client() as c, open(mov, "rb") as fh:
        t0 = time.monotonic()
        r = c.post(f"{svc.base_url}/api/v1/analyze", headers=hdr, files={"file": (mov.name, fh, "video/quicktime")})
        assert r.status_code == 202
        job = r.json()["job_id"]
        # wait until the worker has claimed it (stage beyond 'receiving')
        claimed = False
        for _ in range(200):
            rep = c.get(f"{svc.base_url}/api/v1/jobs/{job}/report", headers=hdr)
            st = c.get(f"{svc.base_url}/api/v1/jobs/{job}", headers=hdr).json()
            if st["status"] != "processing":
                break
            if rep.status_code == 200 and rep.json().get("stage") not in (None, "receiving"):
                claimed = True
                break
            time.sleep(0.05)
        if not claimed:
            pytest.skip("job finished (or was never claimed) before the kill point; T17 not exercised")
        svc.kill("worker", signal.SIGKILL)
        killed_at = time.monotonic() - t0
        svc.start_worker()
        bound = 270 + 20 + 30  # original deadline + lease + sweeper slack
        final = None
        while time.monotonic() - t0 < bound:
            st = c.get(f"{svc.base_url}/api/v1/jobs/{job}", headers=hdr)
            assert st.status_code == 200
            doc = st.json()
            v = qa.validate_job_response(doc, expected_job_id=job, duration_sec=qa.probe_duration(mov))
            assert v.errors == [], v.errors
            if doc["status"] != "processing":
                final = doc
                break
            time.sleep(1.0)
    assert final is not None, f"job still processing {bound}s after upload (killed at {killed_at:.1f}s)"
    if final["status"] == "failed":
        assert final["error"]["code"] in ("worker_lost", "deadline_exceeded")
    print(f"T17 killed_at={killed_at:.2f}s terminal={final['status']} after {time.monotonic() - t0:.1f}s")


# --------------------------------------------------------------------------- T28 (process level)

def test_t28_production_with_fake_backend_refuses_to_start(make_service):
    svc = make_service(mode="production", backend="fake", registry=None, only=("api", "worker"))
    svc.start_worker()
    svc.start_api()
    rc_w = svc.wait_exit("worker", 60)
    rc_a = svc.wait_exit("api", 60)
    assert rc_w not in (None, 0), "worker kept running in production with the fake adapter"
    assert rc_a not in (None, 0), "api kept running in production with the fake adapter"
    assert svc.key not in svc.log_tail(200)
