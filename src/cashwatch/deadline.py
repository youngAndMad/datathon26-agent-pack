"""Deadline accounting: persistent UTC expiry + monotonic in-process budget. Never reset on retry."""

from __future__ import annotations

import time
from datetime import UTC, datetime

from cashwatch.errors import PipelineError


def utc_now() -> datetime:
    return datetime.now(UTC)


def parse_utc(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        raise ValueError("timestamp must carry a timezone")
    return dt.astimezone(UTC)


def iso_utc(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="milliseconds")


class Deadline:
    """Hard deadline. Remaining budget is derived conservatively from the persisted UTC
    deadline at construction and then tracked with the monotonic clock."""

    def __init__(self, deadline_at_utc: str, *, soft_margin_sec: float = 30.0) -> None:
        self.deadline_at_utc = deadline_at_utc
        remaining = (parse_utc(deadline_at_utc) - utc_now()).total_seconds()
        self._mono_end = time.monotonic() + remaining
        self.soft_margin_sec = soft_margin_sec

    @classmethod
    def in_seconds(cls, seconds: float, **kw: float) -> Deadline:
        from datetime import timedelta
        return cls(iso_utc(utc_now() + timedelta(seconds=seconds)), **kw)

    def remaining_sec(self) -> float:
        return self._mono_end - time.monotonic()

    def expired(self) -> bool:
        return self.remaining_sec() <= 0

    def soft_expired(self) -> bool:
        """Past the soft deadline: stop optional work (extra verification, cosmetics)."""
        return self.remaining_sec() <= self.soft_margin_sec

    def require_budget(self, stage: str, estimated_sec: float) -> None:
        if self.remaining_sec() < estimated_sec:
            raise PipelineError("deadline_exceeded", f"insufficient budget for {stage}")

    def check(self, stage: str) -> None:
        if self.expired():
            raise PipelineError("deadline_exceeded", f"deadline reached during {stage}")
