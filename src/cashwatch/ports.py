"""Frozen typed boundaries between lanes (G0). Implementations live in owned packages:

media      -> cashwatch.media      (agent B)   MediaPort
inference  -> cashwatch.inference  (agent C)   PerceptionPort
rules      -> cashwatch.rules      (agent D)   RulesPort / RuleRegistry
reporting  -> cashwatch.reporting  (agent D)   ReportingPort
storage    -> cashwatch.storage    (agent A)   JobStorePort
pipeline   -> cashwatch.pipeline   (orchestrator) consumes all of the above

Factories each package must expose (exact names, used by pipeline wiring):
  cashwatch.media.create_media() -> MediaPort
  cashwatch.inference.create_perception(settings) -> PerceptionPort
  cashwatch.rules.load_registry(path, *, require_approved: bool) -> RuleRegistry
  cashwatch.rules.create_rules(registry) -> RulesPort
  cashwatch.reporting.create_reporting(registry) -> ReportingPort
  cashwatch.storage.create_store(settings) -> JobStorePort
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from cashwatch.deadline import Deadline
from cashwatch.dto import (
    AcceptedEvent,
    CandidateEvent,
    ErrorInfo,
    EvidenceFrame,
    FrameManifest,
    FrameRef,
    JobInput,
    ModelInfo,
    MotionSample,
    ObservationBatch,
    ObservationRequest,
    PredicateDefinition,
    ReviewNote,
    RuleRef,
    SamplingPlan,
    Stage,
    StageProgress,
    VerificationRequest,
    VerificationResult,
    VideoMetadata,
)


@runtime_checkable
class MediaPort(Protocol):
    def probe_video(self, local_path: Path, *, timeout_sec: float) -> VideoMetadata:
        """Raise cashwatch.errors.PipelineError(code in invalid_media/unsupported_container/
        limits_exceeded/timeline_unsupported/decode_failed)."""

    def sample_video(
        self, local_path: Path, meta: VideoMetadata, plan: SamplingPlan, work_dir: Path,
        *, deadline: Deadline,
    ) -> FrameManifest:
        """Decode once, select the first presented frame at/after each requested time (last
        frame for times beyond the end), write aspect-preserving model thumbnails (long side
        plan.model_long_side_px) under work_dir, return actual PTS-derived times. A frame
        shared by windows appears once in frames."""

    def motion_profile(
        self, local_path: Path, meta: VideoMetadata, *, fps: float, deadline: Deadline,
    ) -> list[MotionSample]:
        """Low-resolution dense frame-difference signal (attention hint only)."""

    def frames_for_times(
        self, local_path: Path, meta: VideoMetadata, times_sec: list[float], work_dir: Path,
        *, long_side_px: int, deadline: Deadline,
    ) -> list[FrameRef]:
        """Refinement sampling for candidate intervals; same selection rule as sample_video.
        Returns de-duplicated FrameRefs in presentation order."""

    def extract_evidence(
        self, local_path: Path, meta: VideoMetadata, frame_refs: list[FrameRef], out_dir: Path,
        *, long_side_px: int, jpeg_quality: int, deadline: Deadline,
    ) -> list[EvidenceFrame]:
        """Re-decode the exact source frames (same PTS) at evidence resolution, apply display
        rotation, encode real JPEGs under out_dir; jpeg_path is relative to out_dir."""

    def resolve_frame(self, frame: FrameRef, work_dir: Path) -> Path:
        """Containment-checked absolute path of a model thumbnail."""

    def make_preview(
        self, local_path: Path, meta: VideoMetadata, out_path: Path, *, deadline: Deadline,
        max_sec: float,
    ) -> dict | None:
        """Optional browser preview: H.264 MP4, no audio, display rotation applied, preview
        t=0 == presentation origin. Returns {"offset_sec": 0.0, "codec": ...} or None if it
        could not be produced within max_sec. Never alters the original evidence authority."""


@runtime_checkable
class PerceptionPort(Protocol):
    def info(self) -> ModelInfo: ...

    def ready(self) -> bool:
        """Model loaded and warm-up succeeded."""

    def warmup(self, *, timeout_sec: float) -> None: ...

    def observe(self, request: ObservationRequest, work_dir: Path, deadline: Deadline) -> ObservationBatch:
        """Raise PipelineError(model_output_invalid | gpu_oom | model_unavailable |
        deadline_exceeded). Must validate every frame/predicate id against the request; at most
        one bounded schema-repair attempt."""

    def verify(self, request: VerificationRequest, work_dir: Path, deadline: Deadline) -> VerificationResult: ...

    def close(self) -> None: ...


@runtime_checkable
class RuleRegistry(Protocol):
    version: str

    def approved_rule_ids(self) -> list[str]: ...

    def rule_ref(self, rule_id: str) -> RuleRef:
        """KeyError for unknown or non-approved ids."""

    def cue_predicates(self) -> list[PredicateDefinition]:
        """Neutral coarse-pass cues for all approved rules (may be empty)."""

    def health_errors(self) -> list[str]:
        """Empty when the registry is valid (approved rules have snapshot hash, URL etc.)."""


@runtime_checkable
class RulesPort(Protocol):
    def propose(self, batches: list[ObservationBatch], manifest: FrameManifest) -> list[CandidateEvent]:
        """Candidates only for approved rules; everything else -> review notes (see notes())."""

    def build_verification(
        self, candidate: CandidateEvent, frames: list[FrameRef], *, job_id: str, max_output_tokens: int,
    ) -> VerificationRequest: ...

    def decide(
        self, candidate: CandidateEvent, result: VerificationResult, frames: list[FrameRef],
        manifest: FrameManifest,
    ) -> CandidateEvent:
        """Apply deterministic gates; returns candidate with review_state accepted/rejected/
        unresolved, anchor_frame_id and 1-3 evidence_frame_ids (anchor first) when accepted."""

    def merge(self, accepted: list[CandidateEvent], frames: list[FrameRef]) -> list[AcceptedEvent]:
        """Dedup same action across windows; keep distinct events; chronological order."""

    def notes(self) -> list[ReviewNote]:
        """Review notes accumulated during propose/decide for this job."""


@runtime_checkable
class ReportingPort(Protocol):
    def describe(self, event: AcceptedEvent, anchor_sec: float) -> str:
        """Deterministic Russian description from template + registry metadata (title, clause,
        official URL appended)."""

    def coverage_notice_ru(self, verdict: str, limitations: list[str]) -> str: ...


class ClaimedJob(Protocol):
    job: JobInput
    work_dir: Path  # job-private absolute directory (contains source + scratch)


@runtime_checkable
class JobStorePort(Protocol):
    def claim_job(self, worker_id: str) -> ClaimedJob | None: ...

    def heartbeat(self, job_id: str, generation: int, *, stage: Stage | None = None,
                  progress: StageProgress | None = None) -> bool:
        """Extend lease; False when the lease was lost (caller must stop and not commit)."""

    def source_path(self, job: JobInput) -> Path: ...

    def output_dir(self, job: JobInput) -> Path:
        """Generation-specific staging directory for result files (not yet visible)."""

    def commit_result(self, job_id: str, generation: int, validated_result_dir: Path) -> bool:
        """Atomic CAS running(gen)->succeeded with result_path. False if fenced out."""

    def fail_job(self, job_id: str, generation: int, error: ErrorInfo) -> bool: ...

    def recover_expired_jobs(self, now_utc: str) -> int: ...

    def worker_heartbeat(self, worker_id: str, *, ready: bool, info: dict) -> None:
        """Worker-level liveness used by /readyz."""
