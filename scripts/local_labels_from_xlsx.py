"""LOCAL-ONLY: convert the organizer label workbook into the labels CSV expected by
evaluation/manifest/build_manifest.py (file,label_verdict,label_timestamps_sec,group,split).

Run by a team member; do not paste its output into a cloud assistant. Stdlib only (no openpyxl).

Step 1 — see the columns:   .venv/bin/python scripts/local_labels_from_xlsx.py --xlsx data/dataset/open/open_labels.xlsx --show-header
Step 2 — convert:           ... --file-col "<name>" --label-col "<name>" --time-col "<name>" \
                                --out evaluation/manifest/private/labels.csv
Positive label values default to: 1, yes, true, да, есть, violation, нарушение (case-insensitive).
Times accept seconds ("83.5"), "m:ss", "h:mm:ss", several separated by ; , or whitespace.
group and split are left EMPTY on purpose: fill them by hand (related takes share a group).
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
POS = {"1", "yes", "true", "да", "есть", "violation", "нарушение", "y", "+"}


def _col(ref: str) -> int:
    n = 0
    for ch in re.match(r"[A-Z]+", ref).group(0):
        n = n * 26 + ord(ch) - 64
    return n - 1


def read_rows(path: Path, sheet: int) -> list[list[str]]:
    with zipfile.ZipFile(path) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", NS):
                shared.append("".join(t.text or "" for t in si.iter(f"{{{NS['m']}}}t")))
        root = ET.fromstring(z.read(f"xl/worksheets/sheet{sheet}.xml"))
    rows = []
    for r in root.iter(f"{{{NS['m']}}}row"):
        row: list[str] = []
        for c in r.findall("m:c", NS):
            i = _col(c.get("r", "A1"))
            while len(row) <= i:
                row.append("")
            v = c.find("m:v", NS)
            t = c.get("t")
            if t == "s" and v is not None:
                row[i] = shared[int(v.text)]
            elif t == "inlineStr":
                row[i] = "".join(x.text or "" for x in c.iter(f"{{{NS['m']}}}t"))
            elif v is not None:
                row[i] = v.text or ""
        rows.append(row)
    return rows


def parse_times(s: str) -> list[float]:
    out = []
    for tok in re.split(r"[;,\s]+", s.strip()):
        if not tok:
            continue
        parts = tok.split(":")
        try:
            vals = [float(p) for p in parts]
        except ValueError:
            raise ValueError(f"unparseable time token in label workbook (row value hidden)") from None
        sec = 0.0
        for v in vals:
            sec = sec * 60 + v
        out.append(round(sec, 3))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--xlsx", required=True, type=Path)
    ap.add_argument("--sheet", type=int, default=1)
    ap.add_argument("--show-header", action="store_true")
    ap.add_argument("--file-col")
    ap.add_argument("--label-col")
    ap.add_argument("--time-col")
    ap.add_argument("--positive-values", default=",".join(sorted(POS)))
    ap.add_argument("--file-suffix", default="", help="append to file values lacking an extension, e.g. .MOV")
    ap.add_argument("--time-unit", choices=["sec", "excel-day"], default="sec",
                    help="excel-day: numeric cells are Excel time fractions of a day (00:01:05 -> 0.000752)")
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    rows = [r for r in read_rows(a.xlsx, a.sheet) if any(x.strip() for x in r)]
    header = [h.strip() for h in rows[0]]
    if a.show_header:
        print("columns:", header)
        print("data rows:", len(rows) - 1)
        return 0
    if not (a.file_col and a.label_col and a.out):
        ap.error("--file-col, --label-col and --out are required (use --show-header first)")
    idx = {h: i for i, h in enumerate(header)}
    pos = {x.strip().lower() for x in a.positive_values.split(",")}
    a.out.parent.mkdir(parents=True, exist_ok=True)
    n_pos = 0
    with open(a.out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["file", "label_verdict", "label_timestamps_sec", "group", "split"])
        for r in rows[1:]:
            get = lambda c: (r[idx[c]] if c and idx.get(c, 10**9) < len(r) else "").strip()  # noqa: E731
            f = get(a.file_col)
            if a.file_suffix and "." not in Path(f).name:
                f += a.file_suffix
            verdict = "violation" if get(a.label_col).lower() in pos else "no_violation"
            n_pos += verdict == "violation"
            times = parse_times(get(a.time_col)) if a.time_col else []
            if a.time_unit == "excel-day" and ":" not in get(a.time_col):
                times = [round(t * 86400, 3) for t in times]
            w.writerow([f, verdict, ";".join(str(t) for t in times), "", ""])
    print(f"wrote {a.out} ({len(rows) - 1} rows, {n_pos} positive). Fill group/split columns by hand.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
