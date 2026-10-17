"""Frozen cross-module DTOs (G0 contract, version CONTRACT_VERSION).

Owned by the orchestrator. Agents request changes via coordination/requests/<agent>.md.
Every record is JSON-compatible: no PIL images, tensors or executable paths. Local file
locations are *relative* paths inside a job work directory and are resolved only by the
media/storage layers after containment checks.

Time convention: every ``*_sec`` value is seconds on the playable presentation timeline of
the original source (zero = first presented frame after container edits). Authoritative
timestamps come from decoded PTS * time_base, never from ordinal / nominal FPS. Public
serialization precision is TIME_DECIMALS (see ``ts``).
"""

from __future__ import annotations

import math
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

CONTRACT_VERSION = "g0-1.1.0"  # 1.1: AnalysisReport.coverage_notice_ru (R-A2)
TIME_DECIMALS = 3


def ts(value: float) -> float:
    """Canonical timestamp serialization used for anchors and evidence (stored precision)."""
    if not math.isfinite(value):
        raise ValueError("timestamp must be finite")
    return round(float(value), TIME_DECIMALS)


def _finite(v: float) -> float:
    if not math.isfinite(v):
        raise ValueError("value must be finite")
    return v


Finite = Annotated[float, AfterValidator(_finite)]
NonNegSec = Annotated[float, Field(ge=0), AfterValidator(_finite)]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
ShortText = Annotated[str, Field(max_length=400)]
Id = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:\-]+$")]
def _relpath(v: str) -> str:
    if v.startswith("/") or ".." in v.split("/") or "\\" in v:
        raise ValueError("path must be relative and contained")
    return v


RelPath = Annotated[str, Field(min_length=1, max_length=512, pattern=r"^[A-Za-z0-9_./\-]+$"), AfterValidator(_relpath)]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --------------------------------------------------------------------------- vocabularies

TriState = Literal["supported", "contradicted", "unknown"]
Visibility = Literal["clear", "partial", "unusable"]
Stage = Literal[
    "receiving", "probing", "sampling", "observing", "verifying",
    "rendering_evidence", "committing", "complete",
]
STAGES: tuple[Stage, ...] = (
    "receiving", "probing", "sampling", "observing", "verifying",
    "rendering_evidence", "committing", "complete",
)
ErrorCode = Literal[
    "invalid_media", "unsupported_container", "limits_exceeded", "timeline_unsupported",
    "decode_failed", "model_unavailable", "model_output_invalid", "gpu_oom", "storage_full",
    "worker_lost", "deadline_exceeded", "evidence_invalid", "rule_registry_invalid",
    "input_unusable", "internal_error",
]
InternalJobState = Literal["receiving", "queued", "running", "succeeded", "failed", "expired"]
ReviewState = Literal["proposed", "accepted", "rejected", "unresolved"]
RunMode = Literal["production", "test"]

# Safe Russian messages for each error code (public; no paths/prompts/scene text).
ERROR_MESSAGES_RU: dict[str, str] = {
    "invalid_media": "Файл не является корректной видеозаписью",
    "unsupported_container": "Формат контейнера не поддерживается (ожидается MOV)",
    "limits_exceeded": "Видео превышает допустимые размеры или длительность",
    "timeline_unsupported": "Не удалось однозначно определить временную шкалу видео",
    "decode_failed": "Не удалось прочитать видеозапись",
    "model_unavailable": "Модель анализа недоступна",
    "model_output_invalid": "Модель вернула некорректный ответ",
    "gpu_oom": "Недостаточно памяти ускорителя для анализа",
    "storage_full": "Недостаточно места для хранения",
    "worker_lost": "Обработчик задания был прерван",
    "deadline_exceeded": "Анализ не уложился в отведённое время",
    "evidence_invalid": "Не удалось подготовить достоверные кадры-доказательства",
    "rule_registry_invalid": "Реестр правил недоступен или некорректен",
    "input_unusable": "Видеозапись непригодна для анализа",
    "internal_error": "Внутренняя ошибка обработки",
}


class ErrorInfo(Strict):
    code: ErrorCode
    message: Annotated[str, Field(min_length=1, max_length=300)]


# --------------------------------------------------------------------------- jobs/deadline

class JobInput(Strict):
    job_id: Id
    principal_id: Id
    source_path: RelPath  # relative to storage root; resolved by storage layer
    source_sha256: Sha256
    request_started_at: str  # ISO-8601 UTC, trusted ingress start
    accepted_at: str  # ISO-8601 UTC
    deadline_at: str  # ISO-8601 UTC hard internal deadline
    attempt: Annotated[int, Field(ge=1)]
    lease_generation: Annotated[int, Field(ge=1)]
    config_version: str


