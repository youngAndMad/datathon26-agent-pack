"""Synthetic manifest validation; no organizer media, annotations or model use."""
import contextlib
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from scripts import dev_eval


class ManifestSelectionTests(unittest.TestCase):
    def entry(self, number=1, **updates):
        row = {"clip_id": f"c{number:03d}", "path": "synthetic.mov", "sha256": f"{number:064x}",
               "label_verdict": "violation", "label_timestamps_sec": [1.2, 3.4], "split": "dev"}
        return dict(row, **updates)

    def load(self, entries, **kwargs):
        with patch.object(Path, "read_text", return_value=json.dumps({"version": 1, "clips": entries})), \
             patch.object(Path, "is_file", return_value=True):
            return dev_eval.load_selection(Path("synthetic.json"), manifest=True, **kwargs)

    def test_manifest_preserves_all_times(self):
        rows = self.load([self.entry()], expected_clips=1)
        self.assertEqual(rows[0]["clip"], "c001")
        self.assertEqual(rows[0]["label_timestamps_sec"], [1.2, 3.4])
        self.assertEqual(rows[0]["label_sec"], 1.2)

    def test_manifest_split_does_not_read_split_file(self):
        rows = self.load([self.entry(), self.entry(2, split="holdout")], split="holdout", expected_clips=1)
        self.assertEqual(rows[0]["clip"], "c002")

    def test_exact_count_after_selection(self):
        self.assertEqual(len(self.load([self.entry(), self.entry(2)], clips="c002", expected_clips=1)), 1)
        with self.assertRaisesRegex(ValueError, "expected 49"):
            self.load([self.entry()], expected_clips=49)

    def test_reject_duplicate_ids_and_hashes(self):
        for row in (self.entry(2, clip_id="c001"), self.entry(2, sha256=f"{1:064x}")):
            with self.subTest(row=row), self.assertRaisesRegex(ValueError, "duplicate"):
                self.load([self.entry(), row])

    def test_reject_missing_label_path_and_bad_times(self):
        for update in ({"label_verdict": None}, {"path": ""}, {"label_timestamps_sec": [float("nan")]},
                       {"label_timestamps_sec": [-1]}, {"sha256": ""}, {"clip_id": "../outside"}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                self.load([self.entry(**update)])
        with patch.object(Path, "read_text", return_value=json.dumps({"version": 1, "clips": [self.entry()]})), \
             patch.object(Path, "is_file", return_value=False), self.assertRaisesRegex(ValueError, "file missing"):
            dev_eval.load_selection(Path("synthetic.json"), manifest=True)

    def test_legacy_index(self):
        rows = [{"clip": "legacy", "file": "synthetic.mov", "label": "violation", "label_sec": 7}]
        with patch.object(Path, "read_text", return_value=json.dumps(rows)), patch.object(Path, "is_file", return_value=True):
            selected = dev_eval.load_selection(Path("index.json"), manifest=False)
        self.assertEqual(selected[0]["label_timestamps_sec"], [7])

    def test_path_components(self):
        for value in ("../outside", "x/y", "x\\y", "C:drive", "CON", "NUL.txt", ".", "..", "trailing."):
            with self.subTest(value=value), self.assertRaises(ValueError):
                dev_eval.safe_component(value)
        self.assertEqual(dev_eval.safe_component("cuda-49-v1"), "cuda-49-v1")

    def test_check_selection_without_model_import(self):
        output = io.StringIO()
        with patch("sys.argv", ["dev_eval", "--rules", "CW11", "--manifest", "synthetic.json", "--expected-clips", "1", "--check-selection"]), \
             patch.object(dev_eval, "load_selection", return_value=[{"label": "violation"}]), \
             patch.object(Path, "exists", return_value=False), contextlib.redirect_stdout(output):
            self.assertEqual(dev_eval.main(), 0)
        self.assertEqual(json.loads(output.getvalue()), {"selected": 1, "verdict_counts": {"violation": 1}, "model_started": False})

    def test_existing_run_rejected_before_model(self):
        with patch("sys.argv", ["dev_eval", "--rules", "CW11", "--run", "existing"]), \
             patch.object(dev_eval, "load_selection", return_value=[{"label": "violation"}]), \
             patch.object(Path, "exists", return_value=True), contextlib.redirect_stderr(io.StringIO()), \
             self.assertRaises(SystemExit) as raised:
            dev_eval.main()
        self.assertEqual(raised.exception.code, 2)

    def test_positive_count(self):
        import argparse
        with self.assertRaises(argparse.ArgumentTypeError):
            dev_eval.positive_int("0")
        self.assertEqual(dev_eval.positive_int("49"), 49)


if __name__ == "__main__":
    unittest.main()
