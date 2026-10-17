"""ffprobe / ffmpeg subprocess helpers: argument lists only, ``file`` protocol only, timeouts."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from cashwatch.errors import PipelineError

_CANDIDATE_DIRS = ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin")


def find_tool(name: str, override: str | None = None) -> str | None:
    if override:
        return override if Path(override).is_file() else None
    found = shutil.which(name)
    if found:
        return found
    for d in _CANDIDATE_DIRS:
        p = Path(d) / name
        if p.is_file() and os.access(p, os.X_OK):
            return str(p)
    return None


def _env() -> dict[str, str]:
    # Minimal environment: no proxies / network hints are needed for local files.
    keep = ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL")
    return {k: v for k, v in os.environ.items() if k in keep}


def run_tool(args: list[str], *, timeout: float, what: str) -> subprocess.CompletedProcess:
    if timeout <= 0:
        raise PipelineError("deadline_exceeded", f"no budget left for {what}")
    try:
        return subprocess.run(args, capture_output=True, timeout=timeout, check=False,
                              stdin=subprocess.DEVNULL, env=_env())
    except subprocess.TimeoutExpired:
        raise PipelineError("decode_failed", f"{what} timed out after {timeout:.1f}s") from None
    except OSError as exc:
        raise PipelineError("internal_error", f"{what} could not start: {type(exc).__name__}") from None


def ffprobe(ffprobe_bin: str, path: Path, *, timeout: float) -> dict:
    """Container/stream description. Raises invalid_media when ffprobe cannot parse the file."""
    args = [ffprobe_bin, "-v", "error", "-hide_banner", "-protocol_whitelist", "file",
            "-show_format", "-show_streams", "-of", "json", f"file:{path}"]
    proc = run_tool(args, timeout=timeout, what="ffprobe")
    if proc.returncode != 0:
        raise PipelineError("invalid_media", "ffprobe could not parse the container")
    try:
        return json.loads(proc.stdout.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        raise PipelineError("invalid_media", "ffprobe produced no description") from None
