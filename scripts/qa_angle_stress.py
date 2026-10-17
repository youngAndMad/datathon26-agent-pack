"""Viewpoint/appearance STRESS test (spec §7, §13.2 T24). Not proof of robustness to a genuinely new
camera angle — real footage from another viewpoint is still required (docs/LOCAL_REVIEW.md §7).

For each input clip, renders timing-preserving variants with ffmpeg (no trimming, same frame
times, no audio) and runs original + variants through the running service via the black-box jury
client. Reports, as AGGREGATES ONLY, how often each variant's verdict agrees with the original and
how far its event times move. Variants and per-clip results stay in a private git-ignored dir.

Usage: .venv/bin/python scripts/qa_angle_stress.py --base-url http://127.0.0.1:8000 \
         --key-file secrets/jury_api_key --clips DIR_OR_FILE [--variants hflip,rot7,persp,zoom85,dark]
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "bench" / "private" / "angle_stress"

# Each filter keeps the frame count and timestamps; sizes are forced even for H.264.
VARIANTS = {
    "hflip": "hflip",
    "rot7": "rotate=7*PI/180:ow=iw:oh=ih:fillcolor=black",
    "persp": "perspective=x0=0.06*W:y0=0.04*H:x1=0.94*W:y1=0:x2=0:y2=H:x3=W:y3=0.96*H:interpolation=linear",
    "zoom85": "crop=iw*0.85:ih*0.85,scale=trunc(iw/0.85/2)*2:trunc(ih/0.85/2)*2",
    "dark": "eq=brightness=-0.18:contrast=0.85",
}


def render(src: Path, name: str, vf: str, dest: Path) -> Path:
    out = dest / f"{src.stem}.{name}.mov"
    if not out.exists():
        subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(src), "-map", "0:v:0",
                        "-vf", f"{vf},scale=trunc(iw/2)*2:trunc(ih/2)*2", "-fps_mode", "passthrough",
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-an", "-movflags", "+faststart",
                        str(out)], check=True, timeout=600)
    return out


def run(base: str, key: str, clip: Path, res: Path) -> dict:
    subprocess.run([sys.executable, str(ROOT / "scripts" / "qa_jury_client.py"), "--base-url", base,
                    "--key-file", key, "--file", str(clip), "--timeout", "320", "--json-out", str(res)],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        return json.loads(res.read_text())
    except Exception:
        return {"terminal_status": "client_error"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--key-file", required=True)
    ap.add_argument("--clips", required=True, type=Path)
    ap.add_argument("--variants", default=",".join(VARIANTS))
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args()
    clips = [a.clips] if a.clips.is_file() else sorted(p for p in a.clips.rglob("*") if p.suffix.lower() == ".mov")
    names = [v for v in a.variants.split(",") if v in VARIANTS]
    a.out.mkdir(parents=True, exist_ok=True)
    stats = {v: {"agree": 0, "disagree": 0, "failed": 0, "shift": []} for v in names}
    for i, c in enumerate(clips):
        base = run(a.base_url, a.key_file, c, a.out / f"clip{i:03d}.orig.json")
        if base.get("terminal_status") != "done":
            for v in names:
                stats[v]["failed"] += 1
            continue
        for v in names:
            r = run(a.base_url, a.key_file, render(c, v, VARIANTS[v], a.out), a.out / f"clip{i:03d}.{v}.json")
            s = stats[v]
            if r.get("terminal_status") != "done":
                s["failed"] += 1
                continue
            s["agree" if r.get("verdict") == base.get("verdict") else "disagree"] += 1
            bt, vt = base.get("timecodes_sec") or [], r.get("timecodes_sec") or []
            for t in bt:
                if vt:
                    s["shift"].append(min(abs(t - x) for x in vt))
        print(f"progress {i + 1}/{len(clips)}", flush=True)
    summary = {"clips": len(clips), "note": "stress transforms, not a genuinely new viewpoint", "variants": {}}
    for v, s in stats.items():
        n = s["agree"] + s["disagree"]
        summary["variants"][v] = {
            "verdict_agreement": round(s["agree"] / n, 3) if n else None, "n": n, "failed": s["failed"],
            "event_time_shift_sec_median": round(statistics.median(s["shift"]), 3) if s["shift"] else None,
            "event_time_shift_sec_max": round(max(s["shift"]), 3) if s["shift"] else None}
    (a.out / "summary.json").write_text(json.dumps(summary, indent=2))
    print("SUMMARY", json.dumps(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
