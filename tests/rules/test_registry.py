"""Registry lint, source integrity and production/test separation (T14, T21 registry side)."""

from __future__ import annotations

import json

import pytest
import yaml

from cashwatch.errors import PipelineError
from cashwatch.rules import load_registry
from cashwatch.rules.sources import html_to_text, sha256_file
from tests.rules import synth as S

PROD = S.RULES_DIR / "registry.yaml"
TEST = S.RULES_DIR / "registry.test.yaml"
CW = [f"CW0{i}" for i in range(1, 10)]


def _approve(rule_id):
    def fn(data):
        S.approve_fully(next(r for r in data["rules"] if r["rule_id"] == rule_id))
    return fn


def test_production_registry_has_zero_approved_rules_and_fails_readiness_honestly():
    reg = load_registry(PROD, require_approved=True)
    assert reg.approved_rule_ids() == [] and reg.cue_predicates() == []
    errs = reg.health_errors()
    assert len(errs) == 1 and errs[0].startswith("no approved rules")  # sources/lint otherwise clean
    for rid in CW:
        with pytest.raises(KeyError):
            reg.rule_ref(rid)
        assert reg.spec(rid).state == "disabled" and reg.spec(rid).source_status.local_visual_validation == "blocked"
    assert load_registry(PROD, require_approved=False).health_errors() == []


def test_snapshots_hash_match_and_excerpts_are_verbatim_official_slices():
    data = yaml.safe_load(PROD.read_text(encoding="utf-8"))
    for sid, src in data["sources"].items():
        assert src["official_url"].startswith("https://adilet.zan.kz/rus/docs/")
        snap, txt = S.RULES_DIR / src["snapshot_file"], S.RULES_DIR / src["text_file"]
        assert sha256_file(snap) == src["snapshot_sha256"] and sha256_file(txt) == src["text_sha256"]
        doc = json.loads(snap.read_text(encoding="utf-8"))["document"]
        assert html_to_text(doc["text_content"]) == txt.read_text(encoding="utf-8")
        assert doc["group_number"] in src["official_url"]
    ex = yaml.safe_load((S.RULES_DIR / "sources" / "excerpts.yaml").read_text(encoding="utf-8"))
    assert ex["failures"] == []
    for e in ex["excerpts"]:
        text = (S.RULES_DIR / "sources" / e["text_file"]).read_text(encoding="utf-8")
        assert text[e["start"]:e["end"]] == e["text"]
    by_id = {e["excerpt_id"]: e["text"] for e in ex["excerpts"]}
    assert by_id["L01-p22"].startswith("22. ") and by_id["L01-p12"].startswith("12. ")
    assert by_id["L01-p20"].startswith("20. ")
    # Clause 12 consolidated text equals the amendment wording in L02 (No 71, 10.11.2025).
    assert by_id["L01-p12"] in by_id["L02-amend-p12"].replace('"12.', "12.")


def test_test_registry_refused_in_production_and_loaded_only_in_test_mode():
    with pytest.raises(PipelineError) as ei:
        load_registry(TEST, require_approved=True)
    assert ei.value.code == "rule_registry_invalid"
    with pytest.raises(PipelineError):
        load_registry(TEST, require_approved=False)
    reg = load_registry(TEST, require_approved=True, allow_test=True)
    assert reg.test_only and reg.label == "SYNTHETIC TEST" and reg.approved_rule_ids() == ["TEST01"]
    ref = reg.rule_ref("TEST01")
    assert "ТЕСТОВОЕ ПРАВИЛО" in ref.document_title and "Синтетический тест" in ref.type_ru
    assert "не является нормативным правовым актом" in ref.local_excerpt or "не является" in ref.document_title


def test_fully_validated_cw_rule_would_load_with_official_provenance(tmp_path):
    d = S.copy_rules(tmp_path)
    S.edit_registry(d / "registry.yaml", _approve("CW01"))
    reg = load_registry(d / "registry.yaml", require_approved=True)
    assert reg.health_errors() == [] and reg.approved_rule_ids() == ["CW01"]
    ref = reg.rule_ref("CW01")
    assert ref.official_url == "https://adilet.zan.kz/rus/docs/V1900019680" and ref.clause == "22"
    assert ref.local_excerpt.startswith("22. Кассовому работнику не допускается покидать рабочее место")
    assert ref.source_snapshot_sha256 == sha256_file(d / "sources" / "L01-2026-10-17.api.json")
    assert {p.predicate_id for p in reg.cue_predicates()} == {"CW01.service_in_progress"}  # coarse = triggers


