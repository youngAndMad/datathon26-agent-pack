"""Durable job store: leases, fencing, crash windows, deadlines, recovery, retention (T17, T19)."""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from datetime import timedelta

import pytest
from jobfakes import enqueue, later, make_settings, write_fake_result

from cashwatch.deadline import iso_utc, utc_now
from cashwatch.dto import ErrorInfo, StageProgress
from cashwatch.errors import public_error
from cashwatch.storage import CapacityFull, create_store


def test_migrations_wal_and_idempotent_reopen(settings):
    s1 = create_store(settings)
    s2 = create_store(settings)  # second open must not re-apply
    conn = sqlite3.connect(s1.db_path)
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert [r[0] for r in conn.execute("SELECT version FROM schema_migrations")] == [1, 2]
    assert s2.schema_version == 2


def test_claim_builds_job_input_and_bumps_generation(store):
    jid = enqueue(store)
    c = store.claim_job("w1")
    assert c.job.job_id == jid and c.job.attempt == 1 and c.job.lease_generation == 1
    assert c.work_dir.is_dir() and c.work_dir.is_relative_to(store.root)
    assert store.source_path(c.job).read_bytes() == b"\x00\x00\x00\x08wide"
    assert store.get_job(jid).state == "running"
    # deadline = trusted request start + internal_terminal_sec (never accepted_at)
    from cashwatch.deadline import parse_utc
    delta = parse_utc(c.job.deadline_at) - parse_utc(c.job.request_started_at)
    assert delta.total_seconds() == pytest.approx(270.0)


