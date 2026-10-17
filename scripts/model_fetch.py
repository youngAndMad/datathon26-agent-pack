"""Explicit ONLINE preparation step: download pinned model revisions into models/.

Usage: HF_HUB_OFFLINE=0 .venv/bin/python scripts/model_fetch.py [--only mlx|hf]
Never called at runtime. After download, verify hashes with scripts/model_manifest.py.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODELS = {
    "mlx": ("mlx-community/Qwen3-VL-4B-Instruct-8bit", "0943db6e15185b86be368d3cf0704aec740b142b",
            "qwen3-vl-4b-instruct-mlx-8bit"),
    "hf": ("Qwen/Qwen3-VL-4B-Instruct", "ebb281ec70b05090aa6165b016eac8ec08e71b17", "qwen3-vl-4b-instruct"),
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=sorted(MODELS))
    args = ap.parse_args()
    os.environ["HF_HUB_OFFLINE"] = "0"
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    from huggingface_hub import snapshot_download

    for key, (repo, rev, folder) in MODELS.items():
        if args.only and key != args.only:
            continue
        dest = ROOT / "models" / folder
        print(f"fetching {repo}@{rev[:8]} -> models/{folder}")
        snapshot_download(repo_id=repo, revision=rev, local_dir=dest)


if __name__ == "__main__":
    main()
