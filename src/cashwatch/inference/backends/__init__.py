"""Model backends executed inside the model child process only."""

from __future__ import annotations

import io
from pathlib import Path

from cashwatch.inference.vision_budget import fit_long_side


def check_model_dir(model_path: str) -> Path:
    """Refuse missing folders and any executable remote code (trust_remote_code stays off)."""
    p = Path(model_path)
    if not p.is_dir() or not (p / "config.json").is_file():
        raise FileNotFoundError("model folder or config.json missing")
    if not any(p.glob("*.safetensors")):
        raise FileNotFoundError("no safetensors weights in model folder")
    for need in ("tokenizer.json", "tokenizer_config.json", "preprocessor_config.json"):
        if not (p / need).is_file():
            raise FileNotFoundError(f"{need} missing in model folder")
    py = [q.name for q in p.glob("*.py")]
    if py:
        raise PermissionError("model folder contains python files; remote code is not allowed")
    return p


def decode_images(images: list[bytes], max_long_side: int | None):
    from PIL import Image
    out = []
    for data in images:
        im = Image.open(io.BytesIO(data))
        im.load()
        im = im.convert("RGB")
        w, h = fit_long_side(im.width, im.height, max_long_side)
        if (w, h) != im.size:
            im = im.resize((w, h), Image.Resampling.BICUBIC)
        out.append(im)
    return out


def build_messages(system: str, segments, images, image_entry):
    """Chat messages with images interleaved in segment order. ``image_entry(img)`` returns the
    backend-specific content item for one image."""
    content = []
    for kind, value in segments:
        if kind == "text":
            content.append({"type": "text", "text": value})
        elif kind == "image":
            content.append(image_entry(images[value]))
        else:
            raise ValueError("bad segment kind")
    return [{"role": "system", "content": system}, {"role": "user", "content": content}]
