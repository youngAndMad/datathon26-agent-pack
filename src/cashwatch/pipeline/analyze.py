"""analyze_job: bounded, deadline-aware pipeline.

neutral perception -> tri-state predicates -> curated rules -> real evidence -> validated result.

Stages: probing -> sampling -> observing -> verifying -> rendering_evidence -> committing.
Every mandatory step either completes or raises PipelineError; there is no path that turns a
technical failure into ``no_violation``.
"""

from __future__ import annotations

import json
import logging
import os
import statistics
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageStat

from cashwatch.deadline import Deadline, parse_utc, utc_now
from cashwatch.dto import (
    CONTRACT_VERSION,
    AcceptedEvent,
    AnalysisReport,
    CandidateEvent,
    FrameManifest,
    FrameRef,
    JobInput,
    ObservationBatch,
    ObservationRequest,
    ReportEvent,
    ReviewNote,
    RunMode,
    RunVersions,
    Stage,
    StageProgress,
    VideoMetadata,
    ts,
)
from cashwatch.errors import PipelineError
from cashwatch.pipeline.planner import CoverageLedger, build_plan, proposal_windows, refinement_times, select_tier
from cashwatch.pipeline.validate import project_jury_result, validate_result
from cashwatch.ports import MediaPort, PerceptionPort, ReportingPort, RuleRegistry, RulesPort

log = logging.getLogger("cashwatch.pipeline")


class LeaseLost(Exception):
    """The worker lost its lease; it must stop and must not publish anything."""


@dataclass
class PipelineContext:
    media: MediaPort
    perception: PerceptionPort
    registry: RuleRegistry
    rules_factory: Callable[[RuleRegistry], RulesPort]
    reporting: ReportingPort
    profile: dict
    mode: RunMode
    limits: dict  # max_duration_sec, max_pixel_area
    code_version: str = "unknown"
    config_version: str = "unknown"
    # progress(stage, progress) -> False when the lease is lost
    progress: Callable[[Stage, StageProgress | None], bool] = lambda s, p: True


@dataclass
class _Run:
    timings: dict[str, int] = field(default_factory=dict)
    stages: list[Stage] = field(default_factory=list)
    notes: list[ReviewNote] = field(default_factory=list)
    limitations_ru: list[str] = field(default_factory=list)
    prompt_hashes: dict[str, str] = field(default_factory=dict)


def _stage(ctx: PipelineContext, run: _Run, stage: Stage, progress: StageProgress | None = None) -> None:
    if not ctx.progress(stage, progress):
        raise LeaseLost(stage)


def _timed(run: _Run, name: str, t0: float) -> None:
    run.timings[name] = run.timings.get(name, 0) + int((time.monotonic() - t0) * 1000)


def check_limits(meta: VideoMetadata, limits: dict) -> None:
    if meta.duration_sec <= 0 or meta.last_frame_sec <= meta.first_frame_sec and meta.duration_sec <= 0:
        raise PipelineError("input_unusable", "zero-length presentation")
    if meta.duration_sec > float(limits["max_duration_sec"]) + 0.5:
        raise PipelineError("limits_exceeded", "duration")
    if meta.width * meta.height > int(limits["max_pixel_area"]):
        raise PipelineError("limits_exceeded", "pixel area")


def check_usable(manifest: FrameManifest, media: MediaPort, work_dir: Path) -> list[str]:
    """Whole-clip usability. Fully blank/uniform footage is a failure; local issues are notes."""
    flat = 0
    for f in manifest.frames:
        with Image.open(media.resolve_frame(f, work_dir)) as im:
            stat = ImageStat.Stat(im.convert("L"))
            if stat.stddev[0] < 2.0:
                flat += 1
    if manifest.frames and flat == len(manifest.frames):
        raise PipelineError("input_unusable", "all sampled frames are uniform")
    if flat:
        return [f"Часть кадров ({flat} из {len(manifest.frames)}) почти однородна (засветка/перекрытие)"]
    return []


