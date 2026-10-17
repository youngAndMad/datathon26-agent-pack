"""Rules engine gates on synthetic DTO fixtures (T08-T12, T14 engine side, merge/dedup)."""

from __future__ import annotations

import pytest

from cashwatch.dto import PredicateAssessment
from cashwatch.errors import PipelineError
from cashwatch.rules import create_rules, load_registry
from tests.rules import synth as S


@pytest.fixture(scope="module")
def reg():
    return load_registry(S.RULES_DIR / "registry.test.yaml", require_approved=True, allow_test=True)


@pytest.fixture(scope="module")
def omission_reg(tmp_path_factory):
    d = S.copy_rules(tmp_path_factory.mktemp("om"))
    S.edit_registry(d / "registry.test.yaml", lambda data: data["rules"].append(S.OMISSION_RULE))
    r = load_registry(d / "registry.test.yaml", require_approved=True, allow_test=True)
    assert r.health_errors() == [] and r.approved_rule_ids() == ["TEST01", "TEST02"]
    return r


# ------------------------------------------------------------------ T08

def test_t08_negative_clip_no_candidates_no_events(reg):
    m = S.manifest(60)
    events, decided, cands, rules, _ = S.run_pipeline(reg, m, S.square_oracle([(0, 60, "in")]))
    assert cands == [] and decided == [] and events == []


def test_t08_positive_single_event_anchor_and_evidence(reg):
    m = S.manifest(60)
    events, _, _, _, frames = S.run_pipeline(reg, m, S.square_oracle([(0, 20, "in"), (20, 60, "out")]))
    assert len(events) == 1
    ev = events[0]
    assert frames[ev.anchor_frame_id].relative_sec == 20.0  # first frame fully outside
    assert ev.evidence_frame_ids[0] == ev.anchor_frame_id and 1 <= len(ev.evidence_frame_ids) <= 3
    before, after = (frames[i].relative_sec for i in ev.evidence_frame_ids[1:])
    assert before < 20.0 < after
    assert ev.rule == reg.rule_ref("TEST01")
    assert ev.factual_text_ru == reg.rule("TEST01").explanation.fact_ru
    assert ev.start_sec <= 20.0 <= ev.end_sec


def test_t08_no_hardcoded_result_same_input_different_timing(reg):
    m = S.manifest(60)
    e1, *_ = S.run_pipeline(reg, m, S.square_oracle([(0, 33, "in"), (33, 60, "out")]))
    _, _, _, _, frames = S.run_pipeline(reg, m, S.square_oracle([(0, 33, "in"), (33, 60, "out")]))
    assert [frames[e.anchor_frame_id].relative_sec for e in e1] == [33.0]


# ------------------------------------------------------------------ T09

def test_t09_two_separate_events_kept_and_overlap_duplicates_merged(reg):
    m = S.manifest(60)
    segs = [(0, 20, "in"), (20, 30, "out"), (30, 40, "in"), (40, 60, "out")]
    events, decided, _, _, frames = S.run_pipeline(reg, m, S.square_oracle(segs))
    accepted = [d for d in decided if d.review_state == "accepted"]
    assert len(accepted) > 2  # overlapping windows found the same exits more than once
    assert [frames[e.anchor_frame_id].relative_sec for e in events] == [20.0, 40.0]
    assert len({e.event_id for e in events}) == 2
    assert all(len(e.provenance_candidate_ids) >= 2 for e in events)


def test_t09_persistent_state_across_windows_is_one_event_with_earliest_anchor(reg):
    rules = create_rules(reg)
    m = S.manifest(60)
    oracle = S.square_oracle([(0, 20, "in"), (20, 60, "out")])
    out = []
    for cid, (s, e) in {"c1": (16, 30), "c2": (18, 40)}.items():
        rf = S.refine(s, e)
        from cashwatch.dto import CandidateEvent
        cand = CandidateEvent(candidate_id=cid, rule_id="TEST01", rule_version="1.0.0", start_sec=s, end_sec=e,
                              anchor_frame_id=None, assessments=[])
        res = S.verify(cid, [p.predicate_id for p in reg.rule("TEST01").predicates], rf, oracle)
        out.append((rules.decide(cand, res, rf, m), rf))
    frames = {f.frame_id: f for _, rf in out for f in rf}
    events = rules.merge([d for d, _ in out], list(frames.values()))
    assert len(events) == 1 and frames[events[0].anchor_frame_id].relative_sec == 20.0
    assert events[0].provenance_candidate_ids == ["c1", "c2"]


