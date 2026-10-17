"""Explicit TEST-MODE perception (adapter "fake", is_mock=True). Refused in production.

Runs in-process with a deterministic responder derived only from the request structure (ids),
never from media content or filenames. Default output: no observations, every cue/predicate
"unknown", interval_complete/visibility_sufficient false. Tests may install a scripted
responder via ``set_responder``; the real parsing/validation/repair path is still exercised.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from cashwatch.inference.protocol import GenRequest, GenResult

Responder = Callable[[GenRequest], str]


def default_responder(req: GenRequest) -> str:
    ctx = req.context
    kind = ctx.get("kind")
    unknown = [{"predicate_id": pid, "state": "unknown", "supporting_frame_ids": [],
                "contradicting_frame_ids": [], "visibility": "partial",
                "visible_reason": "test mode: no visual analysis performed"}
               for pid in ctx.get("predicate_ids", [])]
    if kind == "observe":
        return json.dumps({"window_id": ctx["window_id"], "observations": [],
                           "cue_assessments": unknown,
                           "limitations": ["test mode: mock perception, no visual analysis"]})
    if kind == "verify":
        return json.dumps({"candidate_id": ctx["candidate_id"], "assessments": unknown,
                           "plausible_alternatives": [], "interval_complete": False,
                           "visibility_sufficient": False,
                           "limitations": ["test mode: mock perception, no visual analysis"]})
    return "ok"


class InProcessGenerator:
    def __init__(self, responder: Responder | None = None) -> None:
        self.responder: Responder = responder or default_responder
        self._loaded = False
        self.load_ms: int | None = None
        self.load_info: dict[str, Any] = {"device": "none (mock)"}
        self.requests: list[GenRequest] = []

    def start(self, timeout_sec: float) -> int:
        self._loaded = True
        self.load_ms = 0
        return 0

    def alive(self) -> bool:
        return self._loaded

    def loaded(self) -> bool:
        return self._loaded

    def generate(self, req: GenRequest, timeout_sec: float) -> GenResult:
        self.requests.append(req)
        text = self.responder(req)
        return GenResult(text=text, prompt_tokens=0, output_tokens=0, finish_reason="stop",
                         visual_tokens=[0] * len(req.images), visual_tokens_actual_total=None,
                         image_sizes=[], gen_ms=0, peak_memory_bytes=None)

    def kill(self) -> None:
        self._loaded = False

    def close(self) -> None:
        self._loaded = False