def analyze_job(
    job: JobInput, source: Path, work_dir: Path, out_dir: Path, ctx: PipelineContext, deadline: Deadline,
) -> AnalysisReport:
    run = _Run()
    run.stages.append("receiving")  # upload persisted and job accepted before the worker claims it
    t_start = time.monotonic()
    sampling = ctx.profile["sampling"]
    budget = ctx.profile.get("budget_sec", {})
    ev_cfg = ctx.profile["evidence"]
    evidence_reserve = float(budget.get("evidence", 15))

    # ---------------------------------------------------------------- probing
    _stage(ctx, run, "probing")
    t0 = time.monotonic()
    deadline.check("probing")
    meta = ctx.media.probe_video(source, timeout_sec=min(float(budget.get("probe", 20)), max(1.0, deadline.remaining_sec())))
    if meta.source_sha256 != job.source_sha256:
        raise PipelineError("internal_error", "source hash differs from accepted upload")
    check_limits(meta, ctx.limits)
    _timed(run, "probing", t0)
    run.stages.append("probing")
    _, sampling = select_tier(meta, ctx.profile)  # prevalidated tier fixed for the whole job

    # ---------------------------------------------------------------- sampling
    _stage(ctx, run, "sampling")
    t0 = time.monotonic()
    # Mandatory uniform coverage first (one decode pass; the media layer computes the motion
    # signal in the same pass and caches it), then optional motion proposals on top.
    plan = build_plan(meta, ctx.profile)
    manifest = ctx.media.sample_video(source, meta, plan, work_dir, deadline=deadline)
    _validate_manifest(manifest, plan, meta)
    try:
        motion = ctx.media.motion_profile(source, meta, fps=float(sampling["motion_proposal_fps"]), deadline=deadline)
        extra = proposal_windows(motion, meta, sampling, top_k=int(sampling.get("motion_top_k", 4)))
    except PipelineError as e:
        if e.code == "deadline_exceeded":
            raise
        extra = []
        run.limitations_ru.append("Сигнал движения недоступен; использовано только равномерное покрытие")
    if extra:
        manifest = _add_windows(manifest, extra, ctx.media, source, meta, work_dir,
                                int(sampling["model_long_side_px"]), deadline)
        plan = build_plan(meta, ctx.profile, extra)
    run.limitations_ru += check_usable(manifest, ctx.media, work_dir)
    ledger = CoverageLedger(profile_id=plan.profile_id, required=list(plan.mandatory_window_ids),
                            proposals=[w.window_id for w in plan.windows if not w.mandatory])
    _timed(run, "sampling", t0)
    run.stages.append("sampling")

    # ---------------------------------------------------------------- observing
    frames_by_id: dict[str, FrameRef] = {f.frame_id: f for f in manifest.frames}
    cues = ctx.registry.cue_predicates()
    batches: list[ObservationBatch] = []
    win_times: list[float] = []
    windows = [w for w in plan.windows if w.mandatory] + [w for w in plan.windows if not w.mandatory]
    total = len(plan.mandatory_window_ids)
    t0 = time.monotonic()
    _stage(ctx, run, "observing", StageProgress(completed=0, total=total, unit="windows"))
    for w in windows:
        est = (statistics.median(win_times) if win_times else 10.0) * 1.3
        if not w.mandatory and (deadline.soft_expired() or deadline.remaining_sec() < est + 120):
            run.limitations_ru.append("Дополнительные окна по движению пропущены из-за бюджета времени")
            break
        deadline.require_budget(f"observing {w.window_id}", est + evidence_reserve)
        req = ObservationRequest(
            job_id=job.job_id, window_id=w.window_id, start_sec=w.start_sec, end_sec=w.end_sec,
            frames=[frames_by_id[i] for i in manifest.window_frames[w.window_id]],
            task_version="observe-v1", cue_predicates=cues,
            max_output_tokens=int(sampling["observe_max_new_tokens"]))
        tw = time.monotonic()
        try:
            batch = ctx.perception.observe(req, work_dir, deadline)
        except PipelineError as e:
            ledger.fail(w.window_id)
            if w.mandatory or e.code in ("gpu_oom", "deadline_exceeded", "model_unavailable"):
                raise
            run.limitations_ru.append("Одно дополнительное окно не проанализировано (ошибка модели)")
            continue
        win_times.append(time.monotonic() - tw)
        _check_batch(batch, req)
        batches.append(batch)
        run.prompt_hashes["observe"] = batch.prompt_sha256
        ledger.complete(w.window_id)
        done = len([x for x in ledger.completed if x in ledger.required])
        _stage(ctx, run, "observing", StageProgress(completed=done, total=total, unit="windows"))
        if any(o.visibility == "unusable" for o in batch.observations) or batch.limitations:
            ledger.limitations.extend(batch.limitations[:3])
    if not ledger.required_complete:
        raise PipelineError("model_output_invalid", "mandatory coverage incomplete")
    _timed(run, "observing", t0)
    run.stages.append("observing")

    # ---------------------------------------------------------------- verifying
    t0 = time.monotonic()
    rules = ctx.rules_factory(ctx.registry)
    candidates = sorted(rules.propose(batches, manifest), key=lambda c: (c.start_sec, c.candidate_id))
    _stage(ctx, run, "verifying", StageProgress(completed=0, total=len(candidates), unit="candidates"))
    decided: list[CandidateEvent] = []
    unverified: list[CandidateEvent] = []
    all_frames: dict[str, FrameRef] = dict(frames_by_id)
    ver_times: list[float] = []
    for i, cand in enumerate(candidates):
        est = (statistics.median(ver_times) if ver_times else 15.0) * 1.3
        if deadline.remaining_sec() < est + evidence_reserve:
            unverified.extend(candidates[i:])
            break
        times = refinement_times(cand.start_sec, cand.end_sec, sampling, meta)
        rframes = ctx.media.frames_for_times(
            source, meta, times, work_dir, long_side_px=int(sampling["model_long_side_px"]), deadline=deadline)
        for f in rframes:
            all_frames.setdefault(f.frame_id, f)
        vreq = rules.build_verification(cand, rframes, job_id=job.job_id,
                                        max_output_tokens=int(sampling["verify_max_new_tokens"]))
        tv = time.monotonic()
        try:
            vres = ctx.perception.verify(vreq, work_dir, deadline)
        except PipelineError as e:
            if e.code in ("gpu_oom", "deadline_exceeded", "model_unavailable"):
                raise
            unverified.append(cand)
            continue
        ver_times.append(time.monotonic() - tv)
        run.prompt_hashes["verify"] = vres.prompt_sha256
        decided.append(rules.decide(cand, vres, rframes, manifest))
        ledger.refined.append(cand.candidate_id)
        _stage(ctx, run, "verifying", StageProgress(completed=i + 1, total=len(candidates), unit="candidates"))
    accepted = [c for c in decided if c.review_state == "accepted"]
    if unverified:
        if not accepted:
            # Verification of a proposed candidate is a required legal step; skipping it must
            # not produce a clean verdict.
            raise PipelineError("deadline_exceeded" if deadline.remaining_sec() < evidence_reserve + 5
                                else "model_output_invalid", f"{len(unverified)} candidates unverified")
        run.limitations_ru.append(
            f"{len(unverified)} кандидат(а) не удалось проверить; возможны дополнительные события")
        for c in unverified:
            run.notes.append(ReviewNote(note_id=f"n-{c.candidate_id}"[:128], start_sec=c.start_sec,
                                        end_sec=c.end_sec, kind="unresolved_candidate",
                                        text_ru="Кандидат не проверен (ограничение времени/модели)"))
    events: list[AcceptedEvent] = rules.merge(accepted, list(all_frames.values())) if accepted else []
    run.notes.extend(rules.notes())
    _timed(run, "verifying", t0)
    run.stages.append("verifying")

    # ---------------------------------------------------------------- evidence
    _stage(ctx, run, "rendering_evidence", StageProgress(completed=0, total=len(events), unit="events"))
    t0 = time.monotonic()
    deadline.check("rendering_evidence")
    report_events = _render_evidence(events, source, meta, out_dir, all_frames, ctx, ev_cfg, deadline)
    _timed(run, "rendering_evidence", t0)
    run.stages.append("rendering_evidence")

    # ---------------------------------------------------------------- committing (validate + write)
    _stage(ctx, run, "committing")
    t0 = time.monotonic()
    jury = project_jury_result(job.job_id, report_events, out_dir)
    coverage = ledger.to_dto(manifest.coverage_first_sec, manifest.coverage_last_sec)
    accepted_at = parse_utc(job.request_started_at)
    report = AnalysisReport(
        job_id=job.job_id, status="succeeded", mode=ctx.mode, source=meta,
        versions=RunVersions(
            contract_version=CONTRACT_VERSION, code_version=ctx.code_version,
            config_version=ctx.config_version, profile_id=plan.profile_id,
            registry_version=ctx.registry.version, model=ctx.perception.info(),
            prompt_sha256=run.prompt_hashes),
        stage_timings_ms=run.timings,
        upload_to_done_ms=int((utc_now() - accepted_at).total_seconds() * 1000),
        processing_ms=int((time.monotonic() - t_start) * 1000),
        coverage=coverage,
        completed_stages=list(run.stages),
        visual_limitations_ru=_dedup(run.limitations_ru)[:20],
        coverage_notice_ru=ctx.reporting.coverage_notice_ru(
            jury.verdict, _dedup(run.limitations_ru + coverage.visual_limitations)[:5]),
        events=report_events,
        review_notes=run.notes[:200],
        jury_result=jury,
    )
    validate_result(report, out_dir, ctx.registry)
    deadline.check("committing")
    write_outputs(report, out_dir)
    _timed(run, "committing", t0)
    return report