def test_t09_close_anchors_not_merged_without_overlap_or_when_reset(reg):
    """Two exits 3 s apart with a visible return in between are distinct events."""
    m = S.manifest(30)
    segs = [(0, 10, "in"), (10, 12, "out"), (12, 13, "in"), (13, 30, "out")]
    events, _, _, _, frames = S.run_pipeline(reg, m, S.square_oracle(segs))
    assert [frames[e.anchor_frame_id].relative_sec for e in events] == [10.0, 13.0]


# ------------------------------------------------------------------ T10 omission

def _omission_case(reg, *, last: float, removed_at: float | None, interval_complete=True, fps=4.0):
    from cashwatch.dto import CandidateEvent

    m = S.manifest(last)

    def oracle(pid, t):
        if pid == "TEST02.token_placed":
            return "supported" if 2 <= t <= 4 else "contradicted"
        if pid == "TEST02.token_tapped":
            return "contradicted"
        if pid == "TEST02.token_removed":
            return "supported" if removed_at is not None and t >= removed_at else "contradicted"
        if pid == "TEST02.table_visible":
            return "supported"
        if pid == "TEST02.excl_unseen":
            return "contradicted"
        return "unknown"

    rules = create_rules(reg)
    cand = CandidateEvent(candidate_id="om1", rule_id="TEST02", rule_version="1.0.0", start_sec=1.0,
                          end_sec=min(last, 12.0), anchor_frame_id=None, assessments=[])
    rf = S.refine(1.0, min(last, 12.0), fps=fps)
    res = S.verify("om1", [p.predicate_id for p in reg.rule("TEST02").predicates], rf, oracle,
                   interval_complete=interval_complete)
    return rules.decide(cand, res, rf, m), rf


def test_t10_complete_omission_interval_is_eligible(omission_reg):
    d, rf = _omission_case(omission_reg, last=20.0, removed_at=8.0)
    assert d.review_state == "accepted", d.rejection_reasons
    assert {f.frame_id: f.relative_sec for f in rf}[d.anchor_frame_id] == 8.0  # completion transition


def test_t10_clip_cut_before_completion_is_not_eligible(omission_reg):
    d, _ = _omission_case(omission_reg, last=9.0, removed_at=None, interval_complete=False)
    assert d.review_state == "unresolved"
    assert d.anchor_frame_id is None and d.evidence_frame_ids == []


def test_t10_model_says_incomplete_interval_blocks(omission_reg):
    d, _ = _omission_case(omission_reg, last=20.0, removed_at=8.0, interval_complete=False)
    assert d.review_state == "unresolved" and "opportunity interval incomplete" in d.rejection_reasons


def test_t10_sparse_sampling_cannot_prove_absence(omission_reg):
    d, _ = _omission_case(omission_reg, last=20.0, removed_at=8.0, fps=0.4)  # 2.5 s gaps
    assert d.review_state == "unresolved"
    assert any("densely sampled" in r for r in d.rejection_reasons)


def test_t10_omission_proposal_suppressed_when_action_seen(omission_reg):
    rules = create_rules(omission_reg)
    m = S.manifest(20)

    def oracle(pid, t):
        return {"TEST02.token_placed": "supported", "TEST02.token_tapped": "supported"}.get(pid, "unknown")

    cands = rules.propose(S.batches(m, ["TEST02.token_placed", "TEST02.token_tapped", "TEST02.token_removed"],
                                    oracle), m)
    assert [c for c in cands if c.rule_id == "TEST02"] == []


# ------------------------------------------------------------------ T11 unknown / occluded

