# Local dataset review, rule approval and evaluation (team members only)

These steps need a **person** looking at the organizer footage on the local machine. Cloud AI
(Claude, ChatGPT, Copilot chat…) must never see the videos, frames, labels, model outputs on them,
or your notes — that is the competition locality rule (spec §12, R10). Everything below writes to
git-ignored folders; do not commit or paste any of it. Share only aggregate counts and metrics.

Prerequisite: the setup in `docs/LOCAL_SETUP.md` is done, the dataset is extracted to
`data/dataset/open/` (42 MOV + `open_labels.xlsx`), and no other model job is running.

## 1. Organizer labels → labels CSV (≈5 min)

```bash
.venv/bin/python scripts/local_labels_from_xlsx.py --xlsx data/dataset/open/open_labels.xlsx --show-header
# then, with the real column names:
.venv/bin/python scripts/local_labels_from_xlsx.py --xlsx data/dataset/open/open_labels.xlsx \
  --file-col "<video column>" --label-col "<violation column>" --time-col "<timestamp column>" \
  --out evaluation/manifest/private/labels.csv          # add --time-unit excel-day if times are Excel times
```

Open `labels.csv` and fill **group** and **split** by hand:
- `group`: takes of the same staged scene / same actors+background / augmentations share one group.
- `split`: whole groups only. Suggested: ~60 % `dev` (tune prompts/rules here), ~25 % `val`,
  ~15 % `holdout` (locked: never look at model results on it until the final run). If a violation
  type would be absent from a split, note it — don't force a numerically perfect split.

Build the private manifest (checks file hashes, durations, group/split consistency):

```bash
.venv/bin/python evaluation/manifest/build_manifest.py --labels evaluation/manifest/private/labels.csv \
  --clips-dir data/dataset/open/videos
```

## 2. Local-model proposals + review pages (≈1 h compute for 42 clips, unattended)

```bash
make stop                                              # free the accelerator
.venv/bin/python scripts/local_annotation_assist.py --dataset data/dataset
open evaluation/annotations/private/index.html         # Safari or Chrome, works offline
```

Each clip page shows the video, the local model's **proposals** (neutral observations and which
draft-rule predicates it thinks hold, with clickable times) and an annotation form. Proposals are
hints — they are often wrong; verify every fact in the video.

## 3. Annotate (the real work; ≈5–10 min per clip)

Per clip, in the review page:
1. Fill annotator, group, split (same as labels.csv).
2. **Observed actions first**: neutral visible facts (who-role does what with which object, start/end,
   visibility). No identities, intent, authorization or legal words.
3. **Events**: link actions to a rule (CW01…CW09) or `unmapped`; set start/end/anchor per the rule's
   anchor convention (decisive first frame), 1–3 evidence times (anchor first), tick *eligible* only
   when the rule's applicability and visibility conditions really hold; tick *counterexample* for
   compliant look-alikes (they are just as valuable).
4. Keep organizer labels separate: the form records **your** annotation; organizer presence/time
   stays in labels.csv. Where you disagree with the organizer label, note it in `label_uncertainty`.
5. *Export annotation JSON* and move the file into `evaluation/annotations/private/annotations/`.

Then:

```bash
.venv/bin/python scripts/local_rule_validation_report.py     # per-rule counts only
```

## 4. Approve a rule (only if the evidence supports it)

Priority candidates: **CW01** (cashier leaves during cash receipt), **CW02** (previous client's cash
left on the work surface), **CW04** (staff personal items in the cash unit). Approve fewer rules
well rather than many speculatively. Open questions to settle first are in
`coordination/handoffs/D.md` (e.g. is the filmed teller position a «кассовый узел» under п.5?).

In `rules/registry.yaml`, for the rule, only after review:
- check `predicates`, `counterexamples_en`, `temporal`, `anchor_convention` match what you saw in
  `dev` clips; adjust predicate wording (neutral, visual) if needed;
- `source_status: {clause_text: verified, edition: verified, applicability: verified, local_visual_validation: done}`
  (the official snapshot is already captured and hash-checked; applicability is your judgement);
- `approval: {owner: <name>, date: <YYYY-MM-DD>, validation_counts: <from step 3 report>, notes: <limits>}`
  — needs ≥1 positive and ≥1 negative;
- `state: approved`.

```bash
make doctor        # "rule registry ... approved=N" should be OK; any gate failure is printed
make test          # registry lint + rule tests
```

Commit **only** `rules/registry.yaml` changes (no clip names, no scene descriptions in notes).

## 5. Evaluate (aggregate numbers are shareable)

```bash
make start                                             # production mode: now ready
.venv/bin/python scripts/qa_benchmark.py --split dev   # tune on dev only
.venv/bin/python scripts/qa_benchmark.py --split val
# final, once, after freezing prompts/rules/profile:
.venv/bin/python scripts/qa_benchmark.py --split holdout
```

Reports go to `evaluation/reports/private/` (git-ignored). The printed summary — confusion matrix,
precision/recall/F1, false-positive rate, failed-as-failure counts, timestamp match rates at
0.5/1/2/5 s, latency p50/p95/max with n — is aggregate and can be shared with the team or pasted to a
coding assistant. Never paste per-clip rows, descriptions or images.

## 6. Iterate

Typical loop on `dev`: look at misses/false positives locally → adjust the rule's predicate wording,
counterexamples or temporal gate → `make test` → re-run benchmark on `dev`. Don't tune on `holdout`,
and never on jury uploads.
