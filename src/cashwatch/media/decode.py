"""PyAV decoding core. Every public ``op_*`` function takes/returns JSON-compatible values so it
can run either in-process or inside the guarded child process (``cashwatch.media._worker``).

Timeline model
--------------
* libavformat applies container edit lists (pre-roll packets are flagged *discard* and their
  decoded frames are dropped by libavcodec), exactly like ffprobe/ffmpeg.
* ``origin_pts`` = PTS of the first frame the decoder actually presents. ``relative_sec`` of a
  frame = (pts - origin_pts) * time_base, computed with exact ``Fraction`` arithmetic.
* The *presentation index* is the sorted list of PTS of non-discarded video packets. Probe
  checks that the first and last indexed frames decode to exactly those PTS; the sequential
  pass checks that the decoder presents the same number of frames.
* Selection rule (sampling, refinement, motion): the first presented frame with
  relative_sec >= target; the last frame when the target is beyond the end. Targets are
  converted from their decimal representation (``repr``) to exact fractions.
"""

from __future__ import annotations

import bisect
import io
import math
import resource
import sys
import time
from collections.abc import Callable, Iterator
from fractions import Fraction
from pathlib import Path
from typing import Any

import av
import numpy as np
from av.stream import Discard, Disposition
from av.video.reformatter import Colorspace, Interpolation, VideoReformatter
from PIL import Image

from cashwatch.deadline import Deadline
from cashwatch.dto import (
    EvidenceFrame,
    FrameManifest,
    FrameRef,
    MotionSample,
    SamplingPlan,
    Transform,
    VideoMetadata,
    ts,
)
from cashwatch.errors import PipelineError
from cashwatch.media.paths import safe_write, sha256_bytes

THUMB_QUALITY = 90
MOTION_LONG_SIDE = 160
MAX_DECODE_ERRORS = 16
DEADLINE_CHECK_EVERY = 24
CLUSTER_GAP_SEC = 2.0
MAX_SEEK_ATTEMPTS = 6

# AVColorSpace -> swscale matrix
_CS = {1: Colorspace.ITU709, 4: Colorspace.FCC, 5: Colorspace.ITU601, 6: Colorspace.ITU601,
       7: Colorspace.SMPTE240M, 9: Colorspace.BT2020, 10: Colorspace.BT2020}
_PIL_ROT = {90: Image.Transpose.ROTATE_270, 180: Image.Transpose.ROTATE_180,
            270: Image.Transpose.ROTATE_90}  # PIL ROTATE_n is counter-clockwise


# --------------------------------------------------------------------------- helpers

def exact(t: float) -> Fraction:
    if not math.isfinite(t):
        raise PipelineError("internal_error", "non-finite requested time")
    return Fraction(repr(float(t)))


def frame_id_for(pts: int) -> str:
    return f"p{pts}" if pts >= 0 else f"pm{-pts}"


def evidence_id_for(frame_id: str) -> str:
    return f"e{frame_id}"


def _rel(pts: int, origin: int, tb: Fraction) -> Fraction:
    return (pts - origin) * tb


def peak_rss_mb() -> float:
    r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(r / (1024 * 1024) if sys.platform == "darwin" else r / 1024, 1)


def _tb(meta: VideoMetadata) -> Fraction:
    return Fraction(meta.time_base_num, meta.time_base_den)


def _deadline(sec: float) -> Deadline:
    return Deadline.in_seconds(max(0.0, sec))


def open_video(path: str) -> tuple[av.container.InputContainer, av.video.stream.VideoStream]:
    try:
        c = av.open(path, mode="r", options={"protocol_whitelist": "file"})
    except av.FFmpegError as exc:
        raise PipelineError("invalid_media", f"demuxer open failed: {type(exc).__name__}") from None
    vids = [s for s in c.streams.video if not (int(s.disposition) & int(Disposition.attached_pic))]
    if not vids:
        c.close()
        raise PipelineError("invalid_media", "no video stream")
    vs = vids[0]
    for st in c.streams:
        if st.index != vs.index:
            st.discard = Discard.all  # audio/data/other streams are never read
    cc = vs.codec_context
    cc.thread_type = "AUTO"
    cc.thread_count = 0
    return c, vs


