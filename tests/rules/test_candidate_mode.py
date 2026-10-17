"""Evaluation-only candidate mode: draft rules exposed for dev evaluation, never in production."""

import pytest

from cashwatch.errors import PipelineError
from cashwatch.rules import load_registry
from cashwatch.settings import REPO_ROOT

REG = REPO_ROOT / "rules" / "registry.yaml"


def test_production_registry_approved_set_is_exactly_d014():
    assert load_registry(REG, require_approved=False).approved_rule_ids() == ["CW11", "CW12"]


def test_candidate_rules_exposed_only_in_evaluation_mode():
    r = load_registry(REG, require_approved=False, candidate_rules={"CW10", "CW12"})
    assert r.approved_rule_ids() == ["CW10", "CW11", "CW12"] and r.evaluation_only
    assert "https://adilet.zan.kz/" in r.rule_ref("CW10").official_url


def test_candidate_mode_refused_in_production():
    with pytest.raises(PipelineError):
        load_registry(REG, require_approved=True, candidate_rules={"CW10"})
