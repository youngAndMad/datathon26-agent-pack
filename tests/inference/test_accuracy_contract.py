"""Synthetic responses only: uncertainty, exclusions and complete prompt provenance."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from tests.inference import test_neutral_repair_budget as fixtures
from cashwatch.deadline import Deadline
from cashwatch.dto import ObservationRequest, PredicateDefinition, VerificationRequest
from cashwatch.inference import prompts as P
from cashwatch.inference.validation import OutputInvalid, parse_cues, parse_partial_verification


class AccuracyContractTests(unittest.TestCase):
    setUp = fixtures.NeutralRepairTests.setUp
    adapter = fixtures.NeutralRepairTests.adapter
    observe = fixtures.NeutralRepairTests.observe
    neutral = fixtures.NeutralRepairTests.neutral
    def request(self, predicates=None, **extra):
        return VerificationRequest(job_id="job1", candidate_id="c1", rule_id="R1",
            predicates=predicates or [self.predicate], scope_conditions_en=[], boundary_notes_en=[],
            interval_start_sec=0, interval_end_sec=1, frames=[self.frame],
            task_version="test", max_output_tokens=256, **extra)

    def run_verify(self, replies, request=None):
        adapter, generator = self.adapter(replies)
        with patch.object(adapter, "_load_frames", return_value=[b"synthetic"]):
            result = adapter.verify(request or self.request(), Path("."), Deadline.in_seconds(120))
        return result, generator

    def test_explicit_unclear_does_not_repair_or_fail(self):
        result, gen = self.run_verify(["Q1: unclear"])
        self.assertEqual(gen.kinds, ["verify"])
        self.assertEqual(result.assessments[0].state, "unknown")
        self.assertFalse(result.interval_complete)

    def test_incomplete_answer_repaired_without_assuming_missing_false(self):
        other = PredicateDefinition(predicate_id="R1.other", question_en="Other visible fact?")
        result, gen = self.run_verify(["Q1: yes 1", "Q1: yes 1\nQ2: unclear"],
                                      self.request([self.predicate, other]))
        self.assertEqual(gen.kinds, ["verify", "verify-repair"])
        self.assertEqual([a.state for a in result.assessments], ["supported", "unknown"])

    def test_strict_parser_rejects_bad_references_and_syntax(self):
        for answer in ["Q1: no", "Q1: no 99", "Q1: yes", "Q1: nobody", "Q1: yes 1\nQ1: no 1",
                       "Q2: unclear", "Q1: unclear maybe", "Q1: yes 2-1"]:
            with self.subTest(answer=answer), self.assertRaises(OutputInvalid):
                parse_cues(answer, frames=[self.frame], predicate_ids=["R1.a"], strict=True)

    def test_partial_salvage_never_invents_frames_or_accepts_malformed_rows(self):
        for invalid in ["no", "yes", "no 99", "yes 1,99", "yes 1 maybe", "nope 1"]:
            with self.subTest(invalid=invalid):
                result = parse_partial_verification("Q1: yes 1\nQ2: " + invalid,
                    frames=[self.frame], predicate_ids=["R1.a", "R1.b"])
                self.assertEqual(result[0].supporting_frame_ids, ["f1"])
                self.assertEqual(result[1].state, "unknown")
                self.assertEqual(result[1].contradicting_frame_ids, [])

    def test_partial_salvage_conflicting_duplicates_unknown(self):
        result = parse_partial_verification("Q1: yes 1\nQ1: no 1\nQ2: unclear",
            frames=[self.frame], predicate_ids=["R1.a", "R1.b"])
        self.assertEqual([a.state for a in result], ["unknown", "unknown"])

    def test_adapter_salvages_grounded_rows_after_bounded_retry(self):
        other = PredicateDefinition(predicate_id="R1.other", question_en="Other visible fact?")
        result, gen = self.run_verify(["Q1: yes 1\nQ2: no", "extra text\nQ1: yes 1\nQ2: no"],
                                      self.request([self.predicate, other]))
        self.assertEqual(gen.kinds, ["verify", "verify-repair"])
        self.assertEqual([a.state for a in result.assessments], ["supported", "unknown"])

    def test_json_same_frame_conflict_remains_unknown(self):
        answer = json.dumps({"candidate_id": "c1", "assessments": [{
            "predicate_id": self.predicate.predicate_id, "state": "supported",
            "supporting_frame_ids": ["f1"], "contradicting_frame_ids": ["f1"],
            "visibility": "clear", "visible_reason": "synthetic conflict"}],
            "interval_complete": False, "visibility_sufficient": True})
        result, _ = self.run_verify([answer])
        self.assertEqual(result.assessments[0].state, "unknown")

    def test_known_exclusion_not_rechecked(self):
        p = PredicateDefinition(predicate_id="R1.excl_ok", question_en="Compliant alternative?")
        for answer in ["Q1: yes 1", "Q1: no 1"]:
            result, gen = self.run_verify([answer], self.request([p]))
            self.assertEqual(gen.kinds, ["verify"])
            self.assertNotEqual(result.assessments[0].state, "unknown")

    def test_unknown_exclusion_recheck_and_hash(self):
        p = PredicateDefinition(predicate_id="R1.excl_ok", question_en="Compliant alternative?")
        request = self.request([p])
        result, gen = self.run_verify(["Q1: unclear", "Q1: no 1"], request)
        self.assertEqual(gen.kinds, ["verify", "verify-exclusion"])
        self.assertEqual(result.assessments[0].state, "contradicted")
        prompt = P.build_verify_compact_prompt(request)
        self.assertEqual(result.prompt_sha256,
                         P.combined_prompt_hash([prompt.prompt_sha256, prompt.prompt_sha256]))

    def test_conflicting_exclusion_cannot_be_cleared_by_retry(self):
        p = PredicateDefinition(predicate_id="R1.excl_ok", question_en="Compliant alternative?")
        result, gen = self.run_verify(["Q1: yes 1; no 1"], self.request([p]))
        self.assertEqual(gen.kinds, ["verify"])
        self.assertEqual(result.assessments[0].state, "unknown")

    def test_malformed_recheck_remains_unknown(self):
        p = PredicateDefinition(predicate_id="R1.excl_ok", question_en="Compliant alternative?")
        result, _ = self.run_verify(["Q1: unclear", "Q1: no 99"], self.request([p]))
        self.assertEqual(result.assessments[0].state, "unknown")

    def test_prompt_preserves_last_scope_boundary_and_counterexample(self):
        p = self.predicate.model_copy(update={"counterexamples_en": ["first", "second", "third"]})
        request = self.request([p]).model_copy(update={
            "scope_conditions_en": ["scope1", "scope2", "scope3", "scope4"],
            "boundary_notes_en": ["boundary1", "boundary2"]})
        prompt = P.build_verify_compact_prompt(request)
        text = " ".join(str(value) for kind, value in prompt.segments if kind == "text")
        for required in ["scope4", "boundary1", "boundary2", "third"]:
            self.assertIn(required, text)
        changed = P.build_verify_compact_prompt(request.model_copy(update={"boundary_notes_en": ["changed"]}))
        self.assertNotEqual(prompt.prompt_sha256, changed.prompt_sha256)

    def test_observe_hash_changes_when_cue_question_changes(self):
        adapter, _ = self.adapter([self.neutral, "Q1: unclear"])
        before = self.observe(adapter).prompt_sha256
        self.predicate = self.predicate.model_copy(update={"question_en": "Different question?"})
        adapter, _ = self.adapter([self.neutral, "Q1: unclear"])
        self.assertNotEqual(before, self.observe(adapter).prompt_sha256)

    def test_cue_prompt_keeps_all_counterexamples(self):
        p = self.predicate.model_copy(update={"counterexamples_en": ["first", "second", "third"]})
        request = ObservationRequest(job_id="job1", window_id="w0", start_sec=0, end_sec=1,
            frames=[self.frame], task_version="test", max_output_tokens=128, cue_predicates=[p])
        prompt = P.build_cue_prompt(request)
        self.assertIn("third", " ".join(str(value) for kind, value in prompt.segments if kind == "text"))

    def test_temporal_true_and_false_frames_are_retained(self):
        second = self.frame.model_copy(update={"frame_id": "f2", "relative_sec": 1, "pts": 30})
        assessment = parse_cues("Q1: yes 2; no 1", frames=[self.frame, second],
                                predicate_ids=["R1.a"], strict=True)[0]
        self.assertEqual(assessment.state, "supported")
        self.assertEqual(assessment.supporting_frame_ids, ["f2"])
        self.assertEqual(assessment.contradicting_frame_ids, ["f1"])
        self.assertEqual(assessment.interval_start_sec, 1)

    def test_omission_uses_full_json_preserves_both_boolean_values(self):
        for complete in [False, True]:
            answer = json.dumps({"candidate_id": "c1", "assessments": [],
                "interval_complete": complete, "visibility_sufficient": False,
                "limitations": ["Synthetic visibility limitation"]})
            result, gen = self.run_verify([answer], self.request(requires_complete_interval=True))
            self.assertEqual(gen.kinds, ["verify"])
            self.assertEqual(result.interval_complete, complete)
            self.assertFalse(result.visibility_sufficient)
            self.assertIn("Synthetic visibility limitation", result.limitations)


if __name__ == "__main__":
    unittest.main()
