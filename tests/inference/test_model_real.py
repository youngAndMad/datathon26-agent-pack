"""Real local-model smoke on SYNTHETIC frames (requires weights + Apple GPU).

Run: .venv/bin/python -m pytest tests/inference -m model
Plumbing sanity only (does the pipeline produce valid typed output); not domain accuracy.
"""

from __future__ import annotations

import pytest

from cashwatch.deadline import Deadline
from cashwatch.dto import ObservationRequest, PredicateDefinition
from cashwatch.gpulock import gpu_reservation
from cashwatch.inference import create_perception
from cashwatch.inference.synthetic import write_frames
from cashwatch.settings import Settings

pytestmark = pytest.mark.model


def test_mlx_warmup_observe_close(work_dir):
    frames = write_frames(work_dir, [float(i) for i in range(6)])
    cue = PredicateDefinition(predicate_id="syn.red_outside_zone",
                              question_en="Is the red square outside the yellow rectangle in at least one frame?")
    p = create_perception(Settings(mode="test", backend="mlx"))
    with gpu_reservation("C-pytest-model"):
        try:
            p.warmup(timeout_sec=300)
            info = p.info()
            assert info.warmed_up and not info.is_mock and info.revision.startswith("0943db6e")
            req = ObservationRequest(job_id="t", window_id="w0", start_sec=0, end_sec=5, frames=frames,
                                     task_version="t", cue_predicates=[cue], max_output_tokens=384)
            b = p.observe(req, work_dir, Deadline.in_seconds(120))
            assert b.parse_status in ("ok", "repaired")
            assert [c.predicate_id for c in b.cue_assessments] == ["syn.red_outside_zone"]
            ids = {f.frame_id for f in frames}
            assert all(set(o.frame_ids) <= ids for o in b.observations)
            call = [c for c in p.stats.calls if c.kind == "observe"][-1]
            assert call.visual_tokens == [220] * 6
        finally:
            p.close()
    assert not p.ready()
