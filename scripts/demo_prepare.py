"""Prepare completed jobs before a live demo (plan §17): upload clips now, so the demo opens a
finished result instantly from «История проверок» instead of waiting for processing on stage.

Usage: .venv/bin/python scripts/demo_prepare.py --base-url http://127.0.0.1:8000 \
         --key-file secrets/jury_api_key FILE.mov [FILE2.mov ...]
Writes data/demo_jobs.json (git-ignored): [{"label": "<file name>", "job_id": ..., "status": ...}].
Use only clips you are allowed to show to the audience.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--key-file", required=True)
    ap.add_argument("files", nargs="+", type=Path)
    a = ap.parse_args()
    out = []
    for f in a.files:
        with tempfile.NamedTemporaryFile(suffix=".json") as tmp:
            subprocess.run([sys.executable, str(ROOT / "scripts" / "qa_jury_client.py"), "--base-url", a.base_url,
                            "--key-file", a.key_file, "--file", str(f), "--timeout", "320", "--json-out", tmp.name],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            d = json.loads(Path(tmp.name).read_text() or "{}")
        out.append({"label": f.name, "job_id": d.get("job_id"), "status": d.get("terminal_status"),
                    "upload_to_terminal_sec": d.get("upload_to_terminal_sec")})
        print(f"{f.name}: {d.get('terminal_status')} job={d.get('job_id')}")
    p = ROOT / "data" / "demo_jobs.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"wrote {p.relative_to(ROOT)}; open them in the UI under «История проверок»")
    return 0 if all(o["status"] == "done" for o in out) else 1


if __name__ == "__main__":
    sys.exit(main())
