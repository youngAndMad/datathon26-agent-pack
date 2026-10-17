"""Explicit Windows CUDA preparation, isolated from the existing CPU environment."""
from __future__ import annotations

import argparse
from importlib import metadata
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
MIN_DRIVER = (560, 76)
CUDA_INDEX = "https://download.pytorch.org/whl/cu126"
TORCH = "torch==2.14.1+cu126"
TORCHVISION = "torchvision==0.29.1+cu126"
BNB = "bitsandbytes==0.50.2"
EXCLUDED = {"torch", "torchvision", "cashwatch", "bitsandbytes"}


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def check_driver() -> list[str]:
    result = subprocess.run(
        ["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"],
        check=True, capture_output=True, text=True, timeout=20,
    )
    versions = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not versions:
        raise RuntimeError("No NVIDIA GPU reported by nvidia-smi")
    for version in versions:
        if not re.fullmatch(r"\d+\.\d+(?:\.\d+)?", version):
            raise RuntimeError("Cannot parse NVIDIA driver version")
        if tuple(int(part) for part in version.split(".")[:2]) < MIN_DRIVER:
            raise RuntimeError(
                f"NVIDIA driver {version} is below 560.76 required by this CUDA 12.6 profile. "
                "Update the NVIDIA driver locally, reboot, then retry. Nothing was installed."
            )
    return versions


def dependency_pins(distributions=None) -> list[str]:
    distributions = metadata.distributions() if distributions is None else distributions
    pins = {}
    for dist in distributions:
        name = dist.metadata.get("Name", "")
        key = canonical(name)
        if key in EXCLUDED:
            continue
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name):
            raise ValueError("Invalid installed distribution name")
        version = dist.version
        if not re.fullmatch(r"[A-Za-z0-9.!+_-]+", version):
            raise ValueError("Invalid installed distribution version")
        pin = f"{name}=={version}"
        if key in pins and pins[key] != pin:
            raise ValueError("Conflicting installed distribution versions")
        pins[key] = pin
    return [pins[key] for key in sorted(pins)] + [BNB]


def prepare(root=ROOT) -> Path:
    # This gate runs before creating folders, environments or downloading files.
    check_driver()
    if sys.platform != "win32" or sys.version_info[:2] != (3, 12):
        raise RuntimeError("Run with the existing Windows Python 3.12 environment")
    expected = (root / ".venv-windows").resolve()
    if Path(sys.prefix).resolve() != expected:
        raise RuntimeError("Run .venv-windows\\Scripts\\python.exe scripts\\windows_cuda_setup.py --prepare")
    pins = dependency_pins()
    target = root / ".tools" / "venv-cuda"
    target.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, "-m", "venv", str(target)], check=True)
    python = target / "Scripts" / "python.exe"
    requirements = target / "cpu-snapshot-requirements.txt"
    requirements.write_text("\n".join(pins) + "\n", encoding="utf-8")
    # Exact CUDA wheels only; CPU environment and shared lockfiles are untouched.
    subprocess.run([
        str(python), "-m", "pip", "install", "--index-url", CUDA_INDEX,
        "--no-deps", TORCH, TORCHVISION,
    ], check=True)
    constraints = target / "cuda-constraints.txt"
    constraints.write_text(f"{TORCH}\n{TORCHVISION}\n", encoding="utf-8")
    subprocess.run([
        str(python), "-m", "pip", "install", "--index-url", "https://pypi.org/simple",
        "--constraint", str(constraints), "--requirement", str(requirements),
    ], check=True)
    (target / "Lib" / "site-packages" / "cashwatch-local.pth").write_text(
        str((root / "src").resolve()) + "\n", encoding="utf-8"
    )
    subprocess.run([str(python), "-m", "pip", "check"], check=True)
    subprocess.run([str(python), str(root / "scripts" / "windows_cuda_probe.py")], check=True)
    return python


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check-driver", action="store_true", help="Read-only driver check")
    group.add_argument("--prepare", action="store_true", help="Download into isolated .tools/venv-cuda")
    args = parser.parse_args(argv)
    try:
        if args.prepare:
            print(f"Prepared CUDA Python: {prepare()}")
        else:
            print("NVIDIA driver accepted: " + ", ".join(check_driver()))
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        # Preserve network/proxy policy; never retry with alternate network settings.
        if isinstance(exc, RuntimeError):
            print(str(exc), file=sys.stderr)
        else:
            print(f"CUDA preparation failed ({type(exc).__name__}); no service was started.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
