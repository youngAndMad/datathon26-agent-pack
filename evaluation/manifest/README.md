# Evaluation manifest (tooling only — no data committed)

Status: **BLOCKED** — no authorized dataset path and no local reviewer yet (plan.md X1/X2, D004).
Cloud-hosted agents must not run these tools on real footage or read their output.

`build_manifest.py` turns authorized local clips plus the organizer's labels into
`evaluation/manifest/private/manifest.json` (git-ignored, mode 0600):

```bash
.venv/bin/python evaluation/manifest/build_manifest.py \
  --clips-dir /private/path/to/authorized_clips --labels /private/path/labels.csv
```

Each entry: opaque `clip_id` (c001… by SHA-256 order; no filenames), absolute local `path`,
`sha256`, `duration_sec` (ffprobe), `label_verdict`, `label_timestamps_sec` (organizer
timestamps; a single timestamp is not an interval), optional `label_types` (only when locally
reviewed), `group` (related takes / same scene / augmentation descendants share one group),
`split` (`dev`/`val`/`holdout`; a group may not span splits — enforced).

Split guidance (spec §7): for ~40 clips prefer grouped cross-validation or a grouped dev/val
split plus a small locked holdout if class counts permit. Freeze prompts/thresholds before
running the holdout. Never tune on jury/test uploads.

Consumers: `scripts/qa_benchmark.py --manifest …` (see evaluation/reports/README.md).
