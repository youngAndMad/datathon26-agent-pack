"""Dev diagnostics (D012 open training set): show per-window coarse cue answers for one clip.

Usage: .venv/bin/python scripts/dev_probe.py X-15 --rules CW10,CW11 [--windows w001,w002]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("clip")
    ap.add_argument("--rules", required=True)
    ap.add_argument("--windows", default="")
    ap.add_argument("--tokens", type=int, default=0)
    a = ap.parse_args()
    os.environ["CASHWATCH_MODE"] = "test"
    from cashwatch.deadline import Deadline
    from cashwatch.dto import ObservationRequest
    from cashwatch.gpulock import gpu_reservation
    from cashwatch.inference import create_perception
    from cashwatch.media import create_media
    from cashwatch.pipeline.planner import build_plan
    from cashwatch.rules import load_registry
    from cashwatch.settings import get_settings

    s = get_settings()
    idx = {c["clip"]: c for c in json.loads((ROOT / "data/review_sheets/index.json").read_text())}
    src = Path(idx[a.clip]["file"])
    reg = load_registry(ROOT / "rules/registry.yaml", require_approved=False,
                        candidate_rules={r for r in a.rules.split(",") if r})
    cues = reg.cue_predicates()
    media = create_media()
    meta = media.probe_video(src, timeout_sec=30)
    plan = build_plan(meta, s.profile)
    work = Path(tempfile.mkdtemp(dir=ROOT / "data"))
    try:
        man = media.sample_video(src, meta, plan, work, deadline=Deadline.in_seconds(300))
        fr = {f.frame_id: f for f in man.frames}
        with gpu_reservation("dev_probe"):
            p = create_perception(s)
            p.warmup(timeout_sec=180)
            for w in plan.windows:
                if a.windows and w.window_id not in a.windows.split(","):
                    continue
                req = ObservationRequest(job_id="probe", window_id=w.window_id, start_sec=w.start_sec,
                                         end_sec=w.end_sec, frames=[fr[i] for i in man.window_frames[w.window_id]],
                                         task_version="probe", cue_predicates=cues,
                                         max_output_tokens=a.tokens or int(s.profile["sampling"]["observe_max_new_tokens"]))
                try:
                    b = p.observe(req, work, Deadline.in_seconds(120))
                except Exception as e:
                    print(w.window_id, w.start_sec, w.end_sec, "ERROR", getattr(e, "code", e))
                    continue
                t = lambda ids: [round(fr[i].relative_sec, 1) for i in ids if i in fr]  # noqa: E731
                print(f"{w.window_id} {w.start_sec:.1f}-{w.end_sec:.1f} {b.elapsed_ms}ms out={b.output_tokens} obs={len(b.observations)}")
                for o in b.observations:
                    print(f"   obs {t(o.frame_ids)} {o.action[:110]}")
                for c in b.cue_assessments:
                    print(f"   cue {c.predicate_id:28} {c.state:12} +{t(c.supporting_frame_ids)} -{t(c.contradicting_frame_ids)} {c.reasoning_note[:70]}")
            p.close()
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
