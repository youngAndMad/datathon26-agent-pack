"""Parsing/validation of canned model text (no model)."""

from __future__ import annotations

import json

import pytest

from cashwatch.inference.validation import (
    OutputInvalid,
    extract_json_object,
    neutralize,
    parse_observation,
    parse_verification,
)
from inference_canned import good_observation, good_verification

PIDS = ["p.outside", "p.visible"]


def _obs(text, frames):
    return parse_observation(text, window_id="w03", frames=frames, predicate_ids=PIDS)


def _ver(text, frames):
    return parse_verification(text, candidate_id="c1", frames=frames, predicate_ids=PIDS)


def test_good_observation_resolves_times_from_framerefs(frames):
    p = _obs(good_observation(), frames)
    o = p.observations[0]
    assert o.observation_id == "w03.o1"  # application-assigned id, not the model's
    assert o.frame_ids == ["f0001", "f0003"]  # canonical presentation order
    a = {c.predicate_id: c for c in p.cue_assessments}
    assert a["p.outside"].state == "supported"
    assert a["p.outside"].supporting_frame_ids == ["f0003", "f0004"]
    # interval from the request FrameRefs (f0000=0.0 .. f0004=4.5), not from the model
    assert (a["p.outside"].interval_start_sec, a["p.outside"].interval_end_sec) == (0.0, 4.5)
    # missing predicate filled as unknown, with a limitation note
    assert a["p.visible"].state == "unknown"
    assert any("p.visible" in s for s in p.limitations)


def test_code_fences_and_prose_are_stripped(frames):
    text = "Here is the JSON:\n```json\n" + good_observation() + "\n```\nDone."
    assert _obs(text, frames).observations


def test_model_supplied_timestamps_are_ignored(frames):
    obj = json.loads(good_observation())
    obj["cue_assessments"][0]["interval_start_sec"] = 99.0
    obj["observations"][0]["timestamp"] = 123.4
    p = _obs(json.dumps(obj), frames)
    assert p.cue_assessments[0].interval_start_sec == 0.0


@pytest.mark.parametrize("mutate,needle", [
    (lambda o: o["observations"][0].update(frame_ids=["f0001", "f9999"]), "unknown frame id"),
    (lambda o: o["cue_assessments"][0].update(supporting_frame_ids=["frame_7"]), "unknown frame id"),
    (lambda o: o["cue_assessments"][0].update(predicate_id="p.invented"), "predicate_id unknown"),
    (lambda o: o["cue_assessments"].append(dict(o["cue_assessments"][0])), "duplicated"),
    (lambda o: o["cue_assessments"][0].update(state="likely"), "state must be one of"),
    (lambda o: o["cue_assessments"][0].update(state=True), "schema error"),
    (lambda o: o["observations"][0].update(visibility="good"), "visibility must be one of"),
    (lambda o: o["observations"][0].update(action="x" * 301), "longer than"),
    (lambda o: o.update(observations=[o["observations"][0]] * 13), "at most 12"),
    (lambda o: o.update(limitations=["l"] * 9), "at most 8"),
    (lambda o: o["observations"][0].update(actor_refs=["John Smith"]), "anonymous"),
    (lambda o: o.update(window_id="w99"), "window_id must be"),
    (lambda o: o.pop("observations"), "schema error"),
])
def test_observation_rejections(frames, mutate, needle):
    obj = json.loads(good_observation())
    mutate(obj)
    with pytest.raises(OutputInvalid, match=needle):
        _obs(json.dumps(obj), frames)


@pytest.mark.parametrize("text", ["", "no json here", '{"window_id": "w03", "observations": [', "[1, 2]"])
def test_malformed_json(frames, text):
    with pytest.raises(OutputInvalid):
        _obs(text, frames)


def test_verification_ok_and_bool_strictness(frames):
    v = _ver(good_verification(), frames)
    assert [a.state for a in v.assessments] == ["supported", "contradicted"]
    assert v.interval_complete is False and v.visibility_sufficient is True
    with pytest.raises(OutputInvalid):
        _ver(good_verification(interval_complete="false"), frames)
    with pytest.raises(OutputInvalid, match="candidate_id"):
        _ver(good_verification(candidate_id="c2"), frames)


def test_supported_without_frames_is_downgraded_not_strengthened(frames):
    obj = json.loads(good_verification())
    obj["assessments"][0]["supporting_frame_ids"] = []
    obj["assessments"][1]["contradicting_frame_ids"] = []
    v = _ver(json.dumps(obj), frames)
    assert [a.state for a in v.assessments] == ["unknown", "unknown"]


def test_legal_identity_and_verdict_text_is_neutralized(frames):
    obj = json.loads(good_verification())
    obj["assessments"][0]["visible_reason"] = "Violation of Article 12.3 of the Labour Code by Mr. Ivanov, see https://adilet.zan.kz/x"
    obj["plausible_alternatives"] = ["нарушение п. 5 Правил, недостача 5000 тенге", "person named Aigerim recognized as the manager"]
    v = _ver(json.dumps(obj), frames)
    text = " ".join([v.assessments[0].reasoning_note, *v.plausible_alternatives]).lower()
    for banned in ("violation", "article", "12.3", "labour code", "ivanov", "adilet", "http", "нарушение",
                   "5000", "тенге", "aigerim", "manager" if False else "named"):
        assert banned not in text, banned
    assert "[removed]" in text
    assert any("removed from model text" in s for s in v.limitations)


def test_neutralize_keeps_plain_visual_text():
    s = "cashier places a stack of paper into the open drawer"
    assert neutralize(s) == (s, 0)


def test_extract_prefers_first_complete_object():
    assert extract_json_object('noise {"a": 1} {"b": 2}') == {"a": 1}
