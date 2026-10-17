"""Synthetic duplicate-window and evidence-grounding regression tests."""

from itertools import permutations

from cashwatch.dto import CandidateEvent, PredicateAssessment
from cashwatch.rules import create_rules, load_registry
from tests.rules import synth as S


def registry():
    return load_registry(S.RULES_DIR / "registry.test.yaml", require_approved=True, allow_test=True)


def candidate(cid, start, end, anchor, *, evidence=None, support=None, contradict=()):
    return CandidateEvent(
        candidate_id=cid, rule_id="TEST01", rule_version="1.0.0",
        start_sec=start, end_sec=end, anchor_frame_id=S.fid(anchor),
        evidence_frame_ids=[S.fid(t) for t in (evidence or [anchor])],
        assessments=[PredicateAssessment(
            predicate_id="TEST01.red_outside_zone", state="supported",
            supporting_frame_ids=[S.fid(t) for t in (support or [anchor])],
            contradicting_frame_ids=[S.fid(t) for t in contradict])],
        review_state="accepted", visibility_sufficient=True)


def test_bridge_window_merges_all_clusters_independent_of_input_order():
    candidates = [candidate("a", 1, 2, 2), candidate("b", 4, 5, 5),
                  candidate("bridge", 1, 7, 7)]
    frames = [S.frame(t) for t in (1, 2, 4, 5, 7)]
    for ordering in permutations(candidates):
        events = create_rules(registry()).merge(list(ordering), frames)
        assert len(events) == 1
        assert events[0].provenance_candidate_ids == ["a", "b", "bridge"]
        assert events[0].anchor_frame_id == S.fid(2)
        assert events[0].evidence_frame_ids == [S.fid(t) for t in (2, 5, 7)]


def test_close_distinct_actions_do_not_merge_from_shared_context():
    candidates = [candidate("a", 2, 3, 2, evidence=[2, 4]),
                  candidate("b", 5, 6, 5, evidence=[5, 4])]
    events = create_rules(registry()).merge(candidates, [S.frame(t) for t in (2, 4, 5)])
    assert len(events) == 2


def test_shared_decisive_evidence_merges_without_distance_threshold():
    candidates = [candidate("a", 2, 3, 2, evidence=[2, 5], support=[2, 5]),
                  candidate("b", 5, 6, 5)]
    events = create_rules(registry()).merge(candidates, [S.frame(t) for t in (2, 5)])
    assert len(events) == 1
    assert events[0].anchor_frame_id == S.fid(2)


def test_visible_reset_prevents_merge_even_with_overlap():
    candidates = [candidate("a", 2, 6, 2, contradict=[4]), candidate("b", 5, 6, 5)]
    events = create_rules(registry()).merge(candidates, [S.frame(t) for t in (2, 4, 5)])
    assert len(events) == 2


def test_different_sources_are_not_merged_and_mixed_evidence_is_rejected():
    frames = [S.frame(2), S.frame(5, src="c" * 64)]
    rules = create_rules(registry())
    assert len(rules.merge([candidate("a", 1, 6, 2), candidate("b", 1, 6, 5)], frames)) == 2
    assert rules.merge([candidate("mixed", 1, 6, 2, evidence=[2, 5])], frames) == []


def test_candidate_anchor_cannot_replace_missing_verified_decisive_frame():
    reg = registry()
    frames = S.refine(15, 25)
    c = candidate("a", 15, 25, 15).model_copy(update={"review_state": "proposed"})
    oracle = S.square_oracle([(0, 20, "in"), (20, 40, "out")])
    result = S.verify("a", [p.predicate_id for p in reg.rule("TEST01").predicates], frames, oracle)
    result = result.model_copy(update={"assessments": [
        a.model_copy(update={"supporting_frame_ids": []})
        if a.predicate_id == "TEST01.red_outside_zone" else a for a in result.assessments]})
    decided = create_rules(reg).decide(c, result, frames, S.manifest(40))
    assert decided.review_state == "unresolved"
    assert "no anchor frame per rule anchor convention" in decided.rejection_reasons


def test_anchor_uses_decisive_verification_not_window_start():
    reg = registry()
    frames = S.refine(15, 25)
    c = candidate("a", 15, 25, 15).model_copy(update={"review_state": "proposed"})
    result = S.verify("a", [p.predicate_id for p in reg.rule("TEST01").predicates], frames,
                      S.square_oracle([(0, 20, "in"), (20, 40, "out")]))
    decided = create_rules(reg).decide(c, result, frames, S.manifest(40))
    assert decided.review_state == "accepted"
    assert decided.anchor_frame_id == S.fid(20)
