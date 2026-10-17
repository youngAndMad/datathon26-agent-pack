"""Synthetic adapter call sequencing; no model, files or private video required."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from cashwatch.deadline import Deadline
from cashwatch.dto import FrameRef, ObservationRequest, PredicateDefinition, VerificationRequest
from cashwatch.errors import PipelineError
from cashwatch.inference.adapter import LocalPerception
from cashwatch.inference.protocol import GenResult
from cashwatch.inference.supervisor import GenTimeout


class ScriptedGenerator:
    load_info = {"device": "cpu"}
    load_ms = 0

    def __init__(self, responses):
        self.responses = iter(responses)
        self.kinds = []

    def loaded(self):
        return True

    def generate(self, request, timeout_sec):
        self.kinds.append(request.context["kind"] + ("-repair" if request.context.get("repair") else ""))
        value = next(self.responses)
        if isinstance(value, Exception):
            raise value
        return GenResult(value, 10, 5, "stop", [], 0, [], 1, None)


class NeutralRepairTests(unittest.TestCase):
    neutral = json.dumps({"window_id": "w0", "observations": [], "limitations": []})

    def setUp(self):
        self.frame = FrameRef(frame_id="f1", source_sha256="0" * 64, ordinal=0, pts=0,
            time_base_num=1, time_base_den=30, relative_sec=0, local_path="synthetic.jpg",
            sha256="1" * 64, width=32, height=32)
        self.predicate = PredicateDefinition(predicate_id="R1.action", question_en="Is an object visible?")

    def adapter(self, responses):
        generator = ScriptedGenerator(responses)
        adapter = LocalPerception(generator, adapter="fake", model_id="synthetic", revision="test",
                                  local_path_label="synthetic", is_mock=True)
        adapter._ready = True
        return adapter, generator

    def observe(self, adapter, cues=True):
        request = ObservationRequest(job_id="job1", window_id="w0", start_sec=0, end_sec=1,
            frames=[self.frame], task_version="test", max_output_tokens=128,
            cue_predicates=[self.predicate] if cues else [])
        with patch.object(adapter, "_load_frames", return_value=[b"synthetic bytes"]):
            return adapter.observe(request, Path("."), Deadline.in_seconds(120))

    def test_invalid_neutral_then_valid_cue_uses_two_calls(self):
        adapter, generator = self.adapter(["invalid JSON", "Q1: yes 1"])
        result = self.observe(adapter)
        self.assertEqual(generator.kinds, ["observe", "cue"])
        self.assertEqual(result.cue_assessments[0].state, "supported")
        self.assertEqual(result.cue_assessments[0].supporting_frame_ids, ["f1"])
        self.assertTrue(result.limitations)

    def test_cue_operational_failure_is_not_hidden(self):
        adapter, generator = self.adapter(["invalid JSON", GenTimeout("synthetic timeout")])
        with self.assertRaises(PipelineError) as caught:
            self.observe(adapter)
        self.assertEqual(caught.exception.code, "model_unavailable")
        self.assertEqual(generator.kinds, ["observe", "cue"])
        self.assertFalse(adapter.ready())

    def test_neutral_operational_failure_is_not_hidden(self):
        adapter, generator = self.adapter([GenTimeout("synthetic timeout")])
        with self.assertRaises(PipelineError):
            self.observe(adapter)
        self.assertEqual(generator.kinds, ["observe"])

    def test_no_cues_still_attempts_repair(self):
        adapter, generator = self.adapter(["invalid JSON", self.neutral])
        result = self.observe(adapter, cues=False)
        self.assertEqual(result.parse_status, "repaired")
        self.assertEqual(generator.kinds, ["observe", "observe-repair"])

    def test_no_cues_failed_repair_is_fatal(self):
        adapter, generator = self.adapter(["invalid JSON", "still invalid"])
        with self.assertRaises(PipelineError) as caught:
            self.observe(adapter, cues=False)
        self.assertEqual(caught.exception.code, "model_output_invalid")
        self.assertEqual(generator.kinds, ["observe", "observe-repair"])

    def test_garbled_cues_remain_unknown_not_supported(self):
        adapter, generator = self.adapter(["invalid JSON", "unparseable answer"])
        result = self.observe(adapter)
        self.assertEqual(result.cue_assessments[0].state, "unknown")
        self.assertEqual(result.cue_assessments[0].supporting_frame_ids, [])

    def test_verification_still_repairs(self):
        answer = json.dumps({"candidate_id": "c1", "assessments": [],
                             "interval_complete": False, "visibility_sufficient": False})
        adapter, generator = self.adapter(["invalid JSON", answer])
        request = VerificationRequest(job_id="job1", candidate_id="c1", rule_id="R1",
            predicates=[self.predicate], scope_conditions_en=[], boundary_notes_en=[],
            interval_start_sec=0, interval_end_sec=1, frames=[self.frame],
            task_version="test", max_output_tokens=128)
        with patch.object(adapter, "_load_frames", return_value=[b"synthetic bytes"]):
            result = adapter.verify(request, Path("."), Deadline.in_seconds(120))
        self.assertEqual(result.parse_status, "repaired")
        self.assertEqual(generator.kinds, ["verify", "verify-repair"])


if __name__ == "__main__":
    unittest.main()
