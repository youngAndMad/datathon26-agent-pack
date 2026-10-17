"""Full sha256 verification of staged model files against a manifest (offline; agent C).

Usage: .venv/bin/python scripts/model_verify.py [--manifest models/manifest.json] [--key KEY]
Exit 0 only if every listed file exists with matching size and sha256.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    default = REPO_ROOT / "models" / "manifest.json"
    if not default.exists():
        default = REPO_ROOT / "models" / "manifest.proposed.json"
    ap.add_argument("--manifest", default=str(default))
    ap.add_argument("--key", default=None)
    args = ap.parse_args()
    data = json.loads(Path(args.manifest).read_text())
    bad = 0
    for entry in data["models"]:
        if args.key and entry["key"] != args.key:
            continue
        root = REPO_ROOT / entry["local_dir"]
        for f in entry["files"]:
            p = root / f["path"]
            if not p.is_file():
                print(f"MISSING {entry['key']}/{f['path']}")
                bad += 1
            elif p.stat().st_size != f["size_bytes"] or sha256_file(p) != f["sha256"]:
                print(f"MISMATCH {entry['key']}/{f['path']}")
                bad += 1
        print(f"{entry['key']}: {len(entry['files'])} files checked")
    print("OK" if not bad else f"FAILED ({bad})")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
