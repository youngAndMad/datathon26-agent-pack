"""MLX-VLM backend (Apple Silicon, Metal). Runs inside the model child only."""

from __future__ import annotations

import time
from typing import Any

from cashwatch.inference.backends import build_messages, check_model_dir, decode_images
from cashwatch.inference.protocol import GenRequest, GenResult


class MlxBackend:
    def __init__(self, model_path: str, options: dict[str, Any]) -> None:
        self.model_path = model_path
        self.options = options
        self.loaded = False
        self.model = None
        self.processor = None

    def load(self) -> dict[str, Any]:
        path = check_model_dir(self.model_path)
        import mlx.core as mx
        from mlx_vlm import load
        self._mx = mx
        # A local directory path never reaches the hub (get_model_path only downloads when
        # the path does not exist); HF_HUB_OFFLINE=1 is forced in the child as well.
        self.model, self.processor = load(str(path))
        ip = self.processor.image_processor
        if self.options.get("max_pixels"):
            ip.max_pixels = int(self.options["max_pixels"])
        self.loaded = True
        return {
            "device": "mlx-metal",
            "mlx": getattr(mx, "__version__", "unknown"),
            "image_processor": type(ip).__name__,
            "min_pixels": ip.min_pixels,
            "max_pixels": ip.max_pixels,
        }

    def generate(self, req: GenRequest) -> GenResult:
        from mlx_vlm import generate
        mx = self._mx
        proc = self.processor
        ip = proc.image_processor
        images = decode_images(req.images, req.max_long_side)
        msgs = build_messages(req.system, req.segments, images, lambda _im: {"type": "image"})
        prompt = proc.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)
        kw: dict[str, Any] = {}
        if req.max_pixels:
            kw["max_pixels"] = int(req.max_pixels)
        vis = [int(ip.num_image_tokens(im.height, im.width, **kw)) for im in images]
        if req.max_pixels:
            ip.max_pixels, saved = int(req.max_pixels), ip.max_pixels
        else:
            saved = None
        mx.reset_peak_memory()
        t0 = time.monotonic()
        try:
            r = generate(self.model, proc, prompt, image=images or None,
                         max_tokens=int(req.max_tokens), temperature=0.0, verbose=False)
        finally:
            if saved is not None:
                ip.max_pixels = saved
        gen_ms = int((time.monotonic() - t0) * 1000)
        actual_vis = None
        if images and r.prompt_tokens:
            # each <|image_pad|> placeholder in the template expands to that image's tokens
            base = len(proc.tokenizer(prompt, add_special_tokens=False)["input_ids"])
            actual_vis = int(r.prompt_tokens) - base + len(images)
        return GenResult(
            text=r.text,
            prompt_tokens=int(r.prompt_tokens) if r.prompt_tokens is not None else None,
            output_tokens=int(r.generation_tokens) if r.generation_tokens is not None else None,
            finish_reason=getattr(r, "finish_reason", None),
            visual_tokens=vis,
            visual_tokens_actual_total=actual_vis,
            image_sizes=[(im.width, im.height) for im in images],
            gen_ms=gen_ms,
            peak_memory_bytes=int(mx.get_peak_memory()),
            prompt_tps=float(r.prompt_tps) if r.prompt_tps else None,
            generation_tps=float(r.generation_tps) if r.generation_tps else None,
        )
