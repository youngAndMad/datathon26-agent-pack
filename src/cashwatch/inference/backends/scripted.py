"""TEST-ONLY child backend: no model, deterministic scripted replies.

Used by tests to exercise the real supervised process boundary (timeouts, kills, crashes,
OOM mapping) without weights. Options:
  load_sleep_sec: float      sleep during load
  gen_sleep_sec: float       sleep during every generate
  replies: list[str]         texts returned in order (last one repeats); default "{}"
  fail_with: str             raise RuntimeError(fail_with) during generate
  crash: bool                os._exit(3) during generate
Never selectable through create_perception.
"""

from __future__ import annotations

import os
import time
from typing import Any

from cashwatch.inference.protocol import GenRequest, GenResult


class ScriptedBackend:
    def __init__(self, model_path: str, options: dict[str, Any]) -> None:
        self.options = options
        self.loaded = False
        self._i = 0

    def load(self) -> dict[str, Any]:
        time.sleep(float(self.options.get("load_sleep_sec", 0.0)))
        if self.options.get("fail_load"):
            raise FileNotFoundError("scripted load failure")
        self.loaded = True
        return {"device": "scripted-test"}

    def generate(self, req: GenRequest) -> GenResult:
        time.sleep(float(self.options.get("gen_sleep_sec", 0.0)))
        if self.options.get("crash"):
            os._exit(3)
        if self.options.get("fail_with"):
            raise RuntimeError(self.options["fail_with"])
        replies = self.options.get("replies") or ["{}"]
        text = replies[min(self._i, len(replies) - 1)]
        self._i += 1
        return GenResult(text=text, prompt_tokens=0, output_tokens=0, finish_reason="stop",
                         visual_tokens=[0] * len(req.images), visual_tokens_actual_total=None,
                         image_sizes=[], gen_ms=0, peak_memory_bytes=None)
