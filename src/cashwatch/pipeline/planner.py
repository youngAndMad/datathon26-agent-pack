"""Deterministic sampling plans and coverage accounting.

Mandatory coarse windows cover the complete playable clip [first_frame_sec, last_frame_sec],
including the first and last intervals. Motion proposals are optional extras; they never
replace or gate the uniform coverage.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from cashwatch.errors import PipelineError
from cashwatch.dto import CandidateEvent, Coverage, FrameRef, MotionSample, SamplingPlan, SamplingWindow, VideoMetadata, ts


def _times(start: float, end: float, fps: float, max_frames: int) -> list[float]:
    """Uniform targets in [start, end] that always include both endpoints."""
    span = max(0.0, end - start)
    n = max(2, min(max_frames, int(math.ceil(span * fps - 1e-9)) + 1)) if span > 0 else 1
    if n == 1:
        return [ts(start)]
    step = span / (n - 1)
    out = sorted({ts(start + i * step) for i in range(n)})
    out[-1] = ts(end)
    return out


def coarse_windows(meta: VideoMetadata, sampling: dict) -> list[SamplingWindow]:
    win = float(sampling["window_sec"])
    step = win - float(sampling["overlap_sec"])
    if step <= 0:
        raise ValueError("overlap must be smaller than window")
    fps = float(sampling["uniform_fps"])
    max_frames = int(sampling["max_frames_per_observation"])
    t0, t1 = meta.first_frame_sec, meta.last_frame_sec
    starts: list[float] = []
    s = t0
    while True:
        starts.append(s)
        if s + win >= t1:
            break
        s += step
    windows = []
    for i, s in enumerate(starts):
        e = min(s + win, t1)
        if i == len(starts) - 1 and len(starts) > 1:
            # Align the final window to the clip end so the last interval keeps full context.
            s = max(t0, t1 - win)
            e = t1
        windows.append(SamplingWindow(
            window_id=f"w{i:03d}", kind="coarse", start_sec=ts(s), end_sec=ts(e),
            requested_sample_times=_times(s, e, fps, max_frames), mandatory=True))
    return windows


def proposal_windows(
    motion: list[MotionSample], meta: VideoMetadata, sampling: dict, *, top_k: int,
) -> list[SamplingWindow]:
    """Short denser windows around the strongest motion peaks (attention hint only)."""
    if not motion or top_k <= 0:
        return []
    scores = [m.score for m in motion]
    mean = sum(scores) / len(scores)
    var = sum((x - mean) ** 2 for x in scores) / len(scores)
    thresh = mean + 1.5 * math.sqrt(var)
    peaks: list[MotionSample] = []
    for i, m in enumerate(motion):
        left = motion[i - 1].score if i > 0 else -1.0
        right = motion[i + 1].score if i + 1 < len(motion) else -1.0
        if m.score >= thresh and m.score >= left and m.score >= right and m.score > 0:
            peaks.append(m)
    peaks.sort(key=lambda m: m.score, reverse=True)
    radius = float(sampling["refinement_radius_sec"])
    fps = float(sampling["refinement_fps"]) / 2.0
    max_frames = int(sampling["max_frames_per_observation"])
    chosen: list[MotionSample] = []
    for p in peaks:
        if all(abs(p.relative_sec - c.relative_sec) > 2 * radius for c in chosen):
            chosen.append(p)
        if len(chosen) >= top_k:
            break
    out = []
    for i, p in enumerate(sorted(chosen, key=lambda m: m.relative_sec)):
        s = max(meta.first_frame_sec, p.relative_sec - radius)
        e = min(meta.last_frame_sec, p.relative_sec + radius)
        if e <= s:
            continue
        out.append(SamplingWindow(
            window_id=f"m{i:03d}", kind="proposal", start_sec=ts(s), end_sec=ts(e),
            requested_sample_times=_times(s, e, fps, max_frames), mandatory=False))
    return out


def count_coarse_windows(duration: float, sampling: dict) -> int:
    win = float(sampling["window_sec"])
    step = win - float(sampling["overlap_sec"])
    return 1 if duration <= win else 1 + math.ceil((duration - win) / step)


def select_tier(meta: VideoMetadata, profile: dict) -> tuple[str, dict]:
    """Choose a prevalidated sampling tier BEFORE analysis (spec §9.2): the densest tier whose
    measured per-window cost fits the mandatory observation budget. Never adapts mid-job."""
    base = dict(profile["sampling"])
    tiers = profile.get("sampling_tiers") or []
    if not tiers:
        return profile["profile_id"], base
    budget = float(profile.get("budget_sec", {}).get("observe_total", 130))
    duration = meta.last_frame_sec - meta.first_frame_sec
    for tier in tiers:
        merged = {**base, **{k: v for k, v in tier.items() if k not in ("tier_id", "est_window_sec")}}
        est = count_coarse_windows(duration, merged) * float(tier["est_window_sec"])
        if est <= budget:
            return f"{profile['profile_id']}/{tier['tier_id']}", merged
    raise PipelineError("limits_exceeded", "no prevalidated sampling tier fits the observation budget")


def build_plan(meta: VideoMetadata, profile: dict, extra: list[SamplingWindow] | None = None) -> SamplingPlan:
    profile_id, sampling = select_tier(meta, profile)
    windows = coarse_windows(meta, sampling) + list(extra or [])
    return SamplingPlan(
        profile_id=profile_id,
        windows=windows,
        mandatory_window_ids=[w.window_id for w in windows if w.mandatory],
        maximum_frames_per_call=int(sampling["max_frames_per_observation"]),
        model_long_side_px=int(sampling["model_long_side_px"]),
        visual_token_limit=sampling.get("max_visual_tokens"),
    )


def refinement_times(start: float, end: float, sampling: dict, meta: VideoMetadata) -> list[float]:
    radius = float(sampling["refinement_radius_sec"])
    s = max(meta.first_frame_sec, start - radius)
    e = min(meta.last_frame_sec, end + radius)
    return _times(s, e, float(sampling["refinement_fps"]), int(sampling["max_refinement_frames"]))


def anchored_refinement_times(candidate: CandidateEvent, frames: list[FrameRef],
                              sampling: dict, meta: VideoMetadata) -> list[float]:
    """Optional dense proposal around a cited anchor; retain the wider context targets.

    Decoding more targets does not increase the model batch cap. Evidence continues
    to use decoded source PTS, never these requested sampling timestamps.
    """
    base = refinement_times(candidate.start_sec, candidate.end_sec, sampling, meta)
    cfg = sampling.get("anchor_refinement")
    if not cfg or not cfg.get("enabled", False):
        return base
    anchor = next((f for f in frames if f.frame_id == candidate.anchor_frame_id), None)
    if anchor is None or not any(a.state == "supported" and anchor.frame_id in a.supporting_frame_ids
                                 for a in candidate.assessments):
        return base
    radius, fps = float(cfg.get("radius_sec", 2)), float(cfg.get("fps", 4))
    if not (0 < radius <= 2 and 0 < fps <= 4):
        raise ValueError("anchor refinement supports at most +/-2 seconds at 4 fps")
    lo = max(meta.first_frame_sec, anchor.relative_sec - radius)
    hi = min(meta.last_frame_sec, anchor.relative_sec + radius)
    return sorted(set(base + _times(lo, hi, fps, 17) + [anchor.relative_sec]))


def select_verification_frames(candidate: CandidateEvent, coarse_frames: list[FrameRef],
                               refined_frames: list[FrameRef], max_frames: int,
                               *, focus_sec: float | None = None) -> list[FrameRef]:
    """Keep actual cited PTS frames while filling the fixed verification budget.

    Retained coarse frames are not re-requested through rounded floating times.
    This does not assert dense/complete coverage: the rules engine still checks it.
    """
    if max_frames < 1:
        raise PipelineError("limits_exceeded", "verification frame budget is empty")
    anchor = {candidate.anchor_frame_id} if candidate.anchor_frame_id is not None else set()
    supporting: set[str] = set()
    contradicting: set[str] = set()
    for assessment in candidate.assessments:
        if assessment.state in ("supported", "contradicted"):
            supporting.update(assessment.supporting_frame_ids)
            contradicting.update(assessment.contradicting_frame_ids)
    protected = anchor | supporting | contradicting
    frames = {f.frame_id: f for f in coarse_frames}
    for frame in refined_frames:
        old = frames.get(frame.frame_id)
        if old is not None and (old.source_sha256, old.pts, old.time_base_num, old.time_base_den,
                                old.relative_sec) != (frame.source_sha256, frame.pts, frame.time_base_num,
                                                      frame.time_base_den, frame.relative_sec):
            raise PipelineError("evidence_invalid", "same frame id has inconsistent source timeline")
        frames[frame.frame_id] = frame  # prefer the newly rendered refinement thumbnail
    if protected - frames.keys():
        raise PipelineError("evidence_invalid", "candidate cites an unavailable source frame")
    if len(protected) > max_frames:
        # A coarse cue spanning a whole window cites more frames than the verification budget.
        # Failing usable footage would be worse than thinning: keep the anchor, then supporting,
        # then contradicting frames, each spread evenly in time. The rules engine still checks
        # completeness/visibility on what is actually verified.
        def spread(ids: set[str], k: int) -> set[str]:
            ordered = sorted(ids, key=lambda fid: (frames[fid].relative_sec, fid))
            if k <= 0 or not ordered:
                return set()
            if len(ordered) <= k:
                return set(ordered)
            return {ordered[round(i * (len(ordered) - 1) / (k - 1))] for i in range(k)} if k > 1 else {ordered[0]}
        keep = set(anchor)
        keep |= spread(supporting - keep, max_frames - len(keep))
        keep |= spread(contradicting - keep, max_frames - len(keep))
        protected = keep
    selected = set(protected)
    refined = sorted({f.frame_id for f in refined_frames}, key=lambda fid: (frames[fid].relative_sec, fid))
    if refined:
        for fid in (refined[0], refined[-1]):
            if len(selected) < max_frames:
                selected.add(fid)
    # Spread remaining context frames over the largest temporal gaps; deterministic
    # tie breaking keeps repeated evaluations stable without exceeding the cap.
    remaining = set(refined) - selected
    if focus_sec is not None:
        # Spend at most half the remaining slots near the anchor. Preserve the
        # other half for broad context; a close-up must not erase a transition.
        focus_slots = (max_frames - len(selected)) // 2
        nearby = sorted(remaining, key=lambda fid: (abs(frames[fid].relative_sec-focus_sec),
                                                   frames[fid].relative_sec, fid))
        for fid in nearby[:focus_slots]:
            selected.add(fid)
            remaining.remove(fid)
    while remaining and len(selected) < max_frames:
        fid = max(remaining, key=lambda key: (
            min(abs(frames[key].relative_sec - frames[kept].relative_sec) for kept in selected),
            -frames[key].relative_sec, key))
        selected.add(fid)
        remaining.remove(fid)
    result = sorted((frames[fid] for fid in selected), key=lambda f: (f.relative_sec, f.frame_id))
    if not result:
        raise PipelineError("evidence_invalid", "no verification frames available")
    if len({f.source_sha256 for f in result}) != 1:
        raise PipelineError("evidence_invalid", "verification frames belong to different sources")
    return result


@dataclass
class CoverageLedger:
    profile_id: str
    required: list[str]
    proposals: list[str] = field(default_factory=list)
    completed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    refined: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)

    def complete(self, window_id: str) -> None:
        if window_id not in self.completed:
            self.completed.append(window_id)

    def fail(self, window_id: str) -> None:
        if window_id not in self.failed:
            self.failed.append(window_id)

    @property
    def required_complete(self) -> bool:
        return set(self.required) <= set(self.completed) and not (set(self.failed) & set(self.required))

    def to_dto(self, covered_start: float, covered_end: float) -> Coverage:
        return Coverage(
            profile_id=self.profile_id,
            required_window_ids=list(self.required),
            completed_window_ids=[w for w in self.completed],
            failed_window_ids=list(self.failed),
            proposal_window_ids=list(self.proposals),
            refined_candidate_ids=list(self.refined),
            covered_start_sec=ts(covered_start),
            covered_end_sec=ts(covered_end),
            visual_limitations=list(self.limitations)[:50],
        )
