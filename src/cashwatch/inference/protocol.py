"""Parent <-> model-child wire protocol: length-prefixed pickle frames over the child's
stdin/stdout pipes (local, private, parent-spawned child only; never a network socket).

Request ops: init, load, generate, ping, shutdown. Every reply carries the request ``seq``.
Error kinds: oom, load_failed, bad_request, internal.
"""

from __future__ import annotations

import os
import pickle
import select
import struct
import time
from dataclasses import dataclass, field
from typing import Any, BinaryIO

OFFLINE_ENV = {
    "HF_HUB_OFFLINE": "1",
    "HF_HUB_DISABLE_TELEMETRY": "1",
    "TRANSFORMERS_OFFLINE": "1",
    "HF_DATASETS_OFFLINE": "1",
    "DO_NOT_TRACK": "1",
    "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1",
    "TOKENIZERS_PARALLELISM": "false",
    "TRANSFORMERS_NO_ADVISORY_WARNINGS": "1",
    "TRANSFORMERS_VERBOSITY": "error",
    "PYTORCH_ENABLE_MPS_FALLBACK": "0",
}

_HDR = struct.Struct(">I")
MAX_FRAME_BYTES = 256 * 1024 * 1024


class ProtocolTimeout(Exception):
    pass


class ProtocolEOF(Exception):
    pass


def write_frame(fh: BinaryIO, obj: Any) -> None:
    data = pickle.dumps(obj, protocol=pickle.HIGHEST_PROTOCOL)
    fh.write(_HDR.pack(len(data)) + data)
    fh.flush()


def _read_exact(fd: int, n: int, deadline: float | None) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProtocolTimeout()
            ready, _, _ = select.select([fd], [], [], remaining)
            if not ready:
                raise ProtocolTimeout()
        chunk = os.read(fd, n - len(buf))
        if not chunk:
            raise ProtocolEOF()
        buf.extend(chunk)
    return bytes(buf)


def read_frame(fd: int, timeout_sec: float | None = None) -> Any:
    deadline = None if timeout_sec is None else time.monotonic() + max(0.0, timeout_sec)
    (n,) = _HDR.unpack(_read_exact(fd, _HDR.size, deadline))
    if n > MAX_FRAME_BYTES:
        raise ProtocolEOF()
    return pickle.loads(_read_exact(fd, n, deadline))


OOM_MARKERS = (
    "out of memory", "unable to allocate", "metal::malloc", "insufficient memory",
    "outofmemory", "mps backend out of memory", "resource exhausted",
)


def is_oom_message(msg: str) -> bool:
    m = msg.lower()
    return any(k in m for k in OOM_MARKERS)


@dataclass
class GenRequest:
    """One bounded generation call. ``images`` are verified image file bytes (parent read and
    hashed them inside the job work dir); the child never opens job paths itself."""
    system: str
    segments: list[tuple[str, Any]]
    images: list[bytes]
    max_tokens: int
    max_long_side: int | None = None
    max_pixels: int | None = None
    # in-process fake/scripted responders only (ignored by real backends)
    context: dict[str, Any] = field(default_factory=dict)


@dataclass
class GenResult:
    text: str
    prompt_tokens: int | None
    output_tokens: int | None
    finish_reason: str | None
    visual_tokens: list[int]
    visual_tokens_actual_total: int | None
    image_sizes: list[tuple[int, int]]
    gen_ms: int
    peak_memory_bytes: int | None
    prompt_tps: float | None = None
    generation_tps: float | None = None
