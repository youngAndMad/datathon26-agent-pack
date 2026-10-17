"""Offline Qwen3-VL inference on CPU, CUDA or Apple MPS.

The supervisor owns deadlines and terminates this child on cancellation. Allocation
errors deliberately propagate; retrying on another device would reset its budget.
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any

from cashwatch.inference.backends import build_messages, check_model_dir, decode_images
from cashwatch.inference.protocol import GenRequest, GenResult, OFFLINE_ENV


class HfBackend:
    def __init__(self, model_path: str, options: dict[str, Any]) -> None:
        self.model_path = model_path
        self.options = options
        self.loaded = False
        self.model = None
        self.processor = None

    def _device(self, torch) -> str:
        want = str(self.options.get("device") or "auto")
        if want != "auto":
            return want
        # Keep Windows auto conservative until GPU capacity is explicitly chosen.
        if sys.platform == "win32":
            return "cpu"
        if torch.cuda.is_available():
            return "cuda"
        if torch.backends.mps.is_available():
            return "mps"
        return "cpu"

    def load(self) -> dict[str, Any]:
        if self.loaded:
            return self.load_info
        path = check_model_dir(self.model_path).resolve()
        os.environ.update(OFFLINE_ENV)
        import torch
        import transformers
        from transformers import AutoProcessor, Qwen3VLForConditionalGeneration

        device = torch.device(self._device(torch))
        if device.type not in {"cpu", "cuda", "mps"}:
            raise ValueError("HF backend supports cpu, cuda or mps devices")
        if device.type == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("CUDA unavailable; configure device=cpu explicitly")
        if device.type == "mps" and not torch.backends.mps.is_available():
            raise RuntimeError("MPS unavailable; configure device=cpu explicitly")
        # Preserve the reference BF16 path; Windows CUDA also supports older FP16 GPUs.
        dtype = (torch.float32 if device.type == "cpu" else
                 torch.float16 if sys.platform == "win32" and device.type == "cuda" else
                 torch.bfloat16)
        processor = AutoProcessor.from_pretrained(
            str(path), local_files_only=True, trust_remote_code=False, use_fast=False,
        )
        model = Qwen3VLForConditionalGeneration.from_pretrained(
            str(path), local_files_only=True, trust_remote_code=False,
            dtype=dtype, device_map=str(device), attn_implementation="sdpa",
        )
        model.eval()
        gc = model.generation_config
        gc.do_sample, gc.temperature, gc.top_p, gc.top_k = False, None, None, None
        self._torch = torch
        self.device = device
        self.model, self.processor = model, processor
        self.image_token_id = model.config.image_token_id
        ip = processor.image_processor
        self.load_info = {
            "device": str(device), "torch": torch.__version__,
            "transformers": transformers.__version__, "dtype": str(dtype),
            "attn": "sdpa",
            "image_processor": type(ip).__name__,
            "min_pixels": getattr(ip, "min_pixels", None),
            "max_pixels": self.options.get("max_pixels") or getattr(ip, "max_pixels", None),
        }
        self.loaded = True
        return self.load_info

    def generate(self, req: GenRequest) -> GenResult:
        if not self.loaded:
            raise RuntimeError("model not loaded")
        if type(req.max_tokens) is not int or not 1 <= req.max_tokens <= 32768:
            raise ValueError("max_tokens must be between 1 and 32768")
        # Exactly one occurrence per image preserves the adapter's token accounting.
        indices = [value for kind, value in req.segments if kind == "image"]
        if any(type(i) is not int for i in indices) or indices != list(range(len(req.images))):
            raise ValueError("images must appear exactly once in original order")
        images = decode_images(req.images, req.max_long_side)
        proc, torch = self.processor, self._torch
        messages = build_messages(req.system, req.segments, images,
                                  lambda im: {"type": "image", "image": im})
        max_pixels = req.max_pixels or self.options.get("max_pixels")
        ip = proc.image_processor
        saved = None
        if max_pixels is not None:
            if int(max_pixels) <= 0:
                raise ValueError("max_pixels must be positive")
            saved = dict(ip.size)
            ip.size = {"shortest_edge": saved.get("shortest_edge", 65536),
                       "longest_edge": int(max_pixels)}
        try:
            inputs = proc.apply_chat_template(messages, tokenize=True, add_generation_prompt=True,
                                             return_dict=True, return_tensors="pt")
        finally:
            if saved is not None:
                ip.size = saved
        grid = inputs.get("image_grid_thw")
        merge = int(proc.image_processor.merge_size) ** 2
        visual = [int(row.prod().item()) // merge for row in grid] if grid is not None else []
        if len(visual) != len(images):
            raise ValueError("processor image count mismatch")
        actual = int((inputs["input_ids"] == self.image_token_id).sum().item())
        if sum(visual) != actual:
            raise ValueError("processor visual token count mismatch")
        prompt_tokens = int(inputs["input_ids"].shape[-1])
        inputs = inputs.to(self.device)
        cuda = self.device.type == "cuda"
        if cuda:
            torch.cuda.synchronize(self.device)
            torch.cuda.reset_peak_memory_stats(self.device)
        elif self.device.type == "mps":
            torch.mps.synchronize()
        start = time.monotonic()
        with torch.inference_mode():
            output = self.model.generate(
                **inputs, max_new_tokens=req.max_tokens, do_sample=False,
                num_beams=1, use_cache=True, return_dict_in_generate=False,
            )
        if cuda:
            torch.cuda.synchronize(self.device)
        elif self.device.type == "mps":
            torch.mps.synchronize()
        elapsed_ms = int((time.monotonic() - start) * 1000)
        generated = output[0, prompt_tokens:]
        count = int(generated.shape[-1])
        eos = self.model.generation_config.eos_token_id
        eos_ids = set(eos if isinstance(eos, (list, tuple)) else [eos])
        stopped = count > 0 and int(generated[-1].item()) in eos_ids
        text = proc.batch_decode([generated], skip_special_tokens=True,
                                 clean_up_tokenization_spaces=False)[0]
        return GenResult(
            text=text, prompt_tokens=prompt_tokens, output_tokens=count,
            finish_reason="stop" if stopped else "length" if count >= req.max_tokens else "stop",
            visual_tokens=visual, visual_tokens_actual_total=actual,
            image_sizes=[im.size for im in images], gen_ms=elapsed_ms,
            peak_memory_bytes=(int(torch.cuda.max_memory_allocated(self.device)) if cuda else
                               int(torch.mps.driver_allocated_memory()) if self.device.type == "mps" else None),
            generation_tps=(count / (elapsed_ms / 1000)) if elapsed_ms else None,
        )
