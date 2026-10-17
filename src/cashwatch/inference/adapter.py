"""PerceptionPort implementation (agent C): typed observe/verify over a supervised generator.

Flow per call: deadline/readiness check -> request validation (unique ordered frame ids,
predicate ids, frame count and visual-token budget) -> containment-checked, hash-verified
frame bytes -> versioned prompt -> bounded deterministic generation with a hard timeout ->
strict validation -> at most ONE text-only repair call -> typed DTO. Interval times are
resolved from request FrameRefs only.
"""

from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from cashwatch.deadline import Deadline
from cashwatch.dto import (
    FrameRef,
    ModelInfo,
    ObservationBatch,
    ObservationRequest,
    VerificationRequest,
    VerificationResult,
)
from cashwatch.errors import PipelineError
from cashwatch.inference import prompts as P
from cashwatch.inference.protocol import GenRequest, GenResult
from cashwatch.inference.supervisor import GenCrashed, GenError, GenLoadFailed, GenOOM, GenTimeout
from cashwatch.inference.validation import OutputInvalid, parse_observation, parse_verification
from cashwatch.inference.vision_budget import image_tokens


class Generator(Protocol):
    load_ms: int | None
    load_info: dict[str, Any]

    def start(self, timeout_sec: float) -> int: ...
    def alive(self) -> bool: ...
    def loaded(self) -> bool: ...
    def generate(self, req: GenRequest, timeout_sec: float) -> GenResult: ...
    def kill(self) -> None: ...
    def close(self) -> None: ...


@dataclass(frozen=True)
class AdapterConfig:
    observe_call_cap_sec: float = 60.0
    verify_call_cap_sec: float = 60.0
    repair_call_cap_sec: float = 30.0
    repair_max_tokens: int = 384
    max_images_per_call: int = 32
    max_visual_tokens_per_call: int | None = 12288
    max_long_side_px: int | None = 640
    max_pixels: int | None = None
    window_time_tolerance_sec: float = 1.0
    warmup_max_tokens: int = 8
    # Debug for SYNTHETIC benchmarks only: keep raw model text in CallStats. Must stay False
    # for real media (model text about real scenes must not leave the local workspace/logs).
    keep_raw_text: bool = False
    # "id": frames labelled with their real frame ids; "index": per-call integers 1..N mapped
    # back by the application (fewer output tokens). Both are validated identically.
    frame_label_mode: str = "id"


@dataclass
class CallStats:
    kind: str
    prompt_tokens: int | None
    output_tokens: int | None
    visual_tokens: list[int]
    visual_tokens_actual_total: int | None
    image_sizes: list[tuple[int, int]]
    gen_ms: int
    peak_memory_bytes: int | None
    finish_reason: str | None
    raw_text: str | None = None
    prompt_tps: float | None = None
    generation_tps: float | None = None


@dataclass
class _Stats:
    calls: list[CallStats] = field(default_factory=list)


def _warmup_image() -> bytes:
    import io

    from PIL import Image, ImageDraw
    im = Image.new("RGB", (320, 180), (128, 128, 128))
    ImageDraw.Draw(im).rectangle((100, 50, 220, 130), outline=(255, 220, 0), width=4)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


