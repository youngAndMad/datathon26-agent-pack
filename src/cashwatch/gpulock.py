"""Shared accelerator reservation (advisory file lock). Hold it for any real-model run so that
parallel agents/benchmarks do not duplicate model memory or invalidate timings."""

from __future__ import annotations

import fcntl
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from cashwatch.settings import REPO_ROOT

LOCK_PATH = Path(os.environ.get("CASHWATCH_GPU_LOCK", REPO_ROOT / "data" / ".locks" / "gpu.lock"))


@contextmanager
def gpu_reservation(owner: str, *, timeout_sec: float = 3600.0) -> Iterator[None]:
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOCK_PATH, "a+") as fh:
        start = time.monotonic()
        while True:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() - start > timeout_sec:
                    raise TimeoutError("GPU reservation timed out") from None
                time.sleep(1.0)
        fh.seek(0)
        fh.truncate()
        fh.write(f"{owner} pid={os.getpid()}\n")
        fh.flush()
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)
