"""Synthetic-only checks of jury evidence, publication and event independence."""
import json
import shutil
import threading
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from cashwatch.dto import CandidateEvent
from cashwatch.errors import PipelineError
from cashwatch.pipeline.analyze import _covered_by_accepted, _render_evidence
from cashwatch.pipeline import worker
from tests.pipeline import fakes
from tests.pipeline.test_pipeline import _ctx, _run


class TzRegressions(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[2] / ".tmp" / ("tz-pipeline-" + uuid.uuid4().hex)
        self.root.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.root)

    def test_real_report_files_written_on_current_platform(self):
        report, output = _run(self.root, _ctx(fakes.FakePerception(event_times=[10, 30])))
        jury = json.loads((output / "jury.json").read_text(encoding="utf-8"))
        self.assertEqual(jury["verdict"], "violation")
        self.assertEqual(len(jury["violations"]), 2)
        self.assertEqual(len(report.events), 2)
        for item in jury["violations"]:
            self.assertEqual(item["timecode_sec"], item["evidence_frames"][0]["timestamp_sec"])
        self.assertTrue((output / "report.json").is_file())

    def test_nearby_separate_event_not_suppressed_by_context(self):
        accepted = CandidateEvent(candidate_id="a", rule_id="TEST01", rule_version="1",
            start_sec=10, end_sec=11, anchor_frame_id="f1", assessments=[], review_state="accepted")
        nearby = accepted.model_copy(update={"candidate_id": "b", "start_sec": 12,
                                            "end_sec": 13, "review_state": "proposed"})
        inside = nearby.model_copy(update={"start_sec": 10.2, "end_sec": 10.8})
        self.assertFalse(_covered_by_accepted(nearby, [accepted]))
        self.assertTrue(_covered_by_accepted(inside, [accepted]))
        separate = inside.model_copy(update={"anchor_frame_id": "f2"})
        self.assertFalse(_covered_by_accepted(separate, [accepted]))
        uncited = inside.model_copy(update={"anchor_frame_id": None})
        self.assertFalse(_covered_by_accepted(uncited, [accepted]))

    def test_unresolvable_evidence_is_typed_failure(self):
        event = SimpleNamespace(event_id="e", anchor_frame_id="missing", evidence_frame_ids=["missing"])
        with self.assertRaises(PipelineError) as raised:
            _render_evidence([event], self.root / "synthetic.mov", fakes.meta(), self.root,
                             {}, _ctx(fakes.FakePerception()),
                             {"long_side_px": 640, "jpeg_quality": 90}, None)
        self.assertEqual(raised.exception.code, "evidence_invalid")

    def test_preview_does_not_block_and_is_bounded(self):
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        slot = threading.BoundedSemaphore(1)
        calls = []
        def slow(*args):
            calls.append(1)
            entered.set()
            release.wait(5)
            finished.set()
        with patch.object(worker, "_PREVIEW_SLOT", slot), patch.object(worker, "_maybe_preview", slow):
            try:
                worker._start_preview(None, None, SimpleNamespace(preview_enabled=True), None, self.root)
                self.assertTrue(entered.wait(2))
                worker._start_preview(None, None, SimpleNamespace(preview_enabled=True), None, self.root)
                self.assertEqual(len(calls), 1)
                self.assertFalse(finished.is_set())
            finally:
                release.set()
                self.assertTrue(finished.wait(2))
                self.assertTrue(slot.acquire(timeout=2))
                slot.release()


if __name__ == "__main__":
    unittest.main()
