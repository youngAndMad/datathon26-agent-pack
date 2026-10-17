"""Latency run over authorized local clips via the black-box jury client.

Per-clip results (which include model-derived verdicts/timecodes) are written ONLY to a private,
git-ignored directory. stdout carries aggregate timing/status numbers only — safe to share.

Usage: .venv/bin/python scripts/qa_latency_run.py --base-url http://127.0.0.1:8000 \
         --key-file secrets/jury_api_key --dataset data/dataset --out data/bench/private/latency
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--key-file", required=True)
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    files = sorted(p for p in Path(args.dataset).rglob("*") if p.suffix.lower() == ".mov")
    lat, status = [], {}
    for i, f in enumerate(files):
        res = out / f"clip{i:03d}.json"  # opaque name; mapping kept local only
        subprocess.run([sys.executable, str(ROOT / "scripts" / "qa_jury_client.py"), "--base-url", args.base_url,
                        "--key-file", args.key_file, "--file", str(f), "--timeout", "320",
                        "--json-out", str(res)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            d = json.loads(res.read_text())
        except Exception:
            d = {"terminal_status": "client_error"}
        st = d.get("terminal_status") or "unknown"
        if st == "failed":
            st = f"failed:{d.get('error_code')}"
        status[st] = status.get(st, 0) + 1
        if d.get("upload_to_terminal_sec") is not None:
            lat.append(float(d["upload_to_terminal_sec"]))
        print(f"progress {i + 1}/{len(files)}", flush=True)
    lat.sort()
    summary = {"clips": len(files), "status_counts": status, "n_timed": len(lat)}
    if lat:
        summary.update(p50=round(statistics.median(lat), 1), p90=round(lat[int(0.9 * (len(lat) - 1))], 1),
                       max=round(lat[-1], 1), over_240=sum(x > 240 for x in lat), over_270=sum(x > 270 for x in lat),
                       over_300=sum(x > 300 for x in lat))
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    print("SUMMARY", json.dumps(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
