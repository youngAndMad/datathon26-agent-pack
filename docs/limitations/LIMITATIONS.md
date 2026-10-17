# Known limitations

Status at this writing: no competition footage has been analysed by any agent, no accuracy has
been measured, and the production rule registry contains **zero approved rules**. Everything below
is either a design limitation or a measurement that is still missing; none of it is hidden by the
API, which only reports `violation` / `no_violation` / `failed`.

## Detection scope

* **Zero approved rules until local validation.** A rule becomes `approved` only with an official
  adilet.zan.kz snapshot + hash, verified edition, predicate tests and local visual validation on
  authorized clips (D011). That validation is blocked (no dataset, cloud-hosted agents may not view
  footage: X1/X2, D004). Until then production readiness is intentionally refused rather than
  returning an unfounded clean result.
* **Unsupported/unknown types.** The system can only report events mapped to an approved rule.
  New behaviour seen by the model becomes a private review note, never a violation; its reliability
  for unseen event types is unknown.
* **Visually unverifiable facts.** Transaction amounts, authorization/permissions, job roles,
  identities, intent, whether a drawer is locked, whether paper is a banknote, whether a counting
  step happened off-camera — not asserted without visible, sufficient support. Omissions are
  reported only when the full opportunity interval is visible.
* **No audio, faces or identity.** Audio is ignored; no face recognition, embeddings or
  cross-clip tracking. Actor roles are anonymous and within-clip only.

## Visual conditions

* **Camera angle.** Only the organizer's (unseen) angle family would be represented in
  validation; new angles, mirrored layouts and rotations are untested. Flip/warp augmentations are
  stress tests, not proof of a new viewpoint.
* **Occlusion.** Hands, bodies, monitors or the frame edge can hide the decisive moment; the
  result then depends on what remains visible and is disclosed as a limitation in the UI report.
* **Small objects.** Banknotes, stamps and small items at 640 px model resolution (coarse pass)
  may be illegible; evidence frames use up to 1280 px but the model saw the thumbnail.
* **Lighting, motion blur, compression** degrade recognition unpredictably.

## Temporal sampling

* The coarse pass samples about **1 frame per second** in 12 s windows (3 s overlap). Sub-second
  actions can fall between samples and be missed entirely. A 4 fps motion signal proposes extra
  intervals but motion is only a hint; refinement (±3 s at 4 fps) runs only on candidates.
* Timestamps are the actual presentation time of the selected source frame (3-decimal precision),
  so an anchor can lag the true onset by up to one sampling interval.
* Three evidence frames cannot prove continuous absence of an action.

## Model and runtime

* **MLX 8-bit conversion.** The primary profile uses `mlx-community/Qwen3-VL-4B-Instruct-8bit`, a
  third-party 8-bit conversion (mlx-vlm 0.3.4) of `Qwen/Qwen3-VL-4B-Instruct`; the exact base
  revision used for conversion is not recorded, and output parity with the bf16 reference is not
  established.
* Model self-reported confidence is not a calibrated probability and is not exposed.
* **Apple Silicon only measured host.** All timings (when measured) come from one M4 Pro / 48 GB
  machine. CUDA/Linux deployment is untested; CPU-only is not a valid five-minute target.
* One active analysis per accelerator; a second concurrent upload receives 429 with Retry-After.
  Concurrency of jury traffic is unknown (X4).
* Latency p95 is only meaningful with ≥ 20 runs; current numbers (when present) state the sample
  count and maximum.

## Deployment

* Public HTTPS is not yet set up (owner decision on host/DNS, X3). A laptop host needs sleep,
  power and network-change prevention (runbook §5).
* Denied-egress verification uses macOS `sandbox-exec`; see runbook §7 for what it does not prove.
* File deletion on SSD is not secure erasure; rely on FileVault.
