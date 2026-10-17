"""MediaPort implementation (agent B).

* ``probe_video``: ffprobe subprocess (``file`` protocol only, timeout) for container/stream
  facts and display rotation, then a guarded PyAV pass that builds the presentation index and
  decodes the first and last presented frames.
* Heavy decoding (sampling, motion, refinement, evidence) runs in a child process
  (``cashwatch.media._worker``) killed on timeout, unless ``isolate=False``.
* Times are always (pts - origin_pts) * time_base of the source video stream.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections import OrderedDict
from fractions import Fraction
from pathlib import Path

import av

from cashwatch.deadline import Deadline
from cashwatch.dto import EvidenceFrame, FrameManifest, FrameRef, MotionSample, SamplingPlan, VideoMetadata
from cashwatch.errors import PipelineError
from cashwatch.media import decode as D
from cashwatch.media.paths import resolve_existing, sha256_file, validate_source
from cashwatch.media.tools import ffprobe, find_tool, run_tool

MOV_DEMUXER = "mov,mp4,m4a,3gp,3g2,mj2"
_SRC_ROOT = str(Path(__file__).resolve().parents[2])


def _rotation_cw(stream: dict) -> int | None:
    """Clockwise display rotation (what a player applies) from ffprobe stream info."""
    for sd in stream.get("side_data_list") or []:
        if "rotation" in sd:
            ccw = float(sd["rotation"])  # display-matrix angle, counter-clockwise
            return int(round(-ccw)) % 360
    tag = (stream.get("tags") or {}).get("rotate")
    if tag is not None:
        try:
            return int(round(float(tag))) % 360  # legacy tag is clockwise
        except ValueError:
            return None
    return 0


def _fraction(value: str | None) -> Fraction | None:
    if not value or value in ("N/A", "0:1", "0/1"):
        return None
    try:
        num, den = value.replace(":", "/").split("/")
        f = Fraction(int(num), int(den))
    except (ValueError, ZeroDivisionError):
        return None
    return f if f > 0 else None


class MediaService:
    def __init__(self, *, isolate: bool = True, ffprobe_bin: str | None = None,
                 ffmpeg_bin: str | None = None, max_pixel_area: int = 3840 * 2160,
                 max_dimension: int = 8192, max_frames: int = 36_000,
                 max_duration_sec: float = 3600.0, motion_fps: float | None = 4.0,
                 child_grace_sec: float = 2.0, worker_cmd: list[str] | None = None) -> None:
        self.isolate = isolate
        # command of the guarded decoder child (overridable only to test the kill path)
        self.worker_cmd = list(worker_cmd) if worker_cmd else [sys.executable, "-m", "cashwatch.media._worker"]
        self.ffprobe_bin = find_tool("ffprobe", ffprobe_bin)
        self.ffmpeg_bin = find_tool("ffmpeg", ffmpeg_bin)
        self.max_pixel_area = max_pixel_area
        self.max_dimension = max_dimension
        self.max_frames = max_frames
        self.max_duration_sec = max_duration_sec
        self.motion_fps = motion_fps
        self.child_grace_sec = child_grace_sec
        self._motion_cache: OrderedDict[tuple[str, float], list[MotionSample]] = OrderedDict()
        self.last_stats: dict = {}

    # ------------------------------------------------------------------ child dispatch

    def _call(self, op: str, kwargs: dict, *, timeout: float, deadline: Deadline | None = None) -> dict:
        if timeout <= 0:
            raise PipelineError("deadline_exceeded", f"no budget left for {op}")
        t0 = time.monotonic()
        if not self.isolate:
            reply = D.run_op(op, kwargs)
        else:
            env = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL")}
            env["PYTHONPATH"] = os.pathsep.join([_SRC_ROOT, *[p for p in sys.path if p]])
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            try:
                proc = subprocess.run(
                    self.worker_cmd,
                    input=json.dumps({"op": op, "kwargs": kwargs}).encode(), capture_output=True,
                    timeout=timeout + self.child_grace_sec, check=False, env=env)
            except subprocess.TimeoutExpired:
                if deadline is not None and deadline.expired():
                    raise PipelineError("deadline_exceeded", f"{op} exceeded the job deadline") from None
                raise PipelineError("decode_failed", f"decoder child timed out during {op}") from None
            try:
                reply = json.loads(proc.stdout.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                raise PipelineError("decode_failed",
                                    f"decoder child exited abnormally (rc={proc.returncode})") from None
        self.last_stats = {"op": op, "wall_ms": int((time.monotonic() - t0) * 1000),
                           "isolated": int(self.isolate)}
        if not reply.get("ok"):
            raise PipelineError(reply.get("code", "decode_failed"), reply.get("detail", ""))
        result = reply["result"]
        child = result.get("stats") if isinstance(result, dict) else None
        if child is None and isinstance(result, dict) and "manifest" in result:
            child = result["manifest"].get("decode_stats")
        if child is None and isinstance(result, dict) and "peak_rss_mb" in result:
            child = {"peak_rss_mb": result["peak_rss_mb"], "elapsed_ms": result.get("elapsed_ms")}
        self.last_stats["child"] = child or {}
        return result

    @staticmethod
    def _budget(deadline: Deadline) -> float:
        deadline.check("media")
        return deadline.remaining_sec()

    def _check_source(self, local_path: Path, meta: VideoMetadata) -> Path:
        p = validate_source(local_path)
        if p.stat().st_size != meta.size_bytes or sha256_file(p) != meta.source_sha256:
            raise PipelineError("invalid_media", "source changed since probe")
        return p

    # ------------------------------------------------------------------ MediaPort

    def probe_video(self, local_path: Path, *, timeout_sec: float) -> VideoMetadata:
        t_end = time.monotonic() + timeout_sec
        p = validate_source(local_path)
        if not self.ffprobe_bin:
            raise PipelineError("internal_error", "ffprobe not available")
        info = ffprobe(self.ffprobe_bin, p, timeout=max(0.0, t_end - time.monotonic()))
        fmt = (info.get("format") or {}).get("format_name", "")
        if fmt != MOV_DEMUXER:
            raise PipelineError("unsupported_container", "container is not QuickTime/MP4")
        streams = info.get("streams") or []
        vids = [s for s in streams if s.get("codec_type") == "video"
                and not (s.get("disposition") or {}).get("attached_pic")]
        if not vids:
            raise PipelineError("invalid_media", "no video stream")
        vs = vids[0]
        has_audio = any(s.get("codec_type") == "audio" for s in streams)
        try:
            w, h = int(vs["width"]), int(vs["height"])
        except (KeyError, ValueError, TypeError):
            raise PipelineError("invalid_media", "video stream has no dimensions") from None
        if w <= 0 or h <= 0:
            raise PipelineError("invalid_media", "invalid video dimensions")
        if w > self.max_dimension or h > self.max_dimension or w * h > self.max_pixel_area:
            raise PipelineError("limits_exceeded", "frame size exceeds decode guard")
        try:
            header_frames = int(vs.get("nb_frames") or 0)
        except ValueError:
            header_frames = 0
        if header_frames > self.max_frames:
            raise PipelineError("limits_exceeded", "frame count exceeds decode guard")
        try:
            dur = float(vs.get("duration") or (info.get("format") or {}).get("duration") or 0)
        except ValueError:
            dur = 0.0
        if dur > self.max_duration_sec:
            raise PipelineError("limits_exceeded", "duration exceeds decode guard")
        rot = _rotation_cw(vs)
        if rot not in (0, 90, 180, 270):
            raise PipelineError("invalid_media", "unsupported display rotation")
        size = p.stat().st_size
        sha = sha256_file(p)
        av_info = self._call("probe_av", {"path": str(p), "max_frames": self.max_frames,
                                          "deadline_sec": max(0.0, t_end - time.monotonic())},
                             timeout=max(0.0, t_end - time.monotonic()))
        warnings: list[str] = list(av_info["warnings"])
        frame_rot = (-int(av_info["rotation_ccw"])) % 360
        if frame_rot != rot:
            warnings.append("decoder and container rotation metadata disagree; container value used")
        if av_info["codec"] != vs.get("codec_name"):
            warnings.append("decoder codec differs from ffprobe codec name")
        if list(av_info["coded"]) != [w, h]:
            raise PipelineError("invalid_media", "decoded frame size differs from stream header")
        sar = _fraction(vs.get("sample_aspect_ratio"))
        pre_w = w
        if sar is not None and sar != 1:
            pre_w = max(1, round(w * sar))
            warnings.append(f"non-square pixels (SAR {sar.numerator}:{sar.denominator}) applied")
        dw, dh = (pre_w, h) if rot in (0, 180) else (h, pre_w)
        if has_audio:
            warnings.append("audio stream present; ignored")
        if av_info["duration_sec"] > self.max_duration_sec:
            raise PipelineError("limits_exceeded", "duration exceeds decode guard")
        tb_num, tb_den = av_info["time_base"]
        return VideoMetadata(
            source_sha256=sha, size_bytes=size, container=fmt, codec=av_info["codec"],
            width=w, height=h, display_rotation=rot, display_width=dw, display_height=dh,
            duration_sec=av_info["duration_sec"], time_base_num=tb_num, time_base_den=tb_den,
            origin_pts=av_info["origin_pts"], origin_method=av_info["origin_method"],
            first_frame_sec=av_info["first_frame_sec"], last_frame_sec=av_info["last_frame_sec"],
            nominal_fps=av_info["avg_rate"], frame_count=av_info["frame_count"],
            has_audio=has_audio, decode_warnings=[x[:400] for x in warnings])

    def sample_video(self, local_path: Path, meta: VideoMetadata, plan: SamplingPlan, work_dir: Path,
                     *, deadline: Deadline) -> FrameManifest:
        p = self._check_source(local_path, meta)
        work_dir.mkdir(parents=True, exist_ok=True)
        budget = self._budget(deadline)
        res = self._call("sample", {
            "path": str(p), "meta": meta.model_dump(mode="json"), "plan": plan.model_dump(mode="json"),
            "work_dir": str(work_dir.resolve()), "motion_fps": self.motion_fps, "deadline_sec": budget,
        }, timeout=budget, deadline=deadline)
        if res.get("motion") is not None:
            self._cache_motion(meta.source_sha256, float(res["motion_fps"]),
                               [MotionSample.model_validate(m) for m in res["motion"]])
        man = res["manifest"]
        man["decode_stats"].update({"wall_ms": self.last_stats["wall_ms"],
                                    "isolated": self.last_stats["isolated"]})
        return FrameManifest.model_validate(man)

    def _cache_motion(self, sha: str, fps: float, samples: list[MotionSample]) -> None:
        self._motion_cache[(sha, fps)] = samples
        self._motion_cache.move_to_end((sha, fps))
        while len(self._motion_cache) > 4:
            self._motion_cache.popitem(last=False)

    def motion_profile(self, local_path: Path, meta: VideoMetadata, *, fps: float,
                       deadline: Deadline) -> list[MotionSample]:
        cached = self._motion_cache.get((meta.source_sha256, float(fps)))
        if cached is not None:
            return list(cached)
        p = self._check_source(local_path, meta)
        budget = self._budget(deadline)
        res = self._call("motion", {"path": str(p), "meta": meta.model_dump(mode="json"),
                                    "fps": float(fps), "deadline_sec": budget},
                         timeout=budget, deadline=deadline)
        samples = [MotionSample.model_validate(m) for m in res["motion"]]
        self._cache_motion(meta.source_sha256, float(fps), samples)
        return samples

    def frames_for_times(self, local_path: Path, meta: VideoMetadata, times_sec: list[float],
                         work_dir: Path, *, long_side_px: int, deadline: Deadline) -> list[FrameRef]:
        p = self._check_source(local_path, meta)
        work_dir.mkdir(parents=True, exist_ok=True)
        budget = self._budget(deadline)
        res = self._call("frames_for_times", {
            "path": str(p), "meta": meta.model_dump(mode="json"), "times_sec": [float(t) for t in times_sec],
            "work_dir": str(work_dir.resolve()), "long_side_px": int(long_side_px), "deadline_sec": budget,
        }, timeout=budget, deadline=deadline)
        return [FrameRef.model_validate(f) for f in res["frames"]]

    def extract_evidence(self, local_path: Path, meta: VideoMetadata, frame_refs: list[FrameRef],
                         out_dir: Path, *, long_side_px: int, jpeg_quality: int,
                         deadline: Deadline) -> list[EvidenceFrame]:
        if not 50 <= jpeg_quality <= 100:
            raise PipelineError("internal_error", "jpeg_quality out of range")
        p = self._check_source(local_path, meta)
        out_dir.mkdir(parents=True, exist_ok=True)
        budget = self._budget(deadline)
        res = self._call("evidence", {
            "path": str(p), "meta": meta.model_dump(mode="json"),
            "frame_refs": [r.model_dump(mode="json") for r in frame_refs],
            "out_dir": str(out_dir.resolve()), "long_side_px": int(long_side_px),
            "jpeg_quality": int(jpeg_quality), "deadline_sec": budget,
        }, timeout=budget, deadline=deadline)
        return [EvidenceFrame.model_validate(e) for e in res["evidence"]]

    def resolve_frame(self, frame: FrameRef, work_dir: Path) -> Path:
        return resolve_existing(work_dir, frame.local_path)

    def make_preview(self, local_path: Path, meta: VideoMetadata, out_path: Path, *,
                     deadline: Deadline, max_sec: float) -> dict | None:
        """Browser preview (H.264 MP4, no audio, rotation applied, t=0 == presentation origin).
        Returns None when it cannot be produced and verified within ``max_sec``."""
        if not self.ffmpeg_bin:
            return None
        budget = min(max_sec, deadline.remaining_sec() - 1.0)
        if budget <= 1.0:
            return None
        try:
            p = self._check_source(local_path, meta)
        except PipelineError:
            return None
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if out_path.is_symlink():
            return None
        tmp = out_path.with_name(f".{out_path.name}.{os.getpid()}.part.mp4")
        long = "1280"
        vf = (f"scale='if(gte(iw,ih),trunc(min({long},iw)/2)*2,-2)':"
              f"'if(gte(iw,ih),-2,trunc(min({long},ih)/2)*2)',format=yuv420p")
        args = [self.ffmpeg_bin, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                "-protocol_whitelist", "file", "-i", f"file:{p}",
                "-map", "0:v:0", "-an", "-sn", "-dn", "-map_metadata", "-1", "-map_chapters", "-1",
                "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-bf", "0",
                "-g", "30", "-fps_mode", "passthrough", "-movflags", "+faststart",
                "-f", "mp4", f"file:{tmp}"]
        t0 = time.monotonic()
        try:
            proc = run_tool(args, timeout=budget, what="preview")
            if proc.returncode != 0 or not tmp.exists():
                return None
            check = self._verify_preview(tmp, meta)
            if check is None:
                return None
            os.replace(tmp, out_path)
        except PipelineError:
            return None
        finally:
            tmp.unlink(missing_ok=True)
        check.update({"offset_sec": 0.0, "codec": "h264", "encoder": "libx264", "audio": False,
                      "elapsed_sec": round(time.monotonic() - t0, 3)})
        return check

    @staticmethod
    def _verify_preview(path: Path, meta: VideoMetadata) -> dict | None:
        """First preview frame must be at t=0 and the frame count/last time must match the
        source presentation timeline (within one frame interval)."""
        try:
            with av.open(str(path), options={"protocol_whitelist": "file"}) as c:
                vs = c.streams.video[0]
                tb = vs.time_base
                first = last = None
                n = 0
                for pkt in c.demux(vs):
                    if pkt.pts is None or pkt.is_discard:
                        continue
                    n += 1
                    t = pkt.pts
                    first = t if first is None else min(first, t)
                    last = t if last is None else max(last, t)
                c.seek(0)
                frame0 = next(iter(c.decode(vs)))
                w, h = frame0.width, frame0.height
                t_first = float(frame0.pts * tb) if frame0.pts is not None else None
        except (av.FFmpegError, StopIteration, IndexError):
            return None
        if first is None or t_first is None:
            return None
        start = float(first * tb)
        last_rel = float((last - first) * tb)
        tol = 1.0 / max(meta.nominal_fps or 1.0, 1.0)
        if abs(t_first - start) > 1e-6 or abs(start) > 1e-6:
            return None
        if meta.frame_count is not None and n != meta.frame_count:
            return None
        if abs(last_rel - meta.last_frame_sec) > tol:
            return None
        return {"width": w, "height": h, "frame_count": n, "first_frame_sec": 0.0,
                "last_frame_sec": round(last_rel, 6)}
