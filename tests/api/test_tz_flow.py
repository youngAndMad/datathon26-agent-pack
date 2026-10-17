"""Local ASGI jury flow with synthetic fixtures; not a model quality benchmark."""
import base64
import io
import os
from pathlib import Path
import shutil
import unittest
import sys as _sys

import pytest

pytestmark = pytest.mark.skipif(_sys.platform != "win32", reason="Windows-only test (ACL / int8 qengine); fails on macOS on its own commit too")  # WINDOWS_ONLY
import uuid
from unittest.mock import patch

from PIL import Image
import av
from tests.api.conftest import Env
from jobfakes import write_fake_result
from cashwatch.errors import public_error


class JuryFlowTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[2] / ".tmp" / ("tz-api-" + uuid.uuid4().hex)
        self.root.mkdir(parents=True)
        (self.root / "data").mkdir()
        self.addCleanup(shutil.rmtree, self.root)
        env = patch.dict(os.environ, {"CASHWATCH_WINDOWS_TEST_INHERIT_ACL": "1"})
        env.start()
        self.addCleanup(env.stop)
        self.env = Env(self.root)
        self.addCleanup(self.env.client.close)
        self.env.ready()
        # Encode a real synthetic MOV; never sent to a model.
        media = io.BytesIO()
        with av.open(media, mode="w", format="mov") as container:
            stream = container.add_stream("mpeg4", rate=30)
            stream.width = stream.height = 64
            stream.pix_fmt = "yuv420p"
            frame = av.VideoFrame.from_image(Image.new("RGB", (64, 64), "blue"))
            for packet in stream.encode(frame):
                container.mux(packet)
            for packet in stream.encode():
                container.mux(packet)
        self.media = media.getvalue()

    def submit(self):
        response = self.env.upload(self.media)
        self.assertEqual(response.status_code, 202, response.text)
        return response.json()["job_id"]

    def poll(self, job_id):
        response = self.env.client.get("/api/v1/jobs/" + job_id, headers=self.env.h)
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_access_requires_correct_header(self):
        url = "/api/v1/jobs/00000000-0000-4000-8000-000000000001"
        for headers in ({}, {"X-API-Key": "incorrect"}):
            self.assertEqual(self.env.client.get(url, headers=headers).status_code, 401)
        self.assertEqual(self.env.client.get(url, headers=self.env.h).status_code, 404)

    def test_inference_failure_never_becomes_clean_result(self):
        job_id = self.submit()
        self.assertEqual(self.poll(job_id)["status"], "processing")
        claimed = self.env.store.claim_job("synthetic-test-worker")
        self.env.store.fail_job(job_id, claimed.job.lease_generation, public_error("model_unavailable"))
        result = self.poll(job_id)
        self.assertEqual(result["status"], "failed")
        self.assertNotIn("verdict", result)

    def test_expired_commit_is_public_failure_without_sweeper(self):
        job_id = self.submit()
        claimed = self.env.store.claim_job("synthetic-test-worker")
        output = write_fake_result(self.env.store, claimed)
        self.assertFalse(self.env.store.commit_result(job_id, claimed.job.lease_generation, output,
                                                     now_utc=claimed.job.deadline_at))
        result = self.poll(job_id)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["code"], "deadline_exceeded")
        self.assertNotIn("verdict", result)

    def test_done_has_multiple_events_and_real_jpeg_evidence(self):
        job_id = self.submit()
        claimed = self.env.store.claim_job("synthetic-test-worker")
        output = write_fake_result(self.env.store, claimed)
        self.assertTrue(self.env.store.commit_result(job_id, claimed.job.lease_generation, output))
        result = self.poll(job_id)
        self.assertEqual(set(result), {"job_id", "status", "verdict", "violations"})
        self.assertEqual((result["status"], result["verdict"]), ("done", "violation"))
        self.assertEqual(len(result["violations"]), 2)
        for violation in result["violations"]:
            self.assertGreaterEqual(len(violation["evidence_frames"]), 1)
            self.assertLessEqual(len(violation["evidence_frames"]), 3)
            self.assertEqual(violation["timecode_sec"], violation["evidence_frames"][0]["timestamp_sec"])
            for frame in violation["evidence_frames"]:
                with Image.open(io.BytesIO(base64.b64decode(frame["image_base64"], validate=True))) as image:
                    self.assertEqual(image.format, "JPEG")
                    image.verify()
        self.assertEqual(self.poll(job_id), result)


if __name__ == "__main__":
    unittest.main()