def _decode_iter(c, vs, stats: dict) -> Iterator[av.VideoFrame]:
    """Decode the video stream from the current position; bounded tolerance to packet errors."""
    try:
        demux = c.demux(vs)
        for pkt in demux:
            try:
                frames = pkt.decode()
            except av.FFmpegError:
                stats["decode_errors"] = stats.get("decode_errors", 0) + 1
                if stats["decode_errors"] > MAX_DECODE_ERRORS:
                    raise PipelineError("decode_failed", "too many decode errors") from None
                continue
            yield from frames
    except av.FFmpegError as exc:
        raise PipelineError("decode_failed", f"demux failed: {type(exc).__name__}") from None


class Index:
    """Presentation index from demuxed (not decoded) video packets."""

    def __init__(self, pts: list[int], last_duration: int, packets: int, discarded: int,
                 keyframes: int) -> None:
        self.pts = pts
        self.last_duration = last_duration
        self.packets = packets
        self.discarded = discarded
        self.keyframes = keyframes

    def select(self, threshold: int) -> int:
        i = bisect.bisect_left(self.pts, threshold)
        return self.pts[min(i, len(self.pts) - 1)]

    def ordinal(self, pts: int) -> int:
        i = bisect.bisect_left(self.pts, pts)
        if i >= len(self.pts) or self.pts[i] != pts:
            raise PipelineError("evidence_invalid", "frame PTS not present in source index")
        return i


def build_index(path: str, *, max_frames: int, deadline: Deadline) -> tuple[Index, dict]:
    c, vs = open_video(path)
    try:
        info = {
            "codec": vs.codec_context.name,
            "time_base": [vs.time_base.numerator, vs.time_base.denominator] if vs.time_base else None,
            "header_frames": int(vs.frames or 0),
            "container": c.format.name,
            "sar": [vs.sample_aspect_ratio.numerator, vs.sample_aspect_ratio.denominator]
            if vs.sample_aspect_ratio else None,
            "avg_rate": float(vs.average_rate) if vs.average_rate else None,
            "stream_start": vs.start_time,
        }
        pts: list[int] = []
        durations: dict[int, int] = {}
        n = disc = keys = 0
        try:
            for pkt in c.demux(vs):
                if pkt.size == 0 and pkt.pts is None:
                    continue  # flush sentinel
                n += 1
                if n > max_frames + 256:
                    raise PipelineError("limits_exceeded", "frame count exceeds decode guard")
                if n % 2048 == 0:
                    deadline.check("probing")
                if pkt.pts is None:
                    raise PipelineError("timeline_unsupported", "packet without presentation timestamp")
                if pkt.is_discard:
                    disc += 1
                    continue
                if pkt.is_keyframe:
                    keys += 1
                pts.append(int(pkt.pts))
                durations[int(pkt.pts)] = int(pkt.duration or 0)
        except av.FFmpegError as exc:
            raise PipelineError("decode_failed", f"demux failed: {type(exc).__name__}") from None
    finally:
        c.close()
    if not pts:
        raise PipelineError("decode_failed", "no presentable video packets")
    pts.sort()
    for a, b in zip(pts, pts[1:]):
        if a == b:
            raise PipelineError("timeline_unsupported", "duplicate presentation timestamps")
    if info["header_frames"] and n < info["header_frames"]:
        raise PipelineError("decode_failed", f"truncated: {n} of {info['header_frames']} packets readable")
    if len(pts) > max_frames:
        raise PipelineError("limits_exceeded", "frame count exceeds decode guard")
    idx = Index(pts, durations[pts[-1]], n, disc, keys)
    return idx, info


# --------------------------------------------------------------------------- rendering

