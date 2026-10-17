"""`make doctor`: environment, hardware and artifact diagnostics. Never prints secret values.

Exit code 0 when everything required for the configured mode is present, 1 otherwise.
Each line is OK / WARN / FAIL / BLOCKED with a short reason.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import socket
import subprocess
import sys

from cashwatch.settings import REPO_ROOT, get_settings


def main() -> int:
    s = get_settings()
    rows: list[tuple[str, str, str]] = []

    def add(status: str, item: str, note: str) -> None:
        rows.append((status, item, note))

    add("OK", "mode", s.mode)
    add("OK" if sys.version_info[:2] == (3, 12) else "FAIL", "python", platform.python_version())
    for tool in ("ffmpeg", "ffprobe"):
        add("OK" if shutil.which(tool) else "FAIL", tool, shutil.which(tool) or "missing")
    try:
        import av
        add("OK", "pyav", av.__version__)
    except Exception as e:
        add("FAIL", "pyav", type(e).__name__)

    # Accelerator
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        try:
            mem = int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True).stdout)
            add("OK", "accelerator", f"Apple Silicon, {mem // 2**30} GB unified memory (MLX/Metal)")
        except Exception:
            add("WARN", "accelerator", "Apple Silicon; memory unknown")
    else:
        try:
            out = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
                                 capture_output=True, text=True, timeout=10).stdout.strip()
            add("OK" if out else "FAIL", "accelerator", out or "no GPU detected")
        except Exception:
            add("FAIL", "accelerator", "no GPU detected (CPU-only is not a valid 5-minute target)")

    # Model
    if s.backend == "fake":
        add("FAIL" if s.mode == "production" else "WARN", "model", "fake adapter (test mode only)")
    elif s.model_path.is_dir() and any(s.model_path.glob("*.safetensors")):
        add("OK", "model", f"{s.backend}: {s.model_path.name}")
        for f in ("tokenizer.json", "preprocessor_config.json", "config.json"):
            if not (s.model_path / f).exists():
                add("FAIL", "model files", f"missing {f} (incompatible/incomplete processor)")
    else:
        add("FAIL", "model", f"weights missing at {s.model_path.name} (run make fetch-models while online)")
    add("OK" if s.model_manifest.exists() else "FAIL", "model manifest", s.model_manifest.name)

    # Registry
    try:
        from cashwatch.rules import load_registry
        reg = load_registry(s.registry_path, require_approved=s.mode == "production", allow_test=s.mode == "test")
        n = len(reg.approved_rule_ids())
        errs = reg.health_errors()
        add("OK" if n and not errs else "FAIL", "rule registry",
            f"version {reg.version}, approved={n}" + (f"; {errs[0]}" if errs else ""))
    except Exception as e:
        add("FAIL", "rule registry", f"cannot load ({type(e).__name__})")

    # Storage, disk, secrets
    try:
        s.data_dir.mkdir(parents=True, exist_ok=True)
        probe = s.data_dir / ".doctor"
        probe.write_text("ok")
        probe.unlink()
        free = shutil.disk_usage(s.data_dir).free / 2**30
        add("OK" if free > 20 else "WARN" if free > 5 else "FAIL", "storage", f"writable, {free:.0f} GB free")
    except Exception as e:
        add("FAIL", "storage", f"not writable ({type(e).__name__})")
    if s.api_key_file.exists():
        mode = oct(s.api_key_file.stat().st_mode & 0o777)
        add("OK" if mode in ("0o600", "0o400") else "WARN", "api key", f"present (perm {mode})")
    else:
        add("FAIL" if s.mode == "production" else "WARN", "api key", "secret file missing (deploy/gen_api_key.sh)")
    for e in s.production_errors():
        add("FAIL", "config", e)

    # Privacy configuration
    offline = all(os.environ.get(k) == "1" for k in ("HF_HUB_OFFLINE", "HF_HUB_DISABLE_TELEMETRY"))
    add("OK" if offline else "WARN", "offline flags", "HF_HUB_OFFLINE/HF_HUB_DISABLE_TELEMETRY" + ("" if offline else " not set in this shell"))
    leaked = [k for k in os.environ if any(t in k.upper() for t in ("OPENAI", "ANTHROPIC", "GEMINI", "HF_TOKEN"))]
    add("WARN" if leaked else "OK", "provider keys in env", f"{len(leaked)} present" if leaked else "none")

    # Dataset & public routing (external facts)
    ds = os.environ.get("CASHWATCH_DATASET_DIR")
    add("OK" if ds and os.path.isdir(ds) else "BLOCKED", "dataset", "present" if ds and os.path.isdir(ds) else
        "CASHWATCH_DATASET_DIR not set (training clips not supplied)")
    url = os.environ.get("CASHWATCH_PUBLIC_URL")
    if url:
        host = url.split("//", 1)[-1].split("/", 1)[0].split(":")[0]
        try:
            socket.getaddrinfo(host, 443)
            add("OK", "public routing", "DNS resolves (run make smoke against it)")
        except Exception:
            add("FAIL", "public routing", "DNS does not resolve")
    else:
        add("BLOCKED", "public routing", "CASHWATCH_PUBLIC_URL not set (host/ingress decision pending)")
    add("OK" if (s.frontend_dist / "index.html").exists() else "WARN", "frontend build", "frontend/dist")
    prof = json.loads(s.profile_path.read_text()) if s.profile_path.exists() else {}
    add("OK" if prof else "FAIL", "runtime profile", prof.get("profile_id", "missing") + f" ({prof.get('status', '?')})")

    width = max(len(r[1]) for r in rows)
    for status, item, note in rows:
        print(f"{status:8} {item:<{width}}  {note}")
    hard = [r for r in rows if r[0] == "FAIL"]
    print(f"\n{len(hard)} FAIL, {sum(r[0] == 'BLOCKED' for r in rows)} BLOCKED, {sum(r[0] == 'WARN' for r in rows)} WARN")
    _ = REPO_ROOT
    return 1 if hard else 0


if __name__ == "__main__":
    sys.exit(main())
