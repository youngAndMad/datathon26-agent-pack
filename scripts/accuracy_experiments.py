"""Offline experiment preparation; never opens media or starts inference.

Only generated files under ignored data/accuracy-experiments are written. The
split check validates declared metadata, not the truth or independence of labels.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "accuracy-experiments"


def profiles(source: dict) -> dict[str, dict]:
    """One experimental factor at a time; original config is never changed."""
    baseline = copy.deepcopy(source)
    dense = copy.deepcopy(source)
    sampling = dense["sampling"]
    cap = int(sampling["max_frames_per_observation"])
    if cap < 3:
        raise ValueError("observation frame cap must allow at least three frames")
    fps = float(sampling["uniform_fps"]) * 2
    window = min(float(sampling["window_sec"]) / 2, (cap - 1) / fps)
    overlap = min(float(sampling["overlap_sec"]) / 2, window / 4)
    sampling.update(window_sec=window, overlap_sec=overlap, uniform_fps=fps)
    # Existing fallback tiers are kept: a long clip must not silently exceed its
    # deadline. Record the selected tier in measurements; it may undo this factor.
    if dense.get("sampling_tiers"):
        dense["sampling_tiers"][0].update(
            tier_id="experimental-short-dense", window_sec=window,
            overlap_sec=overlap, uniform_fps=fps)
    resolution = copy.deepcopy(source)
    resolution["sampling"]["model_long_side_px"] = int(
        source["sampling"]["model_long_side_px"] * 1.5)
    for name, profile in (("short-dense", dense), ("resolution", resolution)):
        profile["profile_id"] = "experiment-" + name
        profile["status"] = "unmeasured_experiment_only"
        profile["measurements_note"] = (
            "Unmeasured variant. Inherited cost estimates are not validated on this "
            "hardware. Measure latency, actual selected tier and failures before use.")
    return {"baseline": baseline, "short-dense": dense, "resolution": resolution}


def rule_plan(base_rules: list[str], candidates: list[str]) -> list[dict]:
    if not base_rules or any(not re.fullmatch(r"CW\d+", r) for r in base_rules + candidates):
        raise ValueError("provide baseline rules and candidate IDs in CW<number> format")
    base = sorted(set(base_rules))
    return [{"name": "baseline", "rules": base, "evaluation_only": True}] + [
        {"name": "add-" + rule, "rules": sorted(set(base + [rule])), "evaluation_only": True}
        for rule in sorted(set(candidates) - set(base))]


def prepare(source: Path, destination: Path, base_rules: list[str], candidates: list[str]) -> dict:
    destination = destination.resolve()
    if not destination.is_relative_to(OUTPUT.resolve()) or destination == OUTPUT.resolve():
        raise ValueError("output must be a new subdirectory of data/accuracy-experiments")
    raw = source.read_bytes()
    generated = profiles(json.loads(raw))
    plan = {
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "inference_started": False,
        "profiles": [name + ".json" for name in generated],
        "rule_ablation": rule_plan(base_rules, candidates),
        "comparison_policy": "same split, model, quantization, device and rules; one factor at a time",
        "timing_policy": "inherited estimates unmeasured; measure real full-job latency against 300 seconds",
        "approval_policy": "candidate evaluation only; never changes production rule approval",
    }
    destination.mkdir(parents=True, exist_ok=False)
    for name, profile in generated.items():
        (destination / (name + ".json")).write_text(json.dumps(profile, indent=2), encoding="utf-8")
    (destination / "plan.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
    return plan


def validate_split(manifest: dict) -> dict:
    """Validate declarations without opening the referenced clips or echoing IDs."""
    if not isinstance(manifest, dict):
        raise ValueError("manifest must be an object")
    rows = manifest.get("clips")
    if not isinstance(rows, list) or not rows:
        raise ValueError("manifest must contain a nonempty clips list")
    groups: dict[str, str] = {}
    hashes: dict[str, str] = {}
    ids: set[str] = set()
    counts: dict[str, int] = {}
    new_holdout = used_holdout = 0
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("clip metadata must be objects")
        clip, group, digest, split = (row.get(k) for k in ("clip_id", "scene_group", "sha256", "split"))
        if not all(isinstance(x, str) and x.strip() for x in (clip, group, digest, split)):
            raise ValueError("clip_id, scene_group, sha256 and split are required")
        if split not in {"train", "dev", "holdout"}:
            raise ValueError("split must be train, dev or holdout")
        if not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
            raise ValueError("sha256 must contain 64 hexadecimal characters")
        if type(row.get("previously_used")) is not bool:
            raise ValueError("previously_used must explicitly be true or false")
        if clip in ids:
            raise ValueError("duplicate clip_id in manifest")
        ids.add(clip)
        for value, mapping in ((group, groups), (digest.lower(), hashes)):
            if value in mapping and mapping[value] != split:
                raise ValueError("scene group or duplicate content crosses split boundaries")
            mapping[value] = split
        counts[split] = counts.get(split, 0) + 1
        if split == "holdout":
            used_holdout += row["previously_used"]
            new_holdout += not row["previously_used"]
    return {"metadata_valid": True, "counts": counts,
            "declared_new_holdout": new_holdout, "already_used_holdout": used_holdout,
            "independence_certified": False,
            "notice": "Declarations only. Reusing tuned clips cannot establish an independent holdout."}


# Deliberately whitelist scalar aggregate fields. Clip rows, labels, scene text,
# error detail and arbitrary strings must never be echoed by this comparison.
SCALARS = {"n", "total", "completed", "failed", "tp", "fp", "tn", "fn", "precision", "recall",
           "accuracy", "f1", "completion_rate", "end_to_end_accuracy", "accuracy_all_submitted",
           "accuracy_all_submissions",
           "latency_sec_max", "latency_sec_median", "latency_sec_p95", "over_deadline",
           "median", "max", "p95", "within_2s", "within_5s", "count", "unprocessed",
           "successful", "correct_all_rate", "deadline_exceeded", "model_unavailable",
           "model_output_invalid", "gpu_oom", "internal", "internal_error", "limits_exceeded",
           "invalid_media", "unsupported_container", "timeline_unsupported", "decode_failed",
           "storage_full", "worker_lost", "evidence_invalid", "rule_registry_invalid", "input_unusable"}
SECTIONS = {"classification", "classification_completed", "completed_classification", "completed_metrics", "service", "reliability",
            "failures_by_code", "errors", "latency_sec", "time_err_sec", "failure_counts", "counts"}


def aggregate_only(summary: dict) -> dict:
    out = {}
    for key, value in summary.items():
        if key in SCALARS and (value is None or
                (type(value) in (int, float) and math.isfinite(value))):
            out[key] = value
        elif key in SECTIONS and isinstance(value, dict):
            out[key] = aggregate_only(value)
    return out


def compare(summaries: list[dict]) -> dict:
    if len(summaries) < 2:
        raise ValueError("comparison requires at least two summary files")
    if any(not isinstance(s, dict) for s in summaries):
        raise ValueError("summaries must be objects")
    checks = {}
    for section, fields in {
        "reproducibility": ("registry_sha256", "model_manifest_sha256", "model_configuration_sha256", "backend", "device"),
        "evaluation_scope": ("selection_sha256", "splits_sha256", "split"),
    }.items():
        for field in fields:
            if any(section in s and not isinstance(s[section], dict) for s in summaries):
                raise ValueError("comparison metadata must be objects")
            values = [s.get(section, {}).get(field) for s in summaries]
            checks[field] = "missing" if any(v is None for v in values) else (
                "match" if all(v == values[0] for v in values) else "different")
    return {"runs": [{"input_index": i, "metrics": aggregate_only(s)} for i, s in enumerate(summaries)],
            "declared_comparison_checks": checks,
            "comparability_verified": False,
            "notice": "Verify matching split, model, device, rule set and selected tiers locally; "
                      "legacy confusion counts can include technical failures. No automatic winner."}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("prepare", help="write experiment profiles, never run inference")
    p.add_argument("--profile", type=Path, default=ROOT / "configs/runtime-profile.json")
    p.add_argument("--output", type=Path, default=OUTPUT / "prepared")
    p.add_argument("--baseline-rules", default="CW11,CW12")
    p.add_argument("--candidate-rules", default="")
    p = commands.add_parser("validate-split", help="validate local declared split metadata")
    p.add_argument("manifest", type=Path)
    p = commands.add_parser("compare", help="print only allowed numeric aggregate metrics")
    p.add_argument("summaries", type=Path, nargs="+")
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            split_rules = lambda value: [r.strip() for r in value.split(",") if r.strip()]
            result = prepare(args.profile, args.output, split_rules(args.baseline_rules),
                             split_rules(args.candidate_rules))
        elif args.command == "validate-split":
            result = validate_split(json.loads(args.manifest.read_text(encoding="utf-8")))
        else:
            result = compare([json.loads(p.read_text(encoding="utf-8")) for p in args.summaries])
        print(json.dumps(result, indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError):
        # Never echo user-controlled paths, document contents or private identifiers.
        print(json.dumps({"error": "Invalid input or output already exists; check the local runbook/schema."}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
