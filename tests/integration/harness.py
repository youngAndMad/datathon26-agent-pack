"""Black-box service harness for integration tests (agent F).

Starts the real worker (``cashwatch.pipeline.worker:run``) and API (``cashwatch.api.main:run``) as
separate OS processes on a private temp data dir with a temp 0600 key, talks to them only over
HTTP, and tears them down. No in-process imports of lane code are used for assertions.

Synthetic media only: clips come from ``scripts/make_synthetic_media.py`` (agent B) or the tiny
PyAV generator below. Never point this at real competition footage.
"""

from __future__ import annotations

import importlib.util
import os
import secrets
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = str(ROOT / ".venv" / "bin" / "python")
GENERATED = ROOT / "tests" / "fixtures" / "generated"
TEST_REGISTRY = ROOT / "rules" / "registry.test.yaml"

_MODULES = {
    "api": "import cashwatch.api.app, cashwatch.api.main",
    "storage": "from cashwatch.storage import create_store",
    "worker": "import cashwatch.pipeline.worker",
    "media": "from cashwatch.media import create_media",
    "inference": "from cashwatch.inference import create_perception",
    "rules": "from cashwatch.rules import load_registry, create_rules",
    "reporting": "from cashwatch.reporting import create_reporting",
}
_pending_cache: list[str] | None = None


def pending_components() -> list[str]:
    """Names of lane components that cannot be imported yet (checked in a clean subprocess)."""
    global _pending_cache
    if _pending_cache is None:
        missing = []
        for name, stmt in _MODULES.items():
            r = subprocess.run([PY, "-c", stmt], capture_output=True, text=True, timeout=120, cwd=ROOT)
            if r.returncode != 0:
                missing.append(name)
        _pending_cache = missing
    return _pending_cache