class StageProgress(Strict):
    completed: Annotated[int, Field(ge=0)]
    total: Annotated[int, Field(ge=0)]
    unit: Literal["windows", "candidates", "events", "steps"]


# --------------------------------------------------------------------------- media

class Transform(Strict):
    kind: Literal["rotate", "resize", "color_convert", "crop", "jpeg_encode"]
    parameters: dict[str, Any] = Field(default_factory=dict)


class VideoMetadata(Strict):
    source_sha256: Sha256
    size_bytes: Annotated[int, Field(gt=0)]
    container: str  # e.g. "mov,mp4,m4a,3gp,3g2,mj2" as reported by demuxer
    codec: str
    width: Annotated[int, Field(gt=0)]  # coded/stored frame width
    height: Annotated[int, Field(gt=0)]
    display_rotation: Literal[0, 90, 180, 270]
    display_width: Annotated[int, Field(gt=0)]  # after rotation
    display_height: Annotated[int, Field(gt=0)]
    duration_sec: NonNegSec  # playable presentation duration
    time_base_num: Annotated[int, Field(gt=0)]
    time_base_den: Annotated[int, Field(gt=0)]
    origin_pts: int  # PTS mapped to 0.0 s
    origin_method: str  # how origin was established (diagnostic, e.g. "first_decoded_pts")
    first_frame_sec: NonNegSec
    last_frame_sec: NonNegSec
    nominal_fps: Finite | None = None  # diagnostic only, never timing authority
    frame_count: Annotated[int, Field(ge=0)] | None = None
    has_audio: bool = False  # recorded and ignored
    decode_warnings: list[ShortText] = Field(default_factory=list)


class FrameRef(Strict):
    frame_id: Id
    source_sha256: Sha256
    ordinal: Annotated[int, Field(ge=0)]  # decoded presentation-order ordinal; diagnostic
    pts: int
    time_base_num: Annotated[int, Field(gt=0)]
    time_base_den: Annotated[int, Field(gt=0)]
    relative_sec: NonNegSec  # (pts - origin_pts) * time_base
    local_path: RelPath  # model thumbnail JPEG/PNG inside job work dir
    sha256: Sha256  # hash of the stored image file at local_path
    width: Annotated[int, Field(gt=0)]
    height: Annotated[int, Field(gt=0)]
    transforms: list[Transform] = Field(default_factory=list)


class SamplingWindow(Strict):
    window_id: Id
    kind: Literal["coarse", "proposal", "refine"]
    start_sec: NonNegSec
    end_sec: NonNegSec
    requested_sample_times: list[NonNegSec]
    mandatory: bool


class SamplingPlan(Strict):
    profile_id: str
    windows: list[SamplingWindow]
    mandatory_window_ids: list[Id]
    maximum_frames_per_call: Annotated[int, Field(gt=0)]
    model_long_side_px: Annotated[int, Field(gt=0)]
    visual_token_limit: Annotated[int, Field(gt=0)] | None = None


class FrameManifest(Strict):
    video: VideoMetadata
    frames: list[FrameRef]  # unique by frame_id, sorted by relative_sec
    window_frames: dict[str, list[Id]]  # window_id -> ordered frame_ids
    coverage_first_sec: NonNegSec  # actual first sampled frame time
    coverage_last_sec: NonNegSec  # actual last sampled frame time
    decode_stats: dict[str, Finite | int | str] = Field(default_factory=dict)


class MotionSample(Strict):
    relative_sec: NonNegSec
    score: Annotated[float, Field(ge=0)]  # normalized frame-difference energy; hint only


class EvidenceFrame(Strict):
    evidence_id: Id
    frame_id: Id  # FrameRef this evidence corresponds to (same PTS)
    source_sha256: Sha256
    pts: int
    time_base_num: Annotated[int, Field(gt=0)]
    time_base_den: Annotated[int, Field(gt=0)]
    timestamp_sec: NonNegSec  # == ts(FrameRef.relative_sec)
    jpeg_path: RelPath  # inside job output dir
    jpeg_sha256: Sha256
    width: Annotated[int, Field(gt=0)]
    height: Annotated[int, Field(gt=0)]
    jpeg_quality: Annotated[int, Field(ge=50, le=100)]
    transforms: list[Transform] = Field(default_factory=list)


# --------------------------------------------------------------------------- perception

class PredicateAssessment(Strict):
    predicate_id: Id
    state: TriState
    supporting_frame_ids: list[Id] = Field(default_factory=list)
    contradicting_frame_ids: list[Id] = Field(default_factory=list)
    interval_start_sec: NonNegSec | None = None  # resolved from frame ids by application
    interval_end_sec: NonNegSec | None = None
    visibility: Visibility = "partial"
    reasoning_note: ShortText = ""  # visible facts only


