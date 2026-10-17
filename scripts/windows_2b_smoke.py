"""Local synthetic-only 2B API test with sampled system-memory measurements."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import threading
from urllib.request import build_opener, ProxyHandler

import psutil
from qa_jury_client import main as jury_main

ROOT = Path(__file__).resolve().parents[1]


def main():
    client = build_opener(ProxyHandler({}))
    with client.open("http://127.0.0.1:8000/readyz", timeout=5) as response:
        ready = json.load(response)
    if ready.get("model") != "Qwen/Qwen3-VL-2B-Instruct" or ready.get("mode") != "test" or ready.get("is_mock") is not False:
        raise RuntimeError("Expected ready real 2B model in explicit test mode")
    dest = ROOT / "data/windows-local"
    dest.mkdir(parents=True, exist_ok=True)
    clip = dest / "synthetic-2b-smoke.mov"
    ffmpeg = ROOT / ".tools/ffmpeg/bin/ffmpeg.exe"
    subprocess.run([str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30",
                    "-t", "1", "-an", "-c:v", "mpeg4", str(clip)],
                   check=True, timeout=30)
    stopped = threading.Event()
    baseline = psutil.virtual_memory()
    measurement = {"system_total_gib": round(baseline.total / 2**30, 3),
                   "system_available_before_gib": round(baseline.available / 2**30, 3),
                   "system_min_available_gib": round(baseline.available / 2**30, 3),
                   "samples": 0,
                   "scope": "system RAM during synthetic inference; includes all applications, excludes model startup"}

    def sample():
        while not stopped.is_set():
            available = psutil.virtual_memory().available / 2**30
            measurement["system_min_available_gib"] = min(measurement["system_min_available_gib"], round(available, 3))
            measurement["samples"] += 1
            stopped.wait(0.5)

    monitor = threading.Thread(target=sample, daemon=True)
    monitor.start()
    try:
        code = jury_main(["--base-url", "http://127.0.0.1:8000",
                          "--key-file", str(ROOT / "secrets/jury_api_key"),
                          "--file", str(clip), "--no-require-official-source",
                          "--timeout", "300", "--json-out", str(dest / "2b-smoke-summary.json")])
    finally:
        stopped.set()
        monitor.join(timeout=2)
        (dest / "2b-smoke-memory.json").write_text(json.dumps(measurement, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(measurement))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