def _decide_with(reg, oracle, **kw):
    from cashwatch.dto import CandidateEvent

    rules = create_rules(reg)
    m = S.manifest(40)
    rf = S.refine(15, 25)
    cand = CandidateEvent(candidate_id="c1", rule_id="TEST01", rule_version="1.0.0", start_sec=15, end_sec=25,
                          anchor_frame_id=None, assessments=[])
    res = S.verify("c1", [p.predicate_id for p in reg.rule("TEST01").predicates], rf, oracle, **kw)
    return rules.decide(cand, res, rf, m), rules


def test_t11_occluded_exit_yields_no_event(reg):
    d, rules = _decide_with(reg, S.square_oracle([(0, 19, "in"), (19, 22, "occluded"), (22, 40, "out")]))
    assert d.review_state == "rejected" and any("exclusion supported" in r for r in d.rejection_reasons)
    assert any(n.kind == "unresolved_candidate" for n in rules.notes())


def test_t11_all_unknown_is_unresolved_never_coerced(reg):
    d, _ = _decide_with(reg, lambda pid, t: "unknown")
    assert d.review_state == "unresolved"
    assert all(a.state == "unknown" for a in d.assessments)


def test_t11_insufficient_visibility_blocks(reg):
    d, _ = _decide_with(reg, S.square_oracle([(0, 20, "in"), (20, 40, "out")]), visibility_sufficient=False)
    assert d.review_state == "unresolved"


def test_t11_hallucinated_frame_id_downgrades_to_unknown(reg):
    d, _ = _decide_with(reg, S.square_oracle([(0, 20, "in"), (20, 40, "out")]), bad_frame=True)
    assert d.review_state == "unresolved"
    assert any("not supplied" in r for r in d.rejection_reasons)


def test_t11_supported_without_frames_is_unknown(reg):
    oracle = S.square_oracle([(0, 20, "in"), (20, 40, "out")])
    from cashwatch.dto import CandidateEvent

    rules = create_rules(reg)
    rf = S.refine(15, 25)
    res = S.verify("c1", ["TEST01.red_visible", "TEST01.red_inside_zone", "TEST01.excl_ambiguous"], rf, oracle,
                   extra=[PredicateAssessment(predicate_id="TEST01.red_outside_zone", state="supported")])
    cand = CandidateEvent(candidate_id="c1", rule_id="TEST01", rule_version="1.0.0", start_sec=15, end_sec=25,
                          anchor_frame_id=None, assessments=[])
    d = rules.decide(cand, res, rf, S.manifest(40))
    assert d.review_state == "unresolved"


def test_t11_frame_from_other_source_rejected(reg):
    from cashwatch.dto import CandidateEvent

    rules = create_rules(reg)
    oracle = S.square_oracle([(0, 20, "in"), (20, 40, "out")])
    rf = S.refine(15, 25, src="c" * 64)
    res = S.verify("c1", [p.predicate_id for p in reg.rule("TEST01").predicates], rf, oracle)
    cand = CandidateEvent(candidate_id="c1", rule_id="TEST01", rule_version="1.0.0", start_sec=15, end_sec=25,
                          anchor_frame_id=None, assessments=[])
    assert rules.decide(cand, res, rf, S.manifest(40)).review_state == "rejected"


# ------------------------------------------------------------------ T12

def test_t12_one_supported_one_obscured(reg):
    m = S.manifest(60)
    segs = [(0, 20, "in"), (20, 30, "out"), (30, 39, "in"), (39, 42, "occluded"), (42, 60, "out")]
    events, decided, _, rules, frames = S.run_pipeline(reg, m, S.square_oracle(segs))
    assert [frames[e.anchor_frame_id].relative_sec for e in events] == [20.0]
    assert any(d.review_state != "accepted" and d.start_sec >= 30 for d in decided)
    assert any(n.kind == "unresolved_candidate" for n in rules.notes())


# ------------------------------------------------------------------ T14 engine side

