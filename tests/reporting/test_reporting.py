"""Deterministic Russian descriptions and coverage notices (spec §10, D009)."""

from __future__ import annotations

import re

import pytest

from cashwatch.errors import PipelineError
from cashwatch.reporting import CLEAN_NOTICE, create_reporting
from cashwatch.rules import load_registry
from tests.rules import synth as S

FORBIDDEN = re.compile(r"(краж|хищен|мошенн|виновн|преступ|украл|theft|fraud|guilty|unauthori)", re.I)


@pytest.fixture(scope="module")
def reg():
    return load_registry(S.RULES_DIR / "registry.test.yaml", require_approved=True, allow_test=True)


@pytest.fixture(scope="module")
def event(reg):
    m = S.manifest(60)
    events, *_ = S.run_pipeline(reg, m, S.square_oracle([(0, 20, "in"), (20, 60, "out")]))
    return events[0]


def test_description_exact_pattern_from_registry(reg, event):
    s = create_reporting(reg).describe(event, 20.0)
    assert s == ("На 00:20 (20.000 с) видно: красный квадрат полностью вышел за пределы жёлтой прямоугольной зоны. "
                 "Это может указывать на срабатывание синтетического тестового правила (не является нарушением норм "
                 "права), поскольку красный квадрат сначала находился внутри жёлтой зоны, а затем полностью вне её. "
                 "Требует проверки сотрудником. Источник: ТЕСТОВОЕ ПРАВИЛО — не является нормой права, "
                 "п. TEST01 (синтетический тест); https://adilet.zan.kz/rus/docs/V1900019680.")
    assert not FORBIDDEN.search(s)


def test_time_formatting_uses_canonical_precision(reg, event):
    s = create_reporting(reg).describe(event, 125.4567)
    assert s.startswith("На 02:05 (125.457 с) видно: ")


def test_description_for_validated_cw_rule_contains_official_source(tmp_path):
    from cashwatch.pipeline.validate import FORBIDDEN_TERMS
    from cashwatch.reporting.templates import render_description

    d = S.copy_rules(tmp_path)

    def fn(data):
        S.approve_fully(next(r for r in data["rules"] if r["rule_id"] == "CW01"))
    S.edit_registry(d / "registry.yaml", fn)
    reg = load_registry(d / "registry.yaml", require_approved=True)
    spec, ref = reg.rule("CW01"), reg.rule_ref("CW01")
    s = render_description(spec, ref, spec.explanation.fact_ru, 61.5)
    assert s.startswith("На 01:01 (61.500 с) видно: кассовый работник отходит от рабочего места")
    assert "Требует проверки сотрудником." in s
    assert s.endswith("Источник: Правила осуществления кассовых операций и операций по инкассации банкнот, монет и "
                      "ценностей в банках второго уровня, филиалах банков-нерезидентов Республики Казахстан, "
                      "Национальном операторе почты и юридических лицах, исключительной деятельностью которых "
                      "является инкассация банкнот, монет и ценностей (утверждены постановлением Правления "
                      "Национального Банка Республики Казахстан от 29 ноября 2019 года № 231), п. 22; "
                      "https://adilet.zan.kz/rus/docs/V1900019680.")
    assert not FORBIDDEN_TERMS.search(s) and len(s) <= 2000


def test_all_cw_templates_render_without_forbidden_words(tmp_path):
    from cashwatch.reporting.templates import render_description

    d = S.copy_rules(tmp_path)
    S.edit_registry(d / "registry.yaml", lambda data: [S.approve_fully(r) for r in data["rules"]])
    reg = load_registry(d / "registry.yaml", require_approved=True)
    assert reg.approved_rule_ids() == sorted(r.rule_id for r in reg._data.rules), reg.health_errors()
    for rid in reg.approved_rule_ids():
        spec, ref = reg.rule(rid), reg.rule_ref(rid)
        s = render_description(spec, ref, spec.explanation.fact_ru, 3.0)
        assert f"п. {ref.clause};" in s and ref.official_url in s and not FORBIDDEN.search(s)


def test_model_invented_clause_in_event_is_rejected(reg, event):
    forged = event.model_copy(update={"rule": event.rule.model_copy(update={"clause": "99 (выдумано моделью)"})})
    with pytest.raises(PipelineError) as ei:
        create_reporting(reg).describe(forged, 20.0)
    assert ei.value.code == "rule_registry_invalid"


def test_unknown_rule_id_rejected(reg, event):
    forged = event.model_copy(update={"rule": event.rule.model_copy(update={"rule_id": "CW99"})})
    with pytest.raises(PipelineError):
        create_reporting(reg).describe(forged, 20.0)


@pytest.mark.parametrize("fact", ["кассир совершил кражу", "видна мошенническая схема", "Иванов И.И. https://x.y",
                                  "{fact}", ""])
def test_unsafe_fact_text_rejected(reg, event, fact):
    with pytest.raises(PipelineError):
        create_reporting(reg).describe(event.model_copy(update={"factual_text_ru": fact}), 20.0)


def test_coverage_notices(reg):
    rep = create_reporting(reg)
    assert rep.coverage_notice_ru("no_violation", []) == CLEAN_NOTICE
    assert CLEAN_NOTICE == "Нарушение не обнаружено. В пределах видимых действий и проверяемых правил."
    s = rep.coverage_notice_ru("no_violation", ["Часть кадра перекрыта.", "Часть кадра перекрыта.", "Низкий свет"])
    assert s == CLEAN_NOTICE + " Ограничения анализа: Часть кадра перекрыта; Низкий свет."
    v = rep.coverage_notice_ru("violation", ["Второй эпизод частично перекрыт"])
    assert "требует проверки сотрудником" in v and "Второй эпизод частично перекрыт" in v
    with pytest.raises(ValueError):
        rep.coverage_notice_ru("insufficient_data", [])
