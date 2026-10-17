"""Publication deadline regressions; self-created synthetic storage only."""
import hashlib
import json
import os
from datetime import timedelta
from pathlib import Path
import sys
import unittest
import uuid
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from cashwatch.deadline import iso_utc, parse_utc, utc_now
from cashwatch.settings import Settings
from cashwatch.storage.store import JobStore


class CommitDeadlineTests(unittest.TestCase):
    def setUp(self):
        self.repo = Path(__file__).resolve().parents[2]
        self.root = self.repo / (".commit-deadline-test-" + uuid.uuid4().hex)
        self.root.mkdir()
        self.env = patch.dict(os.environ, CASHWATCH_WINDOWS_TEST_INHERIT_ACL="1" if os.name == "nt" else "0")
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(self.cleanup_tree)
        self.store = JobStore(Settings(mode="test", backend="fake", data_dir=self.root))
        rid = self.store.reserve_capacity("jury")
        payload = b"synthetic storage fixture"
        self.store.tmp_upload_path(rid).write_bytes(payload)
        self.jid = str(uuid.uuid4())
        rel = self.store.persist_upload(rid, self.jid)
        self.store.enqueue_job(job_id=self.jid, principal_id="jury", source_rel=rel,
            source_sha256=hashlib.sha256(payload).hexdigest(), size_bytes=len(payload),
            request_started_at=iso_utc(utc_now()), reservation_id=rid)
        self.claimed = self.store.claim_job("synthetic-worker")
        self.deadline = parse_utc(self.claimed.job.deadline_at)
        self.out = self.store.output_dir(self.claimed.job)
        (self.out / "jury.json").write_text(json.dumps({"job_id": self.jid, "status": "done",
            "verdict": "no_violation", "violations": []}), encoding="utf-8")

    def cleanup_tree(self):
        assert self.root.resolve().parent == self.repo.resolve()
        assert self.root.name.startswith(".commit-deadline-test-")
        for item in sorted(self.root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
            if item.is_dir():
                item.rmdir()
            else:
                item.unlink()
        self.root.rmdir()

    def assert_failed_without_result(self):
        view = self.store.get_job(self.jid)
        self.assertEqual(view.state, "failed")
        self.assertEqual(view.error_code, "deadline_exceeded")
        self.assertIsNone(self.store.read_jury_bytes(view))
        with self.store._ro() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM leases WHERE job_id=?", (self.jid,)).fetchone()[0], 0)

    def test_timely_commit_still_succeeds(self):
        self.assertTrue(self.store.commit_result(self.jid, 1, self.out,
            now_utc=iso_utc(self.deadline - timedelta(seconds=1))))
        view = self.store.get_job(self.jid)
        self.assertEqual(view.state, "succeeded")
        self.assertIsNotNone(self.store.read_jury_bytes(view))

    def test_boundary_rejected_without_sweeper(self):
        self.assertFalse(self.store.commit_result(self.jid, 1, self.out, now_utc=iso_utc(self.deadline)))
        self.assert_failed_without_result()

    def test_late_rejected_without_sweeper(self):
        self.assertFalse(self.store.commit_result(self.jid, 1, self.out,
            now_utc=iso_utc(self.deadline + timedelta(seconds=1))))
        self.assert_failed_without_result()

    def test_slow_publication_rechecks_clock_after_summary(self):
        clock = [self.deadline - timedelta(seconds=1)]
        original = self.store._result_summary
        def slow_summary(path):
            summary = original(path)
            clock[0] = self.deadline + timedelta(seconds=1)
            return summary
        with patch.object(self.store, "_now", side_effect=lambda value=None: clock[0]), \
             patch.object(self.store, "_result_summary", side_effect=slow_summary):
            self.assertFalse(self.store.commit_result(self.jid, 1, self.out))
        self.assert_failed_without_result()

    def test_stale_generation_cannot_fail_current_job(self):
        with self.store._tx() as conn:
            conn.execute("UPDATE jobs SET generation=2 WHERE id=?", (self.jid,))
        self.assertFalse(self.store.commit_result(self.jid, 1, self.out,
            now_utc=iso_utc(self.deadline + timedelta(seconds=1))))
        self.assertEqual(self.store.get_job(self.jid).state, "running")


if __name__ == "__main__":
    unittest.main()
