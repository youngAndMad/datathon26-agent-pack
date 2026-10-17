"""Internal registry file schema (agent D). Not a cross-lane contract: other lanes consume only
RuleRef / PredicateDefinition from cashwatch.dto through the RuleRegistry port."""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Id = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:\-]+$")]
Sha = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
RelFile = Annotated[str, Field(min_length=1, max_length=256, pattern=r"^[A-Za-z0-9_./\-]+$")]

PredicateKind = Literal["trigger", "decisive", "exclusion", "visibility", "completion"]
RequiredState = Literal["supported", "contradicted", "not_supported"]
RuleState = Literal["draft", "approved", "disabled"]
TemporalKind = Literal["action", "state", "transition", "omission"]
AnchorConvention = Literal["first_anchor_supported", "first_all_supported", "completion_transition"]
CheckStatus = Literal["verified", "unverified", "blocked", "pending", "done", "fixture"]

# Words that must never appear in a registry template or a published fact (identity, guilt,
# intent). Superset of the pipeline validator's list.
FORBIDDEN_RE = re.compile(
    r"\b(краж\w*|ворует|ворова\w*|украл\w*|хищени\w*|мошенни\w*|виновн\w*|преступ\w*|"
    r"несанкционированн\w*|неуполномоченн\w*|посторонн\w* лиц\w*|умысл\w*|умышленн\w*|"
    r"theft|thief|steal\w*|fraud\w*|guilt\w*|criminal|unauthori[sz]ed|intent\w*)\b",
    re.IGNORECASE,
)


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FileHash(_M):
    file: RelFile
    sha256: Sha


class SourceSpec(_M):
    kind: Literal["official", "fixture"]
    document_title: Annotated[str, Field(min_length=1, max_length=600)]
    authority: str
    document_date: str
    document_number: str
    official_url: Annotated[str, Field(pattern=r"^https://adilet\.zan\.kz/")]
    snapshot_file: RelFile
    snapshot_sha256: Sha
    text_file: RelFile
    text_sha256: Sha
    corroboration_files: list[FileHash] = Field(default_factory=list)
    retrieved_at: str
    edition_date: str
    edition_note: str = ""
    edition_verified: bool
    in_force: bool


class PredicateSpec(_M):
    predicate_id: Id
    kind: PredicateKind
    required_state: RequiredState
    anchor: bool = False
    question_en: Annotated[str, Field(min_length=5, max_length=600)]
    counterexamples_en: list[Annotated[str, Field(max_length=300)]] = Field(default_factory=list)


class SourceStatus(_M):
    clause_text: CheckStatus
    edition: CheckStatus
    applicability: CheckStatus
    local_visual_validation: CheckStatus
    notes: str = ""


class Temporal(_M):
    kind: TemporalKind
    transition_from: Id | None = None
    context_before_sec: Annotated[float, Field(ge=0, le=60)] = 3.0
    context_after_sec: Annotated[float, Field(ge=0, le=60)] = 3.0
    requires_complete_interval: bool = False
    max_frame_gap_sec: Annotated[float, Field(gt=0, le=10)] | None = None


class Explanation(_M):
    template_id: Id
    fact_ru: Annotated[str, Field(min_length=3, max_length=300)]
    interpretation_ru: Annotated[str, Field(min_length=3, max_length=300)]
    predicate_ru: Annotated[str, Field(min_length=3, max_length=300)]


class ValidationCounts(_M):
    positive: Annotated[int, Field(ge=0)]
    negative: Annotated[int, Field(ge=0)]
    unknown: Annotated[int, Field(ge=0)] = 0
    dataset_note: str = ""


class Approval(_M):
    owner: str | None = None
    date: str | None = None
    validation_counts: ValidationCounts | None = None
    notes: str = ""


class RuleSpec(_M):
    rule_id: Id
    version: Annotated[str, Field(min_length=1, max_length=32)]
    state: RuleState
    disabled_reason: str | None = None
    symbol: Annotated[str, Field(pattern=r"^[A-Z0-9_]+$")]
    type_ru: Annotated[str, Field(min_length=1, max_length=200)]
    source_id: Id
    clause: Annotated[str, Field(min_length=1, max_length=120)]
    excerpt_ids: Annotated[list[Id], Field(min_length=1)]
    source_status: SourceStatus
    last_verified: str
    paraphrase_ru: str
    applicability_en: list[Annotated[str, Field(max_length=300)]] = Field(default_factory=list)
    nonvisual_prerequisites_en: list[Annotated[str, Field(max_length=300)]] = Field(default_factory=list)
    predicates: Annotated[list[PredicateSpec], Field(min_length=1)]
    counterexamples_en: list[Annotated[str, Field(max_length=300)]] = Field(default_factory=list)
    boundary_notes_en: list[Annotated[str, Field(max_length=300)]] = Field(default_factory=list)
    visibility_requirements_en: list[Annotated[str, Field(max_length=300)]] = Field(default_factory=list)
    temporal: Temporal
    anchor_convention: AnchorConvention
    evidence_strategy: Literal["anchor_then_context"] = "anchor_then_context"
    explanation: Explanation
    limitations_ru: list[Annotated[str, Field(max_length=400)]] = Field(default_factory=list)
    approval: Approval

    # convenience -----------------------------------------------------------------------
    def by_kind(self, *kinds: str) -> list[PredicateSpec]:
        return [p for p in self.predicates if p.kind in kinds]

    @property
    def anchor_predicate(self) -> PredicateSpec | None:
        a = [p for p in self.predicates if p.anchor]
        return a[0] if len(a) == 1 else None

    @property
    def required(self) -> list[PredicateSpec]:
        return [p for p in self.predicates if p.kind != "exclusion"]


class Excerpt(_M):
    excerpt_id: Id
    source_id: Id
    label: str
    text_file: RelFile
    text_sha256: Sha
    start: Annotated[int, Field(ge=0)]
    end: Annotated[int, Field(gt=0)]
    text: Annotated[str, Field(min_length=1, max_length=4000)]


class ExcerptFile(_M):
    generated_by: str = ""
    offsets: str = ""
    excerpts: list[Excerpt]
    failures: list[dict] = Field(default_factory=list)


class RegistryFile(_M):
    registry_version: Annotated[str, Field(min_length=1, max_length=64)]
    test_only: bool
    label: str
    excerpts_file: RelFile
    sources: dict[str, SourceSpec]
    rules: list[RuleSpec]
