"""Typed pipeline failure. ``detail`` is local-only diagnostics: never returned to clients and
must not contain media/scene text, prompts or secrets."""

from __future__ import annotations

from cashwatch.dto import ERROR_MESSAGES_RU, ErrorCode, ErrorInfo


class PipelineError(Exception):
    def __init__(self, code: ErrorCode, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code: ErrorCode = code
        self.detail = detail

    def public(self) -> ErrorInfo:
        return ErrorInfo(code=self.code, message=ERROR_MESSAGES_RU[self.code])


def public_error(code: ErrorCode) -> ErrorInfo:
    return ErrorInfo(code=code, message=ERROR_MESSAGES_RU[code])
