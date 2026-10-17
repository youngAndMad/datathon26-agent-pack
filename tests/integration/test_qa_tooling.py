"""Tests of agent-F tooling that need no running service: benchmark metrics and one-to-one temporal
matching, manifest builder, T28 settings refusal. Pure/synthetic; no real media."""

from __future__ import annotations

import csv
import importlib.util
import itertools
import json
import random
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def bench():
    _load("qa_jury_client", ROOT / "scripts" / "qa_jury_client.py")
    _load("qa_smoke", ROOT / "scripts" / "qa_smoke.py")
    return _load("qa_benchmark", ROOT / "scripts" / "qa_benchmark.py")


def brute_force(truth, pred, tol):
    """Reference: max #matches, then min total error, over all injective partial assignments."""
    best = (0, 0.0)
    nt, np_ = len(truth), len(pred)
    for k in range(min(nt, np_), 0, -1):
        found = None
        for ti in itertools.combinations(range(nt), k):
            for pj in itertools.permutations(range(np_), k):
                errs = [abs(truth[i] - pred[j]) for i, j in zip(ti, pj)]
                if all(e <= tol + 1e-9 for e in errs):
                    s = sum(errs)
                    if found is None or s < found:
                        found = s
        if found is not None:
            return (k, round(found, 6))
    return best


def test_hungarian_square_optimal(bench):
    c = [[4, 1, 3], [2, 0, 5], [3, 2, 2]]
    a = bench.hungarian(c)
    assert sorted(a) == [0, 1, 2]
    assert sum(c[i][a[i]] for i in range(3)) == 5


def test_matching_prefers_more_matches_over_lower_error(bench):
    # Greedy nearest would pair truth 10.0 with pred 10.9 and lose truth 11.8.
    m = bench.match_events([10.0, 11.8], [9.2, 10.9], tol=1.0)
    assert len(m) == 2


def test_matching_is_one_to_one(bench):
    m = bench.match_events([20.0], [19.9, 20.1, 20.2], tol=1.0)
    assert len(m) == 1 and m[0][2] == pytest.approx(0.1)
    m = bench.match_events([20.0, 20.05], [20.02], tol=1.0)
    assert len(m) == 1


def test_matching_types(bench):
    m = bench.match_events([5.0, 6.0], [5.0, 6.0], tol=0.5, truth_types=["A", "B"], pred_types=["B", "A"])
    assert m == []
    m = bench.match_events([5.0, 6.0], [6.0, 5.0], tol=2.0, truth_types=["A", "B"], pred_types=["B", "A"])
    assert sorted((i, j) for i, j, _ in m) == [(0, 1), (1, 0)]


@pytest.mark.parametrize("seed", range(40))
def test_matching_equals_brute_force(bench, seed):
    rnd = random.Random(seed)
    truth = [round(rnd.uniform(0, 30), 2) for _ in range(rnd.randint(0, 4))]
    pred = [round(rnd.uniform(0, 30), 2) for _ in range(rnd.randint(0, 4))]
    tol = rnd.choice([0.5, 1.0, 2.0, 5.0])
    m = bench.match_events(truth, pred, tol)
    assert (len(m), round(sum(e for *_, e in m), 6)) == brute_force(truth, pred, tol)


def test_confusion_counts_failures_as_errors(bench):
    rows = [{"label_verdict": "violation", "predicted": "violation"},
            {"label_verdict": "violation", "predicted": None},          # failed -> FN
            {"label_verdict": "no_violation", "predicted": None},       # failed -> FP
            {"label_verdict": "no_violation", "predicted": "no_violation"}]
    h = bench.confusion(rows, failures_as_errors=True)
    assert (h["tp"], h["fn"], h["fp"], h["tn"], h["failed_or_unprocessed"]) == (1, 1, 1, 1, 2)
    assert h["precision"] == 0.5 and h["recall"] == 0.5 and h["fpr"] == 0.5
    p = bench.confusion(rows, failures_as_errors=False)
    assert p["n"] == 2 and p["precision"] == 1.0 and p["recall"] == 1.0


def test_latency_stats_small_sample_flag(bench):
    s = bench.latency_stats([10.0, 20.0, 30.0])
    assert s == {"n": 3, "p50": 20.0, "p95": 30.0, "p95_reliable": False, "max": 30.0}
    assert bench.latency_stats([])["n"] == 0


