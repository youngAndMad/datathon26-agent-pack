"""Shared helpers for media tests. All media is SYNTHETIC (scripts/make_synthetic_media.py);
fixtures are generated on first use into tests/fixtures/generated/ (git-ignored)."""

from __future__ import annotations

import importlib.util
import json
import math
import sys
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from cashwatch.dto import SamplingPlan, SamplingWindow, VideoMetadata
from cashwatch.media import create_media

REPO = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("make_synthetic_media", REPO / "scripts" / "make_synthetic_media.py")
synth = importlib.util.module_from_spec(_spec)
sys.modules["make_synthetic_media"] = synth
_spec.loader.exec_module(synth)  # type: ignore[union-attr]


class Fixtures:
    def __init__(self, out: Path) -> None:
        self.out = out

    def path(self, name: str) -> Path:
        return synth.build(name, self.out)

    def truth(self, name: str) -> dict:
        self.path(name)
        return json.loads((self.out / f"{name}.truth.json").read_text())


@pytest.fixture(scope="session")
def fx() -> Fixtures:
    return Fixtures(synth.DEFAULT_OUT)


@pytest.fixture(scope="session")
def media():
    return create_media()  # production configuration: guarded decoder child process


def uniform_plan(meta: VideoMetadata, *, window: float = 12.0, overlap: float = 3.0, fps: float = 1.0,
                 long_side: int = 640, extra_end: float | None = None) -> SamplingPlan:
    """Uniform 1 fps windows covering [0, duration] incl. first frame and the end."""
    wins: list[SamplingWindow] = []
    start, k = 0.0, 0
    dur = meta.duration_sec
    while True:
        end = min(start + window, dur)
        n = int(math.floor((end - start) * fps + 1e-9))
        times = [round(start + i / fps, 6) for i in range(n + 1)]
        times.append(round(end, 6))
        if extra_end is not None and end >= dur:
            times.append(extra_end)
        wins.append(SamplingWindow(window_id=f"w{k:02d}", kind="coarse", start_sec=start, end_sec=end,
                                   requested_sample_times=sorted(set(times)), mandatory=True))
        k += 1
        if end >= dur:
            break
        start += window - overlap
    return SamplingPlan(profile_id="test-uniform", windows=wins, mandatory_window_ids=[w.window_id for w in wins],
                        maximum_frames_per_call=16, model_long_side_px=long_side)


def expected_frame(truth: dict, target: float) -> dict:
    """Ground-truth selection: first presented frame (ffprobe order) with rel >= target."""
    for f in truth["frames"]:
        if f["rel"] >= target - 1e-9:
            return f
    return truth["frames"][-1]


def preceding_gap(truth: dict, f: dict) -> float:
    """Gap between the selected frame and the previous presented frame (VFR-aware): the
    selected frame can be at most this much later than the requested time."""
    rels = [x["rel"] for x in truth["frames"]]
    i = rels.index(f["rel"])
    return rels[i] - rels[i - 1] if i > 0 else 0.0


def read_barcode(path: Path, display_rotation_cw: int) -> int | None:
    """Barcode of an exported image: undo the display rotation to reach stored orientation."""
    with Image.open(path) as im:
        g = im.convert("L")
        if display_rotation_cw:
            g = g.rotate(display_rotation_cw, expand=True)  # PIL rotate() is counter-clockwise
        return synth.decode_barcode(np.asarray(g))


def truth_rel_for_pts(truth: dict, pts: int) -> float | None:
    for f in truth["frames"]:
        if f["pts"] == pts:
            return f["rel"]
    return None


def tb(truth: dict) -> Fraction:
    return Fraction(*truth["time_base"])
