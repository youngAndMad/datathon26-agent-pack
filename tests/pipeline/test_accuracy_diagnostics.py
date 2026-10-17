"""Synthetic orchestration checks; no model quality or private data claims."""
import shutil
import unittest
import uuid
from pathlib import Path

from cashwatch.errors import PipelineError
from cashwatch.pipeline.analyze import _Run, _record_prompt
from cashwatch.pipeline.diagnostics import DiagnosticCounters
from tests.pipeline import fakes
from tests.pipeline.test_pipeline import _ctx, _run


class AccuracyDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[2] / ".tmp" / ("accuracy-" + uuid.uuid4().hex)
        self.root.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.root)

    def test_completed_pipeline_reports_actual_counts_without_scene_data(self):
        snapshots = []
        ctx = _ctx(fakes.FakePerception(event_times=[10, 30]))
        ctx.diagnostics = snapshots.append
        report, _ = _run(self.root, ctx)
        final = snapshots[-1]
        self.assertEqual(final["phase"], "complete")
        counts = final["counters"]
        self.assertEqual(counts["events_emitted"], len(report.events))
        self.assertEqual(counts["coarse_windows_completed"], len(report.coverage.required_window_ids))
        decisions = sum(counts.get("candidates_" + state, 0) for state in ("accepted", "rejected", "unresolved"))
        self.assertEqual(counts["verification_attempted"], decisions)
        self.assertEqual(counts["candidates_proposed"], decisions + counts.get("duplicate_candidates_skipped", 0))
        self.assertEqual(set(final), {"phase", "counters", "per_rule"})
        self.assertTrue(all(type(value) is int for value in counts.values()))
        self.assertEqual(snapshots[0]["counters"], {})

    def test_failure_retains_stage_and_partial_counts_without_clean_result(self):
        snapshots = []
        ctx = _ctx(fakes.FakePerception(fail_window="w002"))
        ctx.diagnostics = snapshots.append
        with self.assertRaises(PipelineError):
            _run(self.root, ctx)
        self.assertEqual(snapshots[-1]["phase"], "observing")
        self.assertEqual(snapshots[-1]["counters"]["observation_failures"], 1)
        self.assertEqual(snapshots[-1]["counters"]["coarse_windows_completed"], 2)
        self.assertFalse((self.root / "out/jury.json").exists())

    def test_callback_cannot_mutate_counter_state(self):
        def mutate(snapshot):
            snapshot["per_rule"]["TEST01"]["candidates_proposed"] = 999
        counters = DiagnosticCounters(mutate)
        counters.bump("candidates_proposed", rule="TEST01")
        counters.emit("proposed")
        self.assertEqual(counters.per_rule["TEST01"]["candidates_proposed"], 1)

    def test_prompt_digest_includes_earlier_windows_and_call_order(self):
        values = []
        for sequence in (("a", "b"), ("c", "b"), ("b", "a"), ("a", "b")):
            run = _Run()
            for value in sequence:
                _record_prompt(run, "observe", value * 64)
            values.append(run.prompt_hashes["observe"])
        self.assertEqual(len(set(values)), 3)
        self.assertEqual(values[0], values[3])


if __name__ == "__main__":
    unittest.main()
