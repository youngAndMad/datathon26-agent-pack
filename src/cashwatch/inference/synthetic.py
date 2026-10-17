"""Synthetic plumbing frames for inference tests and benchmarks ONLY (agent C).

Gray background, a yellow "zone" rectangle, and a red square that moves along a scripted path
so it is inside or outside the zone. Ground truth is known by construction, which allows a
*plumbing* sanity check (does the model perceive a simple visible state change) - this says
nothing about cashier-domain accuracy. Never used by the production pipeline.
"""

from __future__ import annotations

import hashlib
import math
from pathlib import Path

from PIL import Image, ImageDraw

from cashwatch.dto import FrameRef, Transform

ZONE = (0.33, 0.30, 0.67, 0.72)  # relative x0, y0, x1, y1
FAKE_SOURCE_SHA = hashlib.sha256(b"synthetic-plumbing-source").hexdigest()


def square_inside(t: float, period: float = 8.0) -> bool:
    """Scripted ground truth: inside for the first half of each period."""
    return (t % period) < period / 2


def draw_frame(t: float, *, width: int = 640, height: int = 360, period: float = 8.0) -> Image.Image:
    im = Image.new("RGB", (width, height), (128, 128, 128))
    d = ImageDraw.Draw(im)
    zx0, zy0, zx1, zy1 = (ZONE[0] * width, ZONE[1] * height, ZONE[2] * width, ZONE[3] * height)
    d.rectangle((zx0, zy0, zx1, zy1), outline=(255, 215, 0), width=max(3, width // 160))
    side = max(12, width // 16)
    cy = (zy0 + zy1) / 2 + math.sin(t) * (zy1 - zy0) * 0.15
    if square_inside(t, period):
        cx = (zx0 + zx1) / 2 + math.cos(t) * (zx1 - zx0) * 0.15
    else:
        cx = width * (0.12 if int(t // period) % 2 == 0 else 0.88)
    d.rectangle((cx - side / 2, cy - side / 2, cx + side / 2, cy + side / 2), fill=(220, 30, 30))
    d.text((8, 8), f"t={t:06.2f}", fill=(235, 235, 235))
    return im


def write_frames(work_dir: Path, times: list[float], *, prefix: str = "f", width: int = 640,
                 height: int = 360, period: float = 8.0, time_base_den: int = 600,
                 subdir: str = "thumbs") -> list[FrameRef]:
    out_dir = Path(work_dir) / subdir
    out_dir.mkdir(parents=True, exist_ok=True)
    refs: list[FrameRef] = []
    for i, t in enumerate(times):
        rel = f"{subdir}/{prefix}{i:04d}.jpg"
        p = Path(work_dir) / rel
        draw_frame(t, width=width, height=height, period=period).save(p, format="JPEG", quality=90)
        pts = round(t * time_base_den)
        refs.append(FrameRef(
            frame_id=f"{prefix}{i:04d}", source_sha256=FAKE_SOURCE_SHA, ordinal=i, pts=pts,
            time_base_num=1, time_base_den=time_base_den, relative_sec=pts / time_base_den,
            local_path=rel, sha256=hashlib.sha256(p.read_bytes()).hexdigest(), width=width,
            height=height, transforms=[Transform(kind="resize", parameters={"long_side": max(width, height)})],
        ))
    return refs
