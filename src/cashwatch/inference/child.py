"""Model child process entry point: ``python -m cashwatch.inference.child``.

Holds exactly one resident model. Reads requests from stdin and writes replies to the
original stdout fd; ordinary prints are redirected to stderr so they cannot corrupt the
protocol. Exits on stdin EOF (parent gone) or ``shutdown``.

Offline flags are forced before any ML import. Backends: ``mlx``, ``transformers`` and the
test-only ``scripted`` backend (never selectable through create_perception).
"""

from __future__ import annotations

import os

from cashwatch.inference.protocol import OFFLINE_ENV  # noqa: E402  (no ML imports)

for _k, _v in OFFLINE_ENV.items():
    os.environ[_k] = _v

import sys  # noqa: E402
import time  # noqa: E402
import traceback  # noqa: E402
from typing import Any  # noqa: E402

from cashwatch.inference.protocol import GenRequest, is_oom_message, read_frame, write_frame  # noqa: E402


def make_backend(name: str, model_path: str, options: dict[str, Any]):
    if name == "mlx":
        from cashwatch.inference.backends.mlx_backend import MlxBackend
        return MlxBackend(model_path, options)
    if name == "transformers":
        from cashwatch.inference.backends.hf_backend import HfBackend
        return HfBackend(model_path, options)
    if name == "scripted":
        from cashwatch.inference.backends.scripted import ScriptedBackend
        return ScriptedBackend(model_path, options)
    raise ValueError(f"unknown backend {name!r}")


def _err(seq: int, kind: str, exc: BaseException) -> dict[str, Any]:
    msg = f"{type(exc).__name__}: {exc}"[:800]
    if kind != "bad_request" and is_oom_message(msg):
        kind = "oom"
    return {"seq": seq, "ok": False, "kind": kind, "detail": msg}


def main() -> int:
    proto_out = os.fdopen(os.dup(1), "wb")
    os.dup2(2, 1)  # stray prints -> stderr
    sys.stdout = sys.stderr
    in_fd = sys.stdin.fileno()
    backend = None
    while True:
        try:
            msg = read_frame(in_fd, None)
        except Exception:
            return 0  # parent gone
        seq = msg.get("seq", -1)
        op = msg.get("op")
        try:
            if op == "init":
                backend = make_backend(msg["backend"], msg["model_path"], msg.get("options", {}))
                reply = {"seq": seq, "ok": True, "pid": os.getpid()}
            elif op == "load":
                if backend is None:
                    raise RuntimeError("init required")
                t0 = time.monotonic()
                try:
                    info = backend.load()
                except Exception as e:  # noqa: BLE001
                    write_frame(proto_out, _err(seq, "load_failed", e))
                    traceback.print_exc()
                    continue
                reply = {"seq": seq, "ok": True, "load_ms": int((time.monotonic() - t0) * 1000), "info": info}
            elif op == "generate":
                if backend is None or not backend.loaded:
                    raise RuntimeError("model not loaded")
                req: GenRequest = msg["request"]
                res = backend.generate(req)
                reply = {"seq": seq, "ok": True, "result": res}
            elif op == "ping":
                reply = {"seq": seq, "ok": True, "pid": os.getpid()}
            elif op == "shutdown":
                write_frame(proto_out, {"seq": seq, "ok": True})
                return 0
            else:
                write_frame(proto_out, _err(seq, "bad_request", ValueError(f"unknown op {op!r}")))
                continue
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            reply = _err(seq, "internal", e)
        write_frame(proto_out, reply)


if __name__ == "__main__":
    raise SystemExit(main())