def test_two_concurrent_claims_one_winner(tmp_path):
    settings = make_settings(tmp_path, active_jobs=1, max_queue_length=3)
    base = create_store(settings)
    enqueue(base)
    enqueue(base)
    results, barrier = [], threading.Barrier(8)

    def claim(i):
        st = create_store(settings)  # independent connections, as separate processes would have
        barrier.wait()
        results.append(st.claim_job(f"w{i}"))

    threads = [threading.Thread(target=claim, args=(i,)) for i in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    winners = [r for r in results if r is not None]
    # exactly one job claimed: one-active-job admission + atomic claim
    assert len(winners) == 1
    assert base.counts() == {"running": 1, "queued": 1}


def test_heartbeat_stores_stage_progress_and_fences(store):
    enqueue(store)
    c = store.claim_job("w1")
    p = StageProgress(completed=2, total=5, unit="windows")
    assert store.heartbeat(c.job.job_id, 1, stage="observing", progress=p)
    v = store.get_job(c.job.job_id)
    assert v.stage == "observing" and v.progress == p
    assert not store.heartbeat(c.job.job_id, 2, stage="verifying")  # wrong generation
    assert store.get_job(c.job.job_id).stage == "observing"


def test_commit_happy_path_promotes_and_is_stable(store):
    enqueue(store)
    c = store.claim_job("w1")
    out = write_fake_result(store, c)
    assert out.name == "out-g1.tmp"
    assert store.commit_result(c.job.job_id, 1, out)
    v = store.get_job(c.job.job_id)
    assert v.state == "succeeded" and v.result_path == f"jobs/{c.job.job_id}/out-g1"
    assert not out.exists()
    b1 = store.read_jury_bytes(v)
    # second commit (duplicate call) is fenced: already terminal; bytes unchanged
    assert not store.commit_result(c.job.job_id, 1, out.parent / "out-g1")
    assert store.read_jury_bytes(store.get_job(c.job.job_id)) == b1
    assert not store.heartbeat(c.job.job_id, 1)
    assert not store.fail_job(c.job.job_id, 1, public_error("internal_error"))


def test_crash_between_rename_and_db_commit_is_idempotent(store):
    """T17: promotion rename happened, process died before the CAS; a re-run of commit completes."""
    enqueue(store)
    c = store.claim_job("w1")
    out = write_fake_result(store, c)
    final = out.parent / "out-g1"
    os.replace(out, final)  # simulate: rename done, crash before DB update
    v = store.get_job(c.job.job_id)
    assert v.state == "running" and v.result_path is None  # nothing published yet
    assert store.read_jury_bytes(v) is None
    assert store.commit_result(c.job.job_id, 1, out)  # restarted worker passes the staging path
    assert store.get_job(c.job.job_id).state == "succeeded"


def test_crash_before_rename_then_recovery_retry_fences_stale_writer(store):
    """T17: worker dies mid-job; sweeper retries once with a new generation; stale gen-1 writer
    can neither heartbeat, commit nor fail; gen-2 result is the only published one."""
    jid = enqueue(store)
    c1 = store.claim_job("w1")
    stale_out = write_fake_result(store, c1, marker=" STALE")
    expiry = later(store.settings.lease_sec + 1)
    assert store.recover_expired_jobs(expiry) == 1
    v = store.get_job(jid)
    assert v.state == "queued" and v.attempt == 2
    assert not store.commit_result(jid, 1, stale_out)  # fenced while queued
    c2 = store.claim_job("w2", now_utc=expiry)
    assert c2.job.lease_generation == 2 and c2.job.attempt == 2
    assert c2.job.deadline_at == c1.job.deadline_at  # original deadline never reset
    assert not store.heartbeat(jid, 1)
    assert not store.commit_result(jid, 1, stale_out)
    assert not store.fail_job(jid, 1, public_error("worker_lost"))
    out2 = write_fake_result(store, c2)
    assert store.commit_result(jid, 2, out2)
    data = store.read_jury_bytes(store.get_job(jid))
    assert b"STALE" not in data
    # gen-1 staging was never promoted
    assert stale_out.exists() and not (stale_out.parent / "out-g1").exists()


def test_retry_at_most_once_then_worker_lost(store):
    jid = enqueue(store)
    store.claim_job("w1")
    t1 = later(store.settings.lease_sec + 1)
    store.recover_expired_jobs(t1)
    store.claim_job("w2", now_utc=t1)
    t2 = later(2 * store.settings.lease_sec + 3)
    assert store.recover_expired_jobs(t2) == 1
    v = store.get_job(jid)
    assert v.state == "failed" and v.error_code == "worker_lost"


def test_no_retry_when_deadline_too_close(store):
    started = utc_now() - timedelta(seconds=200)  # 70 s left of 270 (< 120 s retry floor)
    jid = enqueue(store, started=started)
    store.claim_job("w1")
    store.recover_expired_jobs(later(store.settings.lease_sec + 1))
    v = store.get_job(jid)
    assert v.state == "failed" and v.error_code == "worker_lost"


def test_deadline_passed_queued_job_failed_not_claimed(store):
    """T19: a queued job whose original deadline passed is failed, never handed to a worker."""
    jid = enqueue(store, started=utc_now() - timedelta(seconds=271))
    assert store.claim_job("w1") is None
    v = store.get_job(jid)
    assert v.state == "failed" and v.error_code == "deadline_exceeded"


def test_sweeper_fails_running_job_at_deadline_and_fences_late_commit(store):
    """T19: deadline reached mid-stage → failed deadline_exceeded; late result is rejected."""
    jid = enqueue(store)
    c = store.claim_job("w1")
    out = write_fake_result(store, c)
    past_deadline = later(271)
    assert store.recover_expired_jobs(past_deadline) == 1
    v = store.get_job(jid)
    assert v.state == "failed" and v.error_code == "deadline_exceeded"
    assert not store.commit_result(jid, 1, out)
    assert store.get_job(jid).state == "failed"


def test_sweeper_fails_queued_job_past_deadline(store):
    jid = enqueue(store)
    store.recover_expired_jobs(later(300))
    assert store.get_job(jid).error_code == "deadline_exceeded"


def test_fail_job_stores_canonical_code(store):
    jid = enqueue(store)
    c = store.claim_job("w1")
    assert store.fail_job(jid, c.job.lease_generation, ErrorInfo(code="decode_failed", message="x"))
    v = store.get_job(jid)
    assert v.state == "failed" and v.error().message == "Не удалось прочитать видеозапись"


def test_commit_rejects_broken_jury_and_foreign_dir(store):
    jid = enqueue(store)
    c = store.claim_job("w1")
    out = write_fake_result(store, c)
    (out / "jury.json").write_text('{"job_id":"x","status":"done","verdict":"violation","violations":[]}')
    with pytest.raises(ValueError):
        store.commit_result(jid, 1, out)
    assert store.get_job(jid).state == "running"
    with pytest.raises(ValueError):
        store.commit_result(jid, 1, store.root / "tmp")


def test_capacity_reservations_and_expiry(tmp_path):
    st = create_store(make_settings(tmp_path, active_jobs=1, max_queue_length=0))
    r = st.reserve_capacity("jury")
    with pytest.raises(CapacityFull) as e:
        st.reserve_capacity("jury")
    assert e.value.retry_after_sec >= 1
    st.release_reservation(r)
    r2 = st.reserve_capacity("jury", expires_at=iso_utc(utc_now() - timedelta(seconds=1)))
    st.tmp_upload_path(r2).write_bytes(b"partial")
    # expired reservation is swept: slot and temp file are reclaimed
    st.recover_expired_jobs()
    assert not st.tmp_upload_path(r2).exists()
    st.reserve_capacity("jury")


def test_orphan_cleanup_after_grace(store):
    orphan_tmp = store.tmp_dir / ("a" * 32 + ".part")
    orphan_tmp.write_bytes(b"x")
    orphan_job = store.jobs_dir / "11111111-1111-4111-8111-111111111111"
    orphan_job.mkdir()
    (orphan_job / "source.mov").write_bytes(b"x")  # crash between rename and DB insert
    live = enqueue(store)
    assert store.cleanup_orphans() == 0  # within grace: untouched
    assert store.cleanup_orphans(utc_now() + timedelta(hours=1)) == 2
    assert not orphan_tmp.exists() and not orphan_job.exists()
    assert (store.jobs_dir / live).is_dir()


def test_retention_disabled_and_never_touches_nonterminal(tmp_path):
    off = create_store(make_settings(tmp_path / "a", retention_hours=0))
    jid = enqueue(off)
    c = off.claim_job("w")
    off.commit_result(jid, 1, write_fake_result(off, c))
    assert off.cleanup_retention(later(10 ** 6)) == 0

    st = create_store(make_settings(tmp_path / "b", retention_hours=1, active_jobs=1, max_queue_length=2))
    done = enqueue(st)
    c = st.claim_job("w")
    st.commit_result(done, 1, write_fake_result(st, c))
    queued = enqueue(st)
    n = st.cleanup_retention(later(2 * 3600))
    assert n == 1
    assert st.get_job(done).state == "expired" and not (st.jobs_dir / done).exists()
    assert st.get_job(queued).state == "queued" and (st.jobs_dir / queued / "source.mov").is_file()


def test_worker_readiness_freshness(store):
    assert not store.worker_ready()
    store.worker_heartbeat("w1", ready=False, info={})
    assert not store.worker_ready()
    store.worker_heartbeat("w1", ready=True, info={"model": "x"})
    assert store.worker_ready()
    assert not store.worker_ready(later(11))


def test_claim_does_not_block_reads_during_long_work(store):
    """Status reads stay responsive: no DB lock is held between claim and commit."""
    jid = enqueue(store)
    store.claim_job("w1")
    t0 = time.monotonic()
    for _ in range(50):
        assert store.get_job(jid).state == "running"
    assert time.monotonic() - t0 < 2.0
