# Third-party licenses (inventory skeleton)

Verified on 2026-10-17 from installed package metadata in `.venv` (`importlib.metadata`
License-Expression / License / classifiers), model-card front matter in `models/*/README.md`,
`ffmpeg -version` and PyAV's `av._core.library_meta`. "Not verified" means the metadata did not
state it; check the upstream LICENSE file before redistribution. This is an engineering inventory,
not legal advice.

## Models (weights are not redistributed in the repository)

| Component | Revision | License | Evidence |
|---|---|---|---|
| Qwen/Qwen3-VL-4B-Instruct | ebb281ec70b05090aa6165b016eac8ec08e71b17 | Apache-2.0 | model card front matter `license: apache-2.0`; no LICENSE file in the snapshot |
| mlx-community/Qwen3-VL-4B-Instruct-8bit (third-party 8-bit conversion, mlx-vlm 0.3.4) | 0943db6e15185b86be368d3cf0704aec740b142b | Apache-2.0 (as declared by the converter) | model card front matter; base revision used for conversion not recorded |

Apache-2.0 requires shipping the license text and NOTICE (if any) with redistributed weights;
add the Apache-2.0 text if the offline bundle includes `models/`.

## Media

| Component | Version | License | Evidence / note |
|---|---|---|---|
| FFmpeg / ffprobe (Homebrew, used by the media layer and tests) | 8.0.1 | **GPL-3.0-or-later** for this build | `ffmpeg -version`: `--enable-gpl --enable-version3` (+ libx264/libx265 etc.). Not bundled; installed on the host via the OS package manager. Redistributing this binary carries GPLv3 obligations. |
| PyAV `av` | 19.0.1 | BSD-3-Clause (PyAV code) | package metadata |
| FFmpeg libraries bundled in the PyAV wheel | 8.0 (libavcodec 63.1.102) | LGPL-3.0-or-later (per `library_meta`) | build config `--enable-version3`, no `--enable-gpl`; **but the wheel also bundles libx264 and libx265 dylibs, which are GPL-2.0-or-later upstream** — review before redistributing the wheel |

## Python runtime dependencies (from `.venv`)

| Package | Version | License (metadata) |
|---|---|---|
| fastapi | 0.143.3 | MIT |
| starlette | 1.8.0 | BSD-3-Clause |
| uvicorn | 0.54.0 | BSD-3-Clause |
| pydantic / pydantic-core / pydantic-settings | 2.14.1 / 2.50.1 / 2.16.0 | MIT |
| python-multipart | 0.0.32 | Apache-2.0 |
| numpy | 2.5.4 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |
| pillow | 13.0.0 | MIT-CMU |
| PyYAML | 6.0.3 | MIT |
| jsonschema / referencing | 4.26.0 / 0.37.0 | MIT |
| mlx / mlx-metal | 0.32.4 | MIT |
| mlx-vlm | 0.7.8 | MIT |
| transformers | 5.20.1 | Apache-2.0 |
| tokenizers | 0.23.3 | Apache-2.0 (classifier) |
| safetensors | 0.8.0 | Apache-2.0 (classifier) |
| huggingface_hub | 2.2.0 | Apache-2.0 |
| torch / torchvision (optional `torch` extra) | 2.14.1 / 0.29.1 | BSD-style (metadata: Apache-2.0 AND … ; BSD) |
| accelerate (optional `torch` extra) | 1.15.0 | Apache-2.0 |
| opencv-python (transitive via mlx-vlm) | 5.0.0.93 | Apache-2.0 (bundled FFmpeg/Qt libs: not verified) |
| datasets (transitive) | 5.1.0 | Apache-2.0 |
| mlx-audio, miniaudio, sounddevice (transitive via mlx-vlm; **not used** — no audio processing) | 0.5.9 / 1.71 / 0.5.6 | MIT |
| certifi | 2026.7.22 | MPL-2.0 |
| tqdm | 4.70.1 | MPL-2.0 AND MIT |

Dev/test only: pytest 9.1.1 (MIT), httpx 0.28.1 (BSD-3-Clause). The full transitive list with
versions is `dist/offline-bundle/requirements.lock.txt`; regenerate this table from `.venv` with
the snippet in coordination/handoffs/F.md before submission.

## Frontend (bundled into `frontend/dist`)

| Package | Version | License |
|---|---|---|
| react | 19.3.1 | MIT |
| react-dom | 19.3.1 | MIT |

Build-time only (not shipped): vite, typescript, vitest, @vitejs/plugin-react, jsdom,
@testing-library/* — licenses not individually verified (see `frontend/package-lock.json`).

## Legal sources

Official texts of the Republic of Kazakhstan from adilet.zan.kz stored in `rules/sources/`
(agent D) — official normative acts; reuse terms not verified here.
