"""Reporting lane (agent D): deterministic Russian descriptions and coverage notices.

Factory: ``create_reporting(registry) -> ReportingPort``.
"""

from __future__ import annotations

import re

from cashwatch.dto import AcceptedEvent
from cashwatch.errors import PipelineError
from cashwatch.ports import ReportingPort, RuleRegistry
from cashwatch.reporting.templates import mmss, render_description, sanitize_fact
from cashwatch.rules.schema import FORBIDDEN_RE

__all__ = ["Reporting", "create_reporting", "mmss", "sanitize_fact", "CLEAN_NOTICE", "VIOLATION_NOTICE"]

CLEAN_NOTICE = "Нарушение не обнаружено. В пределах видимых действий и проверяемых правил."
VIOLATION_NOTICE = ("Обнаружены возможные нарушения; каждое событие требует проверки сотрудником. "
                    "Анализ ограничен видимыми действиями и проверяемыми правилами; не утверждается, "
                    "что обнаружены все события.")
_MAX_LIMITATIONS = 5


class Reporting:
    def __init__(self, registry: RuleRegistry) -> None:
        self._registry = registry

    def describe(self, event: AcceptedEvent, anchor_sec: float) -> str:
        try:
            ref = self._registry.rule_ref(event.rule.rule_id)
            spec = self._registry.rule(event.rule.rule_id)  # type: ignore[attr-defined]
        except KeyError as e:
            raise PipelineError("rule_registry_invalid", "event rule is not approved") from e
        if ref != event.rule:
            raise PipelineError("rule_registry_invalid", "event rule provenance differs from registry")
        if event.template_id != spec.explanation.template_id:
            raise PipelineError("rule_registry_invalid", "event template id differs from registry")
        try:
            return render_description(spec, ref, event.factual_text_ru, anchor_sec)
        except ValueError as e:
            raise PipelineError("internal_error", "description template rejected fact text") from e

    def coverage_notice_ru(self, verdict: str, limitations: list[str]) -> str:
        if verdict == "no_violation":
            base = CLEAN_NOTICE
        elif verdict == "violation":
            base = VIOLATION_NOTICE
        else:
            raise ValueError("verdict must be violation or no_violation")
        items: list[str] = []
        for lim in limitations:
            t = re.sub(r"\s+", " ", str(lim)).strip().rstrip(".;")
            if t and not FORBIDDEN_RE.search(t) and t not in items:
                items.append(t[:200])
            if len(items) >= _MAX_LIMITATIONS:
                break
        if not items:
            return base
        return f"{base} Ограничения анализа: {'; '.join(items)}."


def create_reporting(registry: RuleRegistry) -> ReportingPort:
    return Reporting(registry)