class LocalPerception:
    """Resident perception adapter. Thread-safe (calls are serialized)."""

    def __init__(self, generator: Generator, *, adapter: str, model_id: str, revision: str,
                 local_path_label: str, is_mock: bool, config: AdapterConfig | None = None) -> None:
        self.gen = generator
        self.cfg = config or AdapterConfig()
        self._adapter = adapter
        self._model_id = model_id
        self._revision = revision
        self._label = local_path_label
        self._is_mock = is_mock
        self._ready = False
        self._lock = threading.RLock()
        self.stats = _Stats()
        self.last_warmup: dict[str, Any] = {}

    # ------------------------------------------------------------------ PerceptionPort
    def info(self) -> ModelInfo:
        return ModelInfo(adapter=self._adapter, model_id=self._model_id, revision=self._revision,
                         local_path_label=self._label, is_mock=self._is_mock,
                         warmed_up=self.ready(), device=str(self.gen.load_info.get("device", "unknown")))

    @property
    def model_version(self) -> str:
        return f"{self._adapter}:{self._model_id}@{self._revision}"

    def ready(self) -> bool:
        return self._ready and self.gen.loaded()

    def warmup(self, *, timeout_sec: float) -> None:
        """(Re)spawn the child if needed, load once, run one tiny generation. ready() becomes
        true only after this succeeds."""
        with self._lock:
            self._ready = False
            end = time.monotonic() + timeout_sec
            try:
                if not self.gen.loaded():
                    self.gen.start(timeout_sec)
                req = GenRequest(system="You describe images.",
                                 segments=[("text", "Frame w0 at 0.000s:\n"), ("image", 0),
                                           ("text", "\nReply with the single word ok.")],
                                 images=[_warmup_image()], max_tokens=self.cfg.warmup_max_tokens,
                                 max_long_side=self.cfg.max_long_side_px, max_pixels=self.cfg.max_pixels,
                                 context={"kind": "warmup"})
                t0 = time.monotonic()
                self.gen.generate(req, max(0.5, end - time.monotonic()))
                self.last_warmup = {"load_ms": self.gen.load_ms,
                                    "warmup_ms": int((time.monotonic() - t0) * 1000)}
            except GenError as e:
                self.gen.kill()
                raise self._map(e, deadline_bound=False, where="warmup") from None
            self._ready = True

    def observe(self, request: ObservationRequest, work_dir: Path, deadline: Deadline) -> ObservationBatch:
        with self._lock:
            t0 = time.monotonic()
            frames = list(request.frames)
            pred_ids = [p.predicate_id for p in request.cue_predicates]
            self._check_request(frames, pred_ids, window=(request.start_sec, request.end_sec))
            images = self._load_frames(frames, work_dir)
            mode = self.cfg.frame_label_mode
            prompt = P.build_observe_prompt(request, mode)
            labels = P.frame_labels(frames, mode) if mode == "index" else None
            ctx = {"kind": "observe", "window_id": request.window_id,
                   "frame_ids": [f.frame_id for f in frames], "predicate_ids": pred_ids, "label_mode": mode}

            def parse(text: str):
                return parse_observation(text, window_id=request.window_id, frames=frames,
                                         predicate_ids=pred_ids, labels=labels)

            parsed, status, in_tok, out_tok = self._run(
                "observe", prompt, images, request.max_output_tokens, self.cfg.observe_call_cap_sec,
                deadline, parse, ctx, frames, pred_ids)
            return ObservationBatch(
                window_id=request.window_id, observations=parsed.observations,
                cue_assessments=parsed.cue_assessments, parse_status=status,
                limitations=parsed.limitations, elapsed_ms=int((time.monotonic() - t0) * 1000),
                model_version=self.model_version, prompt_sha256=prompt.prompt_sha256,
                input_tokens=in_tok, output_tokens=out_tok,
            )

    def verify(self, request: VerificationRequest, work_dir: Path, deadline: Deadline) -> VerificationResult:
        with self._lock:
            t0 = time.monotonic()
            frames = list(request.frames)
            pred_ids = [p.predicate_id for p in request.predicates]
            if not pred_ids:
                raise PipelineError("internal_error", "verification request without predicates")
            if request.interval_end_sec < request.interval_start_sec:
                raise PipelineError("internal_error", "malformed candidate interval")
            self._check_request(frames, pred_ids, window=None)
            images = self._load_frames(frames, work_dir)
            mode = self.cfg.frame_label_mode
            prompt = P.build_verify_prompt(request, mode)
            labels = P.frame_labels(frames, mode) if mode == "index" else None
            ctx = {"kind": "verify", "candidate_id": request.candidate_id,
                   "frame_ids": [f.frame_id for f in frames], "predicate_ids": pred_ids, "label_mode": mode}

            def parse(text: str):
                return parse_verification(text, candidate_id=request.candidate_id, frames=frames,
                                          predicate_ids=pred_ids, labels=labels)

            parsed, status, _in, _out = self._run(
                "verify", prompt, images, request.max_output_tokens, self.cfg.verify_call_cap_sec,
                deadline, parse, ctx, frames, pred_ids)
            return VerificationResult(
                candidate_id=request.candidate_id, assessments=parsed.assessments,
                plausible_alternatives=parsed.plausible_alternatives,
                interval_complete=parsed.interval_complete,
                visibility_sufficient=parsed.visibility_sufficient, schema_valid=True,
                parse_status=status, limitations=parsed.limitations,
                elapsed_ms=int((time.monotonic() - t0) * 1000), model_version=self.model_version,
                prompt_sha256=prompt.prompt_sha256,
            )

    def close(self) -> None:
        with self._lock:
            self._ready = False
            self.gen.close()

    # ------------------------------------------------------------------ internals
    def _check_request(self, frames: list[FrameRef], pred_ids: list[str],
                       window: tuple[float, float] | None) -> None:
        if not frames:
            raise PipelineError("internal_error", "model call without frames")
        if len(frames) > self.cfg.max_images_per_call:
            raise PipelineError("internal_error",
                                f"{len(frames)} frames exceed max_images_per_call; split the window")
        ids = [f.frame_id for f in frames]
        if len(set(ids)) != len(ids):
            raise PipelineError("internal_error", "duplicate frame ids in request")
        if any(frames[i + 1].relative_sec < frames[i].relative_sec for i in range(len(frames) - 1)):
            raise PipelineError("internal_error", "frames not in presentation order")
        if len(set(pred_ids)) != len(pred_ids):
            raise PipelineError("internal_error", "duplicate predicate ids in request")
        if window is not None:
            start, end = window
            tol = self.cfg.window_time_tolerance_sec
            if end < start:
                raise PipelineError("internal_error", "malformed window interval")
            for f in frames:
                if f.relative_sec < start - tol or f.relative_sec > end + tol:
                    raise PipelineError("internal_error", "frame timestamp outside window interval")
        if self.cfg.max_visual_tokens_per_call:
            total = sum(image_tokens(f.width, f.height, max_long_side=self.cfg.max_long_side_px,
                                     max_pixels=self.cfg.max_pixels) for f in frames)
            if total > self.cfg.max_visual_tokens_per_call:
                raise PipelineError("internal_error",
                                    f"visual token budget exceeded ({total}); split the window")

    def _load_frames(self, frames: list[FrameRef], work_dir: Path) -> list[bytes]:
        root = Path(work_dir).resolve(strict=True)
        out: list[bytes] = []
        for f in frames:
            p = (root / f.local_path).resolve()
            if not p.is_relative_to(root):
                raise PipelineError("internal_error", "frame path escapes the job work dir")
            try:
                data = p.read_bytes()
            except OSError:
                raise PipelineError("internal_error", "model thumbnail missing/unreadable") from None
            if hashlib.sha256(data).hexdigest() != f.sha256:
                raise PipelineError("internal_error", "model thumbnail hash mismatch")
            out.append(data)
        return out

    def _map(self, e: GenError, *, deadline_bound: bool, where: str) -> PipelineError:
        self._ready = False
        if isinstance(e, GenTimeout):
            return PipelineError("deadline_exceeded" if deadline_bound else "model_unavailable",
                                 f"{where}: {e.detail}")
        if isinstance(e, GenOOM):
            return PipelineError("gpu_oom", f"{where}: accelerator out of memory")
        if isinstance(e, (GenCrashed, GenLoadFailed)):
            return PipelineError("model_unavailable", f"{where}: {e.detail}")
        return PipelineError("model_unavailable", f"{where}: generation failed: {e.detail[:200]}")

    def _generate(self, kind: str, req: GenRequest, cap: float, deadline: Deadline) -> GenResult:
        if not self.ready():
            raise PipelineError("model_unavailable", f"{kind}: model not ready (warmup required)")
        remaining = deadline.remaining_sec()
        if remaining <= 0:
            raise PipelineError("deadline_exceeded", f"deadline reached before {kind}")
        timeout = min(remaining, cap)
        try:
            res = self.gen.generate(req, timeout)
        except GenError as e:
            raise self._map(e, deadline_bound=remaining < cap, where=kind) from None
        self.stats.calls.append(CallStats(kind, res.prompt_tokens, res.output_tokens, list(res.visual_tokens),
                                          res.visual_tokens_actual_total, list(res.image_sizes),
                                          res.gen_ms, res.peak_memory_bytes, res.finish_reason,
                                          res.text if self.cfg.keep_raw_text else None,
                                          res.prompt_tps, res.generation_tps))
        return res

    def _run(self, kind, prompt, images, max_tokens, cap, deadline, parse, ctx, frames, pred_ids):
        req = GenRequest(system=prompt.system, segments=prompt.segments, images=images,
                         max_tokens=int(max_tokens), max_long_side=self.cfg.max_long_side_px,
                         max_pixels=self.cfg.max_pixels, context=ctx)
        res = self._generate(kind, req, cap, deadline)
        in_tok, out_tok = res.prompt_tokens, res.output_tokens or 0
        truncated = res.finish_reason == "length"
        try:
            return parse(res.text), "ok", in_tok, out_tok
        except OutputInvalid as first:
            error = str(first)
            if truncated:
                error = f"output truncated at the token limit. {error}"
        allowed = ([str(i + 1) for i in range(len(frames))] if self.cfg.frame_label_mode == "index"
                   else [f.frame_id for f in frames])
        rp = P.build_repair_prompt(kind=kind, error=error, previous=res.text,
                                   frame_ids=allowed, predicate_ids=pred_ids, truncated=truncated,
                                   index_mode=self.cfg.frame_label_mode == "index")
        rreq = GenRequest(system=rp.system, segments=rp.segments, images=[],
                          max_tokens=min(int(max_tokens), self.cfg.repair_max_tokens),
                          context=dict(ctx, repair=True, error=error))
        rres = self._generate(f"{kind}-repair", rreq, self.cfg.repair_call_cap_sec, deadline)
        out_tok += rres.output_tokens or 0
        try:
            return parse(rres.text), "repaired", in_tok, out_tok
        except OutputInvalid as second:
            raise PipelineError("model_output_invalid", f"{kind}: {str(second)[:200]}") from None
