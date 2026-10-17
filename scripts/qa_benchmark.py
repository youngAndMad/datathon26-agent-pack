#!/usr/bin/env python3
"""Local evaluation benchmark over a PRIVATE manifest of authorized clips (agent F, spec §13).

Runs the black-box jury client sequentially over every clip in the manifest against a running
service, then reports:
  * video-level confusion matrix (positive = violation) with precision/recall/F1/FPR, in two
    variants: ``failures_as_errors`` (failed/unprocessed counted as wrong — the headline metric)
    and ``processed_only`` (explicitly labeled, never the headline);
  * one-to-one temporal matching (Hungarian assignment with dummy rows/columns: maximize the number
    of matches within tolerance, then minimize total absolute error) at 0.5/1/2/5 s, absolute anchor
    error median/p90/max at the 5 s tolerance — internal diagnostics, not organizer thresholds;
  * latency upload->terminal p50/p95/max with sample counts (p95 flagged unreliable for n<20);
  * the same per split.

Refuses mock mode (fake adapter) unconditionally and refuses test mode unless --allow-test-mode,
in which case the report is labeled ``evaluation_level: test_mode_plumbing`` and is NOT a
detection evaluation. Writes evaluation/reports/private/<run>.json (git-ignored; per-clip rows use
opaque clip ids only) and <run>.summary.json (aggregates only).

Exit codes: 0 completed; 1 completed with contract violations; 2 blocked (no manifest / no
authorized data / service not certifiable); 5 usage error.

Manifest (JSON list or {"clips": [...]}) — see evaluation/manifest/README.md:
  {"clip_id": "c001", "path": "/abs/private/x.mov", "sha256": "...", "duration_sec": 41.2,
   "label_verdict": "violation"|"no_violation", "label_timestamps_sec": [20.1],
   "label_types": ["..."] (optional, reviewed only), "group": "g03", "split": "dev"|"val"|"holdout"}
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from qa_jury_client import read_key, run_job  # noqa: E402
from qa_smoke import service_identity  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "evaluation" / "manifest" / "private" / "manifest.json"
REPORT_DIR = ROOT / "evaluation" / "reports" / "private"
TOLERANCES = (0.5, 1.0, 2.0, 5.0)


# --------------------------------------------------------------------------- assignment

def hungarian(cost: list[list[float]]) -> list[int]:
    """Min-cost perfect assignment for a square matrix (Kuhn-Munkres, O(n^3)).
    Returns col index assigned to each row."""
    n = len(cost)
    if n == 0:
        return []
    INF = float("inf")
    u = [0.0] * (n + 1)
    v = [0.0] * (n + 1)
    p = [0] * (n + 1)
    way = [0] * (n + 1)
    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = [INF] * (n + 1)
        used = [False] * (n + 1)
        while True:
            used[j0] = True
            i0 = p[j0]
            delta = INF
            j1 = 0
            for j in range(1, n + 1):
                if not used[j]:
                    cur = cost[i0 - 1][j - 1] - u[i0] - v[j]
                    if cur < minv[j]:
                        minv[j] = cur
                        way[j] = j0
                    if minv[j] < delta:
                        delta = minv[j]
                        j1 = j
            for j in range(n + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while True:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
            if j0 == 0:
                break
    ans = [0] * n
    for j in range(1, n + 1):
        if p[j]:
            ans[p[j] - 1] = j - 1
    return ans


def match_events(truth: list[float], pred: list[float], tol: float,
                 truth_types: list[str] | None = None, pred_types: list[str] | None = None,
                 ) -> list[tuple[int, int, float]]:
    """One-to-one matching: maximum number of pairs with |t-p| <= tol (and compatible types when
    both type lists are given), then minimum total absolute error. Returns (ti, pi, abs_err)."""
    nt, np_ = len(truth), len(pred)
    if nt == 0 or np_ == 0:
        return []
    valid = [[abs(t - p) <= tol + 1e-9 and (truth_types is None or pred_types is None
                                            or truth_types[i] == pred_types[j])
              for j, p in enumerate(pred)] for i, t in enumerate(truth)]
    # Big-M: any valid match saves more than the sum of all valid errors -> count first.
    big = 1.0 + sum(abs(t - p) for i, t in enumerate(truth) for j, p in enumerate(pred) if valid[i][j])
    n = nt + np_  # square with dummies: truth i may pair with dummy column, pred j with dummy row
    cost = [[big] * n for _ in range(n)]
    for i in range(nt):
        for j in range(np_):
            if valid[i][j]:
                cost[i][j] = abs(truth[i] - pred[j])
    for i in range(nt, n):  # dummy rows vs dummy columns are free
        for j in range(np_, n):
            cost[i][j] = 0.0
    assign = hungarian(cost)
    out = []
    for i in range(nt):
        j = assign[i]
        if j < np_ and valid[i][j]:
            out.append((i, j, abs(truth[i] - pred[j])))
    return out


# --------------------------------------------------------------------------- metrics

def _ratio(a: float, b: float) -> float | None:
    return None if b == 0 else round(a / b, 4)


def confusion(rows: list[dict], *, failures_as_errors: bool) -> dict:
    tp = fp = tn = fn = failed = 0
    for r in rows:
        pos = r["label_verdict"] == "violation"
        pred = r.get("predicted")
        if pred not in ("violation", "no_violation"):
            failed += 1
            if not failures_as_errors:
                continue
            pred = "no_violation" if pos else "violation"  # always the wrong answer
        if pos and pred == "violation":
            tp += 1
        elif pos:
            fn += 1
        elif pred == "violation":
            fp += 1
        else:
            tn += 1
    prec = _ratio(tp, tp + fp)
    rec = _ratio(tp, tp + fn)
    f1 = None if not prec or not rec else round(2 * prec * rec / (prec + rec), 4)
    return {"variant": "failures_as_errors" if failures_as_errors else "processed_only",
            "n": tp + fp + tn + fn, "failed_or_unprocessed": failed,
            "tp": tp, "fp": fp, "tn": tn, "fn": fn,
            "precision": prec, "recall": rec, "f1": f1, "fpr": _ratio(fp, fp + tn),
            "accuracy": _ratio(tp + tn, tp + fp + tn + fn)}


def quantile(xs: list[float], q: float) -> float | None:
    """Nearest-rank quantile (no interpolation; honest for small n)."""
    if not xs:
        return None
    s = sorted(xs)
    k = max(1, math.ceil(q * len(s)))
    return round(s[k - 1], 3)


def latency_stats(values: list[float]) -> dict:
    v = [x for x in values if x is not None]
    return {"n": len(v), "p50": quantile(v, 0.5), "p95": quantile(v, 0.95),
            "p95_reliable": len(v) >= 20, "max": round(max(v), 3) if v else None}


def temporal(rows: list[dict], *, use_types: bool) -> dict:
    out = {"truth_events": 0, "predicted_events": 0, "by_tolerance": {}}
    errs5: list[float] = []
    for tol in TOLERANCES:
        matched = 0
        for r in rows:
            truth = r.get("label_timestamps_sec") or []
            pred = r.get("timecodes_sec") or []
            tt = r.get("label_types") if use_types else None
            pt = r.get("violation_types") if use_types else None
            m = match_events(truth, pred, tol, tt, pt)
            matched += len(m)
            if tol == TOLERANCES[-1]:
                errs5 += [e for _, _, e in m]
        out["by_tolerance"][str(tol)] = matched
    out["truth_events"] = sum(len(r.get("label_timestamps_sec") or []) for r in rows)
    out["predicted_events"] = sum(len(r.get("timecodes_sec") or []) for r in rows)
    te = out["truth_events"]
    out["match_rate"] = {k: _ratio(v, te) for k, v in out["by_tolerance"].items()}
    out["abs_error_at_5s"] = {"n": len(errs5), "median": round(statistics.median(errs5), 3) if errs5 else None,
                              "p90": quantile(errs5, 0.9) if len(errs5) >= 10 else None,
                              "max": round(max(errs5), 3) if errs5 else None}
    out["types_used"] = use_types
    return out


def summarize(rows: list[dict]) -> dict:
    use_types = bool(rows) and all(r.get("label_types") is not None for r in rows if r.get("label_timestamps_sec"))
    return {
        "clips": len(rows),
        "groups": len({r.get("group") for r in rows}),
        "video_level": confusion(rows, failures_as_errors=True),
        "video_level_processed_only": confusion(rows, failures_as_errors=False),
        "temporal": temporal(rows, use_types=use_types),
        "latency_upload_to_terminal_sec": latency_stats([r.get("upload_to_terminal_sec") for r in rows]),
        "latency_accepted_to_terminal_sec": latency_stats([r.get("accepted_to_terminal_sec") for r in rows]),
        "contract_violations": sum(1 for r in rows if r.get("contract_errors")),
        "failed_error_codes": sorted({r["error_code"] for r in rows if r.get("error_code")}),
    }


# --------------------------------------------------------------------------- manifest

def load_manifest(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    clips = data["clips"] if isinstance(data, dict) else data
    seen = set()
    for c in clips:
        for k in ("clip_id", "path", "sha256", "label_verdict", "split"):
            if k not in c:
                raise ValueError(f"manifest entry missing {k}")
        if c["label_verdict"] not in ("violation", "no_violation"):
            raise ValueError("label_verdict must be violation|no_violation")
        if c["clip_id"] in seen:
            raise ValueError("duplicate clip_id")
        seen.add(c["clip_id"])
    return clips


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Local evaluation benchmark (private data only)")
    ap.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    ap.add_argument("--key-file", type=Path, default=ROOT / "secrets" / "jury_api_key")
    ap.add_argument("--data-dir", type=Path, default=None, help="service data dir (worker status fallback)")
    ap.add_argument("--split", action="append", help="only these splits (repeatable)")
    ap.add_argument("--run-name", default=None)
    ap.add_argument("--timeout", type=float, default=300.0)
    ap.add_argument("--allow-test-mode", action="store_true",
                    help="permit a TEST-mode service; output is labeled plumbing, not evaluation")
    a = ap.parse_args(argv)

    if not a.manifest.is_file():
        print(f"BLOCKED: no private manifest at {a.manifest.relative_to(ROOT) if a.manifest.is_relative_to(ROOT) else a.manifest}. "
              "Authorized dataset + local reviewer required (plan.md X1/X2, D004). "
              "Build one with evaluation/manifest/build_manifest.py on the local machine.")
        return 2
    try:
        clips = load_manifest(a.manifest)
    except (ValueError, KeyError, json.JSONDecodeError) as e:
        print(f"usage error: invalid manifest ({e})", file=sys.stderr)
        return 5
    if a.split:
        clips = [c for c in clips if c["split"] in a.split]
    if not clips:
        print("BLOCKED: manifest selects no clips")
        return 2
    try:
        key = read_key(a.key_file)
    except (OSError, ValueError):
        print("usage error: cannot read key file", file=sys.stderr)
        return 5

    ident = service_identity(a.base_url, data_dir=a.data_dir)
    if ident.get("is_mock") is True or ident.get("adapter") == "fake":
        print("REFUSED: service runs the mock/fake adapter; mock results are never an evaluation")
        return 2
    if ident.get("is_mock") is None:
        print("BLOCKED: cannot determine whether the service model is real (no model info via "
              "/readyz or local worker status); refusing to label results as an evaluation")
        return 2
    level = "local_real_model"
    if ident.get("mode") != "production":
        if not a.allow_test_mode:
            print(f"REFUSED: service mode is {ident.get('mode')!r}; pass --allow-test-mode to run a "
                  "labeled plumbing benchmark")
            return 2
        level = "test_mode_plumbing"

    rows = []
    for c in clips:
        p = Path(c["path"])
        row = {k: c.get(k) for k in ("clip_id", "split", "group", "label_verdict", "label_timestamps_sec",
                                     "label_types")}
        if not p.is_file():
            row.update(predicted=None, error_code="clip_missing", contract_errors=[])
            rows.append(row)
            continue
        if sha256_file(p) != c["sha256"]:
            row.update(predicted=None, error_code="sha256_mismatch", contract_errors=[])
            rows.append(row)
            continue
        res = run_job(a.base_url, key, p, timeout_sec=a.timeout, file_label=c["clip_id"],
                      duration_sec=c.get("duration_sec"))
        row.update(predicted=res.verdict if res.terminal_status == "done" and not res.errors else None,
                   terminal_status=res.terminal_status, error_code=res.error_code
                   or (None if res.terminal_status else f"http_{res.submit_http}" if res.submit_http else "timeout"),
                   timecodes_sec=res.timecodes_sec, violation_types=res.violation_types,
                   upload_to_terminal_sec=res.upload_to_terminal_sec,
                   accepted_to_terminal_sec=res.accepted_to_terminal_sec,
                   contract_errors=res.errors)
        rows.append(row)
        print(f"{c['clip_id']}: {row['terminal_status']} {row.get('predicted')} "
              f"{row.get('upload_to_terminal_sec')}s errors={len(res.errors)}")

    run = a.run_name or datetime.now(UTC).strftime("bench-%Y%m%dT%H%M%SZ")
    splits = sorted({r["split"] for r in rows})
    summary = {
        "run": run, "created_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "evaluation_level": level, "service": {k: ident.get(k) for k in ("mode", "adapter", "model", "revision",
                                                                          "registry", "source")},
        "note": "Tolerances 0.5/1/2/5 s are internal diagnostics, not organizer thresholds. "
                "Single-timestamp labels do not imply intervals. Small-sample results are uncertain.",
        "overall": summarize(rows),
        "per_split": {s: summarize([r for r in rows if r["split"] == s]) for s in splits},
    }
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / f"{run}.json").write_text(json.dumps({"summary": summary, "clips": rows}, ensure_ascii=False,
                                                       indent=2) + "\n", encoding="utf-8")
    (REPORT_DIR / f"{run}.summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
                                                    encoding="utf-8")
    print(json.dumps(summary["overall"], indent=2))
    print(f"wrote {REPORT_DIR.relative_to(ROOT)}/{run}.json (private, git-ignored)")
    return 1 if summary["overall"]["contract_violations"] else 0


if __name__ == "__main__":
    sys.exit(main())
