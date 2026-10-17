"""T06 / T07 provenance checks, preview alignment, motion hint and path containment."""

from __future__ import annotations

import base64
import os

import av
import numpy as np
import pytest
from PIL import Image

from cashwatch.deadline import Deadline
from cashwatch.errors import PipelineError
from cashwatch.media import create_media

from .conftest import read_barcode, synth, uniform_plan


@pytest.fixture(scope="module")
def sampled(fx, media, tmp_path_factory):
    work = tmp_path_factory.mktemp("work")
    p = fx.path("editlist_10s")
    meta = media.probe_video(p, timeout_sec=30)
    man = media.sample_video(p, meta, uniform_plan(meta), work, deadline=Deadline.in_seconds(60))
    return p, meta, man, work


def test_evidence_dedup_and_base64(sampled, media, tmp_path):
    p, meta, man, _ = sampled
    f = man.frames[2]
    ev = media.extract_evidence(p, meta, [f, f], tmp_path, long_side_px=640, jpeg_quality=90,
                                deadline=Deadline.in_seconds(30))
    assert len(ev) == 1 and ev[0].evidence_id == "e" + f.frame_id
    raw = (tmp_path / ev[0].jpeg_path).read_bytes()
    b64 = base64.b64encode(raw).decode()
    assert base64.b64decode(b64) == raw and len(raw) > 1000
    assert ev[0].jpeg_quality == 90 and max(ev[0].width, ev[0].height) == 640


def test_evidence_rejects_foreign_or_tampered_refs(sampled, media, tmp_path):
    p, meta, man, _ = sampled
    f = man.frames[3]
    bad = [
        f.model_copy(update={"source_sha256": "0" * 64}),
        f.model_copy(update={"pts": f.pts + 1, "frame_id": f"p{f.pts + 1}"}),  # PTS not a frame
        f.model_copy(update={"relative_sec": f.relative_sec + 0.5}),  # relabelled time
        f.model_copy(update={"frame_id": "p999999"}),
    ]
    for ref in bad:
        with pytest.raises(PipelineError) as ei:
            media.extract_evidence(p, meta, [ref], tmp_path, long_side_px=640, jpeg_quality=88,
                                   deadline=Deadline.in_seconds(30))
        assert ei.value.code == "evidence_invalid"


def test_evidence_never_upscales(fx, media, tmp_path):
    p = fx.path("mov_renamed_bin")  # 640x360 source
    meta = media.probe_video(p, timeout_sec=30)
    ref = media.frames_for_times(p, meta, [1.0], tmp_path / "w", long_side_px=640, deadline=Deadline.in_seconds(30))
    ev = media.extract_evidence(p, meta, ref, tmp_path / "o", long_side_px=1280, jpeg_quality=88,
                                deadline=Deadline.in_seconds(30))
    assert (ev[0].width, ev[0].height) == (640, 360)
    assert not any(t.kind == "resize" for t in ev[0].transforms)


@pytest.mark.parametrize("name", ["editlist_10s", "rotated_cw90_10s", "offset_start_10s", "audio_10s"])
def test_preview_aligned_to_origin(fx, media, tmp_path, name):
    p = fx.path(name)
    meta = media.probe_video(p, timeout_sec=30)
    out = tmp_path / "preview.mp4"
    info = media.make_preview(p, meta, out, deadline=Deadline.in_seconds(120), max_sec=60)
    assert info is not None and info["offset_sec"] == 0.0 and info["codec"] == "h264"
    truth = fx.truth(name)
    with av.open(str(out)) as c:
        assert not c.streams.audio
        vs = c.streams.video[0]
        frames = []
        for i, fr in enumerate(c.decode(vs)):
            frames.append(fr)
            if i >= 30:
                break
        first = frames[0]
        assert float(first.pts * vs.time_base) == 0.0
        g = first.to_image().convert("L")
        assert (g.width, g.height) == (info["width"], info["height"])
        assert (g.width > g.height) == (meta.display_width > meta.display_height)
        if meta.display_rotation:
            g = g.rotate(meta.display_rotation, expand=True)
        assert synth.decode_barcode(np.asarray(g)) == truth["frames"][0]["barcode"]
        # frame 30 of the preview == frame 30 of the source presentation timeline
        f30 = frames[30]
        assert float(f30.pts * vs.time_base) == pytest.approx(truth["frames"][30]["rel"], abs=1e-3)
    assert info["frame_count"] == meta.frame_count


