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
from collections import Counter
import hashlib
import json
import math
import os
import re
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


def positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("expected a positive integer")
    return number


def safe_component(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value) or value.endswith("."):
        raise ValueError("run and clip names must be safe single path components")
    if value.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)], *[f"LPT{i}" for i in range(1, 10)]}:
        raise ValueError("reserved Windows path component")
    return value


def load_selection(source: Path, *, manifest: bool, clips: str = "", split: str = "",
                   splits_path: Path | None = None, expected_clips: int | None = None) -> list[dict]:
    """Validate selection locally before model imports; never expose organizer data to prompts."""
    raw = json.loads(source.read_text(encoding="utf-8-sig"))
    if manifest:
        if not isinstance(raw, dict) or raw.get("version") != 1 or not isinstance(raw.get("clips"), list):
            raise ValueError("expected a version 1 evaluation manifest with clips")
        raw = raw["clips"]
    if not isinstance(raw, list):
        raise ValueError("evaluation index must be a list")
    rows, seen_ids, seen_hashes = [], set(), set()
    for entry in raw:
        if not isinstance(entry, dict):
            raise ValueError("evaluation entries must be objects")
        row = dict(entry)
        if manifest:
            row.update(clip=entry.get("clip_id"), file=entry.get("path"), label=entry.get("label_verdict"))
        name = row.get("clip")
        if not isinstance(name, str):
            raise ValueError("missing clip id")
        safe_component(name)
        if name in seen_ids:
            raise ValueError("duplicate clip ids")
        seen_ids.add(name)
        digest = row.get("sha256")
        if manifest and (not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest)):
            raise ValueError("manifest entries require SHA-256")
        if digest:
            digest = digest.lower()
            if digest in seen_hashes:
                raise ValueError("duplicate video SHA-256 values")
            seen_hashes.add(digest)
            row["sha256"] = digest
        if row.get("label") not in {"violation", "no_violation"}:
            raise ValueError("evaluation requires explicit violation/no_violation labels")
        path = row.get("file")
        if not isinstance(path, str) or not path or not Path(path).is_file():
            raise ValueError("evaluation source file missing")
        timestamps = row.get("label_timestamps_sec", [] if row.get("label_sec") is None else [row["label_sec"]])
        if not isinstance(timestamps, list) or any(isinstance(t, bool) or not isinstance(t, (int, float)) or not math.isfinite(t) or t < 0 for t in timestamps):
            raise ValueError("label timestamps must be finite nonnegative numbers")
        row["label_timestamps_sec"] = timestamps
        row["label_sec"] = timestamps[0] if timestamps else None
        rows.append(row)
    if clips:
        wanted = {c.strip() for c in clips.split(",") if c.strip()}
        if not wanted.issubset(seen_ids):
            raise ValueError("requested clip ids are absent")
        rows = [r for r in rows if r["clip"] in wanted]
    if split:
        splits = {} if manifest else json.loads(splits_path.read_text(encoding="utf-8"))
        rows = [r for r in rows if (r.get("split") if manifest else splits.get(r["clip"])) == split]
    if not rows:
        raise ValueError("evaluation selection is empty; no model was started")
    if expected_clips is not None and len(rows) != expected_clips:
        raise ValueError(f"expected {expected_clips} selected clips, found {len(rows)}; no model was started")
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rules", required=True)
    ap.add_argument("--manifest", type=Path)
    ap.add_argument("--expected-clips", type=positive_int)
    ap.add_argument("--check-selection", action="store_true")
    ap.add_argument("--clips", default="")
    ap.add_argument("--split", default="")
    ap.add_argument("--splits", type=Path, default=ROOT / "data" / "review_sheets" / "splits.json")
    ap.add_argument("--run", default="")
    ap.add_argument("--registry", type=Path, default=ROOT / "rules" / "registry.yaml")
    ap.add_argument("--profile", type=Path, default=None)
    a = ap.parse_args()
    source = a.manifest or INDEX
    try:
        clips = load_selection(source, manifest=a.manifest is not None, clips=a.clips,
                               split=a.split, splits_path=a.splits, expected_clips=a.expected_clips)
        run = safe_component(a.run or datetime.now(UTC).strftime("ev-%m%d-%H%M%S"))
        out_root = ROOT / "data" / "dev_eval" / run
        if out_root.exists():
            raise ValueError("run output already exists; choose a new --run name")
    except (ValueError, OSError, TypeError) as exc:
        ap.error(str(exc))
    if a.check_selection:
        print(json.dumps({"selected": len(clips), "verdict_counts": dict(Counter(c["label"] for c in clips)), "model_started": False}))
        return 0
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
    out_root.mkdir(parents=True, exist_ok=False)
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
            diagnostics = {}
            ctx = PipelineContext(media=media, perception=perception, registry=registry, rules_factory=create_rules,
                                  reporting=create_reporting(registry), profile=settings.profile, mode="test",
                                  limits={"max_duration_sec": settings.max_duration_sec,
                                          "max_pixel_area": settings.max_pixel_area},
                                  config_version="dev", diagnostics=diagnostics.update)
            os.environ["CASHWATCH_DEBUG_DECISIONS"] = str(out_root / f"{c['clip']}.decisions.jsonl")
            t0 = time.monotonic()
            row = {"clip": c["clip"], "label": c.get("label"), "label_sec": c.get("label_sec"),
                   "label_timestamps_sec": c["label_timestamps_sec"]}
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
                    try:
                        perception.warmup(timeout_sec=180)
                    except Exception:
                        # Preserve this failure in the denominator even if recovery fails.
                        row["recovery_error"] = "warmup_failed"
            except Exception:
                row.update(status="failed", error="internal", detail=traceback.format_exc(limit=3)[-400:])
            row["sec"] = round(time.monotonic() - t0, 1)
            row["diagnostics"] = diagnostics
            shutil.rmtree(work, ignore_errors=True)
            (out_root / f"{c['clip']}.json").write_text(json.dumps(row, ensure_ascii=False, indent=1))
            results.append(row)
            print(f"{c['clip']:6} {row.get('label','?'):12} -> {row.get('verdict', row.get('error'))!s:12} "
                  f"events={[(e['rule'], e['t']) for e in row.get('events', [])]} label_t={c.get('label_sec')} {row['sec']}s",
                  flush=True)
        perception.close()
    summary = summarize(results)
    summary.update(run=run, rules=sorted(rules), profile=settings.profile.get("profile_id"))
    summary["reproducibility"] = configuration_fingerprint(settings, a.registry)
    summary["evaluation_scope"] = {
        "split": a.split or "all_selected",
        "independent_holdout": False,
        "note": "Development evaluation; a split name does not establish untouched holdout data.",
        "selection_sha256": hashlib.sha256(json.dumps(sorted(c["clip"] for c in clips)).encode()).hexdigest(),
        "selection_source_sha256": sha256(source),
        "selection_source_kind": "manifest" if a.manifest else "index",
        "splits_sha256": sha256(a.splits) if a.split and not a.manifest else None,
    }
    (out_root / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1))
    print("SUMMARY", json.dumps(summary, ensure_ascii=False))
    return 0


