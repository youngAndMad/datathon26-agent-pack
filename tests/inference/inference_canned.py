"""Canned model texts for inference tests (no model)."""

from __future__ import annotations

import json


def good_observation(window_id="w03", **over) -> str:
    obj = {
        "window_id": window_id,
        "observations": [{"observation_id": "o1", "frame_ids": ["f0003", "f0001"], "actor_refs": ["A1"],
                          "object_hypotheses": ["red square"], "action": "red square moves out of the rectangle",
                          "visibility": "clear", "uncertainty": ""}],
        "cue_assessments": [{"predicate_id": "p.outside", "state": "supported", "supporting_frame_ids": ["f0004", "f0003"],
                             "contradicting_frame_ids": ["f0000"], "visibility": "clear",
                             "visible_reason": "square left of the rectangle"}],
        "limitations": ["1.5 s gap between some frames"],
    }
    obj.update(over)
    return json.dumps(obj)


def good_verification(candidate_id="c1", **over) -> str:
    obj = {
        "candidate_id": candidate_id,
        "assessments": [
            {"predicate_id": "p.outside", "state": "supported", "supporting_frame_ids": ["f0003"],
             "contradicting_frame_ids": [], "visibility": "clear", "visible_reason": "square outside"},
            {"predicate_id": "p.visible", "state": "contradicted", "supporting_frame_ids": [],
             "contradicting_frame_ids": ["f0005"], "visibility": "partial", "visible_reason": "edge cut"},
        ],
        "plausible_alternatives": ["square only partly outside"],
        "interval_complete": False,
        "visibility_sufficient": True,
        "limitations": [],
    }
    obj.update(over)
    return json.dumps(obj)