def test_preview_returns_none_without_budget(fx, media, tmp_path):
    p = fx.path("cfr_10s_30")
    meta = media.probe_video(p, timeout_sec=30)
    assert media.make_preview(p, meta, tmp_path / "x.mp4", deadline=Deadline.in_seconds(60), max_sec=0.5) is None
    assert not (tmp_path / "x.mp4").exists()


def test_motion_profile_marks_marker_jump(fx, tmp_path):
    p = fx.path("marker_violation_40s")
    m = create_media()
    meta = m.probe_video(p, timeout_sec=30)
    standalone = m.motion_profile(p, meta, fps=4.0, deadline=Deadline.in_seconds(60))
    assert len(standalone) == 160
    times = [s.relative_sec for s in standalone]
    assert times == sorted(times) and times[0] == 0.0
    # Both marker twins carry identical frame barcodes, so their per-sample difference isolates
    # the red square jumping out of the zone at t=20.0 s.
    neg_p = fx.path("marker_negative_40s")
    neg = m.motion_profile(neg_p, m.probe_video(neg_p, timeout_sec=30), fps=4.0, deadline=Deadline.in_seconds(60))
    assert [s.relative_sec for s in neg] == times
    diff = [(a.relative_sec, a.score - b.score) for a, b in zip(standalone, neg, strict=True)]
    peak = max(diff, key=lambda d: abs(d[1]))
    assert peak[0] == pytest.approx(20.0, abs=1e-6) and peak[1] > 0
    assert peak[1] > 20 * max(abs(d) for t, d in diff if abs(t - 20.0) > 0.3) + 1e-6
    # the combined sampling pass yields the same profile (served from cache afterwards)
    m2 = create_media()
    m2.sample_video(p, meta, uniform_plan(meta), tmp_path, deadline=Deadline.in_seconds(60))
    assert m2.motion_profile(p, meta, fps=4.0, deadline=Deadline.in_seconds(1)) == standalone


def test_marker_fixture_ground_truth(fx, media, tmp_path):
    """The orchestrator's e2e fixture: red square inside the zone until exactly t=20.0 s."""
    for name, leaves in (("marker_violation_40s", True), ("marker_negative_40s", False)):
        p = fx.path(name)
        meta = media.probe_video(p, timeout_sec=30)
        refs = media.frames_for_times(p, meta, [19.95, 19.967, 20.0, 39.9], tmp_path / name,
                                      long_side_px=1280, deadline=Deadline.in_seconds(30))
        assert [r.relative_sec for r in refs] == pytest.approx([19.966667, 20.0, 39.9], abs=1e-5)
        for r in refs:
            with Image.open(tmp_path / name / r.local_path) as im:
                inside = im.getpixel((960, 400))
                outside = im.getpixel((360, 400))
            red_inside = inside[0] > 180 and inside[1] < 80
            red_outside = outside[0] > 180 and outside[1] < 80
            moved = leaves and r.relative_sec >= 20.0
            assert red_inside == (not moved) and red_outside == moved


def test_resolve_frame_containment(sampled, media, tmp_path):
    _, _, man, work = sampled
    f = man.frames[0]
    assert media.resolve_frame(f, work).is_file()
    outside = tmp_path / "secret.jpg"
    outside.write_bytes(b"x")
    link_rel = "frames/link.jpg"
    os.symlink(outside, work / link_rel)
    try:
        with pytest.raises(PipelineError):
            media.resolve_frame(f.model_copy(update={"local_path": link_rel}), work)
        with pytest.raises(PipelineError):
            media.resolve_frame(f.model_copy(update={"local_path": "frames/missing.jpg"}), work)
    finally:
        (work / link_rel).unlink()


def test_thumbnail_reads_back_same_frame(sampled):
    _, meta, man, work = sampled
    codes = [read_barcode(work / f.local_path, meta.display_rotation) for f in man.frames]
    assert codes == sorted(codes) and codes[0] == 75  # edit list starts at source frame 75 (t=2.5 s)
