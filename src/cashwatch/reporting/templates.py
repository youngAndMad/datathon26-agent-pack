"""Deterministic Russian description templates (spec §10, decision D009).

Every non-fact part comes from the approved registry entry; the fact phrase is the registry's
bounded ``fact_ru`` (AcceptedEvent.factual_text_ru), sanitized here. No model prose.
"""

from __future__ import annotations

import re

from cashwatch.dto import RuleRef, ts
from cashwatch.rules.schema import FORBIDDEN_RE, RuleSpec

DESCRIPTION_TEMPLATE = (
    "На {mmss} ({sec:.3f} с) видно: {fact}. Это может указывать на {interpretation}, "
    "поскольку {predicate}. Требует проверки сотрудником. "
    "Источник: {title}, п. {clause}; {url}."
)
MAX_DESCRIPTION = 2000


def mmss(sec: float) -> str:
    s = ts(sec)
    if s < 0:
        raise ValueError("negative time")
    whole = int(s)
    return f"{whole // 60:02d}:{whole % 60:02d}"


def sanitize_fact(text: str) -> str:
    """Bounded single-sentence fact: collapse whitespace, drop trailing punctuation, refuse
    identity/guilt/intent vocabulary, braces, URLs or digits that look like identifiers."""
    t = re.sub(r"\s+", " ", text or "").strip().rstrip(".;:!? ")
    if not t or len(t) > 300:
        raise ValueError("fact text empty or too long")
    if FORBIDDEN_RE.search(t):
        raise ValueError("forbidden term in fact text")
    if any(c in t for c in "{}<>") or re.search(r"https?://|@|\d{6,}", t):
        raise ValueError("fact text contains markup, URL or identifier-like content")
    return t


def render_description(rule: RuleSpec, ref: RuleRef, fact: str, anchor_sec: float) -> str:
    e = rule.explanation
    out = DESCRIPTION_TEMPLATE.format(
        mmss=mmss(anchor_sec), sec=ts(anchor_sec), fact=sanitize_fact(fact),
        interpretation=e.interpretation_ru.strip().rstrip("."), predicate=e.predicate_ru.strip().rstrip("."),
        title=ref.document_title, clause=ref.clause, url=ref.official_url)
    if len(out) > MAX_DESCRIPTION or FORBIDDEN_RE.search(out):
        raise ValueError("description invalid")
    return out
