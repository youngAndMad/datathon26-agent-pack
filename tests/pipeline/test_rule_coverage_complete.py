"""Coverage completeness tests; synthetic DTOs only, no media/model execution."""
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from cashwatch.dto import ObservationBatch, PredicateAssessment, PredicateDefinition
from cashwatch.pipeline.analyze import rule_coverage


class Registry:
    def __init__(self, approved=("R1",), cues=("R1.trigger", "R1.decisive")):
        self.approved, self.cues = approved, cues

    def approved_rule_ids(self):
        return list(self.approved)

    def cue_predicates(self):
        return [PredicateDefinition(predicate_id=pid, question_en="Synthetic visible cue?") for pid in self.cues]

    def rule_ref(self, rid):
        return SimpleNamespace(type_ru="Синтетическое правило")

    def predicate_rule(self, pid):
        return pid.split(".")[0]


def batch(window, *states):
    return ObservationBatch(window_id=window, observations=[],
        cue_assessments=[PredicateAssessment(predicate_id=pid, state=state) for pid, state in states],
        parse_status="ok", elapsed_ms=0, model_version="synthetic", prompt_sha256="0" * 64)


class CoverageTests(unittest.TestCase):
    windows = {"w0": (0, 10), "w1": (10, 20)}
    complete = (("R1.trigger", "supported"), ("R1.decisive", "contradicted"))

    def result(self, batches, *, registry=None, unverified=()):
        return rule_coverage(registry or Registry(), batches, self.windows, list(unverified))[0]

    def test_fully_assessed_supported_and_contradicted_checked(self):
        result = self.result([batch("w0", *self.complete), batch("w1", *self.complete)])
        self.assertEqual(result.status, "checked")
        self.assertEqual(result.unverifiable_intervals, [])

    def test_missing_window_partial_with_exact_interval(self):
        result = self.result([batch("w0", *self.complete)])
        self.assertEqual(result.status, "partially_checked")
        self.assertEqual([(i.start_sec, i.end_sec) for i in result.unverifiable_intervals], [(10, 20)])

    def test_missing_or_unknown_cue_partial_even_if_trigger_known(self):
        for states in ((self.complete[0],), (self.complete[0], ("R1.decisive", "unknown"))):
            with self.subTest(states=states):
                result = self.result([batch("w0", *states), batch("w1", *self.complete)])
                self.assertEqual(result.status, "partially_checked")
                self.assertEqual(result.unverifiable_intervals[0].end_sec, 10)

    def test_all_unknown_or_missing_not_checkable(self):
        for batches in ([], [batch("w0", ("R1.trigger", "unknown"))]):
            result = self.result(batches)
            self.assertEqual(result.status, "not_checkable")
            self.assertEqual([(i.start_sec, i.end_sec) for i in result.unverifiable_intervals], [(0, 20)])

    def test_optional_window_and_foreign_predicates_cannot_fill_gaps(self):
        result = self.result([batch("optional", *self.complete), batch("w0", ("R2.trigger", "supported"))])
        self.assertEqual(result.status, "not_checkable")

    def test_conflicting_duplicate_assessment_is_unverifiable(self):
        result = self.result([batch("w0", *self.complete, ("R1.decisive", "supported")),
                              batch("w1", *self.complete)])
        self.assertEqual(result.status, "partially_checked")

    def test_unverified_candidate_leaves_partial_interval(self):
        result = self.result([batch("w0", *self.complete), batch("w1", *self.complete)],
            unverified=[SimpleNamespace(rule_id="R1", start_sec=4, end_sec=8)])
        self.assertEqual(result.status, "partially_checked")
        self.assertEqual(result.unverifiable_intervals[0].start_sec, 4)

    def test_no_approved_rules_returns_empty(self):
        self.assertEqual(rule_coverage(Registry(approved=(), cues=()), [], self.windows, []), [])

    def test_rule_without_expected_cues_cannot_claim_checked(self):
        result = self.result([], registry=Registry(cues=()))
        self.assertEqual(result.status, "not_checkable")


if __name__ == "__main__":
    unittest.main()
