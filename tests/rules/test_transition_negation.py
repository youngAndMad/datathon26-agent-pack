"""Transition prior state derived from earlier contradictions of the decisive predicate."""

from cashwatch.dto import PredicateAssessment
from cashwatch.rules.engine import _augment_transition
from cashwatch.rules.schema import Temporal


class _P:
    predicate_id = "R.out"


class _Rule:
    def __init__(self, negates: bool):
        self.temporal = Temporal(kind="transition", transition_from="R.in", transition_from_negates_anchor=negates)
        self.anchor_predicate = _P()


TIMES = {"a": 1.0, "b": 2.0, "c": 3.0, "d": 4.0}
DEC = PredicateAssessment(predicate_id="R.out", state="supported", supporting_frame_ids=["c", "d"],
                          contradicting_frame_ids=["a", "b"])
INCONSISTENT_IN = PredicateAssessment(predicate_id="R.in", state="contradicted", contradicting_frame_ids=["c", "d"])


def test_prior_state_derived_only_when_declared():
    ass = {"R.out": DEC, "R.in": INCONSISTENT_IN}
    assert _augment_transition(_Rule(False), ass, TIMES)["R.in"].state == "contradicted"
    got = _augment_transition(_Rule(True), ass, TIMES)["R.in"]
    assert got.state == "supported" and got.supporting_frame_ids == ["a", "b"]


def test_no_prior_contradiction_means_no_transition():
    dec = DEC.model_copy(update={"contradicting_frame_ids": []})
    got = _augment_transition(_Rule(True), {"R.out": dec, "R.in": INCONSISTENT_IN}, TIMES)
    assert got["R.in"].state == "contradicted"


def test_later_contradictions_do_not_count_as_prior():
    dec = DEC.model_copy(update={"supporting_frame_ids": ["a"], "contradicting_frame_ids": ["c", "d"]})
    got = _augment_transition(_Rule(True), {"R.out": dec}, TIMES)
    assert "R.in" not in got
