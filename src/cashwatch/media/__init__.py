"""Media timeline and evidence (agent B): local-file-only probing, PTS-exact sampling and
real-JPEG evidence extraction. Entry point: :func:`create_media`."""

from __future__ import annotations

from cashwatch.media.service import MediaService
from cashwatch.ports import MediaPort


def create_media(**kwargs: object) -> MediaPort:
    """Production MediaPort. Keyword overrides (tests/tuning): isolate, max_pixel_area,
    max_dimension, max_frames, max_duration_sec, motion_fps, ffprobe_bin, ffmpeg_bin."""
    return MediaService(**kwargs)  # type: ignore[arg-type]


__all__ = ["MediaService", "create_media"]
