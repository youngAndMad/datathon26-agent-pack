"""Synthetic-only latency/memory benchmark for the local perception adapter (agent C).

Uses generated moving-marker frames only (gray background, yellow zone, red square inside or
outside). The red-square check is SYNTHETIC PLUMBING SANITY, not domain accuracy. Holds the
shared accelerator reservation for the whole run. Writes JSON to data/bench/ (git-ignored).

Usage:
  .venv/bin/python scripts/model_bench.py --backend mlx --runs 5 \
      --profiles 640x16x512,640x16x256,480x16x256,384x16x256,480x12x256 --verify 640x24x384
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from cashwatch.deadline import Deadline
from cashwatch.dto import ObservationRequest, PredicateDefinition, VerificationRequest
from cashwatch.errors import PipelineError
from cashwatch.gpulock import gpu_reservation
from cashwatch.inference import adapter_config_from_profile, create_perception
from cashwatch.inference.synthetic import square_inside, write_frames
from cashwatch.settings import REPO_ROOT, Settings

CUE = PredicateDefinition(
    predicate_id="syn.red_outside_zone",
    question_en=("Is the red square outside the yellow rectangle in at least one frame? List frames where it is "
                 "outside as supporting and frames where it is inside as contradicting."),
)
CUE2 = PredicateDefinition(
    predicate_id="syn.zone_visible",
    question_en="Is the whole yellow rectangle visible in every frame?",
    counterexamples_en=["rectangle partly cut by the image border"],
)


class RssSampler:
    def __init__(self, pid_fn) -> None:
        self.pid_fn = pid_fn
        self.max_kb = 0
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self._stop.is_set():
            pid = self.pid_fn()
            if pid:
                try:
                    out = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True).stdout
                    self.max_kb = max(self.max_kb, int(out.strip() or 0))
                except (ValueError, OSError):
                    pass
            self._stop.wait(0.25)

    def __enter__(self):
        self._t.start()
        return self

    def __exit__(self, *a):
        self._stop.set()
        self._t.join()


def parse_profile(s: str) -> tuple[int, int, int]:
    px, n, tok = (int(x) for x in s.split("x"))
    return px, n, tok


def dims(px: int) -> tuple[int, int]:
    return px, round(px * 9 / 16)


def summarize(xs: list[float]) -> dict:
    if not xs:
        return {}
    return {"n": len(xs), "p50": round(statistics.median(xs), 3), "max": round(max(xs), 3),
            "min": round(min(xs), 3), "mean": round(statistics.fmean(xs), 3)}


def sanity(cues, frames, times) -> dict:
    gt_out = {f.frame_id for f, t in zip(frames, times) if not square_inside(t)}
    gt_in = {f.frame_id for f in frames} - gt_out
    a = next((c for c in cues if c.predicate_id == CUE.predicate_id), None)
    if a is None:
        return {}
    sup, con = set(a.supporting_frame_ids), set(a.contradicting_frame_ids)
    return {"state": a.state, "expected_state": "supported" if gt_out else "contradicted",
            "supporting_correct": len(sup & gt_out), "supporting_wrong": len(sup - gt_out),
            "contradicting_correct": len(con & gt_in), "contradicting_wrong": len(con - gt_in),
            "gt_outside": len(gt_out), "gt_inside": len(gt_in)}


def run_calls(p, kind, px, n, max_tok, runs, spacing, wd_root: Path, start_offset: float) -> dict:
    w, h = dims(px)
    rows = []
    for r in range(runs):
        t0 = start_offset + r * 17.0
        times = [round(t0 + i * spacing, 3) for i in range(n)]
        wd = wd_root / f"{kind}-{px}-{n}-{max_tok}-{r}"
        wd.mkdir(parents=True)
        frames = write_frames(wd, times, width=w, height=h)
        before = len(p.stats.calls)
        t = time.monotonic()
        err = None
        res = None
        try:
            if kind == "observe":
                req = ObservationRequest(job_id="bench", window_id=f"w{r:02d}", start_sec=times[0], end_sec=times[-1],
                                         frames=frames, task_version="bench", cue_predicates=[CUE, CUE2],
                                         max_output_tokens=max_tok)
                res = p.observe(req, wd, Deadline.in_seconds(600))
                cues = res.cue_assessments
            else:
                req = VerificationRequest(job_id="bench", candidate_id=f"c{r:02d}", rule_id="syn.rule",
                                          predicates=[CUE, CUE2], scope_conditions_en=["synthetic test scene"],
                                          boundary_notes_en=["trigger and completion unknown"],
                                          interval_start_sec=times[0], interval_end_sec=times[-1], frames=frames,
                                          task_version="bench", max_output_tokens=max_tok)
                res = p.verify(req, wd, Deadline.in_seconds(600))
                cues = res.assessments
        except PipelineError as e:
            err = e.code
            cues = []
        elapsed = time.monotonic() - t
        calls = p.stats.calls[before:]
        first = calls[0] if calls else None
        rows.append({
            "elapsed_sec": round(elapsed, 3),
            "error": err,
            "parse_status": getattr(res, "parse_status", None),
            "model_calls": len(calls),
            "prompt_tokens": first.prompt_tokens if first else None,
            "output_tokens": sum(c.output_tokens or 0 for c in calls),
            "finish_reason": first.finish_reason if first else None,
            "visual_tokens_per_image": sorted(set(first.visual_tokens)) if first else None,
            "visual_tokens_expected_total": sum(first.visual_tokens) if first else None,
            "visual_tokens_actual_total": first.visual_tokens_actual_total if first else None,
            "image_size": first.image_sizes[0] if first and first.image_sizes else None,
            "prefill_tps": round(first.prompt_tps, 1) if first and first.prompt_tps else None,
            "decode_tps": round(first.generation_tps, 1) if first and first.generation_tps else None,
            "peak_memory_gb": round(max((c.peak_memory_bytes or 0) for c in calls) / 1e9, 3) if calls else None,
            "sanity": sanity(cues, frames, times) if cues else {},
            "observations": len(res.observations) if res is not None and kind == "observe" else None,
        })
        print(f"{kind} {px}px {n}f {max_tok}tok run{r}: {rows[-1]['elapsed_sec']}s "
              f"out={rows[-1]['output_tokens']} status={rows[-1]['parse_status'] or err}", file=sys.stderr)
    ok = [x["elapsed_sec"] for x in rows if not x["error"]]
    return {"kind": kind, "long_side_px": px, "frames": n, "max_new_tokens": max_tok, "spacing_sec": spacing,
            "latency_sec": summarize(ok), "all_latency_sec": [x["elapsed_sec"] for x in rows],
            "output_tokens": summarize([x["output_tokens"] for x in rows]),
            "repaired": sum(1 for x in rows if x["parse_status"] == "repaired"),
            "failed": [x["error"] for x in rows if x["error"]], "runs": rows}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", default="mlx", choices=["mlx", "transformers"])
    ap.add_argument("--model-path", default=None)
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--profiles", default="640x16x512")
    ap.add_argument("--verify", default="640x24x384")
    ap.add_argument("--verify-runs", type=int, default=5)
    ap.add_argument("--label-mode", default="id", choices=["id", "index"])
    ap.add_argument("--max-pixels", type=int, default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    kw = {"mode": "test", "backend": args.backend}
    if args.model_path:
        kw["model_path"] = Path(args.model_path)
    elif args.backend == "transformers":
        kw["model_path"] = REPO_ROOT / "models" / "qwen3-vl-4b-instruct"
    settings = Settings(**kw)
    result: dict = {"synthetic_only": True, "note": "plumbing sanity on generated frames; NOT domain accuracy",
                    "started_at": datetime.now(UTC).isoformat(timespec="seconds"), "backend": args.backend,
                    "label_mode": args.label_mode, "max_pixels": args.max_pixels,
                    "profiles": []}
    with gpu_reservation(f"C-bench-{args.backend}"), tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for spec in args.profiles.split(","):
            px, n, tok = parse_profile(spec)
            cfg = adapter_config_from_profile(settings.profile, max_long_side_px=max(px, 640),
                                              frame_label_mode=args.label_mode, max_pixels=args.max_pixels)
            p = create_perception(settings, config=cfg)
            with RssSampler(lambda: getattr(p.gen, "pid", None)) as rss:
                t = time.monotonic()
                p.warmup(timeout_sec=600)
                cold = time.monotonic() - t
                if "cold_start" not in result:
                    result["cold_start"] = {"spawn_load_warmup_sec": round(cold, 3), **p.last_warmup,
                                            "info": p.info().model_dump()}
                prof = run_calls(p, "observe", px, n, tok, args.runs, 1.0, root, 0.0)
                prof["child_peak_rss_gb"] = round(rss.max_kb / 1024 / 1024, 3)
            result["profiles"].append(prof)
            p.close()
        result["verify"] = []
        for vspec in [v for v in args.verify.split(",") if v]:
            px, n, tok = parse_profile(vspec)
            p = create_perception(settings, config=adapter_config_from_profile(
                settings.profile, max_long_side_px=max(px, 640), frame_label_mode=args.label_mode,
                max_pixels=args.max_pixels))
            with RssSampler(lambda: getattr(p.gen, "pid", None)) as rss:
                p.warmup(timeout_sec=600)
                v = run_calls(p, "verify", px, n, tok, args.verify_runs, 0.25, root, 3.0)
                v["child_peak_rss_gb"] = round(rss.max_kb / 1024 / 1024, 3)
            result["verify"].append(v)
            p.close()

    # 145 s clip planning estimate (profile: ~16 coarse windows + up to 8 refinements)
    est = []
    for prof in result["profiles"]:
        lat = prof["latency_sec"]
        if not lat:
            continue
        est.append({"profile": f"{prof['long_side_px']}px x {prof['frames']} frames x {prof['max_new_tokens']} tok",
                    "observe_16_windows_p50_sec": round(16 * lat["p50"], 1),
                    "observe_16_windows_max_sec": round(16 * lat["max"], 1),
                    "fits_130s_at_max": 16 * lat["max"] <= 130, "failed_runs": len(prof["failed"])})
    result["budget_estimate_145s"] = est
    result["verify_budget_estimate"] = [
        {"profile": f"{v['long_side_px']}px x {v['frames']} frames x {v['max_new_tokens']} tok",
         "verify_8_p50_sec": round(8 * v["latency_sec"]["p50"], 1) if v["latency_sec"] else None,
         "verify_8_max_sec": round(8 * v["latency_sec"]["max"], 1) if v["latency_sec"] else None,
         "fits_70s_at_max": bool(v["latency_sec"]) and 8 * v["latency_sec"]["max"] <= 70,
         "failed_runs": len(v["failed"])}
        for v in result["verify"]]
    out = Path(args.out) if args.out else REPO_ROOT / "data" / "bench" / f"C-bench-{args.backend}-{int(time.time())}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: result[k] for k in ("cold_start", "budget_estimate_145s") if k in result}
                     | {"verify_budget_estimate": result.get("verify_budget_estimate")}, indent=2))
    print(f"wrote {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
