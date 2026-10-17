"""Transformers backend (reference/comparison): Qwen3-VL bf16, SDPA, MPS (or CUDA/CPU).

Runs inside the model child only. local_files_only=True, trust_remote_code=False, greedy
decoding (do_sample=False). Same GenRequest/GenResult interface as the MLX backend.
"""

from __future__ import annotations

import time
from typing import Any

from cashwatch.inference.backends import build_messages, check_model_dir, decode_images
from cashwatch.inference.protocol import GenRequest, GenResult


class HfBackend:
    def __init__(self, model_path: str, options: dict[str, Any]) -> None:
        self.model_path = model_path
        self.options = options
        self.loaded = False

    def _device(self, torch) -> str:
        want = str(self.options.get("device") or "auto")
        if want != "auto":
            return want
        if torch.cuda.is_available():
            return "cuda"
        if torch.backends.mps.is_available():
            return "mps"
        return "cpu"

    def load(self) -> dict[str, Any]:
        path = check_model_dir(self.model_path)
        import torch
        import transformers
        from transformers import AutoProcessor, Qwen3VLForConditionalGeneration
        self._torch = torch
        self.device = self._device(torch)
        dtype = torch.bfloat16 if self.device in ("mps", "cuda") else torch.float32
        self.processor = AutoProcessor.from_pretrained(str(path), local_files_only=True, trust_remote_code=False)
        self.model = Qwen3VLForConditionalGeneration.from_pretrained(
            str(path), local_files_only=True, trust_remote_code=False, dtype=dtype,
            attn_implementation="sdpa",
        ).to(self.device).eval()
        gc = self.model.generation_config
        gc.do_sample, gc.temperature, gc.top_p, gc.top_k = False, None, None, None
        self.image_token_id = self.model.config.image_token_id
        self.loaded = True
        return {"device": f"torch-{self.device}", "torch": torch.__version__,
                "transformers": transformers.__version__, "dtype": str(dtype),
                "attn": "sdpa", "image_processor": type(self.processor.image_processor).__name__}

    def _peak(self) -> int | None:
        torch = self._torch
        if self.device == "mps":
            return int(torch.mps.driver_allocated_memory())
        if self.device == "cuda":
            return int(torch.cuda.max_memory_allocated())
        return None

    def generate(self, req: GenRequest) -> GenResult:
        torch = self._torch
        images = decode_images(req.images, req.max_long_side)
        msgs = build_messages(req.system, req.segments, images, lambda im: {"type": "image", "image": im})
        ip = self.processor.image_processor
        saved = None
        if req.max_pixels:
            saved = dict(ip.size)
            ip.size = {"shortest_edge": saved.get("shortest_edge", 65536), "longest_edge": int(req.max_pixels)}
        try:
            inputs = self.processor.apply_chat_template(msgs, tokenize=True, add_generation_prompt=True,
                                                        return_dict=True, return_tensors="pt")
        finally:
            if saved is not None:
                ip.size = saved
        inputs = inputs.to(self.device)
        n_prompt = int(inputs["input_ids"].shape[1])
        vis: list[int] = []
        if "image_grid_thw" in inputs:
            vis = [int(x) // 4 for x in inputs["image_grid_thw"].prod(-1).tolist()]
        actual = int((inputs["input_ids"] == self.image_token_id).sum().item()) if images else None
        if self.device == "cuda":
            torch.cuda.reset_peak_memory_stats()
        t0 = time.monotonic()
        with torch.inference_mode():
            out = self.model.generate(**inputs, max_new_tokens=int(req.max_tokens), do_sample=False)
        if self.device == "mps":
            torch.mps.synchronize()
        gen_ms = int((time.monotonic() - t0) * 1000)
        new = out[0, n_prompt:]
        eos = set(self.model.generation_config.eos_token_id or [])
        n_out = int(new.shape[0])
        finish = "stop" if n_out and int(new[-1]) in eos else ("length" if n_out >= req.max_tokens else "stop")
        text = self.processor.decode(new, skip_special_tokens=True)
        return GenResult(text=text, prompt_tokens=n_prompt, output_tokens=n_out, finish_reason=finish,
                         visual_tokens=vis, visual_tokens_actual_total=actual,
                         image_sizes=[(im.width, im.height) for im in images], gen_ms=gen_ms,
                         peak_memory_bytes=self._peak(), prompt_tps=None,
                         generation_tps=(n_out / (gen_ms / 1000)) if gen_ms else None)
