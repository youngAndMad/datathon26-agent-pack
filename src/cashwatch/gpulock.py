"""Shared accelerator reservation (advisory file lock). Hold it for any real-model run so that
parallel agents/benchmarks do not duplicate model memory or invalidate timings."""

from __future__ import annotations

import errno
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

if os.name == "nt":
    import msvcrt
else:
    import fcntl


def _lock(fh) -> None:
    if os.name == "nt":
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(fh) -> None:
    if os.name == "nt":
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(fh, fcntl.LOCK_UN)

LOCK_PATH = Path(os.environ.get("CASHWATCH_GPU_LOCK", REPO_ROOT / "data" / ".locks" / "gpu.lock"))


@contextmanager
def gpu_reservation(owner: str, *, timeout_sec: float = 3600.0) -> Iterator[None]:
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Windows can lock a byte beyond EOF; never truncate or replace the lock file.
    with open(LOCK_PATH, "a+b") as fh:
        start = time.monotonic()
        while True:
            try:
                _lock(fh)
                break
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                    raise
                if time.monotonic() - start > timeout_sec:
                    raise TimeoutError("GPU reservation timed out") from None
                time.sleep(min(0.1, max(0.0, timeout_sec - (time.monotonic() - start))))
        try:
            yield
        finally:
            _unlock(fh)
