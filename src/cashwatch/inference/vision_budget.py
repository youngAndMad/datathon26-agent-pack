"""Qwen3-VL image sizing math (pure Python, no ML imports).

Mirrors the processor's ``smart_resize`` (patch 16, merge 2 -> factor 32; min_pixels 65536 and
max_pixels 16777216 from the shipped preprocessor_config.json) so the parent can compute the
exact visual-token cost of a call *before* sending it, refuse an over-budget call (split the
window instead of truncating) and cross-check the child's actual token counts.
"""

from __future__ import annotations

import math

PATCH = 16
MERGE = 2
FACTOR = PATCH * MERGE
DEFAULT_MIN_PIXELS = 65536
DEFAULT_MAX_PIXELS = 16777216


def smart_resize(height: int, width: int, *, min_pixels: int = DEFAULT_MIN_PIXELS,
                 max_pixels: int = DEFAULT_MAX_PIXELS, factor: int = FACTOR) -> tuple[int, int]:
    if max(height, width) / min(height, width) > 200:
        raise ValueError("aspect ratio too extreme")
    h_bar = round(height / factor) * factor
    w_bar = round(width / factor) * factor
    if h_bar * w_bar > max_pixels:
        beta = math.sqrt((height * width) / max_pixels)
        h_bar = max(factor, math.floor(height / beta / factor) * factor)
        w_bar = max(factor, math.floor(width / beta / factor) * factor)
    elif h_bar * w_bar < min_pixels:
        beta = math.sqrt(min_pixels / (height * width))
        h_bar = math.ceil(height * beta / factor) * factor
        w_bar = math.ceil(width * beta / factor) * factor
    return h_bar, w_bar


def fit_long_side(width: int, height: int, max_long_side: int | None) -> tuple[int, int]:
    """Aspect-preserving downscale applied by the backend before the processor (never upscale)."""
    if not max_long_side or max(width, height) <= max_long_side:
        return width, height
    s = max_long_side / max(width, height)
    return max(1, round(width * s)), max(1, round(height * s))


def image_tokens(width: int, height: int, *, max_long_side: int | None = None,
                 max_pixels: int | None = None) -> int:
    w, h = fit_long_side(width, height, max_long_side)
    rh, rw = smart_resize(h, w, max_pixels=max_pixels or DEFAULT_MAX_PIXELS)
    return (rh // PATCH) * (rw // PATCH) // (MERGE * MERGE)
