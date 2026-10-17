"""Synthetic assessments exercise rule gates; no footage or model-quality assertions."""
from pathlib import Path

import pytest

from cashwatch.dto import CandidateEvent
from cashwatch.rules import create_rules, load_registry
from tests.rules import synth as S


@pytest.fixture(scope="module")
def registry():
    return load_registry(Path(__file__).resolve().parents[2] / "rules" / "registry.yaml", require_approved=True)


def decide(registry, rid, overrides):
    rule = registry.rule(rid)
    defaults = {p.predicate_id: "contradicted" if p.kind == "exclusion" else "supported"
                for p in rule.predicates}
    defaults.update(overrides)
    frames = [S.frame(1), S.frame(2), S.frame(3)]
    candidate = CandidateEvent(candidate_id="synthetic", rule_id=rid, rule_version=rule.version,
                               start_sec=1, end_sec=3, anchor_frame_id=None, assessments=[])
    result = S.verify(candidate.candidate_id, list(defaults), frames, lambda pid, t: defaults[pid])
    return create_rules(registry).decide(candidate, result, frames, S.manifest(5))


def test_visible_storage_rejects_prior_cash_event(registry):
    result = decide(registry, "CW11", {"CW11.excl_prior_cash_stored": "supported"})
    assert result.review_state == "rejected"


@pytest.mark.parametrize("state", ["unknown", "contradicted"])
def test_arrival_without_supported_service_change_cannot_pass(registry, state):
    assert decide(registry, "CW11", {"CW11.client_change": state}).review_state != "accepted"


def test_unseen_storage_does_not_fill_unknown_cash_continuity(registry):
    result = decide(registry, "CW11", {"CW11.excl_prior_cash_stored": "unknown",
                                       "CW11.prior_cash_on_desk": "unknown"})
    assert result.review_state == "unresolved"
    assert next(a for a in result.assessments if a.predicate_id == "CW11.excl_prior_cash_stored").state == "unknown"


def test_unknown_storage_can_coexist_with_positive_visible_continuity(registry):
    # Storage is not an omission gate: positive cash continuity must establish the event.
    result = decide(registry, "CW11", {"CW11.excl_prior_cash_stored": "unknown"})
    assert result.review_state == "accepted"
    assert next(a for a in result.assessments if a.predicate_id == "CW11.excl_prior_cash_stored").state == "unknown"


@pytest.mark.parametrize("state", ["unknown", "contradicted"])
def test_item_without_cashier_action_cannot_pass(registry, state):
    assert decide(registry, "CW12", {"CW12.personal_item_action": state}).review_state != "accepted"


def test_bank_or_client_item_rejects_personal_item(registry):
    assert decide(registry, "CW12", {"CW12.excl_client_or_equipment": "supported"}).review_state == "rejected"


@pytest.mark.parametrize("rid", ["CW11", "CW12"])
def test_positive_synthetic_assessments_pass_without_new_rules(registry, rid):
    assert decide(registry, rid, {}).review_state == "accepted"
    assert registry.approved_rule_ids() == ["CW11", "CW12"]


@pytest.fixture(scope="module")
def candidate_registry():
    return load_registry(Path(__file__).resolve().parents[2] / "rules" / "registry.yaml",
                         require_approved=False, candidate_rules={"CW14"})


def test_cw14_revision_stays_disabled_outside_candidate_evaluation(registry, candidate_registry):
    assert registry.spec("CW14").state == "disabled"
    assert registry.spec("CW14").approval.owner is None
    assert registry.spec("CW14").version == "0.3.0"
    assert candidate_registry.evaluation_only
    assert candidate_registry.approved_rule_ids() == ["CW11", "CW12", "CW14"]
    with pytest.raises(KeyError):
        registry.rule_ref("CW14")


@pytest.mark.parametrize("predicate", ["CW14.client_handover", "CW14.client_gone_while_counting", "CW14.area_visible"])
def test_cw14_unknown_receipt_departure_or_visibility_blocks(candidate_registry, predicate):
    assert decide(candidate_registry, "CW14", {predicate: "unknown"}).review_state == "unresolved"


@pytest.mark.parametrize("predicate", ["CW14.excl_internal", "CW14.excl_count_finished"])
def test_cw14_observed_alternative_blocks(candidate_registry, predicate):
    assert decide(candidate_registry, "CW14", {predicate: "supported"}).review_state == "rejected"


def test_cw14_positive_assessments_work_only_as_candidate(candidate_registry):
    assert decide(candidate_registry, "CW14", {}).review_state == "accepted"
