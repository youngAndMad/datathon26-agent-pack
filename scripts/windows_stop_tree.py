"""Stop only an identity-verified Windows launch tree; never hunt orphan processes."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import psutil

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from cashwatch.inference.windows_process import stop_tree


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record", required=True, type=Path)
    args = parser.parse_args()
    try:
        saved = json.loads(args.record.read_text(encoding="utf-8-sig"))
        pid, ticks = int(saved["id"]), int(saved["start_ticks"])
        if pid <= 0 or ticks <= 0:
            raise ValueError("Invalid launch identity")
        count = stop_tree(pid, ticks)
    except (OSError, psutil.Error, RuntimeError, ValueError, KeyError) as exc:
        print(f"Tree cleanup failed: {exc}", file=sys.stderr)
        return 1
    print(f"Stopped {count} identity-verified processes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
