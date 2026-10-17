"""Build models/manifest.proposed.json from the locally staged model folders (agent C).

Offline: reads only local files (no hub access). Hashes every weight/config/tokenizer file
(excluding the huggingface download cache folder) and records provenance, license (from the
model card front matter shipped in the folder), inference library versions and host hardware.

Usage: .venv/bin/python scripts/model_manifest.py [--out models/manifest.proposed.json]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import subprocess
import sys
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Provenance facts recorded when the orchestrator staged the folders (decision D003).
MODELS = [
    {
        "key": "qwen3-vl-4b-instruct-mlx-8bit",
        "role": "primary",
        "adapter": "mlx",
        "repo_id": "mlx-community/Qwen3-VL-4B-Instruct-8bit",
        "revision": "0943db6e15185b86be368d3cf0704aec740b142b",
        "local_dir": "models/qwen3-vl-4b-instruct-mlx-8bit",
        "base_model": {
            "repo_id": "Qwen/Qwen3-VL-4B-Instruct",
            "revision_used_for_conversion": "not recorded by the converter (model card names the base repo only)",
        },
    },
    {
        "key": "qwen3-vl-4b-instruct",
        "role": "reference",
        "adapter": "transformers",
        "repo_id": "Qwen/Qwen3-VL-4B-Instruct",
        "revision": "ebb281ec70b05090aa6165b016eac8ec08e71b17",
        "local_dir": "models/qwen3-vl-4b-instruct",
        "base_model": None,
    },
    {
        "key": "qwen3-vl-4b-instruct-mlx-4bit-local",
        "role": "candidate (latency option)",
        "adapter": "mlx",
        "repo_id": "local-conversion/Qwen3-VL-4B-Instruct-mlx-4bit",
        "revision": "local-from-ebb281ec70b05090aa6165b016eac8ec08e71b17-mlxvlm0.7.8-affine-q4g64-rtn",
        "local_dir": "models/qwen3-vl-4b-instruct-mlx-4bit-local",
        "optional": True,
        "license_override": "apache-2.0",
        "base_model": {
            "repo_id": "Qwen/Qwen3-VL-4B-Instruct",
            "revision_used_for_conversion": "ebb281ec70b05090aa6165b016eac8ec08e71b17 (local folder models/qwen3-vl-4b-instruct)",
        },
        "conversion_command": ("HF_HUB_OFFLINE=1 .venv/bin/python -m mlx_vlm convert --hf-path models/qwen3-vl-4b-instruct "
                               "--mlx-path models/qwen3-vl-4b-instruct-mlx-4bit-local -q --q-bits 4 --q-group-size 64 "
                               "--q-mode affine --quant-method rtn --dtype bfloat16"),
    },
]

LIBS = ["mlx", "mlx-metal", "mlx-vlm", "transformers", "torch", "torchvision", "accelerate",
        "tokenizers", "safetensors", "huggingface-hub", "pillow", "numpy"]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def card_front_matter(readme: Path) -> dict[str, str]:
    if not readme.exists():
        return {}
    text = readme.read_text(encoding="utf-8", errors="replace")
    m = re.match(r"^---\n(.*?)\n---", text, re.S)
    out: dict[str, str] = {}
    if m:
        for line in m.group(1).splitlines():
            if ":" in line and not line.startswith(" "):
                k, v = line.split(":", 1)
                out[k.strip()] = v.strip()
    conv = re.search(r"using mlx-vlm version \*\*([0-9.]+)\*\*", text)
    if conv:
        out["_converted_with_mlx_vlm"] = conv.group(1)
    return out


def libs_version(name: str) -> str | None:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def hardware() -> dict:
    def sysctl(key: str) -> str:
        try:
            return subprocess.run(["sysctl", "-n", key], capture_output=True, text=True, check=True).stdout.strip()
        except Exception:
            return "unknown"
    mem = sysctl("hw.memsize")
    return {
        "machine": platform.machine(),
        "cpu_brand": sysctl("machdep.cpu.brand_string"),
        "memory_gb": round(int(mem) / 1024**3, 1) if mem.isdigit() else "unknown",
        "os": f"macOS {platform.mac_ver()[0]}",
        "python": platform.python_version(),
        "accelerator": "Apple GPU (Metal, unified memory); no CUDA",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(REPO_ROOT / "models" / "manifest.proposed.json"))
    args = ap.parse_args()
    entries = []
    for spec in MODELS:
        folder = REPO_ROOT / spec["local_dir"]
        if not folder.is_dir() and spec.get("optional"):
            continue
        if not folder.is_dir():
            print(f"missing model folder {spec['local_dir']}", file=sys.stderr)
            return 2
        files = []
        for p in sorted(folder.rglob("*")):
            rel = p.relative_to(folder).as_posix()
            if p.is_dir() or rel.startswith(".cache/"):
                continue
            files.append({"path": rel, "size_bytes": p.stat().st_size, "sha256": sha256_file(p)})
            print(f"hashed {spec['key']}/{rel}", file=sys.stderr)
        # cross-check against the hub client's download metadata (commit hash + etag per file)
        commits, etag_matches, etag_lfs = set(), 0, 0
        for f in files:
            meta = folder / ".cache" / "huggingface" / "download" / (f["path"] + ".metadata")
            if meta.is_file():
                lines = meta.read_text().splitlines()
                if lines:
                    commits.add(lines[0].strip())
                if len(lines) > 1 and len(lines[1].strip()) == 64:
                    etag_lfs += 1
                    etag_matches += int(lines[1].strip() == f["sha256"])
        card = card_front_matter(folder / "README.md")
        cfg = json.loads((folder / "config.json").read_text())
        entry = dict(spec)
        entry["license"] = {
            "spdx": spec.get("license_override") or card.get("license", "unknown"),
            "source": "model card front matter (README.md in the model folder); no separate LICENSE file is shipped in either repo snapshot",
            "notes": "Apache-2.0 per Qwen/Qwen3-VL-4B-Instruct model card; the MLX conversion card declares apache-2.0 as well",
        }
        entry["architecture"] = cfg.get("architectures")
        entry["model_type"] = cfg.get("model_type")
        if spec.get("conversion_command"):
            entry["conversion"] = {
                "format": "MLX safetensors (single shard)",
                "tool": "mlx-vlm convert (run locally, offline, from the pinned reference folder)",
                "tool_version": libs_version("mlx-vlm"),
                "quantization": cfg.get("quantization"),
                "converted_by": "agent C on the deployment host",
                "parity": "not established against the bf16 reference; synthetic plumbing sanity only",
            }
            entry["license"]["source"] = "inherited from the base model card (derived local conversion)"
            entry["revision_check"] = {"note": "local artifact; identity is the per-file sha256 list below"}
        elif spec["adapter"] == "mlx":
            entry["conversion"] = {
                "format": "MLX safetensors (single shard)",
                "tool": "mlx-vlm convert",
                "tool_version": card.get("_converted_with_mlx_vlm", "unknown"),
                "quantization": cfg.get("quantization"),
                "converted_by": "mlx-community (third party, not the Qwen publisher)",
                "parity": "not established against the bf16 reference; see coordination/handoffs/C.md",
            }
        if not spec.get("conversion_command"):
            entry["revision_check"] = {
                "source": ".cache/huggingface/download/*.metadata written at download time",
                "commits_seen": sorted(commits),
                "revision_matches": commits == {spec["revision"]},
                "lfs_files_etag_equals_sha256": f"{etag_matches}/{etag_lfs}",
            }
        entry["remote_code_files"] = [f["path"] for f in files if f["path"].endswith(".py")]
        entry["files"] = files
        entries.append(entry)
    libs = {}
    for name in LIBS:
        try:
            libs[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            libs[name] = None
    manifest = {
        "manifest_version": 1,
        "status": "proposed_by_agent_C",
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "models": entries,
        "inference_libraries": libs,
        "hardware": hardware(),
        "runtime_policy": {
            "local_files_only": True,
            "env": {"HF_HUB_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1", "TRANSFORMERS_OFFLINE": "1"},
            "trust_remote_code": False,
            "auto_download": False,
        },
    }
    Path(args.out).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
