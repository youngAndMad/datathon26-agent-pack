"""Synthetic metadata only; no organizer media, model or network."""
import copy
import json
import math
import shutil
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from scripts.accuracy_experiments import aggregate_only, compare, prepare, profiles, rule_plan, validate_split


class AccuracyExperimentsTest(unittest.TestCase):
    def setUp(self):
        self.source = json.loads((Path(__file__).resolve().parents[1] /
                                  "configs/runtime-profile.json").read_text())

    def test_source_and_baseline_unchanged(self):
        before = copy.deepcopy(self.source)
        output = profiles(self.source)
        self.assertEqual(before, self.source)
        self.assertEqual(before, output["baseline"])
        self.assertEqual(before["budget_sec"], output["short-dense"]["budget_sec"])

    def test_density_is_real_not_silently_clamped(self):
        p = profiles(self.source)["short-dense"]
        s = p["sampling"]
        self.assertLessEqual(math.ceil(s["window_sec"] * s["uniform_fps"]) + 1,
                             s["max_frames_per_observation"])
        self.assertGreater(s["uniform_fps"], self.source["sampling"]["uniform_fps"])
        self.assertLess(s["overlap_sec"], s["window_sec"])
        self.assertEqual(p["sampling_tiers"][1:], self.source["sampling_tiers"][1:])

    def test_resolution_is_only_sampling_change(self):
        expected = copy.deepcopy(self.source["sampling"])
        expected["model_long_side_px"] = int(expected["model_long_side_px"] * 1.5)
        self.assertEqual(expected, profiles(self.source)["resolution"]["sampling"])

    def test_rules_added_one_at_a_time_without_approval(self):
        p = rule_plan(["CW11", "CW12"], ["CW01", "CW02", "CW11"])
        self.assertEqual([2, 3, 3], [len(x["rules"]) for x in p])
        self.assertTrue(all(x["evaluation_only"] for x in p))
        with self.assertRaises(ValueError):
            rule_plan(["arbitrary text"], [])

    def rows(self):
        return {"clips": [
            {"clip_id": "a", "scene_group": "g1", "sha256": "a" * 64,
             "split": "dev", "previously_used": True},
            {"clip_id": "b", "scene_group": "g2", "sha256": "b" * 64,
             "split": "holdout", "previously_used": False}]}

    def test_holdout_is_declared_never_certified(self):
        rows = self.rows()
        result = validate_split(rows)
        self.assertEqual(1, result["declared_new_holdout"])
        self.assertFalse(result["independence_certified"])
        rows["clips"][1]["previously_used"] = True
        self.assertEqual(1, validate_split(rows)["already_used_holdout"])

    def test_group_or_content_leakage_rejected(self):
        for key in ("scene_group", "sha256", "clip_id"):
            with self.subTest(key=key):
                rows = self.rows()
                rows["clips"][1][key] = rows["clips"][0][key]
                with self.assertRaises(ValueError):
                    validate_split(rows)

    def test_unknown_prior_use_rejected(self):
        rows = self.rows()
        del rows["clips"][1]["previously_used"]
        with self.assertRaises(ValueError):
            validate_split(rows)

    def test_summary_does_not_echo_scene_text_paths_or_nan(self):
        result = aggregate_only({"n": 4, "detail": "private text", "profile": "private path",
                                 "accuracy": float("nan"), "failed": "private text",
                                 "failure_counts": {"model_unavailable": 2, "private text": 5},
                                 "completed_classification": {"n": 2, "accuracy": 0.5},
                                 "accuracy_all_submissions": 0.25})
        self.assertNotIn("private", json.dumps(result))
        self.assertEqual(0.25, result["accuracy_all_submissions"])
        self.assertEqual(2, result["failure_counts"]["model_unavailable"])

    def test_comparison_flags_mismatched_selection_not_a_winner(self):
        result = compare([
            {"n": 2, "evaluation_scope": {"selection_sha256": "a" * 64}},
            {"n": 2, "evaluation_scope": {"selection_sha256": "b" * 64}}])
        self.assertEqual("different", result["declared_comparison_checks"]["selection_sha256"])
        self.assertEqual("missing", result["declared_comparison_checks"]["device"])
        self.assertFalse(result["comparability_verified"])

    def test_prepare_contained_and_never_overwrites(self):
        root = Path(__file__).resolve().parents[1]
        scratch = root / ".tmp" / ("accuracy-exp-" + uuid.uuid4().hex)
        scratch.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, scratch)
        source = root / "configs/runtime-profile.json"
        original = source.read_bytes()
        with patch("scripts.accuracy_experiments.OUTPUT", scratch):
            with self.assertRaises(ValueError):
                prepare(source, scratch.parent / "outside", ["CW11"], [])
            plan = prepare(source, scratch / "first", ["CW11"], ["CW01"])
            self.assertFalse(plan["inference_started"])
            self.assertEqual(self.source, json.loads((scratch / "first/baseline.json").read_text()))
            with self.assertRaises(FileExistsError):
                prepare(source, scratch / "first", ["CW11"], [])
        self.assertEqual(original, source.read_bytes())


if __name__ == "__main__":
    unittest.main()