def load_qa_client():
    if "qa_jury_client" in sys.modules:
        return sys.modules["qa_jury_client"]
    spec = importlib.util.spec_from_file_location("qa_jury_client", ROOT / "scripts" / "qa_jury_client.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["qa_jury_client"] = mod
    spec.loader.exec_module(mod)
    return mod


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# --------------------------------------------------------------------------- synthetic media

def synthetic_clip(name: str, out_dir: Path) -> Path | None:
    """Return a synthetic fixture by agent-B name, building it into out_dir if needed."""
    for d in (GENERATED, out_dir):
        for p in d.glob(f"{name}.*"):
            if not p.name.endswith(".truth.json"):
                return p
    gen = ROOT / "scripts" / "make_synthetic_media.py"
    if gen.exists():
        out_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run([PY, str(gen), "--out", str(out_dir), "--only", name], cwd=ROOT,
                       capture_output=True, text=True, timeout=600)
        for p in out_dir.glob(f"{name}.*"):
            if not p.name.endswith(".truth.json"):
                return p
    return None


def tiny_mov(path: Path, *, seconds: float = 4.0, fps: int = 30, size=(320, 180)) -> Path:
    """Fallback generator: moving-square H.264 MOV via PyAV (synthetic geometry only)."""
    import av
    import numpy as np

    path.parent.mkdir(parents=True, exist_ok=True)
    with av.open(str(path), "w", format="mov") as c:
        s = c.add_stream("libx264", rate=fps)
        s.width, s.height, s.pix_fmt = size[0], size[1], "yuv420p"
        n = int(seconds * fps)
        for i in range(n):
            img = np.full((size[1], size[0], 3), 90, dtype=np.uint8)
            x = int((size[0] - 40) * i / max(n - 1, 1))
            img[60:100, x:x + 40] = (220, 30, 30)
            fr = av.VideoFrame.from_ndarray(img, format="rgb24")
            for pkt in s.encode(fr):
                c.mux(pkt)
        for pkt in s.encode():
            c.mux(pkt)
    return path


# --------------------------------------------------------------------------- service

@dataclass
class Service:
    tmp: Path
    mode: str = "test"
    backend: str = "fake"
    registry: Path | None = None
    extra_env: dict[str, str] = field(default_factory=dict)
    port: int = field(default_factory=free_port)
    procs: dict[str, subprocess.Popen] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.data_dir = self.tmp / "data"
        self.log_dir = self.tmp / "logs"
        self.key_file = self.tmp / "secrets" / "jury_api_key"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.key_file.parent.mkdir(parents=True, exist_ok=True)
        self.key = secrets.token_hex(32)
        fd = os.open(self.key_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(self.key)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def env(self) -> dict[str, str]:
        keep = {k: v for k, v in os.environ.items()
                if k in ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "USER", "CASHWATCH_GPU_LOCK")}
        keep.update({
            "CASHWATCH_MODE": self.mode,
            "CASHWATCH_BACKEND": self.backend,
            "CASHWATCH_DATA_DIR": str(self.data_dir),
            "CASHWATCH_API_KEY_FILE": str(self.key_file),
            "CASHWATCH_HOST": "127.0.0.1",
            "CASHWATCH_PORT": str(self.port),
            "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1",
            "DO_NOT_TRACK": "1", "PYTHONUNBUFFERED": "1",
        })
        if self.registry is not None:
            keep["CASHWATCH_REGISTRY_PATH"] = str(self.registry)
        keep.update(self.extra_env)
        return keep

    def _spawn(self, name: str, stmt: str, wrapper: list[str] | None = None) -> subprocess.Popen:
        log = open(self.log_dir / f"{name}.log", "ab")
        cmd = (wrapper or []) + [PY, "-c", stmt]
        p = subprocess.Popen(cmd, cwd=ROOT, env=self.env(), stdout=log, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL, start_new_session=True)
        self.procs[name] = p
        return p

    def start_worker(self, wrapper: list[str] | None = None) -> subprocess.Popen:
        return self._spawn("worker", "from cashwatch.pipeline.worker import run; run()", wrapper)

    def start_api(self, wrapper: list[str] | None = None) -> subprocess.Popen:
        return self._spawn("api", "from cashwatch.api.main import run; run()", wrapper)

    def start(self, timeout: float = 90.0) -> None:
        self.start_worker()
        self.start_api()
        if not self.wait_ready(timeout):
            raise RuntimeError(f"service not ready after {timeout}s:\n{self.log_tail()}")

    def wait_ready(self, timeout: float) -> bool:
        import httpx
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            for n, p in self.procs.items():
                if p.poll() is not None:
                    return False
            try:
                r = httpx.get(f"{self.base_url}/readyz", timeout=2.0, trust_env=False)
                if r.status_code == 200:
                    return True
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        return False

    def wait_healthy(self, timeout: float) -> bool:
        import httpx
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if any(p.poll() is not None for p in self.procs.values()):
                return False
            try:
                if httpx.get(f"{self.base_url}/healthz", timeout=2.0, trust_env=False).status_code == 200:
                    return True
            except httpx.HTTPError:
                pass
            time.sleep(0.3)
        return False

    def wait_exit(self, name: str, timeout: float) -> int | None:
        try:
            return self.procs[name].wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return None

    def kill(self, name: str, sig: int = signal.SIGKILL) -> None:
        """Signal the whole process group (supervisor + model child)."""
        p = self.procs.get(name)
        if p and p.poll() is None:
            try:
                os.killpg(p.pid, sig)
            except ProcessLookupError:
                pass
            try:
                p.wait(timeout=20)
            except subprocess.TimeoutExpired:
                os.killpg(p.pid, signal.SIGKILL)
                p.wait(timeout=10)

    def stop(self) -> None:
        for name in ("api", "worker"):
            self.kill(name, signal.SIGTERM)

    def log_tail(self, n: int = 25) -> str:
        out = []
        for f in sorted(self.log_dir.glob("*.log")):
            lines = f.read_text(errors="replace").splitlines()[-n:]
            out.append(f"--- {f.name} ---\n" + "\n".join(lines))
        return "\n".join(out)

    def files_snapshot(self) -> set[str]:
        """Every file under the data dir except the SQLite db/WAL (which legitimately changes)."""
        return {str(p.relative_to(self.data_dir)) for p in self.data_dir.rglob("*")
                if p.is_file() and "db" not in p.relative_to(self.data_dir).parts[:1]}
