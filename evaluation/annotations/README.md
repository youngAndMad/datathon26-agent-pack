# Local annotation protocol (template only)

This directory holds only the empty template and this protocol. Real annotations derived from
competition footage are restricted. They go under `evaluation/annotations/private/`, which is
git-ignored and must never be shown to a cloud-hosted agent (AGENTS.md rule 3, decision D004).

Status: BLOCKED for cloud agents (tasks D02/D04). A local human or local model has to do this step.

Procedure (spec §7, RULES_CATALOG activation gates):

1. Assign each clip an opaque `clip_id`. Record its sha256, duration, recording/scenario/angle
   `group_id` and split. Related takes and augmentations share a group and a split.
2. Copy the organizer labels unchanged into `official` (presence and timestamp only). Never mix
   them with your own labels.
3. First record neutral `observed_actions` (no rule IDs, no legal words). Then link each action
   to a candidate rule (`CW01`–`CW09`) or to `unmapped`.
4. For each linked event, record start/end, the anchor per the rule's `anchor_convention` in
   `rules/registry.yaml`, the best evidence times, visible roles (scene interaction only, no
   identities), occlusion, transaction boundaries, eligibility and label uncertainty.
5. Count positives, negatives (including compliant counterexamples) and unknowns for each rule.
   These counts go into `approval.validation_counts` when a local owner approves a rule.
   Do not tune on the holdout or on jury uploads.