class Observation(Strict):
    observation_id: Id
    frame_ids: list[Id]
    actor_refs: list[Annotated[str, Field(max_length=32)]] = Field(default_factory=list)  # anonymous, window-local
    object_hypotheses: list[Annotated[str, Field(max_length=80)]] = Field(default_factory=list)
    action: ShortText
    visibility: Visibility
    uncertainty: ShortText = ""


class PredicateDefinition(Strict):
    """Neutral, visual predicate text supplied to the model (from the approved registry)."""
    predicate_id: Id
    question_en: Annotated[str, Field(max_length=600)]
    counterexamples_en: list[Annotated[str, Field(max_length=300)]] = Field(default_factory=list)


class ObservationRequest(Strict):
    job_id: Id
    window_id: Id
    start_sec: NonNegSec
    end_sec: NonNegSec
    frames: list[FrameRef]  # ordered; adapter resolves image bytes locally
    task_version: str
    cue_predicates: list[PredicateDefinition] = Field(default_factory=list)  # coarse cues
    max_output_tokens: Annotated[int, Field(gt=0, le=2048)]


class ObservationBatch(Strict):
    window_id: Id
    observations: list[Observation]
    cue_assessments: list[PredicateAssessment] = Field(default_factory=list)
    parse_status: Literal["ok", "repaired"]
    limitations: list[ShortText] = Field(default_factory=list)
    elapsed_ms: Annotated[int, Field(ge=0)]
    model_version: str
    prompt_sha256: Sha256
    input_tokens: Annotated[int, Field(ge=0)] | None = None
    output_tokens: Annotated[int, Field(ge=0)] | None = None


class VerificationRequest(Strict):
    job_id: Id
    candidate_id: Id
    rule_id: Id
    predicates: list[PredicateDefinition]
    scope_conditions_en: list[Annotated[str, Field(max_length=300)]] = Field(default_factory=list)
    boundary_notes_en: list[Annotated[str, Field(max_length=300)]] = Field(default_factory=list)
    interval_start_sec: NonNegSec
    interval_end_sec: NonNegSec
    frames: list[FrameRef]
    task_version: str
    max_output_tokens: Annotated[int, Field(gt=0, le=2048)]


class VerificationResult(Strict):
    candidate_id: Id
    assessments: list[PredicateAssessment]
    plausible_alternatives: list[ShortText] = Field(default_factory=list)
    interval_complete: bool
    visibility_sufficient: bool
    schema_valid: bool
    parse_status: Literal["ok", "repaired"]
    limitations: list[ShortText] = Field(default_factory=list)
    elapsed_ms: Annotated[int, Field(ge=0)]
    model_version: str
    prompt_sha256: Sha256


class ModelInfo(Strict):
    adapter: Literal["mlx", "transformers", "fake"]
    model_id: str
    revision: str
    local_path_label: str  # display label only, not an absolute path
    is_mock: bool
    warmed_up: bool
    device: str


# --------------------------------------------------------------------------- rules/reporting

class RuleRef(Strict):
    """Public provenance of an approved rule (assembled from registry only)."""
    rule_id: Id
    rule_version: str
    type_ru: Annotated[str, Field(min_length=1, max_length=200)]
    document_title: Annotated[str, Field(max_length=600)]
    authority: str
    document_date: str
    document_number: str
    clause: str
    official_url: Annotated[str, Field(pattern=r"^https://adilet\.zan\.kz/")]
    local_excerpt: Annotated[str, Field(max_length=4000)]
    source_snapshot_sha256: Sha256


class CandidateEvent(Strict):
    candidate_id: Id
    rule_id: Id
    rule_version: str
    start_sec: NonNegSec
    end_sec: NonNegSec
    anchor_frame_id: Id | None
    assessments: list[PredicateAssessment]
    counterexamples_considered: list[ShortText] = Field(default_factory=list)
    provenance_observation_ids: list[Id] = Field(default_factory=list)
    evidence_frame_ids: list[Id] = Field(default_factory=list)
    interval_complete: bool = False
    visibility_sufficient: bool = False
    review_state: ReviewState = "proposed"
    rejection_reasons: list[ShortText] = Field(default_factory=list)