def render(frame: av.VideoFrame, meta: VideoMetadata, long_side: int, *, quality: int,
           interpolation: str = "AREA") -> tuple[bytes, int, int, list[Transform]]:
    """Real source pixels -> colour conversion -> aspect-preserving downscale (never upscale)
    -> display rotation -> JPEG. Returns (jpeg bytes, width, height, transforms)."""
    sw, sh = frame.width, frame.height
    # sample aspect ratio implied by the probed display size (pre-rotation width / coded width)
    pre_rot_w = meta.display_width if meta.display_rotation in (0, 180) else meta.display_height
    sar = Fraction(pre_rot_w, meta.width) if meta.width else Fraction(1)
    # pre-rotation display size (sample aspect ratio applied)
    dw = sw * sar
    dh = Fraction(sh)
    scale = min(Fraction(1), Fraction(long_side) / max(dw, dh))
    tw = max(1, round(dw * scale))
    th = max(1, round(dh * scale))
    cs = _CS.get(int(frame.colorspace or 0))
    rng = int(frame.color_range or 0)
    kw: dict[str, Any] = {}
    if cs is not None:
        kw["src_colorspace"] = cs
    if rng in (1, 2):
        kw["src_color_range"] = rng
    rgb = VideoReformatter().reformat(frame, width=tw, height=th, format="rgb24",
                                      interpolation=Interpolation[interpolation], **kw)
    img = Image.fromarray(rgb.to_ndarray(), "RGB")
    transforms = [Transform(kind="color_convert", parameters={
        "from": frame.format.name, "to": "rgb24",
        "matrix": cs.name if cs is not None else "default_bt601",
        "range": {1: "limited", 2: "full"}.get(rng, "unspecified_as_limited")})]
    if (tw, th) != (sw, sh):
        transforms.append(Transform(kind="resize", parameters={
            "from": [sw, sh], "to": [tw, th], "filter": interpolation.lower(),
            "sample_aspect_ratio": f"{sar.numerator}:{sar.denominator}"}))
    rot = meta.display_rotation
    if rot:
        img = img.transpose(_PIL_ROT[rot])
        transforms.append(Transform(kind="rotate", parameters={"degrees_clockwise": rot}))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    transforms.append(Transform(kind="jpeg_encode", parameters={"quality": quality}))
    return buf.getvalue(), img.width, img.height, transforms


# --------------------------------------------------------------------------- probe

def op_probe_av(path: str, max_frames: int, deadline_sec: float) -> dict:
    """Index + decode first and last presented frames (verifies decodability and origin)."""
    deadline = _deadline(deadline_sec)
    t0 = time.monotonic()
    idx, info = build_index(path, max_frames=max_frames, deadline=deadline)
    if not info["time_base"]:
        raise PipelineError("timeline_unsupported", "stream has no time base")
    tb = Fraction(*info["time_base"])
    warnings: list[str] = []
    stats: dict = {}
    # first presented frame
    c, vs = open_video(path)
    first = None
    try:
        for fr in _decode_iter(c, vs, stats):
            if fr.pts is None:
                raise PipelineError("timeline_unsupported", "decoded frame without PTS")
            first = fr
            break
        if first is None:
            raise PipelineError("decode_failed", "no frame could be decoded")
        origin = int(first.pts)
        rotation_ccw = int(round(first.rotation or 0))
        pix_fmt = first.format.name
        coded = (first.width, first.height)
    finally:
        c.close()
    method = "first_decoded_pts"
    if origin != idx.pts[0]:
        if origin < idx.pts[0] or origin not in idx.pts:
            raise PipelineError("timeline_unsupported", "first decoded frame not in presentation index")
        skipped = idx.pts.index(origin)
        warnings.append(f"{skipped} leading indexed frames not decodable; origin at first decodable frame")
        idx.pts = idx.pts[skipped:]
        method = "first_decoded_pts_after_undecodable_leading"
    # last presented frame must decode to the indexed PTS
    last_pts = idx.pts[-1]
    got: list[int] = []
    fetch_frames(path, idx, [last_pts], lambda fr, p: got.append(p), deadline, tb, stage="probing")
    if info["stream_start"] not in (None, 0):
        warnings.append("stream starts at nonzero time; origin set to first presented frame")
    if idx.discarded:
        warnings.append(f"edit list: {idx.discarded} pre-roll packets not presented")
    if len(idx.pts) > 2:
        d = np.diff(np.asarray(idx.pts, dtype=np.int64))
        if d.min() <= 0 or d.max() > d.min() * 1.02:
            warnings.append("variable frame rate")
    if stats.get("decode_errors"):
        warnings.append(f"{stats['decode_errors']} packet decode errors near start")
    return {
        "codec": info["codec"], "container": info["container"], "time_base": info["time_base"],
        "origin_pts": origin, "origin_method": method,
        "first_frame_sec": 0.0,
        "last_frame_sec": float(_rel(last_pts, origin, tb)),
        "duration_sec": float(_rel(last_pts + max(idx.last_duration, 0), origin, tb)),
        "frame_count": len(idx.pts), "packets": idx.packets, "keyframes": idx.keyframes,
        "rotation_ccw": rotation_ccw, "pix_fmt": pix_fmt, "coded": list(coded), "sar": info["sar"],
        "avg_rate": info["avg_rate"], "warnings": warnings,
        "elapsed_ms": int((time.monotonic() - t0) * 1000), "peak_rss_mb": peak_rss_mb(),
    }


