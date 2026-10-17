"""T05 / T06: presentation timeline, complete planned coverage and timestamp round-trips.

Ground truth is independent of PyAV: ffprobe frame PTS (edit lists applied) zipped with the
synthetic frame barcode decoded from ffmpeg rawvideo (see scripts/make_synthetic_media.py).
"""

from __future__ import annotations

import hashlib
import random
import subprocess
import time
from io import BytesIO

import numpy as np
import pytest
from PIL import Image

from cashwatch.deadline import Deadline
from cashwatch.dto import ts
from cashwatch.media import create_media

from .conftest import expected_frame, preceding_gap, read_barcode, synth, truth_rel_for_pts, uniform_plan

TIMELINE_FIXTURES = [
    "cfr_10s_2997", "cfr_10s_30", "cfr_10s_60", "hevc_10s", "vfr_10s", "offset_start_10s",
    "bframes_10s", "editlist_10s", "rotated_cw90_10s", "rotated_ccw90_10s", "audio_10s",
    "mov_renamed_bin",
]
EXPECTED_ROTATION = {"rotated_cw90_10s": 90, "rotated_ccw90_10s": 270}


def _check_manifest(fx, media, name, tmp_path, *, long_side=640):
    truth = fx.truth(name)
    p = fx.path(name)
    meta = media.probe_video(p, timeout_sec=30)
    frames = truth["frames"]
    # probe agrees with ffprobe presentation timeline
    assert [meta.time_base_num, meta.time_base_den] == truth["time_base"]
    assert meta.origin_pts == frames[0]["pts"]
    assert meta.frame_count == len(frames)
    assert meta.first_frame_sec == 0.0
    assert meta.last_frame_sec == pytest.approx(frames[-1]["rel"], abs=1e-9)
    assert meta.duration_sec > meta.last_frame_sec
    assert meta.display_rotation == EXPECTED_ROTATION.get(name, 0)
    assert all(f["barcode"] is not None for f in frames)

    plan = uniform_plan(meta, long_side=long_side, extra_end=meta.duration_sec + 5.0)
    man = media.sample_video(p, meta, plan, tmp_path, deadline=Deadline.in_seconds(120))
    by_id = {f.frame_id: f for f in man.frames}
    assert len(by_id) == len(man.frames), "frames must be unique"
    assert [f.relative_sec for f in man.frames] == sorted(f.relative_sec for f in man.frames)
    assert man.coverage_first_sec == 0.0
    assert man.coverage_last_sec == pytest.approx(frames[-1]["rel"], abs=1e-9)
    assert set(man.window_frames) == {w.window_id for w in plan.windows}

    for w in plan.windows:
        ids = man.window_frames[w.window_id]
        got_rels = [by_id[i].relative_sec for i in ids]
        assert got_rels == sorted(got_rels)
        for t in w.requested_sample_times:
            exp = expected_frame(truth, t)
            match = [by_id[i] for i in ids if by_id[i].pts == exp["pts"]]
            assert match, f"{name}: target {t} -> expected pts {exp['pts']} missing in {w.window_id}"
            f = match[0]
            if t <= frames[-1]["rel"]:
                assert 0 <= f.relative_sec - t <= preceding_gap(truth, exp) + 1e-9

    rot = meta.display_rotation
    for f in man.frames:
        img = man_path = tmp_path / f.local_path
        assert man_path.is_file()
        data = man_path.read_bytes()
        assert hashlib.sha256(data).hexdigest() == f.sha256
        with Image.open(BytesIO(data)) as im:
            assert im.format == "JPEG" and im.size == (f.width, f.height)
        assert max(f.width, f.height) == min(long_side, max(meta.display_width, meta.display_height))
        assert abs(f.width / f.height - meta.display_width / meta.display_height) < 0.01
        # actual time of the actual frame (barcode) matches ffprobe's timeline
        exp_rel = truth_rel_for_pts(truth, f.pts)
        assert exp_rel is not None and f.relative_sec == pytest.approx(exp_rel, abs=1e-9)
        code = read_barcode(img, rot)
        assert code == next(x["barcode"] for x in frames if x["pts"] == f.pts), f"{name}: barcode mismatch"
        assert f.ordinal == next(i for i, x in enumerate(frames) if x["pts"] == f.pts)
        assert media.resolve_frame(f, tmp_path) == man_path.resolve()
    return meta, man, truth


