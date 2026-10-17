"""Guarded decoder child: ``python -m cashwatch.media._worker`` reads one JSON request
``{"op": str, "kwargs": {...}}`` from stdin and writes one JSON reply to stdout.

Running PyAV/libavcodec in a separate process lets the parent kill a hung or crashing decoder
(malformed input, decoder bugs) without losing the worker process, and bounds its memory to
the child's lifetime. Diagnostics never include media content.
"""

from __future__ import annotations

import json
import sys


def main() -> int:
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    except (ImportError, ValueError, OSError):
        pass
    out = sys.stdout
    sys.stdout = sys.stderr  # stray prints must not corrupt the reply channel
    try:
        req = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        from cashwatch.media.decode import run_op

        reply = run_op(req["op"], req["kwargs"])
    except Exception as exc:  # noqa: BLE001 - reported as typed failure to the parent
        reply = {"ok": False, "code": "internal_error", "detail": f"child error: {type(exc).__name__}"}
    out.write(json.dumps(reply))
    out.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
