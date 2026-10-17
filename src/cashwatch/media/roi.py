"""Explicit, local-only model crops from native source frames.

The caller must retain the full-frame context and enforce its batch budget. Crops
are model inputs, never substitutes for original full-frame evidence JPEGs.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from cashwatch.dto import FrameRef, Transform
from cashwatch.errors import PipelineError
from cashwatch.media.paths import resolve_existing, safe_write


def crop_frame(
    frame: FrameRef, image_path: Path, work_dir: Path,
    roi: list[float], long_side_px: int,
) -> FrameRef:
    """Crop an explicitly configured normalized ROI without changing its timeline.

    Input must be the unresized, uncropped display-oriented source frame. A native
    frame cannot be recovered from a thumbnail; callers must decode it separately.
    Coordinates are relative to the display-oriented image, not the coded video.
    """
    if (len(roi) != 4 or any(isinstance(v, bool) or not isinstance(v, (int, float))
                            or not math.isfinite(v) for v in roi)):
        raise PipelineError("internal_error", "ROI requires four finite normalized coordinates")
    x0, y0, x1, y1 = roi
    if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
        raise PipelineError("internal_error", "ROI must be a nonempty normalized rectangle")
    if isinstance(long_side_px, bool) or not isinstance(long_side_px, int) or long_side_px <= 0:
        raise PipelineError("internal_error", "ROI long side must be a positive integer")
    if any(t.kind in {"resize", "crop"} for t in frame.transforms):
        raise PipelineError("evidence_invalid", "ROI requires a native full-frame image")
    source = resolve_existing(work_dir, frame.local_path)
    if image_path.is_symlink() or image_path.resolve() != source:
        raise PipelineError("evidence_invalid", "ROI image does not match the frame path")
    try:
        data = source.read_bytes()
        if hashlib.sha256(data).hexdigest() != frame.sha256:
            raise PipelineError("evidence_invalid", "ROI source image hash mismatch")
        with Image.open(io.BytesIO(data)) as image:
            if image.size != (frame.width, frame.height):
                raise PipelineError("evidence_invalid", "ROI source dimensions mismatch")
            image.load()
            box = (math.floor(x0 * image.width), math.floor(y0 * image.height),
                   math.ceil(x1 * image.width), math.ceil(y1 * image.height))
            crop = image.crop(box).convert("RGB")
            transforms = list(frame.transforms) + [Transform(kind="crop", parameters={
                "normalized_roi": list(roi), "box_xyxy": list(box),
                "source_frame_id": frame.frame_id,
                "source_width": frame.width, "source_height": frame.height,
            })]
            if max(crop.size) > long_side_px:
                before = crop.size
                scale = long_side_px / max(before)
                size = tuple(max(1, round(side * scale)) for side in before)
                crop = crop.resize(size, Image.Resampling.LANCZOS)
                transforms.append(Transform(kind="resize", parameters={
                    "from_width": before[0], "from_height": before[1],
                    "width": crop.width, "height": crop.height, "resample": "lanczos",
                }))
            stream = io.BytesIO()
            crop.save(stream, format="JPEG", quality=90, subsampling=0)
            encoded = stream.getvalue()
            width, height = crop.size
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise PipelineError("evidence_invalid", "ROI image could not be decoded") from exc
    transforms.append(Transform(kind="jpeg_encode", parameters={"quality": 90, "subsampling": 0}))
    identity = json.dumps({
        "frame": frame.model_dump(mode="json"), "roi": list(roi),
        "long_side_px": long_side_px, "encoding": "jpeg-90-444-v1",
    }, sort_keys=True, separators=(",", ":")).encode("utf-8")
    frame_id = "roi-" + hashlib.sha256(identity).hexdigest()[:32]
    relative = f"roi/{frame_id}.jpg"
    safe_write(work_dir, relative, encoded)
    return frame.model_copy(update={
        "frame_id": frame_id, "local_path": relative,
        "sha256": hashlib.sha256(encoded).hexdigest(), "width": width, "height": height,
        "transforms": transforms,
    })