# --------------------------------------------------------------------------- seek + decode forward

def fetch_frames(path: str, idx: Index, wanted: list[int],
                 on_frame: Callable[[av.VideoFrame, int], None], deadline: Deadline,
                 tb: Fraction, *, stage: str) -> None:
    """Decode exactly the frames whose PTS are in ``wanted`` (must be in the index) using
    keyframe seek + decode forward; retries from earlier positions on overshoot."""
    want = sorted(set(wanted))
    for p in want:
        idx.ordinal(p)  # raises evidence_invalid for unknown PTS
    gap = int(CLUSTER_GAP_SEC / tb)
    clusters: list[list[int]] = []
    for p in want:
        if clusters and p - clusters[-1][-1] <= gap:
            clusters[-1].append(p)
        else:
            clusters.append([p])
    c, vs = open_video(path)
    stats: dict = {}
    try:
        for cl in clusters:
            remaining = list(cl)
            back = Fraction(0)
            for _attempt in range(MAX_SEEK_ATTEMPTS + 1):
                target = remaining[0] - int(back / tb)
                from_start = target <= idx.pts[0]
                if from_start:
                    c.close()
                    c, vs = open_video(path)
                else:
                    try:
                        c.seek(target, stream=vs, backward=True, any_frame=False)
                    except av.FFmpegError:
                        c.close()
                        c, vs = open_video(path)
                        from_start = True
                n = 0
                for fr in _decode_iter(c, vs, stats):
                    n += 1
                    if n % DEADLINE_CHECK_EVERY == 0:
                        deadline.check(stage)
                    if fr.pts is None:
                        continue
                    p = int(fr.pts)
                    if p < remaining[0]:
                        continue
                    if p == remaining[0]:
                        on_frame(fr, p)
                        remaining.pop(0)
                        if not remaining:
                            break
                        continue
                    break  # overshoot: a wanted frame was not presented after this seek
                if not remaining:
                    break
                if from_start:
                    raise PipelineError("evidence_invalid" if stage == "rendering_evidence" else "decode_failed",
                                        "requested PTS not produced by decoder")
                back = max(Fraction(1), back * 2)  # overshoot or EOF: restart earlier
            else:
                raise PipelineError("evidence_invalid" if stage == "rendering_evidence" else "decode_failed",
                                    "seek did not reach requested PTS")
    finally:
        c.close()


# --------------------------------------------------------------------------- sequential pass

def _thresholds(times: list[float], origin: int, tb: Fraction) -> list[int]:
    return [origin + math.ceil(exact(t) / tb) for t in times]


