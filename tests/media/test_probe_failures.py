"""T03 / T04: invalid, unsupported, corrupt and oversized inputs fail safely with typed codes."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from cashwatch.deadline import Deadline
from cashwatch.errors import PipelineError
from cashwatch.media import create_media

from .conftest import uniform_plan


@pytest.mark.parametrize(("name", "code"), [
    ("empty", "invalid_media"),
    ("png_renamed", "unsupported_container"),
    ("mkv_renamed", "unsupported_container"),
    ("truncated_nomoov", "invalid_media"),
    ("truncated_faststart", "decode_failed"),
    ("huge_res", "limits_exceeded"),
])
def test_bad_inputs_fail_with_typed_code(fx, media, name, code):
    with pytest.raises(PipelineError) as ei:
        media.probe_video(fx.path(name), timeout_sec=20)
    assert ei.value.code == code
    # public projection never leaks local diagnostics
    assert ei.value.public().code == code and str(fx.out) not in ei.value.public().message


def test_valid_mov_renamed_bin_is_accepted(fx, media):
    meta = media.probe_video(fx.path("mov_renamed_bin"), timeout_sec=20)
    assert meta.container.startswith("mov,mp4") and meta.frame_count == len(fx.truth("mov_renamed_bin")["frames"])


def test_garbage_bytes_and_non_files(fx, media, tmp_path):
    junk = tmp_path / "junk.mov"
    junk.write_bytes(os.urandom(4096))
    with pytest.raises(PipelineError) as ei:
        media.probe_video(junk, timeout_sec=10)
    assert ei.value.code in ("invalid_media", "unsupported_container")
    for bad in (tmp_path, Path("http://example.com/a.mov"), "file:/etc/hosts", tmp_path / "missing.mov"):
        with pytest.raises(PipelineError) as ei:
            media.probe_video(bad, timeout_sec=10)  # type: ignore[arg-type]
        assert ei.value.code == "invalid_media"


def test_frame_count_guard(fx):
    with pytest.raises(PipelineError) as ei:
        create_media(max_frames=1000).probe_video(fx.path("many_frames"), timeout_sec=20)
    assert ei.value.code == "limits_exceeded"
    meta = create_media().probe_video(fx.path("many_frames"), timeout_sec=20)
    assert meta.frame_count == 1200


def test_pixel_area_guard_is_configurable(fx):
    with pytest.raises(PipelineError) as ei:
        create_media(max_pixel_area=640 * 360).probe_video(fx.path("cfr_10s_30"), timeout_sec=20)
    assert ei.value.code == "limits_exceeded"


def test_probe_timeout_is_typed(fx, media):
    with pytest.raises(PipelineError) as ei:
        media.probe_video(fx.path("cfr_10s_30"), timeout_sec=0.0)
    assert ei.value.code in ("deadline_exceeded", "decode_failed")


def test_sampling_respects_deadline(fx, media, tmp_path):
    p = fx.path("cfr_145s_2997_1080p")
    meta = media.probe_video(p, timeout_sec=30)
    with pytest.raises(PipelineError) as ei:
        media.sample_video(p, meta, uniform_plan(meta), tmp_path, deadline=Deadline.in_seconds(0.8))
    assert ei.value.code == "deadline_exceeded"
    with pytest.raises(PipelineError) as ei:
        media.sample_video(p, meta, uniform_plan(meta), tmp_path, deadline=Deadline.in_seconds(-1))
    assert ei.value.code == "deadline_exceeded"


def test_source_swapped_after_probe_is_rejected(fx, media, tmp_path):
    meta = media.probe_video(fx.path("cfr_10s_30"), timeout_sec=20)
    with pytest.raises(PipelineError) as ei:
        media.sample_video(fx.path("cfr_10s_60"), meta, uniform_plan(meta), tmp_path,
                           deadline=Deadline.in_seconds(60))
    assert ei.value.code == "invalid_media"


def test_hung_decoder_child_is_killed(fx, tmp_path):
    """T16 (media part): a decoder child that never answers is killed at the budget."""
    import time

    hang = tmp_path / "hang.sh"
    hang.write_text("#!/bin/sh\nexec sleep 60\n")
    hang.chmod(0o755)
    p = fx.path("cfr_10s_30")
    meta = create_media().probe_video(p, timeout_sec=20)
    m = create_media(worker_cmd=[str(hang)], child_grace_sec=0.2)
    t0 = time.monotonic()
    with pytest.raises(PipelineError) as ei:
        m.sample_video(p, meta, uniform_plan(meta), tmp_path / "w", deadline=Deadline.in_seconds(1.0))
    assert ei.value.code == "deadline_exceeded" and time.monotonic() - t0 < 5
    t0 = time.monotonic()
    with pytest.raises(PipelineError) as ei:
        m.probe_video(p, timeout_sec=1.0)
    assert ei.value.code == "decode_failed" and time.monotonic() - t0 < 5
    # a hung ffprobe is bounded too
    m2 = create_media(ffprobe_bin=str(hang))
    with pytest.raises(PipelineError) as ei:
        m2.probe_video(p, timeout_sec=1.0)
    assert ei.value.code == "decode_failed"


def test_crashing_decoder_child_is_typed(fx, tmp_path):
    crash = tmp_path / "crash.sh"
    crash.write_text("#!/bin/sh\nkill -SEGV $$\n")
    crash.chmod(0o755)
    p = fx.path("cfr_10s_30")
    with pytest.raises(PipelineError) as ei:
        create_media(worker_cmd=[str(crash)]).probe_video(p, timeout_sec=10)
    assert ei.value.code == "decode_failed"