def _dedup(items: list[str]) -> list[str]:
    seen: set[str] = set()
    return [x for x in items if not (x in seen or seen.add(x))]


def _validate_manifest(manifest: FrameManifest, plan, meta: VideoMetadata) -> None:
    ids = {f.frame_id for f in manifest.frames}
    for w in plan.windows:
        fids = manifest.window_frames.get(w.window_id)
        if not fids or any(i not in ids for i in fids):
            raise PipelineError("decode_failed", f"window {w.window_id} has no resolvable frames")
    for f in manifest.frames:
        if f.source_sha256 != meta.source_sha256 or not (0 <= f.relative_sec <= meta.duration_sec + 1e-6):
            raise PipelineError("timeline_unsupported", "frame outside source timeline")
    first = min(f.relative_sec for f in manifest.frames)
    last = max(f.relative_sec for f in manifest.frames)
    # First/last playable frames must be covered (within one second of the clip ends).
    if first - meta.first_frame_sec > 1.0 or meta.last_frame_sec - last > 1.0:
        raise PipelineError("internal_error", "sampling does not cover clip endpoints")


def _add_windows(manifest: FrameManifest, windows, media: MediaPort, source: Path, meta: VideoMetadata,
                 work_dir: Path, long_side: int, deadline: Deadline) -> FrameManifest:
    frames = {f.frame_id: f for f in manifest.frames}
    wf = dict(manifest.window_frames)
    for w in windows:
        got = media.frames_for_times(source, meta, list(w.requested_sample_times), work_dir,
                                     long_side_px=long_side, deadline=deadline)
        for f in got:
            frames.setdefault(f.frame_id, f)
        wf[w.window_id] = [f.frame_id for f in got]
    ordered = sorted(frames.values(), key=lambda f: f.relative_sec)
    return manifest.model_copy(update={"frames": ordered, "window_frames": wf})