@pytest.mark.parametrize("name", TIMELINE_FIXTURES)
def test_sampling_timeline_and_evidence_roundtrip(fx, media, tmp_path, name):
    meta, man, truth = _check_manifest(fx, media, name, tmp_path / "work")
    p = fx.path(name)
    # evidence: anchor + context frames re-decoded at the same PTS (T06/T07)
    picks = [man.frames[1], man.frames[len(man.frames) // 2], man.frames[-1]]
    ev = media.extract_evidence(p, meta, picks, tmp_path / "out", long_side_px=1280, jpeg_quality=88,
                                deadline=Deadline.in_seconds(60))
    assert [e.frame_id for e in ev] == [f.frame_id for f in picks]
    for e, f in zip(ev, picks, strict=True):
        assert e.pts == f.pts and e.timestamp_sec == ts(f.relative_sec)
        jp = tmp_path / "out" / e.jpeg_path
        assert e.jpeg_path.startswith("evidence/") and jp.is_file()
        data = jp.read_bytes()
        assert data[:3] == b"\xff\xd8\xff" and hashlib.sha256(data).hexdigest() == e.jpeg_sha256
        with Image.open(BytesIO(data)) as im:
            im.load()
            assert im.format == "JPEG" and im.size == (e.width, e.height)
        assert max(e.width, e.height) == min(1280, max(meta.display_width, meta.display_height))
        assert read_barcode(jp, meta.display_rotation) == read_barcode(tmp_path / "work" / f.local_path,
                                                                      meta.display_rotation)
        assert e.source_sha256 == meta.source_sha256
        assert {t.kind for t in e.transforms} >= {"color_convert", "jpeg_encode"}
        assert any(t.kind == "rotate" for t in e.transforms) == bool(meta.display_rotation)


@pytest.mark.parametrize("name", ["hevc_10s", "editlist_10s", "bframes_10s", "vfr_10s", "offset_start_10s"])
def test_seek_refinement_matches_selection_rule(fx, media, tmp_path, name):
    truth = fx.truth(name)
    p = fx.path(name)
    meta = media.probe_video(p, timeout_sec=30)
    rng = random.Random(7)
    times = sorted({round(rng.uniform(0, meta.duration_sec + 1), 3) for _ in range(20)})
    times += [0.0, 4.0, 4.01, 4.02]  # exact frame times and near-duplicates
    refs = media.frames_for_times(p, meta, times, tmp_path, long_side_px=320, deadline=Deadline.in_seconds(60))
    exp_pts = sorted({expected_frame(truth, t)["pts"] for t in times})
    assert [r.pts for r in refs] == exp_pts
    for r in refs:
        assert read_barcode(tmp_path / r.local_path, meta.display_rotation) == next(
            x["barcode"] for x in truth["frames"] if x["pts"] == r.pts)


@pytest.mark.parametrize("name", ["rotated_cw90_10s", "rotated_ccw90_10s"])
def test_rotation_matches_ffmpeg_autorotate(fx, media, tmp_path, name):
    """Our display rotation must equal what ffmpeg (player semantics) shows."""
    p = fx.path(name)
    meta = media.probe_video(p, timeout_sec=30)
    ref = media.frames_for_times(p, meta, [0.0], tmp_path, long_side_px=320, deadline=Deadline.in_seconds(30))[0]
    png = subprocess.run([synth.FFMPEG, "-v", "error", "-i", str(p), "-frames:v", "1", "-vf",
                          f"scale={ref.width}:{ref.height}", "-f", "image2pipe", "-c:v", "png", "pipe:1"],
                         capture_output=True, check=True, timeout=60).stdout
    with Image.open(BytesIO(png)) as a, Image.open(tmp_path / ref.local_path) as b:
        assert a.size == b.size
        diff = np.abs(np.asarray(a.convert("L"), dtype=np.int16) - np.asarray(b.convert("L"), dtype=np.int16))
    assert float(diff.mean()) < 8.0


def test_in_process_mode_matches_child(fx, tmp_path):
    p = fx.path("bframes_10s")
    a, b = create_media(isolate=True), create_media(isolate=False)
    meta = a.probe_video(p, timeout_sec=30)
    assert b.probe_video(p, timeout_sec=30) == meta
    plan = uniform_plan(meta)
    ma = a.sample_video(p, meta, plan, tmp_path / "a", deadline=Deadline.in_seconds(60))
    mb = b.sample_video(p, meta, plan, tmp_path / "b", deadline=Deadline.in_seconds(60))
    assert ma.frames == mb.frames and ma.window_frames == mb.window_frames


def test_145s_coverage_and_latency(fx, media, tmp_path):
    """T05 long clip: complete coverage incl. first/last interval; sampling budget < 20 s."""
    name = "cfr_145s_2997_1080p"
    fx.path(name)
    t0 = time.monotonic()
    meta, man, truth = _check_manifest(fx, media, name, tmp_path)
    elapsed = time.monotonic() - t0
    assert meta.frame_count == 4346 and meta.nominal_fps == pytest.approx(29.97, abs=0.01)
    assert len(man.frames) >= 146
    assert man.decode_stats["wall_ms"] < 20_000
    assert elapsed < 60  # includes barcode verification of every thumbnail
