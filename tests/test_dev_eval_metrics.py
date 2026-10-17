"""Synthetic metrics only: no organizer files, model or media are accessed."""
import importlib.util
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("dev_eval", Path(__file__).resolve().parents[1] / "scripts/dev_eval.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def row(label, verdict=None, error=None, events=None):
    return {"label": label, "status": "failed" if error else "done", "error": error,
            "verdict": verdict, "events": events or [], "sec": 1}


class EvaluationMetricsTests(unittest.TestCase):
    def test_fingerprint_hashes_config_not_secrets_or_weights(self):
        settings = SimpleNamespace(profile_path=Path("profile.json"), model_path=Path("model"),
                                   model_manifest=Path("manifest.json"), backend="fake", device="cpu")
        hashed = []
        def digest(path):
            hashed.append(str(path))
            return "synthetic-digest"
        with patch.object(MODULE, "sha256", side_effect=digest), patch.object(Path, "is_file", return_value=True):
            result = MODULE.configuration_fingerprint(settings, Path("registry.yaml"))
        self.assertEqual(result["profile_sha256"], "synthetic-digest")
        self.assertFalse(result["weights_verified_by_this_evaluator"])
        self.assertEqual(result["model_configuration_sha256"]["config.json"], "synthetic-digest")
        self.assertFalse(any("secret" in p or "safetensors" in p for p in hashed))

    def test_failures_penalize_service_but_not_classifier(self):
        rows = [row("violation", "violation"), row("no_violation", "no_violation"),
                row("violation", error="deadline_exceeded"), row("no_violation", error="model_unavailable")]
        result = MODULE.summarize(rows)
        self.assertEqual(result["accuracy_all_submissions"], .5)
        self.assertEqual(result["completion_rate"], .5)
        self.assertEqual(result["completed_classification"], {
            "n": 2, "tp": 1, "fp": 0, "tn": 1, "fn": 0, "accuracy": 1.,
            "precision": 1., "recall": 1., "f1": 1.})
        self.assertEqual(result["failure_counts"], {"deadline_exceeded": 1, "model_unavailable": 1})
        self.assertEqual((result["fp"], result["fn"]), (1, 1))

    def test_zero_f1_is_zero_not_undefined(self):
        result = MODULE.summarize([row("violation", "no_violation")])["completed_classification"]
        self.assertEqual(result["f1"], 0)
        self.assertEqual(result["recall"], 0)
        self.assertIsNone(result["precision"])

    def test_all_failures_have_no_classifier_quality(self):
        result = MODULE.summarize([row("violation", error="gpu_oom")])
        self.assertEqual(result["accuracy_all_submissions"], 0)
        self.assertEqual(result["completed_classification"]["n"], 0)
        self.assertIsNone(result["completed_classification"]["accuracy"])

    def test_counts_do_not_invent_rule_accuracy_or_fn_causes(self):
        rows = [row("violation", "violation", events=[{"rule": "TEST01", "t": 3}]),
                row("violation", error="deadline_exceeded")]
        rows[0]["diagnostics"] = {"phase": "complete", "counters": {"candidates_proposed": 2},
            "per_rule": {"TEST01": {"candidates_proposed": 2, "candidates_accepted": 1}}}
        rows[1]["diagnostics"] = {"phase": "verifying", "counters": {"candidates_proposed": 1},
            "per_rule": {"TEST01": {"candidates_proposed": 1}}}
        result = MODULE.summarize(rows)
        self.assertEqual(result["per_rule_emitted_events"], {"TEST01": 1})
        self.assertEqual(result["pipeline_diagnostics"]["counters"]["candidates_proposed"], 3)
        self.assertEqual(result["pipeline_diagnostics"]["per_rule"]["TEST01"],
                         {"candidates_proposed": 3, "candidates_accepted": 1})
        self.assertNotIn("per_rule_accuracy", result)

    def test_empty_and_unknown_labels_rejected(self):
        for rows in ([], [row(None, "no_violation")], [row("violation", "unknown")]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                MODULE.summarize(rows)

    def test_time_error_missing_events_and_nearest_legacy_metric(self):
        a = row("violation", "violation")
        a["label_sec"] = 5
        b = row("violation", "violation", events=[{"rule": "TEST01", "t": 8}, {"rule": "TEST01", "t": 6}])
        b["label_sec"] = 5
        result = MODULE.summarize([a, b])
        self.assertEqual(result["time_err_sec"]["n"], 1)
        self.assertEqual(result["time_err_sec"]["median"], 1)


if __name__ == "__main__":
    unittest.main()
