"""Synthetic-only Windows ACL opt-in; never changes existing private ACLs."""
from __future__ import annotations

import hashlib
import os
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from cashwatch.deadline import iso_utc, utc_now
from cashwatch.settings import Settings
from cashwatch.storage.store import JobStore


class WindowsStorageTests(unittest.TestCase):
    def test_production_refuses_inherited_acl(self):
        with patch.dict(os.environ, CASHWATCH_WINDOWS_TEST_INHERIT_ACL="1"):
            with self.assertRaisesRegex(ValueError, "Windows test mode"):
                JobStore(Settings(mode="production"))

    @unittest.skipUnless(os.name == "nt", "Windows-specific inherited ACL")
    def test_synthetic_job_directories_and_sqlite(self):
        repo = Path(__file__).resolve().parents[2]
        root = repo / (".windows-storage-test-" + uuid.uuid4().hex)
        root.mkdir()  # inherit the existing workspace ACL, no permissions edits
        try:
            with patch.dict(os.environ, CASHWATCH_WINDOWS_TEST_INHERIT_ACL="1"):
                store = JobStore(Settings(mode="test", data_dir=root))
                self.assertTrue(store.storage_writable())
                rid = store.reserve_capacity("synthetic-test")
                payload = b"synthetic fixture, not a real video"
                store.tmp_upload_path(rid).write_bytes(payload)
                jid = str(uuid.uuid4())
                rel = store.persist_upload(rid, jid)
                store.enqueue_job(
                    job_id=jid, principal_id="synthetic-test", source_rel=rel,
                    source_sha256=hashlib.sha256(payload).hexdigest(),
                    size_bytes=len(payload), request_started_at=iso_utc(utc_now()),
                    reservation_id=rid,
                )
                claimed = store.claim_job("synthetic-worker")
                self.assertIsNotNone(claimed)
                self.assertEqual(store.source_path(claimed.job).read_bytes(), payload)
                (claimed.work_dir / "probe").write_bytes(b"synthetic")
                output = store.output_dir(claimed.job)
                (output / "probe").write_bytes(b"synthetic")
                self.assertEqual(store.get_job(jid).state, "running")
        finally:
            # Explicit containment check before removing this synthetic tree.
            assert root.resolve().parent == repo.resolve()
            assert root.name.startswith(".windows-storage-test-")
            for item in sorted(root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
                if item.is_dir():
                    item.rmdir()
                else:
                    item.unlink()
            root.rmdir()

    @unittest.skipUnless(os.name == "nt", "Windows-specific inherited ACL")
    def test_root_must_be_preprovisioned(self):
        absent = Path(__file__).resolve().parents[2] / (".absent-" + uuid.uuid4().hex)
        with patch.dict(os.environ, CASHWATCH_WINDOWS_TEST_INHERIT_ACL="1"):
            with self.assertRaisesRegex(ValueError, "preprovisioned"):
                JobStore(Settings(mode="test", data_dir=absent))
        self.assertFalse(absent.exists())


if __name__ == "__main__":
    unittest.main()