def summarize(rows: list[dict]) -> dict:
    """Keep legacy penalized confusion fields, add explicit completed-only metrics.

    Failed jobs never imply a model verdict. Legacy FP/FN include failures for
    backward compatibility only; use completed_classification for recognition.
    Per-rule output and stage counts cannot identify the cause of a false negative.
    """
    if not rows:
        raise ValueError("evaluation selection is empty")
    if any(r.get("label") not in {"violation", "no_violation"} for r in rows):
        raise ValueError("explicit binary labels required")
    tp = fp = tn = fn = failed = 0
    complete = Counter(tp=0, fp=0, tn=0, fn=0)
    failures = Counter()
    emitted = Counter()
    stage_counts = Counter()
    per_rule = {}
    diagnostics_rows = 0
    errs = []
    for r in rows:
        diagnostic = r.get("diagnostics", {})
        if diagnostic:
            diagnostics_rows += 1
            stage_counts.update(diagnostic.get("counters", {}))
            for rule, counts in diagnostic.get("per_rule", {}).items():
                per_rule.setdefault(rule, Counter()).update(counts)
        pos = r.get("label") == "violation"
        if r.get("status") != "done":
            failed += 1
            fn += pos
            fp += not pos
            failures[str(r.get("error") or "unspecified")] += 1
            continue
        if r.get("verdict") not in {"violation", "no_violation"}:
            raise ValueError("completed result requires a valid verdict")
        pred = r["verdict"] == "violation"
        complete["tp" if pos and pred else "fn" if pos else "fp" if pred else "tn"] += 1
        emitted.update(e["rule"] for e in r.get("events", []))
        tp += pos and pred
        fn += pos and not pred
        fp += (not pos) and pred
        tn += (not pos) and not pred
        if pos and pred and r.get("label_sec") is not None and r.get("events"):
            errs.append(min(abs(e["t"] - r["label_sec"]) for e in r["events"]))
    prec = tp / (tp + fp) if tp + fp else None
    rec = tp / (tp + fn) if tp + fn else None
    return {"n": len(rows), "tp": tp, "fp": fp, "tn": tn, "fn": fn, "failed": failed,
            "metrics_version": 2,
            "legacy_confusion_semantics": "Top-level FP/FN and precision/recall/f1 penalize failed jobs; not a classifier confusion matrix.",
            "completed": len(rows) - failed,
            "completion_rate": round((len(rows) - failed) / len(rows), 3),
            "failure_counts": dict(sorted(failures.items())),
            "accuracy_all_submissions": round((tp + tn) / len(rows), 3),
            "completed_classification": classification_metrics(complete),
            "per_rule_emitted_events": dict(sorted(emitted.items())),
            "pipeline_diagnostics": {"rows_with_diagnostics": diagnostics_rows,
                "counters": dict(stage_counts), "per_rule": {k: dict(v) for k, v in per_rule.items()},
                "note": "Observed pipeline counts only; not per-rule accuracy or inferred causes of false negatives."},
            "precision": round(prec, 3) if prec is not None else None,
            "recall": round(rec, 3) if rec is not None else None,
            "f1": round(2 * prec * rec / (prec + rec), 3) if prec and rec else None,
            "time_err_sec": {"n": len(errs), "median": round(statistics.median(errs), 2) if errs else None,
                             "max": round(max(errs), 2) if errs else None,
                             "within_2s": sum(e <= 2 for e in errs), "within_5s": sum(e <= 5 for e in errs)},
            "time_err_semantics": "Nearest emitted event to the first clip timestamp on true-positive clips; remaining timestamps are preserved but not scored. Not one-to-one event localization accuracy.",
            "latency_sec_max": max((r["sec"] for r in rows), default=None)}


