"""Development evaluation over the organizer OPEN training set (D012), in-process.

Runs the real pipeline (real media, real local model, rules from rules/registry.yaml) on each clip,
with selected DRAFT rules exposed in evaluation-only candidate mode, and compares the verdict and
event times with the organizer labels. Never used by the production service.

Usage:
  .venv/bin/python scripts/dev_eval.py --rules CW01,CW02,CW04 [--clips X-1,X-2] [--split dev] \
      [--splits data/review_sheets/splits.json] [--run NAME]
Outputs (git-ignored): data/dev_eval/<run>/<clip>.json and summary.json.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import statistics
import sys
import time
import traceback
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "data" / "review_sheets" / "index.json"


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rules", required=True)
    ap.add_argument("--clips", default="")
    ap.add_argument("--split", default="")
    ap.add_argument("--splits", type=Path, default=ROOT / "data" / "review_sheets" / "splits.json")
    ap.add_argument("--run", default="")
    ap.add_argument("--registry", type=Path, default=ROOT / "rules" / "registry.yaml")
    ap.add_argument("--profile", type=Path, default=None)
    a = ap.parse_args()
    os.environ["CASHWATCH_MODE"] = "test"
    os.environ.setdefault("CASHWATCH_BACKEND", "mlx")
    if a.profile:
        os.environ["CASHWATCH_PROFILE_PATH"] = str(a.profile)

    from cashwatch.deadline import Deadline, iso_utc, utc_now
    from cashwatch.dto import JobInput
    from cashwatch.errors import PipelineError
    from cashwatch.gpulock import gpu_reservation
    from cashwatch.inference import create_perception
    from cashwatch.media import create_media
    from cashwatch.pipeline.analyze import PipelineContext, analyze_job
    from cashwatch.reporting import create_reporting
    from cashwatch.rules import create_rules, load_registry
    from cashwatch.settings import get_settings

    settings = get_settings()
    rules = {r.strip() for r in a.rules.split(",") if r.strip()}
    registry = load_registry(a.registry, require_approved=False, candidate_rules=rules)
    exposed = registry.approved_rule_ids()
    if set(exposed) != rules:
        print("rules not exposed:", sorted(rules - set(exposed)), registry.health_errors()[:5], file=sys.stderr)
        return 2
    index = json.loads(INDEX.read_text())
    clips = index
    if a.clips:
        want = {c.strip() for c in a.clips.split(",")}
        clips = [c for c in index if c["clip"] in want]
    if a.split:
        splits = json.loads(a.splits.read_text())
        clips = [c for c in clips if splits.get(c["clip"]) == a.split]
    run = a.run or datetime.now(UTC).strftime("ev-%m%d-%H%M%S")
    out_root = ROOT / "data" / "dev_eval" / run
    out_root.mkdir(parents=True, exist_ok=True)
    media = create_media(max_pixel_area=settings.max_pixel_area,
                         motion_fps=float(settings.profile["sampling"]["motion_proposal_fps"]))
    results = []
    with gpu_reservation(f"dev_eval-{run}"):
        perception = create_perception(settings)
        perception.warmup(timeout_sec=180)
        for c in clips:
            src = Path(c["file"])
            job_id = str(uuid.uuid4())
            work = out_root / "_work" / job_id
            out = out_root / "_out" / c["clip"]
            shutil.rmtree(out, ignore_errors=True)
            work.mkdir(parents=True)
            out.mkdir(parents=True)
            now = utc_now()
            job = JobInput(job_id=job_id, principal_id="dev", source_path="x/source.mov", source_sha256=sha256(src),
                           request_started_at=iso_utc(now), accepted_at=iso_utc(now),
                           deadline_at=iso_utc(now + timedelta(seconds=settings.internal_terminal_sec)),
                           attempt=1, lease_generation=1, config_version="dev")
            ctx = PipelineContext(media=media, perception=perception, registry=registry, rules_factory=create_rules,
                                  reporting=create_reporting(registry), profile=settings.profile, mode="test",
                                  limits={"max_duration_sec": settings.max_duration_sec,
                                          "max_pixel_area": settings.max_pixel_area},
                                  config_version="dev")
            os.environ["CASHWATCH_DEBUG_DECISIONS"] = str(out_root / f"{c['clip']}.decisions.jsonl")
            t0 = time.monotonic()
            row = {"clip": c["clip"], "label": c.get("label"), "label_sec": c.get("label_sec")}
            try:
                rep = analyze_job(job, src, work, out, ctx, Deadline(job.deadline_at))
                row.update(status="done", verdict=rep.jury_result.verdict,
                           events=[{"rule": e.event.rule.rule_id, "t": e.evidence[0].timestamp_sec,
                                    "start": e.event.start_sec, "end": e.event.end_sec} for e in rep.events],
                           candidates=len(rep.coverage.refined_candidate_ids),
                           unresolved=[n.text_ru for n in rep.review_notes if n.kind == "unresolved_candidate"][:20],
                           timings=rep.stage_timings_ms)
            except PipelineError as e:
                row.update(status="failed", error=e.code, detail=e.detail[:200])
                if not perception.ready():
                    perception.warmup(timeout_sec=180)
            except Exception:
                row.update(status="failed", error="internal", detail=traceback.format_exc(limit=3)[-400:])
            row["sec"] = round(time.monotonic() - t0, 1)
            shutil.rmtree(work, ignore_errors=True)
            (out_root / f"{c['clip']}.json").write_text(json.dumps(row, ensure_ascii=False, indent=1))
            results.append(row)
            print(f"{c['clip']:6} {row.get('label','?'):12} -> {row.get('verdict', row.get('error'))!s:12} "
                  f"events={[(e['rule'], e['t']) for e in row.get('events', [])]} label_t={c.get('label_sec')} {row['sec']}s",
                  flush=True)
        perception.close()
    summary = summarize(results)
    summary.update(run=run, rules=sorted(rules), profile=settings.profile.get("profile_id"))
    (out_root / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1))
    print("SUMMARY", json.dumps(summary, ensure_ascii=False))
    return 0


def summarize(rows: list[dict]) -> dict:
    tp = fp = tn = fn = failed = 0
    errs = []
    for r in rows:
        pos = r.get("label") == "violation"
        if r.get("status") != "done":
            failed += 1
            fn += pos
            fp += not pos
            continue
        pred = r["verdict"] == "violation"
        tp += pos and pred
        fn += pos and not pred
        fp += (not pos) and pred
        tn += (not pos) and not pred
        if pos and pred and r.get("label_sec") is not None:
            errs.append(min(abs(e["t"] - r["label_sec"]) for e in r["events"]))
    prec = tp / (tp + fp) if tp + fp else None
    rec = tp / (tp + fn) if tp + fn else None
    return {"n": len(rows), "tp": tp, "fp": fp, "tn": tn, "fn": fn, "failed": failed,
            "precision": round(prec, 3) if prec is not None else None,
            "recall": round(rec, 3) if rec is not None else None,
            "f1": round(2 * prec * rec / (prec + rec), 3) if prec and rec else None,
            "time_err_sec": {"n": len(errs), "median": round(statistics.median(errs), 2) if errs else None,
                             "max": round(max(errs), 2) if errs else None,
                             "within_2s": sum(e <= 2 for e in errs), "within_5s": sum(e <= 5 for e in errs)},
            "latency_sec_max": max((r["sec"] for r in rows), default=None)}


if __name__ == "__main__":
    sys.exit(main())
