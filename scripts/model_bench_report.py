"""Print a compact table from scripts/model_bench.py JSON results (synthetic benchmarks only)."""

from __future__ import annotations

import json
import statistics
import sys


def row(p: dict) -> str:
    runs = p["runs"]
    ok = [r for r in runs if not r["error"]]
    first_out = [r["output_tokens"] for r in runs]
    san = [r["sanity"] for r in ok if r.get("sanity")]
    sc = sum(s["supporting_correct"] for s in san)
    sw = sum(s["supporting_wrong"] for s in san)
    cc = sum(s["contradicting_correct"] for s in san)
    cw = sum(s["contradicting_wrong"] for s in san)
    st = sum(1 for s in san if s["state"] == s["expected_state"])
    lat = p["latency_sec"]
    alll = p["all_latency_sec"]
    return (f"| {p['kind']} | {p['long_side_px']} | {p['frames']} | {p['max_new_tokens']} | {len(runs)} | "
            f"{lat.get('p50', '-')} | {lat.get('max', '-')} | {statistics.median(alll):.2f} / {max(alll):.2f} | "
            f"{runs[0]['prompt_tokens']} | {runs[0]['visual_tokens_per_image']} | {statistics.median(first_out):.0f} | "
            f"{p['repaired']} | {len(p['failed'])} | {max(r['peak_memory_gb'] or 0 for r in runs)} | "
            f"{p.get('child_peak_rss_gb')} | state {st}/{len(san)}, sup {sc}+/{sw}-, con {cc}+/{cw}- |")


def main() -> None:
    for path in sys.argv[1:]:
        r = json.load(open(path))
        print(f"\n{path} label_mode={r.get('label_mode', 'id')} cold={r.get('cold_start', {}).get('spawn_load_warmup_sec')}s "
              f"load_ms={r.get('cold_start', {}).get('load_ms')} warmup_ms={r.get('cold_start', {}).get('warmup_ms')}")
        print("| kind | px | frames | max_tok | n | p50 ok s | max ok s | p50/max all s | prompt tok | vis tok/img | "
              "median out tok | repaired | failed | mlx peak GB | child RSS GB | synthetic sanity |")
        print("|" + "---|" * 16)
        v = r.get("verify")
        for p in r["profiles"] + (v if isinstance(v, list) else ([v] if v else [])):
            print(row(p))


if __name__ == "__main__":
    main()
