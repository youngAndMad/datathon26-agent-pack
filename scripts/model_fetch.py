"""Explicit online preparation only; runtime never calls this downloader."""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
from datetime import UTC, datetime
from pathlib import Path

from model_manifest import LIBS, card_front_matter, sha256_file
from importlib import metadata

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", default="ebb281ec70b05090aa6165b016eac8ec08e71b17")
    parser.add_argument("--out", type=Path, default=ROOT / "models" / "manifest.windows.json")
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{40}", args.revision):
        parser.error("revision must be an immutable 40-character upstream commit SHA")
    from huggingface_hub import HfApi, snapshot_download

    repo = "Qwen/Qwen3-VL-4B-Instruct"
    revision = HfApi().model_info(repo, revision=args.revision).sha
    if revision != args.revision:
        raise RuntimeError("Resolved revision did not match requested immutable revision")
    folder = ROOT / "models" / "qwen3-vl-4b-instruct"
    snapshot_download(repo_id=repo, revision=revision, local_dir=folder,
                      allow_patterns=["*.json", "*.safetensors", "*.txt", "*.model", "*.jinja", "README.md", "LICENSE*"])
    config = json.loads((folder / "config.json").read_text(encoding="utf-8"))
    if not list(folder.glob("*.safetensors")) or not (folder / "tokenizer.json").is_file():
        raise RuntimeError("Downloaded snapshot is incomplete")
    files = [{"path": p.relative_to(folder).as_posix(), "size_bytes": p.stat().st_size,
              "sha256": sha256_file(p)} for p in sorted(folder.rglob("*"))
             if p.is_file() and ".cache" not in p.relative_to(folder).parts]
    libs = {}
    for name in LIBS:
        try:
            libs[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            libs[name] = None
    manifest = {
        "manifest_version": 1, "status": "downloaded_not_benchmarked",
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "models": [{"key": folder.name, "role": "primary", "adapter": "transformers",
                    "repo_id": repo, "revision": revision, "local_dir": folder.relative_to(ROOT).as_posix(),
                    "base_model": None, "architecture": config.get("architectures"),
                    "model_type": config.get("model_type"), "remote_code_files": [],
                    "license": {"spdx": card_front_matter(folder / "README.md").get("license", "unknown"),
                                "source": "downloaded model card README.md"}, "files": files}],
        "inference_libraries": libs,
        "hardware": {"os": platform.platform(), "machine": platform.machine(), "python": platform.python_version()},
        "runtime_policy": {"local_files_only": True, "trust_remote_code": False, "auto_download": False,
                           "env": {"HF_HUB_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1", "TRANSFORMERS_OFFLINE": "1"}},
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.out.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, args.out)
    print(f"Downloaded immutable snapshot and hashed {len(files)} files; manifest: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
