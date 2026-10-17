"""LOCAL-ONLY: aggregate reviewed annotations into per-rule validation counts for the
`approval.validation_counts` block in rules/registry.yaml. Prints counts only (no scene text).

Input: evaluation/annotations/private/annotations/*.annotation.json (exported from the review page).
Usage: .venv/bin/python scripts/local_rule_validation_report.py [--dir ...]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / "evaluation" / "annotations" / "private" / "annotations"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", type=Path, default=DEFAULT)
    a = ap.parse_args()
    files = sorted(a.dir.glob("*.annotation.json"))
    if not files:
        print(f"no annotations in {a.dir}")
        return 2
    clips = {}
    for f in files:
        d = json.loads(f.read_text(encoding="utf-8"))
        clips[d["clip_id"]] = d
    rules = sorted({e["rule_id"] for d in clips.values() for e in d.get("events", []) if e.get("rule_id") not in (None, "", "unmapped")})
    missing_meta = [c for c, d in clips.items() if not (d.get("annotator") and d.get("group_id") and d.get("split"))]
    print(f"annotated clips: {len(clips)}; missing annotator/group/split: {len(missing_meta)}")
    for rid in rules:
        pos_ev = cex = unc = 0
        pos_clips, neg_clips = set(), set()
        splits = defaultdict(int)
        for cid, d in clips.items():
            evs = [e for e in d.get("events", []) if e.get("rule_id") == rid]
            if not evs:
                neg_clips.add(cid)
                continue
            for e in evs:
                if e.get("counterexample"):
                    cex += 1
                elif e.get("eligible"):
                    pos_ev += 1
                    pos_clips.add(cid)
                    splits[d.get("split") or "?"] += 1
                else:
                    unc += 1
        print(f"\n{rid}: eligible positive events={pos_ev} in {len(pos_clips)} clips; compliant counterexamples={cex}; "
              f"ineligible/uncertain={unc}; clips with no {rid} event={len(neg_clips)}; positives by split={dict(splits)}")
        print("  registry snippet (edit owner/date; negatives = reviewed counterexamples + clips confirmed without it):")
        print(f"  validation_counts: {{positive: {pos_ev}, negative: {cex + len(neg_clips)}, unknown: {unc}, "
              f"dataset_note: \"open.zip, {len(clips)} reviewed clips\"}}")
    if not rules:
        print("no events linked to a rule yet")
    return 0


if __name__ == "__main__":
    sys.exit(main())