class AcceptedEvent(Strict):
    event_id: Id
    rule: RuleRef
    start_sec: NonNegSec
    end_sec: NonNegSec
    anchor_frame_id: Id
    evidence_frame_ids: Annotated[list[Id], Field(min_length=1, max_length=3)]  # includes anchor
    factual_text_ru: ShortText  # bounded, template-assembled observable fact
    template_id: str
    limitations_ru: list[ShortText] = Field(default_factory=list)
    provenance_candidate_ids: list[Id] = Field(default_factory=list)


class ReviewNote(Strict):
    """Unmapped/unknown observation kept for human review; never a public violation."""
    note_id: Id
    start_sec: NonNegSec
    end_sec: NonNegSec
    kind: Literal["unmapped_behavior", "unresolved_candidate", "visibility", "technical"]
    text_ru: ShortText


class Coverage(Strict):
    profile_id: str
    required_window_ids: list[Id]
    completed_window_ids: list[Id]
    failed_window_ids: list[Id]
    proposal_window_ids: list[Id] = Field(default_factory=list)
    refined_candidate_ids: list[Id] = Field(default_factory=list)
    covered_start_sec: NonNegSec
    covered_end_sec: NonNegSec
    visual_limitations: list[ShortText] = Field(default_factory=list)


class RunVersions(Strict):
    contract_version: str
    code_version: str
    config_version: str
    profile_id: str
    registry_version: str
    model: ModelInfo
    prompt_sha256: dict[str, Sha256] = Field(default_factory=dict)


# --------------------------------------------------------------------------- jury (exact)

class SubmitResponse(Strict):
    job_id: Annotated[str, Field(min_length=1)]


class JuryEvidenceFrame(Strict):
    timestamp_sec: Annotated[float, Field(ge=0)]
    image_base64: Annotated[str, Field(min_length=4, pattern=r"^[A-Za-z0-9+/]+={0,2}$")]


class JuryViolation(Strict):
    type: Annotated[str, Field(min_length=1)]
    timecode_sec: Annotated[float, Field(ge=0)]
    description: Annotated[str, Field(min_length=1)]
    evidence_frames: Annotated[list[JuryEvidenceFrame], Field(min_length=1, max_length=3)]


class JuryDoneResult(Strict):
    job_id: Annotated[str, Field(min_length=1)]
    status: Literal["done"] = "done"
    verdict: Literal["no_violation", "violation"]
    violations: list[JuryViolation]


class ProcessingResponse(Strict):
    job_id: str
    status: Literal["processing"] = "processing"


class FailedResponse(Strict):
    job_id: str
    status: Literal["failed"] = "failed"
    error: ErrorInfo


# --------------------------------------------------------------------------- report

class ReportEvent(Strict):
    """An accepted event with resolved evidence, as stored in report.json."""
    event: AcceptedEvent
    evidence: Annotated[list[EvidenceFrame], Field(min_length=1, max_length=3)]
    description_ru: Annotated[str, Field(min_length=1, max_length=2000)]  # == jury description


class AnalysisReport(Strict):
    job_id: Id
    status: Literal["succeeded", "failed"]
    mode: RunMode
    source: VideoMetadata | None
    versions: RunVersions
    stage_timings_ms: dict[str, int]
    upload_to_done_ms: int | None = None
    processing_ms: int
    coverage: Coverage | None
    completed_stages: list[Stage]
    visual_limitations_ru: list[ShortText] = Field(default_factory=list)
    coverage_notice_ru: Annotated[str, Field(max_length=1000)] | None = None
    events: list[ReportEvent] = Field(default_factory=list)
    review_notes: list[ReviewNote] = Field(default_factory=list)
    jury_result: JuryDoneResult | None = None
    error: ErrorInfo | None = None


# --------------------------------------------------------------------------- UI report API

class UiEvidence(Strict):
    timestamp_sec: float
    image_url: str  # job-scoped, same-origin, requires X-API-Key; UI fetches -> blob URL


class UiSource(Strict):
    rule_id: str
    document_title: str
    clause: str
    official_url: str
    local_excerpt: str


class UiInterval(Strict):
    start_sec: float
    end_sec: float


class UiEvent(Strict):
    event_id: str
    type: str
    timecode_sec: float
    description: str
    evidence: list[UiEvidence]
    source: UiSource
    interval: UiInterval
    limitations_ru: list[str]


class UiReport(Strict):
    """GET /api/v1/jobs/{job_id}/report — UI-only projection (never the jury object)."""
    job_id: str
    status: Literal["processing", "done", "failed"]
    stage: Stage
    elapsed_sec: float
    progress: StageProgress | None = None
    mode: RunMode = "production"
    verdict: Literal["no_violation", "violation"] | None = None
    coverage_notice_ru: str | None = None
    events: list[UiEvent] = Field(default_factory=list)
    media_available: bool = False
    error: ErrorInfo | None = None
