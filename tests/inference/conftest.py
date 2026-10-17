from __future__ import annotations

import json
from pathlib import Path

import pytest

from cashwatch.dto import ObservationRequest, PredicateDefinition, VerificationRequest
from cashwatch.inference.synthetic import write_frames


@pytest.fixture()
def work_dir(tmp_path: Path) -> Path:
    d = tmp_path / "work"
    d.mkdir()
    return d


@pytest.fixture()
def frames(work_dir: Path):
    return write_frames(work_dir, [0.0, 1.0, 2.0, 3.0, 4.5, 6.0], width=320, height=180)


@pytest.fixture()
def cues():
    return [
        PredicateDefinition(predicate_id="p.outside", question_en="Is the red square outside the yellow rectangle?"),
        PredicateDefinition(predicate_id="p.visible", question_en="Is the yellow rectangle fully visible?",
                            counterexamples_en=["rectangle partly covered"]),
    ]


@pytest.fixture()
def obs_request(frames, cues):
    return ObservationRequest(job_id="job1", window_id="w03", start_sec=0.0, end_sec=6.0, frames=frames,
                              task_version="t1", cue_predicates=cues, max_output_tokens=256)


@pytest.fixture()
def ver_request(frames, cues):
    return VerificationRequest(job_id="job1", candidate_id="c1", rule_id="r.synthetic", predicates=cues,
                               scope_conditions_en=["synthetic scene"], boundary_notes_en=["none"],
                               interval_start_sec=1.0, interval_end_sec=4.5, frames=frames,
                               task_version="t1", max_output_tokens=256)