def test_t14_unknown_rule_candidate_rejected_and_not_verifiable(reg):
    from cashwatch.dto import CandidateEvent

    rules = create_rules(reg)
    cand = CandidateEvent(candidate_id="cx", rule_id="CW99", rule_version="1", start_sec=0, end_sec=5,
                          anchor_frame_id=None, assessments=[])
    with pytest.raises(PipelineError) as ei:
        rules.build_verification(cand, S.refine(0, 5), job_id="j", max_output_tokens=256)
    assert ei.value.code == "rule_registry_invalid"
    res = S.verify("cx", [], S.refine(0, 5), lambda p, t: "supported")
    assert rules.decide(cand, res, S.refine(0, 5), S.manifest(10)).review_state == "rejected"


def test_t14_model_invented_predicate_cannot_support_event(reg):
    from cashwatch.dto import CandidateEvent

    rules = create_rules(reg)
    rf = S.refine(15, 25)
    oracle = S.square_oracle([(0, 20, "in"), (20, 40, "out")])
    # decisive predicate replaced by an invented "clause" id the registry does not define
    res = S.verify("c1", ["TEST01.red_visible", "TEST01.red_inside_zone", "TEST01.excl_ambiguous"], rf, oracle,
                   extra=[PredicateAssessment(predicate_id="TEST01.invented_clause_p99", state="supported",
                                              supporting_frame_ids=[rf[0].frame_id])])
    cand = CandidateEvent(candidate_id="c1", rule_id="TEST01", rule_version="1.0.0", start_sec=15, end_sec=25,
                          anchor_frame_id=None, assessments=[])
    d = rules.decide(cand, res, rf, S.manifest(40))
    assert d.review_state == "unresolved"
    assert any("undefined predicate" in r for r in d.rejection_reasons)
    assert "TEST01.invented_clause_p99" not in {a.predicate_id for a in d.assessments}


def test_t14_stale_rule_version_rejected(reg):
    from cashwatch.dto import CandidateEvent

    rules = create_rules(reg)
    rf = S.refine(15, 25)
    oracle = S.square_oracle([(0, 20, "in"), (20, 40, "out")])
    res = S.verify("c1", [p.predicate_id for p in reg.rule("TEST01").predicates], rf, oracle)
    cand = CandidateEvent(candidate_id="c1", rule_id="TEST01", rule_version="0.9", start_sec=15, end_sec=25,
                          anchor_frame_id=None, assessments=[])
    assert rules.decide(cand, res, rf, S.manifest(40)).review_state == "rejected"


# ------------------------------------------------------------------ propose / notes / request

def test_unmapped_observations_become_review_notes_not_candidates(reg):
    rules = create_rules(reg)
    m = S.manifest(30)
    bs = S.batches(m, [], lambda p, t: "unknown", observations=True)
    assert rules.propose(bs, m) == []
    notes = rules.notes()
    assert notes and all(n.kind == "unmapped_behavior" for n in notes)
    assert all("не является нарушением" in n.text_ru for n in notes)


def test_production_registry_proposes_nothing():
    prod = load_registry(S.RULES_DIR / "registry.yaml", require_approved=True)
    rules = create_rules(prod)
    m = S.manifest(30)
    assert prod.cue_predicates() == []
    assert rules.propose(S.batches(m, [], lambda p, t: "supported", observations=True), m) == []


def test_verification_request_carries_registry_predicates_and_boundaries(reg):
    from cashwatch.dto import CandidateEvent

    rules = create_rules(reg)
    cand = CandidateEvent(candidate_id="c1", rule_id="TEST01", rule_version="1.0.0", start_sec=15, end_sec=25,
                          anchor_frame_id=None, assessments=[])
    rf = list(reversed(S.refine(15, 25)))
    req = rules.build_verification(cand, rf, job_id="job1", max_output_tokens=300)
    assert [p.predicate_id for p in req.predicates] == [p.predicate_id for p in reg.rule("TEST01").predicates]
    assert [f.relative_sec for f in req.frames] == sorted(f.relative_sec for f in rf)
    assert any("TEST01.red_inside_zone" in n for n in req.boundary_notes_en)
    assert req.scope_conditions_en and req.task_version.startswith("verify-v1:test-")
