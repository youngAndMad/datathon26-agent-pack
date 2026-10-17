"""Deterministic sampling plans and coverage accounting.

Mandatory coarse windows cover the complete playable clip [first_frame_sec, last_frame_sec],
including the first and last intervals. Motion proposals are optional extras; they never
replace or gate the uniform coverage.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from cashwatch.dto import Coverage, MotionSample, SamplingPlan, SamplingWindow, VideoMetadata, ts


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


def build_plan(meta: VideoMetadata, profile: dict, extra: list[SamplingWindow] | None = None) -> SamplingPlan:
    sampling = profile["sampling"]
    windows = coarse_windows(meta, sampling) + list(extra or [])
    return SamplingPlan(
        profile_id=profile["profile_id"],
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
