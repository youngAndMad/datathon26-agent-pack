"""Durable job storage (agent A). Factory used by pipeline wiring: ``create_store(settings)``."""

from __future__ import annotations

from cashwatch.settings import Settings
from cashwatch.storage.store import (
    CapacityFull,
    ClaimedJobImpl,
    IdempotencyConflict,
    JobStore,
    JobView,
    is_job_id,
)

__all__ = [
    "CapacityFull", "ClaimedJobImpl", "IdempotencyConflict", "JobStore", "JobView", "create_store",
    "is_job_id",
]


def create_store(settings: Settings) -> JobStore:
    return JobStore(settings)
