"""PerceptionPort implementation (agent C): typed observe/verify over a supervised generator.

Flow per call: deadline/readiness check -> request validation (unique ordered frame ids,
predicate ids, frame count and visual-token budget) -> containment-checked, hash-verified
frame bytes -> versioned prompt -> bounded deterministic generation with a hard timeout ->
strict validation -> at most ONE text-only repair call -> typed DTO. Interval times are
resolved from request FrameRefs only.
"""

from __future__ import annotations

import hashlib
import logging
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
from cashwatch.inference.validation import OutputInvalid, parse_cues, parse_observation, parse_verification
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
    frame_label_mode: str = "index"


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
            # Call 1: neutral observations only (no cue questions; small models answer few cues
            # reliably inside the large JSON schema).
            obs_req = request.model_copy(update={"cue_predicates": []})
            prompt = P.build_observe_prompt(obs_req, mode)
            labels = P.frame_labels(frames, mode) if mode == "index" else None
            ctx = {"kind": "observe", "window_id": request.window_id,
                   "frame_ids": [f.frame_id for f in frames], "predicate_ids": [], "label_mode": mode}

            def parse(text: str):
                return parse_observation(text, window_id=request.window_id, frames=frames,
                                         predicate_ids=[], labels=labels)

            observations, limitations, status, in_tok, out_tok = [], [], "ok", 0, 0
            try:
                parsed, status, in_tok, out_tok = self._run(
                    "observe", prompt, images, request.max_output_tokens, self.cfg.observe_call_cap_sec,
                    deadline, parse, ctx, frames, [], allow_repair=not pred_ids)
                observations, limitations = parsed.observations, parsed.limitations
            except PipelineError as e:
                # Neutral observations are diagnostic; rule cues are the detection input. Without
                # cues a failed observation stays fatal (mandatory coverage).
                if e.code != "model_output_invalid" or not pred_ids:
                    raise
                status = "repaired"
                limitations = ["neutral observation output invalid for this window"]
            cues = []
            if pred_ids:
                # Call 2: compact numbered yes/no cue questions.
                cprompt = P.build_cue_prompt(request)
                creq = GenRequest(system=cprompt.system, segments=cprompt.segments, images=images,
                                  max_tokens=16 + 24 * len(pred_ids), max_long_side=self.cfg.max_long_side_px,
                                  max_pixels=self.cfg.max_pixels,
                                  context=dict(ctx, kind="cue", predicate_ids=pred_ids))
                cres = self._generate("cue", creq, self.cfg.observe_call_cap_sec, deadline)
                cues = parse_cues(cres.text, frames=frames, predicate_ids=pred_ids,
                                  yes_without_frames_is_window=True)
                in_tok += cres.prompt_tokens or 0
                out_tok += cres.output_tokens or 0
            return ObservationBatch(
                window_id=request.window_id, observations=observations,
                cue_assessments=cues, parse_status=status,
                limitations=limitations, elapsed_ms=int((time.monotonic() - t0) * 1000),
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
            # Compact numbered statements (small models fail large JSON schemas, see D013).
            prompt = P.build_verify_compact_prompt(request)
            ctx = {"kind": "verify", "candidate_id": request.candidate_id,
                   "frame_ids": [f.frame_id for f in frames], "predicate_ids": pred_ids, "label_mode": "index"}
            req = GenRequest(system=prompt.system, segments=prompt.segments, images=images,
                             max_tokens=min(int(request.max_output_tokens), 24 + 40 * len(pred_ids)),
                             max_long_side=self.cfg.max_long_side_px, max_pixels=self.cfg.max_pixels, context=ctx)
            res = self._generate("verify", req, self.cfg.verify_call_cap_sec, deadline)
            assessments, status = self._read_verify(res.text, request, frames, pred_ids)
            if assessments is None:
                # One bounded repair (text only): reformat the previous answer, add no facts.
                n = len(pred_ids)
                rreq = GenRequest(
                    system=("You fix the format of an earlier answer about video frames. Do not add facts. "
                            "Rewrite it as one line per statement: Q<number>: yes <frames>; no <frames> "
                            "or Q<number>: unclear. Frames are integers 1 to " + str(len(frames)) + "."),
                    segments=[("text", f"There are {n} statements (Q1 to Q{n}).\nPrevious answer:\n"
                                       f"{res.text[:2000]}\nRewrite it now.")],
                    images=[], max_tokens=24 + 40 * n,
                    context=dict(ctx, repair=True, error="no statement answered"))
                rres = self._generate("verify-repair", rreq, self.cfg.repair_call_cap_sec, deadline)
                assessments, _ = self._read_verify(rres.text, request, frames, pred_ids)
                status = "repaired"
                if assessments is None:
                    raise PipelineError("model_output_invalid", "verify: no statement answered")
            # Exclusions (compliant alternatives) asked one at a time: inside a list a small model
            # tends to skip them or echo the other answers.
            for i, p in enumerate(request.predicates):
                if ".excl_" not in p.predicate_id or deadline.remaining_sec() < 20:
                    continue
                single = request.model_copy(update={"predicates": [p]})
                sp = P.build_verify_compact_prompt(single)
                sreq = GenRequest(system=sp.system, segments=sp.segments, images=images, max_tokens=48,
                                  max_long_side=self.cfg.max_long_side_px, max_pixels=self.cfg.max_pixels,
                                  context=dict(ctx, kind="verify-exclusion", predicate_ids=[p.predicate_id]))
                sres = self._generate("verify", sreq, self.cfg.verify_call_cap_sec, deadline)
                got = parse_cues(sres.text, frames=frames, predicate_ids=[p.predicate_id])[0]
                if got.state != "unknown":
                    assessments[i] = got
            return VerificationResult(
                candidate_id=request.candidate_id, assessments=assessments, plausible_alternatives=[],
                # Completeness of an opportunity interval cannot be established by this format;
                # stays False so omission rules (requires_complete_interval) remain unresolved.
                interval_complete=False, visibility_sufficient=True, schema_valid=True,  # visibility via rule predicates
                parse_status=status, limitations=[], elapsed_ms=int((time.monotonic() - t0) * 1000),
                model_version=self.model_version, prompt_sha256=prompt.prompt_sha256,
            )

    def _read_verify(self, text: str, request: VerificationRequest, frames: list[FrameRef], pred_ids: list[str]):
        """Compact lines first; legacy JSON verification objects are still accepted."""
        a = parse_cues(text, frames=frames, predicate_ids=pred_ids)
        if any(x.state != "unknown" for x in a):
            return a, "ok"
        try:
            parsed = parse_verification(text, candidate_id=request.candidate_id, frames=frames,
                                        predicate_ids=pred_ids, labels=None)
        except OutputInvalid:
            return None, "ok"
        by_id = {x.predicate_id: x for x in parsed.assessments}
        return [by_id.get(p, a[i]) for i, p in enumerate(pred_ids)], "ok"

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
            # Operational metadata only: never log prompts, frames or model text.
            logging.getLogger("cashwatch.inference").warning(
                "generation_failure kind=%s stage=%s call_limit_sec=%.1f",
                e.kind, kind, timeout,
            )
            raise self._map(e, deadline_bound=remaining < cap, where=kind) from None
        self.stats.calls.append(CallStats(kind, res.prompt_tokens, res.output_tokens, list(res.visual_tokens),
                                          res.visual_tokens_actual_total, list(res.image_sizes),
                                          res.gen_ms, res.peak_memory_bytes, res.finish_reason,
                                          res.text if self.cfg.keep_raw_text else None,
                                          res.prompt_tps, res.generation_tps))
        return res

    def _run(self, kind, prompt, images, max_tokens, cap, deadline, parse, ctx, frames, pred_ids,
             *, allow_repair=True):
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
            if not allow_repair:
                # With required compact cues following, malformed neutral prose
                # is diagnostic only. Preserve time for that detection input.
                raise PipelineError("model_output_invalid", f"{kind}: {error[:200]}") from None
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
