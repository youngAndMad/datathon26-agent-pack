"""Explicit online preparation of a pinned Windows-only model; no inference/data.

Uses installed dependencies and existing network settings. Never downloads at
runtime, changes proxy settings, or modifies the shared model manifest.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
REPO = "Qwen/Qwen3-VL-2B-Instruct"
REVISION = "89644892e4d85e24eaac8bacfd4f463576704203"
FOLDER = "qwen3-vl-2b-instruct"
MANIFEST = ROOT / "models/manifest.windows-2b.json"
FILES = (
    "README.md", "config.json", "generation_config.json", "model.safetensors",
    "chat_template.json", "preprocessor_config.json", "tokenizer.json",
    "tokenizer_config.json", "video_preprocessor_config.json", "merges.txt", "vocab.json",
)


def hashes(path):
    size = path.stat().st_size
    sha256, blob = hashlib.sha256(), hashlib.sha1()
    blob.update(f"blob {size}\0".encode())
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            sha256.update(block)
            blob.update(block)
    return size, sha256.hexdigest(), blob.hexdigest()


def verify(manifest=MANIFEST, root=ROOT):
    data = json.loads(manifest.read_text(encoding="utf-8"))
    entries = data.get("models", [])
    if len(entries) != 1:
        raise ValueError("Expected exactly one Windows model")
    entry = entries[0]
    if (entry.get("repo_id"), entry.get("revision"), entry.get("local_dir"), entry.get("adapter")) != (
        REPO, REVISION, "models/" + FOLDER, "transformers"
    ):
        raise ValueError("Windows model identity mismatch")
    records = entry.get("files", [])
    if len(records) != len(FILES) or {f["path"] for f in records} != set(FILES):
        raise ValueError("Incomplete Windows model manifest")
    for record in records:
        path = root / "models" / FOLDER / record["path"]
        size, digest, _ = hashes(path)
        if (size, digest) != (record["size_bytes"], record["sha256"]):
            raise ValueError("Model checksum mismatch: " + record["path"])
    return len(records)


def fetch():
    # Explicit preparation only. Preserve sandbox/user proxy configuration.
    os.environ["HF_HUB_OFFLINE"] = "0"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"
    from huggingface_hub import HfApi, snapshot_download

    print(f"Preparing {REPO}@{REVISION}", flush=True)
    info = HfApi().model_info(REPO, revision=REVISION, files_metadata=True, timeout=15, token=False)
    if info.sha != REVISION or info.card_data is None or info.card_data.get("license") != "apache-2.0":
        raise ValueError("Unexpected model revision or license metadata")
    remote = {f.rfilename: f for f in info.siblings}
    if not set(FILES).issubset(remote):
        raise ValueError("Pinned repository lacks required model files")
    dest = ROOT / "models" / FOLDER
    snapshot_download(repo_id=REPO, revision=REVISION, local_dir=dest,
                      allow_patterns=list(FILES), max_workers=1, token=False)
    records = []
    for name in FILES:
        size, digest, blob = hashes(dest / name)
        expected = remote[name]
        if size != expected.size:
            raise ValueError("Publisher size mismatch: " + name)
        if expected.lfs is not None:
            if digest != expected.lfs.sha256:
                raise ValueError("Publisher SHA256 mismatch: " + name)
        elif blob != expected.blob_id:
            raise ValueError("Publisher Git blob mismatch: " + name)
        records.append({"path": name, "size_bytes": size, "sha256": digest})
    config = json.loads((dest / "config.json").read_text(encoding="utf-8"))
    if config.get("model_type") != "qwen3_vl":
        raise ValueError("Unexpected model architecture")
    data = {"manifest_version": 1, "status": "Windows experimental; not quality-qualified",
            "models": [{"key": FOLDER, "adapter": "transformers", "repo_id": REPO,
                        "revision": REVISION, "local_dir": "models/" + FOLDER,
                        "license": {"spdx": "apache-2.0"}, "files": records}]}
    temp = MANIFEST.with_suffix(".json.tmp")
    temp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    temp.replace(MANIFEST)
    print(f"Verified {len(records)} files; wrote models/{MANIFEST.name}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-only", action="store_true", help="Offline checksum verification")
    args = parser.parse_args()
    try:
        if args.verify_only:
            print(f"Verified {verify()} local files")
        else:
            fetch()
    except Exception as exc:
        # No remote credentials, URLs with tokens or arbitrary response bodies.
        print(f"Model preparation failed ({type(exc).__name__}). No service was started.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
