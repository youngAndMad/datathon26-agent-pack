"""Supervised model child process (decision D005).

The worker process owns a ``ChildGenerator``; the model lives in a separate OS process started
with an argument list (no shell), a scrubbed environment (offline flags forced, provider/API
credentials removed) and its own session. Every call has a hard timeout; on timeout, crash or
OOM the child is SIGKILLed and must be respawned + warmed before it is used again.
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from cashwatch.inference.protocol import (
    OFFLINE_ENV,
    GenRequest,
    GenResult,
    ProtocolEOF,
    ProtocolTimeout,
    read_frame,
    write_frame,
)
from cashwatch.settings import REPO_ROOT


class GenError(Exception):
    """Base generation failure; ``kind`` in timeout|crash|oom|load_failed|failed."""
    kind = "failed"

    def __init__(self, detail: str = "") -> None:
        super().__init__(detail)
        self.detail = detail


class GenTimeout(GenError):
    kind = "timeout"


class GenCrashed(GenError):
    kind = "crash"


class GenOOM(GenError):
    kind = "oom"


class GenLoadFailed(GenError):
    kind = "load_failed"


_SECRET_ENV = re.compile(
    r"(API[_-]?KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|OPENAI|ANTHROPIC|GEMINI|GOOGLE_|AWS_|AZURE|"
    r"HF_TOKEN|HUGGING_FACE_HUB_TOKEN|CASHWATCH_API_KEY)", re.I)


def child_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not _SECRET_ENV.search(k)}
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        env.pop(k, None)
    env.update(OFFLINE_ENV)
    src = str(REPO_ROOT / "src")
    env["PYTHONPATH"] = src + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["PYTHONUNBUFFERED"] = "1"
    if extra:
        env.update(extra)
    return env


class ChildGenerator:
    """Client for one resident model child process."""

    def __init__(self, backend: str, model_path: Path | str, *, options: dict[str, Any] | None = None,
                 command_prefix: list[str] | None = None, stderr_path: Path | None = None,
                 python: str | None = None) -> None:
        self.backend = backend
        self.model_path = str(model_path)
        self.options = dict(options or {})
        self.command_prefix = list(command_prefix or [])
        self.stderr_path = stderr_path
        self.python = python or sys.executable
        self._proc: subprocess.Popen | None = None
        self._seq = 0
        self._lock = threading.Lock()
        self._loaded = False
        self.load_info: dict[str, Any] = {}
        self.load_ms: int | None = None

    # ---------------------------------------------------------------- lifecycle
    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc else None

    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def loaded(self) -> bool:
        return self._loaded and self.alive()

    def _spawn(self) -> None:
        self.kill()
        cmd = self.command_prefix + [self.python, "-m", "cashwatch.inference.child"]
        if self.stderr_path is not None:
            self.stderr_path.parent.mkdir(parents=True, exist_ok=True)
            err: Any = open(self.stderr_path, "ab")
        else:
            err = subprocess.DEVNULL
        try:
            self._proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=err,
                env=child_env(), cwd=str(REPO_ROOT), start_new_session=True, close_fds=True,
            )
        finally:
            if err is not subprocess.DEVNULL:
                err.close()
        self._loaded = False

    def start(self, timeout_sec: float) -> int:
        """Spawn (if needed) and load the model. Returns load time in ms."""
        with self._lock:
            end = time.monotonic() + timeout_sec
            if not self.alive():
                self._spawn()
            self._call({"op": "init", "backend": self.backend, "model_path": self.model_path,
                        "options": self.options}, max(0.1, end - time.monotonic()))
            r = self._call({"op": "load"}, max(0.1, end - time.monotonic()))
            self._loaded = True
            self.load_info = r.get("info", {})
            self.load_ms = r.get("load_ms")
            return int(self.load_ms or 0)

    def generate(self, req: GenRequest, timeout_sec: float) -> GenResult:
        with self._lock:
            if not self.loaded():
                raise GenCrashed("model child not loaded")
            r = self._call({"op": "generate", "request": req}, timeout_sec)
            return r["result"]

    def kill(self) -> None:
        p = self._proc
        self._loaded = False
        if p is None:
            return
        if p.poll() is None:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                try:
                    p.kill()
                except ProcessLookupError:
                    pass
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
        for fh in (p.stdin, p.stdout):
            try:
                if fh:
                    fh.close()
            except OSError:
                pass
        self._proc = None

    def close(self, timeout_sec: float = 5.0) -> None:
        with self._lock:
            if self.alive():
                try:
                    self._call({"op": "shutdown"}, timeout_sec)
                    self._proc.wait(timeout=timeout_sec)  # type: ignore[union-attr]
                except (GenError, subprocess.TimeoutExpired):
                    pass
            self.kill()

    # ---------------------------------------------------------------- transport
    def _call(self, msg: dict[str, Any], timeout_sec: float) -> dict[str, Any]:
        p = self._proc
        if p is None or p.poll() is not None:
            self._loaded = False
            raise GenCrashed(f"model child not running (exit={p.returncode if p else None})")
        self._seq += 1
        seq = self._seq
        msg = dict(msg, seq=seq)
        try:
            write_frame(p.stdin, msg)  # type: ignore[arg-type]
        except (BrokenPipeError, OSError) as e:
            self.kill()
            raise GenCrashed(f"model child pipe closed: {type(e).__name__}") from None
        try:
            reply = read_frame(p.stdout.fileno(), timeout_sec)  # type: ignore[union-attr]
        except ProtocolTimeout:
            self.kill()
            raise GenTimeout(f"no reply within {timeout_sec:.1f}s; child killed") from None
        except ProtocolEOF:
            code = p.poll()
            self.kill()
            raise GenCrashed(f"model child exited (exit={code})") from None
        if reply.get("seq") != seq:
            self.kill()
            raise GenCrashed("protocol sequence mismatch")
        if not reply.get("ok"):
            kind = reply.get("kind")
            detail = str(reply.get("detail", ""))
            if kind == "oom":
                self.kill()  # release accelerator memory; requires respawn + warmup
                raise GenOOM(detail)
            if kind == "load_failed":
                self.kill()
                raise GenLoadFailed(detail)
            self.kill()
            raise GenError(detail)
        return reply
