#!/usr/bin/env python3
"""Real local inference smoke test via HTTP against the RUNNING service (`make smoke`, spec §14).

1. Determines what the service runs: /readyz JSON if it exposes model info, otherwise the local
   worker status row in <data_dir>/db/cashwatch.sqlite3 (read-only). Refuses if the adapter is the
   fake/mock one or if this cannot be determined (never assumes "real").
2. Refuses unless mode == production, except with --allow-test-mode (output then says TEST MODE).
3. Uploads one synthetic, non-sensitive clip with the black-box jury client, validates every
   response (schema + §5.3 semantics) and checks the UI report's mode label agrees.

The service process owns the model; this script does not take the GPU reservation (the worker
serializes jobs itself). Exit: 0 pass, 1 validation failure, 2 refused/blocked, 3 timeout,
4 HTTP error, 5 usage.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from qa_jury_client import EXIT_USAGE, read_key, run_job  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CLIPS = ("marker_violation_40s.mov", "cfr_10s_30.mov")


def _find(d: object, key: str):
    if isinstance(d, dict):
        if key in d:
            return d[key]
        for v in d.values():
            r = _find(v, key)
            if r is not None:
                return r
    return None


def service_identity(base_url: str, *, data_dir: Path | None = None) -> dict:
    """{mode, adapter, is_mock, model, revision, registry, source}; unknown values are None."""
    import httpx

    ident: dict = {"mode": None, "adapter": None, "is_mock": None, "model": None, "revision": None,
                   "registry": None, "source": None, "ready": False}
    try:
        r = httpx.get(base_url.rstrip("/") + "/readyz", timeout=5.0, trust_env=False)
        ident["ready"] = r.status_code == 200
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if _find(body, "is_mock") is not None or _find(body, "adapter") is not None:
            for k in ("mode", "adapter", "is_mock", "model", "revision", "registry"):
                ident[k] = _find(body, k)
            ident["source"] = "readyz"
            return ident
    except (httpx.HTTPError, ValueError):
        pass
    dd = data_dir or Path(os.environ.get("CASHWATCH_DATA_DIR", ROOT / "data"))
    db = dd / "db" / "cashwatch.sqlite3"
    if db.is_file():
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2.0)
            try:
                row = con.execute("SELECT info FROM worker_status WHERE ready=1 "
                                  "ORDER BY heartbeat_at DESC LIMIT 1").fetchone()
            finally:
                con.close()
            if row:
                info = json.loads(row[0])
                for k in ("mode", "adapter", "is_mock", "model", "revision", "registry"):
                    ident[k] = info.get(k)
                ident["source"] = "local_worker_status"
        except (sqlite3.Error, ValueError):
            pass
    return ident


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Real-inference smoke against the running service")
    ap.add_argument("--base-url", default=os.environ.get("CASHWATCH_BASE_URL", "http://127.0.0.1:8000"))
    ap.add_argument("--key-file", type=Path,
                    default=Path(os.environ.get("CASHWATCH_API_KEY_FILE", ROOT / "secrets" / "jury_api_key")))
    ap.add_argument("--data-dir", type=Path, default=None)
    ap.add_argument("--file", type=Path, default=None, help="synthetic clip (default: generated fixture)")
    ap.add_argument("--timeout", type=float, default=300.0)
    ap.add_argument("--allow-test-mode", action="store_true")
    a = ap.parse_args(argv)

    clip = a.file
    if clip is None:
        for name in DEFAULT_CLIPS:
            p = ROOT / "tests" / "fixtures" / "generated" / name
            if p.is_file():
                clip = p
                break
    if clip is None or not clip.is_file():
        print("usage error: no synthetic clip; run `make fixtures` or pass --file", file=sys.stderr)
        return EXIT_USAGE
    try:
        key = read_key(a.key_file)
    except (OSError, ValueError):
        print("usage error: cannot read key file", file=sys.stderr)
        return EXIT_USAGE

    ident = service_identity(a.base_url, data_dir=a.data_dir)
    print(json.dumps({"service": {k: ident[k] for k in ("ready", "mode", "adapter", "is_mock", "model", "source")}}))
    if not ident["ready"]:
        print("REFUSED: service /readyz is not 200")
        return 2
    if ident["is_mock"] is True or ident["adapter"] == "fake":
        print("REFUSED: mock/fake adapter is active; a smoke must exercise real local inference")
        return 2
    if ident["is_mock"] is None:
        print("BLOCKED: cannot determine the model adapter (expose model info in /readyz or pass --data-dir)")
        return 2
    if ident["mode"] != "production" and not a.allow_test_mode:
        print(f"REFUSED: service mode is {ident['mode']!r}; use --allow-test-mode for a labeled test smoke")
        return 2

    res = run_job(a.base_url, key, clip, timeout_sec=a.timeout)
    out = res.to_dict()
    # Cross-check the UI report labels the run mode honestly.
    if res.job_id:
        import httpx
        try:
            rep = httpx.get(f"{a.base_url.rstrip('/')}/api/v1/jobs/{res.job_id}/report",
                            headers={"X-API-Key": key}, timeout=10.0, trust_env=False)
            if rep.status_code == 200:
                rmode = rep.json().get("mode")
                out["report_mode"] = rmode
                if rmode != ident["mode"]:
                    res.errors.append(f"UI report mode {rmode!r} disagrees with service mode {ident['mode']!r}")
                    res.exit_code = 1
        except (httpx.HTTPError, ValueError):
            out["report_mode"] = None
    if res.terminal_status == "failed":
        res.exit_code = 1  # a processing failure fails the smoke (never treated as clean)
    out["errors"] = res.errors
    label = "TEST MODE smoke (not a release check)" if ident["mode"] != "production" else "production smoke"
    print(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"{label}: {'PASS' if res.exit_code == 0 else 'FAIL'} exit={res.exit_code} "
          f"upload_to_terminal={res.upload_to_terminal_sec}s")
    return res.exit_code


if __name__ == "__main__":
    sys.exit(main())
