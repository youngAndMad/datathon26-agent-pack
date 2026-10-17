#!/usr/bin/env python3
"""Build a PRIVATE evaluation manifest from authorized local clips + organizer labels.

LOCAL USE ONLY (a human or local agent on the permitted machine). Never run this from, or paste
its output into, a cloud-hosted assistant: the manifest contains organizer labels and hashes of
restricted videos. Output defaults to evaluation/manifest/private/manifest.json (git-ignored).

Input labels CSV (UTF-8, header row):
  file,label_verdict,label_timestamps_sec,group,split[,label_types]
  - file: path relative to --clips-dir (the filename is NOT copied into the manifest)
  - label_verdict: violation | no_violation  (organizer presence label)
  - label_timestamps_sec: ';'-separated seconds (organizer timestamps; may be empty)
  - group: recording/scenario/angle group id (related takes MUST share a group)
  - split: dev | val | holdout   (whole groups per split; enforced below)
  - label_types: optional ';'-separated REVIEWED type labels (omit unless locally reviewed)

Clip ids are opaque (c001, c002, … ordered by SHA-256), so ids reveal nothing about filenames.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PRIVATE = ROOT / "evaluation" / "manifest" / "private"


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def duration(p: Path) -> float | None:
    exe = shutil.which("ffprobe")
    if not exe:
        return None
    r = subprocess.run([exe, "-v", "error", "-show_entries", "format=duration", "-of", "json", "--", str(p)],
                       capture_output=True, text=True, timeout=60, check=False)
    try:
        d = float(json.loads(r.stdout)["format"]["duration"])
        return round(d, 3) if math.isfinite(d) else None
    except (KeyError, ValueError, json.JSONDecodeError):
        return None


def _floats(s: str) -> list[float]:
    return [float(x) for x in (s or "").replace(",", ".").split(";") if x.strip()]


def build(clips_dir: Path, labels_csv: Path) -> list[dict]:
    rows = list(csv.DictReader(labels_csv.open(encoding="utf-8")))
    entries, group_split = [], {}
    for r in rows:
        p = (clips_dir / r["file"]).resolve()
        if not p.is_file() or not p.is_relative_to(clips_dir.resolve()):
            raise SystemExit(f"clip not found or outside clips dir (row {rows.index(r) + 2})")
        verdict = r["label_verdict"].strip()
        if verdict not in ("violation", "no_violation"):
            raise SystemExit(f"bad label_verdict in row {rows.index(r) + 2}")
        split = r["split"].strip()
        if split not in ("dev", "val", "holdout"):
            raise SystemExit(f"bad split in row {rows.index(r) + 2}")
        g = r["group"].strip()
        if group_split.setdefault(g, split) != split:
            raise SystemExit(f"group {g!r} spans several splits — keep related takes in one split")
        ts = _floats(r.get("label_timestamps_sec", ""))
        types = [t.strip() for t in (r.get("label_types") or "").split(";") if t.strip()] or None
        entries.append({"path": str(p), "sha256": sha256(p), "duration_sec": duration(p),
                        "label_verdict": verdict, "label_timestamps_sec": ts,
                        "label_types": types, "group": g, "split": split,
                        "label_source": "organizer" + ("+local_review" if types else "")})
    entries.sort(key=lambda e: e["sha256"])
    if len({e["sha256"] for e in entries}) != len(entries):
        raise SystemExit("duplicate videos (same SHA-256) in labels")
    for i, e in enumerate(entries, 1):
        e["clip_id"] = f"c{i:03d}"
    return entries


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--clips-dir", type=Path, required=True)
    ap.add_argument("--labels", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=PRIVATE / "manifest.json")
    a = ap.parse_args(argv)
    out = a.out.resolve()
    if out.is_relative_to(ROOT) and not out.is_relative_to(PRIVATE.resolve()):
        print("refusing: inside the repository the manifest may only be written under "
              "evaluation/manifest/private/ (git-ignored)", file=sys.stderr)
        return 2
    entries = build(a.clips_dir, a.labels)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"version": 1, "clips": entries}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    out.chmod(0o600)
    splits = {s: sum(1 for e in entries if e["split"] == s) for s in ("dev", "val", "holdout")}
    print(f"wrote {len(entries)} clips ({splits}); groups={len({e['group'] for e in entries})}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