def sequential_pass(path: str, meta: VideoMetadata, thumb_thr: list[int], motion_thr: list[int],
                    on_thumb: Callable[[av.VideoFrame, int], None] | None,
                    on_motion: Callable[[av.VideoFrame], None] | None,
                    deadline: Deadline, stage: str) -> dict:
    thumb_thr = sorted(set(thumb_thr))
    motion_thr = sorted(set(motion_thr))
    i = j = 0
    ordinal = -1
    prev_pts: int | None = None
    last: av.VideoFrame | None = None
    stats: dict = {"decoded_frames": 0, "corrupt_frames": 0, "pre_origin_frames": 0}
    t0 = time.monotonic()
    reached_eof = True
    c, vs = open_video(path)
    try:
        for fr in _decode_iter(c, vs, stats):
            if fr.pts is None:
                raise PipelineError("timeline_unsupported", "decoded frame without PTS")
            p = int(fr.pts)
            if p < meta.origin_pts:
                stats["pre_origin_frames"] += 1
                continue
            if prev_pts is not None and p <= prev_pts:
                raise PipelineError("timeline_unsupported", "non-monotonic presentation timestamps")
            prev_pts = p
            ordinal += 1
            stats["decoded_frames"] += 1
            if fr.is_corrupt:
                stats["corrupt_frames"] += 1
            if ordinal % DEADLINE_CHECK_EVERY == 0:
                deadline.check(stage)
            hit = False
            while i < len(thumb_thr) and thumb_thr[i] <= p:
                i += 1
                hit = True
            if hit and on_thumb:
                on_thumb(fr, ordinal)
            mhit = False
            while j < len(motion_thr) and motion_thr[j] <= p:
                j += 1
                mhit = True
            if mhit and on_motion:
                on_motion(fr)
            last = fr
            if i >= len(thumb_thr) and j >= len(motion_thr):
                reached_eof = False
                break
        if last is None:
            raise PipelineError("decode_failed", "no frame decoded")
        if reached_eof:
            tb = _tb(meta)
            if meta.frame_count is not None and stats["decoded_frames"] < meta.frame_count:
                raise PipelineError("decode_failed",
                                    f"decoder presented {stats['decoded_frames']} of {meta.frame_count} frames")
            if float(_rel(int(last.pts), meta.origin_pts, tb)) != meta.last_frame_sec:
                raise PipelineError("timeline_unsupported", "last decoded frame differs from probe")
            if i < len(thumb_thr) and on_thumb:
                on_thumb(last, ordinal)  # targets beyond the end -> last presented frame
    finally:
        c.close()
    stats["elapsed_ms"] = int((time.monotonic() - t0) * 1000)
    stats["reached_eof"] = int(reached_eof)
    return stats


class _MotionAcc:
    def __init__(self, meta: VideoMetadata) -> None:
        self.meta = meta
        self.tb = _tb(meta)
        self.prev: np.ndarray | None = None
        self.samples: list[MotionSample] = []
        w, h = meta.width, meta.height
        s = MOTION_LONG_SIDE / max(w, h)
        self.size = (max(2, round(w * s)), max(2, round(h * s)))

    def __call__(self, fr: av.VideoFrame) -> None:
        g = fr.reformat(width=self.size[0], height=self.size[1], format="gray",
                        interpolation="AREA").to_ndarray().astype(np.int16)
        score = 0.0 if self.prev is None else float(np.abs(g - self.prev).mean()) / 255.0
        self.prev = g
        self.samples.append(MotionSample(
            relative_sec=float(_rel(int(fr.pts), self.meta.origin_pts, self.tb)), score=score))


def _motion_times(meta: VideoMetadata, fps: float) -> list[float]:
    if not (fps > 0 and math.isfinite(fps)):
        raise PipelineError("internal_error", "motion fps must be positive")
    n = int(math.floor(meta.last_frame_sec * fps + 1e-9)) + 1
    step = exact(1 / fps) if fps != int(fps) else Fraction(1, int(fps))
    return [float(k * step) for k in range(n)]


class _ThumbWriter:
    def __init__(self, meta: VideoMetadata, work_dir: Path, long_side: int) -> None:
        self.meta = meta
        self.work_dir = work_dir
        self.long_side = long_side
        self.tb = _tb(meta)
        self.refs: dict[int, FrameRef] = {}

    def __call__(self, fr: av.VideoFrame, ordinal: int) -> None:
        p = int(fr.pts)
        if p in self.refs:
            return
        data, w, h, transforms = render(fr, self.meta, self.long_side, quality=THUMB_QUALITY)
        fid = frame_id_for(p)
        rel = f"frames/{fid}_L{self.long_side}.jpg"
        safe_write(self.work_dir, rel, data)
        self.refs[p] = FrameRef(
            frame_id=fid, source_sha256=self.meta.source_sha256, ordinal=ordinal, pts=p,
            time_base_num=self.meta.time_base_num, time_base_den=self.meta.time_base_den,
            relative_sec=float(_rel(p, self.meta.origin_pts, self.tb)), local_path=rel,
            sha256=sha256_bytes(data), width=w, height=h, transforms=transforms)