def _check_batch(batch: ObservationBatch, req: ObservationRequest) -> None:
    allowed = {f.frame_id for f in req.frames}
    cue_ids = {c.predicate_id for c in req.cue_predicates}
    if batch.window_id != req.window_id:
        raise PipelineError("model_output_invalid", "window id mismatch")
    for o in batch.observations:
        if not set(o.frame_ids) <= allowed:
            raise PipelineError("model_output_invalid", "observation references unknown frame")
    for a in batch.cue_assessments:
        if a.predicate_id not in cue_ids or not set(a.supporting_frame_ids + a.contradicting_frame_ids) <= allowed:
            raise PipelineError("model_output_invalid", "cue assessment references unknown id")


def _render_evidence(
    events: list[AcceptedEvent], source: Path, meta: VideoMetadata, out_dir: Path,
    frames: dict[str, FrameRef], ctx: PipelineContext, ev_cfg: dict, deadline: Deadline,
) -> list[ReportEvent]:
    if not events:
        return []
    per_event = int(ev_cfg.get("max_frames_per_event", 3))
    long_side = int(ev_cfg["long_side_px"])
    quality = int(ev_cfg["jpeg_quality"])
    # No event truncation: with many events reduce redundant frames and size first.
    if len(events) > 10:
        per_event, long_side, quality = 1, min(long_side, 960), min(quality, 85)
    chosen: dict[str, list[str]] = {}
    for ev in events:
        ids = [ev.anchor_frame_id] + [i for i in ev.evidence_frame_ids if i != ev.anchor_frame_id]
        chosen[ev.event_id] = ids[:per_event]
    need = sorted({i for ids in chosen.values() for i in ids}, key=lambda i: frames[i].relative_sec)
    missing = [i for i in need if i not in frames]
    if missing:
        raise PipelineError("evidence_invalid", "evidence frame id does not resolve")
    rendered = ctx.media.extract_evidence(
        source, meta, [frames[i] for i in need], out_dir, long_side_px=long_side, jpeg_quality=quality,
        deadline=deadline)
    by_frame = {e.frame_id: e for e in rendered}
    out: list[ReportEvent] = []
    for ev in events:
        evid = []
        for fid in chosen[ev.event_id]:
            e = by_frame.get(fid)
            if e is None or e.pts != frames[fid].pts or e.source_sha256 != meta.source_sha256:
                raise PipelineError("evidence_invalid", "evidence does not match selected frame")
            evid.append(e)
        anchor_sec = ts(evid[0].timestamp_sec)
        ev2 = ev.model_copy(update={"evidence_frame_ids": [e.frame_id for e in evid]})
        out.append(ReportEvent(event=ev2, evidence=evid, description_ru=ctx.reporting.describe(ev2, anchor_sec)))
    out.sort(key=lambda r: (r.evidence[0].timestamp_sec, r.event.event_id))
    return out


def _write_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_suffix(path.suffix + ".part")
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def write_outputs(report: AnalysisReport, out_dir: Path) -> None:
    assert report.jury_result is not None
    jury_bytes = json.dumps(json.loads(report.jury_result.model_dump_json()), ensure_ascii=False,
                            separators=(",", ":")).encode("utf-8")
    _write_atomic(out_dir / "jury.json", jury_bytes)
    _write_atomic(out_dir / "report.json", report.model_dump_json().encode("utf-8"))
    ev_dir = out_dir / "evidence"
    if ev_dir.exists():
        for p in ev_dir.iterdir():
            with open(p, "rb") as fh:
                os.fsync(fh.fileno())
    fd = os.open(out_dir, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