def test_summarize_shape(bench):
    rows = [{"label_verdict": "violation", "predicted": "violation", "label_timestamps_sec": [20.0],
             "timecodes_sec": [20.4], "upload_to_terminal_sec": 100.0, "split": "dev", "group": "g1",
             "contract_errors": []}]
    s = bench.summarize(rows)
    assert s["temporal"]["by_tolerance"] == {"0.5": 1, "1.0": 1, "2.0": 1, "5.0": 1}
    assert s["temporal"]["abs_error_at_5s"]["median"] == 0.4


def test_benchmark_blocked_without_manifest(bench, tmp_path):
    assert bench.main(["--manifest", str(tmp_path / "none.json")]) == 2


def test_manifest_loader_rejects_bad_entries(bench, tmp_path):
    p = tmp_path / "m.json"
    p.write_text(json.dumps([{"clip_id": "c1", "path": "x", "sha256": "0", "label_verdict": "maybe", "split": "dev"}]))
    with pytest.raises(ValueError):
        bench.load_manifest(p)


def test_build_manifest_on_synthetic_clips(tmp_path):
    from .harness import tiny_mov
    bm = _load("build_manifest", ROOT / "evaluation" / "manifest" / "build_manifest.py")
    clips = tmp_path / "clips"
    tiny_mov(clips / "secret-name-a.mov", seconds=2)
    tiny_mov(clips / "secret-name-b.mov", seconds=3, size=(160, 96))
    labels = tmp_path / "labels.csv"
    with labels.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["file", "label_verdict", "label_timestamps_sec", "group", "split"])
        w.writerow(["secret-name-a.mov", "violation", "1.5", "g1", "dev"])
        w.writerow(["secret-name-b.mov", "no_violation", "", "g2", "holdout"])
    out = tmp_path / "out" / "manifest.json"
    assert bm.main(["--clips-dir", str(clips), "--labels", str(labels), "--out", str(out)]) == 0
    data = json.loads(out.read_text())
    ids = [c["clip_id"] for c in data["clips"]]
    assert ids == ["c001", "c002"]
    assert all("secret-name" not in c["clip_id"] for c in data["clips"])
    assert all(c["duration_sec"] and c["duration_sec"] > 1 for c in data["clips"])
    assert (out.stat().st_mode & 0o777) == 0o600
    # A group may not span splits.
    with labels.open("a", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerow(["secret-name-b.mov", "no_violation", "", "g1", "val"])
    with pytest.raises(SystemExit):
        bm.build(clips, labels)
    # Refuses to write inside the repo outside the private dir.
    assert bm.main(["--clips-dir", str(clips), "--labels", str(labels),
                    "--out", str(ROOT / "evaluation" / "manifest" / "leak.json")]) == 2


def test_t28_settings_refuse_fake_in_production():
    from cashwatch.settings import Settings
    s = Settings(mode="production", backend="fake")
    assert any("fake" in e for e in s.production_errors())
    t = Settings(mode="test", backend="fake")
    assert not any("fake" in e for e in t.production_errors())


def test_submission_secret_scan_detects_planted_values(tmp_path, monkeypatch, capsys):
    sc = _load("qa_submission_check", ROOT / "scripts" / "qa_submission_check.py")
    monkeypatch.setattr(sc, "ROOT", tmp_path)
    fake_hf = "hf_" + "A1b2C3d4E5" * 4
    (tmp_path / "a.py").write_text(f'TOKEN = "{fake_hf}"\n')
    (tmp_path / "b.txt").write_text("JURY_API_KEY" + "=" + "Zq81kd93" + "hf74mzpq02lsx7c1vv93\n")  # built at runtime
    (tmp_path / "ok.md").write_text("CASHWATCH_API_KEY_FILE=/path/to/file\napi_key = example-placeholder-value-here\n")
    (tmp_path / ".env").write_text("X=1\n")
    c = sc.Checks()
    sc.scan_secrets(["a.py", "b.txt", "ok.md", ".env"], "t", c)
    status, _, note = c.rows[0]
    assert status == "FAIL"
    assert "a.py:1" in note and "b.txt:1" in note and ".env" in note and "ok.md" not in note
    assert fake_hf not in capsys.readouterr().out  # values are never printed
