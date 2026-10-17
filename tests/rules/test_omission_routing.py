"""Synthetic omission checks: model completeness alone never establishes absence."""
import shutil
import unittest
import uuid
from pathlib import Path

from cashwatch.dto import CandidateEvent
from cashwatch.rules import create_rules, load_registry
from tests.rules import synth as S
from tests.rules.test_engine import _omission_case


class OmissionRoutingTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[2] / ".tmp" / ("omission-" + uuid.uuid4().hex)
        self.root.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.root)
        rules = S.copy_rules(self.root)
        S.edit_registry(rules / "registry.test.yaml", lambda data: data["rules"].append(S.OMISSION_RULE))
        self.registry = load_registry(rules / "registry.test.yaml", require_approved=True, allow_test=True)

    def test_request_routes_only_interval_rules_to_structured_verification(self):
        engine = create_rules(self.registry)
        for rid, expected in (("TEST01", False), ("TEST02", True)):
            candidate = CandidateEvent(candidate_id="synthetic", rule_id=rid, rule_version="1.0.0",
                                       start_sec=1, end_sec=12, anchor_frame_id=None, assessments=[])
            req = engine.build_verification(candidate, S.refine(1, 12), job_id="test", max_output_tokens=512)
            self.assertIs(req.requires_complete_interval, expected)

    def test_complete_dense_interval_can_pass_existing_gates(self):
        decision, _ = _omission_case(self.registry, last=20, removed_at=8)
        self.assertEqual(decision.review_state, "accepted")

    def test_model_true_with_sparse_frames_stays_unresolved(self):
        decision, _ = _omission_case(self.registry, last=20, removed_at=8, fps=0.4)
        self.assertEqual(decision.review_state, "unresolved")
        self.assertTrue(any("densely sampled" in reason for reason in decision.rejection_reasons))

    def test_model_incomplete_stays_unresolved(self):
        decision, _ = _omission_case(self.registry, last=20, removed_at=8, interval_complete=False)
        self.assertEqual(decision.review_state, "unresolved")


if __name__ == "__main__":
    unittest.main()
