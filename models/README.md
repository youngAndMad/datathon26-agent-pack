# Local model weights (not in git)

Weights are pre-staged during preparation and never downloaded at runtime. Provenance, file
sizes and sha256 hashes: `models/manifest.proposed.json` (agent C proposal; the orchestrator
promotes it to `models/manifest.json`). Regenerate with `.venv/bin/python scripts/model_manifest.py`,
verify fully with `.venv/bin/python scripts/model_verify.py`.

| Folder | Repo | Revision | Use | License |
|---|---|---|---|---|
| `qwen3-vl-4b-instruct-mlx-8bit/` | `mlx-community/Qwen3-VL-4B-Instruct-8bit` | `0943db6e15185b86be368d3cf0704aec740b142b` | primary (MLX, Apple Silicon) | Apache-2.0 (model card) |
| `qwen3-vl-4b-instruct/` | `Qwen/Qwen3-VL-4B-Instruct` | `ebb281ec70b05090aa6165b016eac8ec08e71b17` | reference (Transformers bf16) | Apache-2.0 (model card) |

Notes

* The 8-bit MLX weights are a third-party conversion (mlx-community, mlx-vlm 0.3.4, affine 8-bit,
  group size 64) of `Qwen/Qwen3-VL-4B-Instruct`; the converter did not record the base revision.
  Numerical parity with the bf16 reference has not been established; treat it as a separate model.
* Neither repo snapshot ships a separate LICENSE file; the license is taken from the model card
  front matter (`license: apache-2.0`).
* Revisions were cross-checked against the hub client's download metadata in each folder's
  `.cache/huggingface/download/` (commit hash; LFS etag == sha256).
* No `*.py` files are present; the loader refuses folders containing Python files
  (`trust_remote_code` stays off).

Runtime (see `src/cashwatch/inference/`): the model is loaded once in a supervised child process
with `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`, `HF_HUB_DISABLE_TELEMETRY=1`, local paths only.