def classification_metrics(counts: dict) -> dict:
    tp, fp, tn, fn = (counts[k] for k in ("tp", "fp", "tn", "fn"))
    def ratio(n, d):
        return round(n / d, 3) if d else None
    return {**counts, "n": tp + fp + tn + fn,
            "accuracy": ratio(tp + tn, tp + fp + tn + fn),
            "precision": ratio(tp, tp + fp), "recall": ratio(tp, tp + fn),
            "f1": ratio(2 * tp, 2 * tp + fp + fn)}


def configuration_fingerprint(settings, registry_path: Path) -> dict:
    """Hash configuration, never dump environment variables, keys or model files."""
    model_files = {}
    for name in ("config.json", "generation_config.json", "preprocessor_config.json"):
        p = settings.model_path / name
        model_files[name] = sha256(p) if p.is_file() else None
    return {"profile_sha256": sha256(settings.profile_path),
            "registry_sha256": sha256(registry_path),
            "model_manifest_sha256": sha256(settings.model_manifest) if settings.model_manifest.is_file() else None,
            "model_configuration_sha256": model_files,
            "evaluator_sha256": sha256(Path(__file__)),
            "inference_code_sha256": {name: sha256(ROOT / "src" / "cashwatch" / "inference" / name)
                for name in ("prompts.py", "adapter.py")},
            "backend": settings.backend, "device": settings.device,
            "weights_verified_by_this_evaluator": False}


if __name__ == "__main__":
    sys.exit(main())
