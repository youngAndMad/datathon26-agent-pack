"""Denied-egress inference check (agent C, synthetic frames only).

Outer process: takes the accelerator reservation, then runs this script again with --inner
under macOS ``sandbox-exec`` with outbound network denied (local unix sockets allowed) and
HF_HUB_OFFLINE=1. The inner process (and the model child it spawns, which inherits the
sandbox) first proves that outbound TCP/DNS is blocked, then performs one real warmup +
observe call on generated frames. Writes a JSON summary to stdout.

Usage: .venv/bin/python scripts/model_offline_check.py [--backend mlx]
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
POLICY = "(version 1)(allow default)(deny network-outbound)(allow network-outbound (remote unix-socket))"


def probe_network() -> dict:
    out = {}
    try:
        socket.create_connection(("1.1.1.1", 443), timeout=3).close()
        out["tcp_1.1.1.1:443"] = "CONNECTED"
    except OSError as e:
        out["tcp_1.1.1.1:443"] = f"blocked ({type(e).__name__}: {e.errno})"
    try:
        socket.create_connection(("huggingface.co", 443), timeout=3).close()
        out["tcp_huggingface.co:443"] = "CONNECTED"
    except OSError as e:
        out["tcp_huggingface.co:443"] = f"blocked ({type(e).__name__})"
    return out


def inner(backend: str) -> int:
    from cashwatch.deadline import Deadline
    from cashwatch.dto import ObservationRequest, PredicateDefinition
    from cashwatch.inference import create_perception
    from cashwatch.inference.synthetic import write_frames
    from cashwatch.settings import Settings

    res: dict = {"env_HF_HUB_OFFLINE": os.environ.get("HF_HUB_OFFLINE"), "network": probe_network()}
    kw = {"mode": "test", "backend": backend}
    if backend == "transformers":
        kw["model_path"] = REPO_ROOT / "models" / "qwen3-vl-4b-instruct"
    p = create_perception(Settings(**kw))
    with tempfile.TemporaryDirectory() as tmp:
        wd = Path(tmp)
        frames = write_frames(wd, [float(i) for i in range(8)])
        t = time.monotonic()
        try:
            p.warmup(timeout_sec=600)
            req = ObservationRequest(job_id="offline", window_id="w0", start_sec=0, end_sec=7, frames=frames,
                                     task_version="offline", max_output_tokens=256, cue_predicates=[
                                         PredicateDefinition(predicate_id="syn.red_outside_zone",
                                                             question_en="Is the red square outside the yellow rectangle in at least one frame?")])
            b = p.observe(req, wd, Deadline.in_seconds(300))
            res["inference"] = {"ok": True, "parse_status": b.parse_status, "observations": len(b.observations),
                                "output_tokens": b.output_tokens, "elapsed_sec": round(time.monotonic() - t, 2),
                                "model": p.info().model_dump()}
        except Exception as e:  # noqa: BLE001
            res["inference"] = {"ok": False, "error": f"{type(e).__name__}: {e}"[:300]}
        finally:
            p.close()
    print(json.dumps(res, indent=2))
    blocked = all(v.startswith("blocked") for v in res["network"].values())
    return 0 if blocked and res["inference"]["ok"] else 1


def outer(backend: str) -> int:
    from cashwatch.gpulock import gpu_reservation
    env = dict(os.environ, HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1")
    cmd = ["/usr/bin/sandbox-exec", "-p", POLICY, sys.executable, str(Path(__file__).resolve()),
           "--inner", "--backend", backend]
    with gpu_reservation(f"C-offline-{backend}"):
        r = subprocess.run(cmd, env=env, cwd=str(REPO_ROOT))
    print(f"sandboxed run exit code {r.returncode}", file=sys.stderr)
    return r.returncode


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--inner", action="store_true")
    ap.add_argument("--backend", default="mlx", choices=["mlx", "transformers"])
    a = ap.parse_args()
    sys.exit(inner(a.backend) if a.inner else outer(a.backend))
