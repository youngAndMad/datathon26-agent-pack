"""Reviewer decisions table, cached history summary, lazy backfill, retention of reviews (synthetic only)."""

from __future__ import annotations

import hashlib
import shutil
import sqlite3

import pytest
from jobfakes import enqueue, finish, later, make_settings, write_fake_result

from cashwatch.dto import QualitySummary, ReviewRequest
from cashwatch.errors import public_error
from cashwatch.storage import JobNotDone, create_store
from cashwatch.storage.schema import MIGRATIONS, connect


def _jury_hash(store, jid):
    return hashlib.sha256((store.jobs_dir / jid / "out-g1" / "jury.json").read_bytes()).hexdigest()


def test_commit_caches_summary(store):
    jid = enqueue(store)
    finish(store, review_required=True)
    row = sqlite3.connect(store.db_path).execute(
        "SELECT verdict, event_count, review_required, mode, summary_cached FROM jobs WHERE id=?", (jid,)).fetchone()
    assert row == ("violation", 2, 1, "test", 1)


def test_reviews_append_only_chronological_and_results_untouched(store):
    jid = enqueue(store)
    finish(store)
    before = _jury_hash(store, jid)
    report_before = (store.jobs_dir / jid / "out-g1" / "report.json").read_bytes()
    a = store.add_review(jid, "e1", ReviewRequest(decision="needs_review"), now_utc=later(1))
    b = store.add_review(jid, "e1", ReviewRequest(decision="confirmed", reviewer="r"), now_utc=later(2))
    c = store.add_review(jid, "missed", ReviewRequest(decision="missed", corrected_timecode_sec=5.0), now_utc=later(3))
    got = store.list_reviews(jid)
    assert [r.review_id for r in got] == [a.review_id, b.review_id, c.review_id]
    assert got[1].decision == "confirmed" and got[2].corrected_timecode_sec == 5.0
    assert _jury_hash(store, jid) == before
    assert (store.jobs_dir / jid / "out-g1" / "report.json").read_bytes() == report_before


def test_add_review_refuses_non_succeeded_job(store):
    jid = enqueue(store)
    with pytest.raises(JobNotDone):
        store.add_review(jid, "e1", ReviewRequest(decision="confirmed"))
    c = store.claim_job("w")
    store.fail_job(jid, c.job.lease_generation, public_error("decode_failed"))
    with pytest.raises(JobNotDone):
        store.add_review(jid, "e1", ReviewRequest(decision="confirmed"))
    assert store.list_reviews(jid) == []


def test_list_jobs_order_filter_limit_and_counts(tmp_path):
    store = create_store(make_settings(tmp_path, active_jobs=1, max_queue_length=10))
    from datetime import timedelta

    from cashwatch.deadline import utc_now
    t0 = utc_now()
    done = enqueue(store, started=t0 - timedelta(seconds=30))
    failed = enqueue(store, started=t0 - timedelta(seconds=20))
    nov = enqueue(store, started=t0 - timedelta(seconds=10))
    proc = enqueue(store, started=t0)
    finish(store, quality=QualitySummary(sampled_frames=1, dark_frames=0, low_detail_frames=0), review_required=True)
    c = store.claim_job("w")
    store.fail_job(failed, c.job.lease_generation, public_error("gpu_oom"))
    finish(store, verdict="no_violation")
    store.add_review(done, "e1", ReviewRequest(decision="confirmed"))
    store.add_review(done, "e1", ReviewRequest(decision="rejected"))
    store.add_review(done, "e2", ReviewRequest(decision="needs_review"))
    store.add_review(done, "missed", ReviewRequest(decision="missed", corrected_timecode_sec=1.0))

    jobs = store.list_jobs("jury")
    assert [j.job_id for j in jobs] == [proc, nov, failed, done]
    by = {j.job_id: j for j in jobs}
    assert by[done].model_dump(exclude={"received_at", "finished_at"}) == {
        "job_id": done, "status": "done", "verdict": "violation", "event_count": 2, "reviewed_event_count": 2,
        "review_required": True, "mode": "test", "error_code": None}
    assert by[nov].verdict == "no_violation" and by[nov].event_count == 0 and by[nov].reviewed_event_count == 0
    assert by[failed].status == "failed" and by[failed].error_code == "gpu_oom" and by[failed].verdict is None
    assert by[proc].status == "processing" and by[proc].finished_at is None and by[proc].mode == "test"
    assert [j.job_id for j in store.list_jobs("jury", status="done")] == [nov, done]
    assert [j.job_id for j in store.list_jobs("jury", status="failed")] == [failed]
    assert [j.job_id for j in store.list_jobs("jury", status="processing")] == [proc]
    assert [j.job_id for j in store.list_jobs("jury", limit=2)] == [proc, nov]
    assert store.list_jobs("someone-else") == []


def test_upgrade_from_v1_backfills_summary_lazily(tmp_path):
    """A DB created at schema v1 with a committed job is migrated; the summary is backfilled on list."""
    settings = make_settings(tmp_path)
    db = settings.data_dir / "db" / "cashwatch.sqlite3"
    db.parent.mkdir(parents=True)
    conn = connect(str(db))
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
    for stmt in [s for s in MIGRATIONS[0][1].split(";") if s.strip()]:
        conn.execute(stmt)
    conn.execute("INSERT INTO schema_migrations VALUES (1, '2026-01-01T00:00:00.000+00:00')")
    conn.close()

    store = create_store(settings)
    assert store.schema_version == 2
    jid = enqueue(store)
    c = store.claim_job("w")
    out = write_fake_result(store, c)
    assert store.commit_result(jid, 1, out)
    # emulate a row committed by the v1 code: no cached summary
    sqlite3.connect(store.db_path).execute(
        "UPDATE jobs SET verdict=NULL, event_count=0, mode=NULL, summary_cached=0 WHERE id=?", (jid,)).connection.commit()
    [j] = store.list_jobs("jury")
    assert (j.verdict, j.event_count, j.mode) == ("violation", 2, "test")
    row = sqlite3.connect(store.db_path).execute(
        "SELECT verdict, event_count, summary_cached FROM jobs WHERE id=?", (jid,)).fetchone()
    assert row == ("violation", 2, 1)


def test_backfill_with_missing_result_dir_does_not_crash(store):
    jid = enqueue(store)
    finish(store)
    sqlite3.connect(store.db_path).execute("UPDATE jobs SET summary_cached=0 WHERE id=?", (jid,)).connection.commit()
    shutil.rmtree(store.jobs_dir / jid / "out-g1")
    [j] = store.list_jobs("jury")
    assert j.status == "done" and j.verdict is None and j.event_count == 0


def test_retention_deletes_reviews(tmp_path):
    store = create_store(make_settings(tmp_path, retention_hours=1))
    jid = enqueue(store)
    finish(store)
    store.add_review(jid, "e1", ReviewRequest(decision="confirmed"))
    assert store.cleanup_retention(later(7200)) == 1
    assert sqlite3.connect(store.db_path).execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 0
    assert store.list_jobs("jury") == []  # expired jobs are not listed