def test_approved_rule_without_matching_snapshot_hash_rejected(tmp_path):
    d = S.copy_rules(tmp_path)

    def fn(data):
        _approve("CW01")(data)
        data["sources"]["L01"]["snapshot_sha256"] = "0" * 64
    S.edit_registry(d / "registry.yaml", fn)
    reg = load_registry(d / "registry.yaml", require_approved=True)
    assert reg.approved_rule_ids() == []
    assert any("snapshot sha256 mismatch" in e for e in reg.health_errors())
    assert any("CW01: approved rule rejected" in e for e in reg.health_errors())


def test_approved_rule_with_missing_snapshot_hash_field_is_invalid(tmp_path):
    d = S.copy_rules(tmp_path)

    def fn(data):
        _approve("CW01")(data)
        del data["sources"]["L01"]["snapshot_sha256"]
    S.edit_registry(d / "registry.yaml", fn)
    with pytest.raises(PipelineError) as ei:
        load_registry(d / "registry.yaml", require_approved=True)
    assert ei.value.code == "rule_registry_invalid"


def test_tampered_snapshot_or_excerpt_rejected(tmp_path):
    d = S.copy_rules(tmp_path)
    S.edit_registry(d / "registry.yaml", _approve("CW01"))
    p = d / "sources" / "excerpts.yaml"
    ex = yaml.safe_load(p.read_text(encoding="utf-8"))
    for e in ex["excerpts"]:
        if e["excerpt_id"] == "L01-p22":
            e["text"] = e["text"].replace("не допускается", "допускается")
    p.write_text(yaml.safe_dump(ex, allow_unicode=True, sort_keys=False), encoding="utf-8")
    reg = load_registry(d / "registry.yaml", require_approved=True)
    assert reg.approved_rule_ids() == []
    assert any("verbatim" in e for e in reg.health_errors())


def test_approval_requires_local_validation_and_verified_scope(tmp_path):
    d = S.copy_rules(tmp_path)

    def fn(data):
        r = next(r for r in data["rules"] if r["rule_id"] == "CW04")
        r["state"] = "approved"  # source fields verified, but local validation still blocked
    S.edit_registry(d / "registry.yaml", fn)
    reg = load_registry(d / "registry.yaml", require_approved=True)
    assert reg.approved_rule_ids() == []
    errs = " | ".join(reg.health_errors())
    assert "local visual validation not done" in errs and "approval owner/date/validation counts missing" in errs


def test_non_adilet_url_rejected_by_schema(tmp_path):
    d = S.copy_rules(tmp_path)
    S.edit_registry(d / "registry.yaml", lambda data: data["sources"]["L01"].update(
        official_url="https://adilet.kz/rus/docs/V1900019680"))
    with pytest.raises(PipelineError):
        load_registry(d / "registry.yaml", require_approved=True)


def test_lint_rejects_forbidden_template_words_and_namespace_mixing(tmp_path):
    d = S.copy_rules(tmp_path)

    def fn(data):
        _approve("CW01")(data)
        data["rules"][0]["explanation"]["fact_ru"] = "кассир совершает кражу наличных"
        t = yaml.safe_load((d / "registry.test.yaml").read_text(encoding="utf-8"))["rules"][0]
        data["rules"].append(t)  # TEST rule inside the production registry
    S.edit_registry(d / "registry.yaml", fn)
    reg = load_registry(d / "registry.yaml", require_approved=True)
    errs = " | ".join(reg.health_errors())
    assert "forbidden term" in errs and "namespace mismatch" in errs and "fixture" not in reg.approved_rule_ids()
    assert reg.approved_rule_ids() == []


def test_duplicate_predicate_ids_rejected(tmp_path):
    d = S.copy_rules(tmp_path)

    def fn(data):
        r = data["rules"][0]
        r["predicates"].append(dict(r["predicates"][0]))
    S.edit_registry(d / "registry.test.yaml", fn)
    reg = load_registry(d / "registry.test.yaml", require_approved=True, allow_test=True)
    assert reg.approved_rule_ids() == [] and any("duplicate predicate id" in e for e in reg.health_errors())


def test_missing_registry_file_fails_clearly(tmp_path):
    with pytest.raises(PipelineError) as ei:
        load_registry(tmp_path / "nope.yaml", require_approved=True)
    assert ei.value.code == "rule_registry_invalid"