# --------------------------------------------------------------------------- ops

def op_sample(path: str, meta: dict, plan: dict, work_dir: str, motion_fps: float | None,
              deadline_sec: float) -> dict:
    deadline = _deadline(deadline_sec)
    m = VideoMetadata.model_validate(meta)
    pl = SamplingPlan.model_validate(plan)
    tb = _tb(m)
    wd = Path(work_dir)
    win_thr = {w.window_id: _thresholds(w.requested_sample_times, m.origin_pts, tb) for w in pl.windows}
    all_thr = [t for v in win_thr.values() for t in v]
    if not all_thr:
        raise PipelineError("internal_error", "sampling plan has no requested times")
    writer = _ThumbWriter(m, wd, pl.model_long_side_px)
    motion = _MotionAcc(m) if motion_fps else None
    m_thr = _thresholds(_motion_times(m, motion_fps), m.origin_pts, tb) if motion_fps else []
    t0 = time.monotonic()
    stats = sequential_pass(path, m, all_thr, m_thr, writer, motion, deadline, "sampling")
    sel = sorted(writer.refs)

    def pick(thr: int) -> int:
        k = bisect.bisect_left(sel, thr)
        return sel[min(k, len(sel) - 1)]

    window_frames: dict[str, list[str]] = {}
    for w in pl.windows:
        ids: list[str] = []
        for p in sorted({pick(t) for t in win_thr[w.window_id]}):
            ids.append(writer.refs[p].frame_id)
        window_frames[w.window_id] = ids
    frames = [writer.refs[p] for p in sel]
    stats.update({
        "selected_frames": len(frames), "requested_times": len(all_thr),
        "motion_samples": len(motion.samples) if motion else 0,
        "total_ms": int((time.monotonic() - t0) * 1000), "peak_rss_mb": peak_rss_mb(),
        "seek_strategy": "sequential_single_pass",
    })
    stats["decode_fps"] = round(stats["decoded_frames"] / max(stats["elapsed_ms"], 1) * 1000, 1)
    manifest = FrameManifest(video=m, frames=frames, window_frames=window_frames,
                             coverage_first_sec=frames[0].relative_sec,
                             coverage_last_sec=frames[-1].relative_sec, decode_stats=stats)
    return {"manifest": manifest.model_dump(mode="json"),
            "motion": [s.model_dump(mode="json") for s in motion.samples] if motion else None,
            "motion_fps": motion_fps}


def op_motion(path: str, meta: dict, fps: float, deadline_sec: float) -> dict:
    deadline = _deadline(deadline_sec)
    m = VideoMetadata.model_validate(meta)
    acc = _MotionAcc(m)
    thr = _thresholds(_motion_times(m, fps), m.origin_pts, _tb(m))
    stats = sequential_pass(path, m, [], thr, None, acc, deadline, "sampling")
    stats["peak_rss_mb"] = peak_rss_mb()
    return {"motion": [s.model_dump(mode="json") for s in acc.samples], "stats": stats}


def _index_for(path: str, m: VideoMetadata, deadline: Deadline) -> Index:
    idx, info = build_index(path, max_frames=(m.frame_count or 0) + 1024, deadline=deadline)
    if info["time_base"] != [m.time_base_num, m.time_base_den]:
        raise PipelineError("evidence_invalid", "time base changed since probe")
    idx.pts = [p for p in idx.pts if p >= m.origin_pts]
    if not idx.pts or idx.pts[0] != m.origin_pts:
        raise PipelineError("timeline_unsupported", "origin frame missing from index")
    return idx


def op_frames_for_times(path: str, meta: dict, times_sec: list[float], work_dir: str,
                        long_side_px: int, deadline_sec: float) -> dict:
    deadline = _deadline(deadline_sec)
    m = VideoMetadata.model_validate(meta)
    tb = _tb(m)
    if not times_sec:
        return {"frames": [], "stats": {}}
    idx = _index_for(path, m, deadline)
    wanted = sorted({idx.select(t) for t in _thresholds(times_sec, m.origin_pts, tb)})
    writer = _ThumbWriter(m, Path(work_dir), long_side_px)
    t0 = time.monotonic()
    fetch_frames(path, idx, wanted, lambda fr, p: writer(fr, idx.ordinal(p)), deadline, tb,
                 stage="verifying")
    frames = [writer.refs[p].model_dump(mode="json") for p in wanted]
    return {"frames": frames, "stats": {"elapsed_ms": int((time.monotonic() - t0) * 1000),
                                        "peak_rss_mb": peak_rss_mb()}}


def op_evidence(path: str, meta: dict, frame_refs: list[dict], out_dir: str, long_side_px: int,
                jpeg_quality: int, deadline_sec: float) -> dict:
    deadline = _deadline(deadline_sec)
    m = VideoMetadata.model_validate(meta)
    tb = _tb(m)
    refs = [FrameRef.model_validate(r) for r in frame_refs]
    for r in refs:
        if r.source_sha256 != m.source_sha256 or (r.time_base_num, r.time_base_den) != (
                m.time_base_num, m.time_base_den):
            raise PipelineError("evidence_invalid", "frame reference from a different source/timeline")
        if float(_rel(r.pts, m.origin_pts, tb)) != r.relative_sec:
            raise PipelineError("evidence_invalid", "frame reference time does not match its PTS")
        if r.frame_id != frame_id_for(r.pts):
            raise PipelineError("evidence_invalid", "frame id does not match PTS")
    if not refs:
        return {"evidence": [], "stats": {}}
    idx = _index_for(path, m, deadline)
    od = Path(out_dir)
    out: dict[int, EvidenceFrame] = {}

    def on_frame(fr: av.VideoFrame, p: int) -> None:
        if int(fr.pts) != p:
            raise PipelineError("evidence_invalid", "decoded PTS mismatch")
        data, w, h, transforms = render(fr, m, long_side_px, quality=jpeg_quality)
        with Image.open(io.BytesIO(data)) as chk:
            chk.verify()
        fid = frame_id_for(p)
        eid = evidence_id_for(fid)
        rel = f"evidence/{eid}.jpg"
        safe_write(od, rel, data)
        out[p] = EvidenceFrame(
            evidence_id=eid, frame_id=fid, source_sha256=m.source_sha256, pts=p,
            time_base_num=m.time_base_num, time_base_den=m.time_base_den,
            timestamp_sec=ts(float(_rel(p, m.origin_pts, tb))), jpeg_path=rel,
            jpeg_sha256=sha256_bytes(data), width=w, height=h, jpeg_quality=jpeg_quality,
            transforms=transforms)

    t0 = time.monotonic()
    fetch_frames(path, idx, [r.pts for r in refs], on_frame, deadline, tb, stage="rendering_evidence")
    seen: set[int] = set()
    result = []
    for r in refs:
        if r.pts in seen:
            continue
        seen.add(r.pts)
        if r.pts not in out:
            raise PipelineError("evidence_invalid", "evidence frame not decoded")
        result.append(out[r.pts].model_dump(mode="json"))
    return {"evidence": result, "stats": {"elapsed_ms": int((time.monotonic() - t0) * 1000),
                                          "peak_rss_mb": peak_rss_mb()}}


OPS: dict[str, Callable[..., dict]] = {
    "probe_av": op_probe_av,
    "sample": op_sample,
    "motion": op_motion,
    "frames_for_times": op_frames_for_times,
    "evidence": op_evidence,
}


def run_op(op: str, kwargs: dict) -> dict:
    """Execute an op and return a JSON reply; unexpected exceptions become typed failures."""
    try:
        return {"ok": True, "result": OPS[op](**kwargs)}
    except PipelineError as exc:
        return {"ok": False, "code": exc.code, "detail": exc.detail[:300]}
    except av.FFmpegError as exc:
        return {"ok": False, "code": "decode_failed", "detail": f"libav error: {type(exc).__name__}"}
    except MemoryError:
        return {"ok": False, "code": "limits_exceeded", "detail": "out of memory while decoding"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "code": "internal_error", "detail": f"{type(exc).__name__}"}
