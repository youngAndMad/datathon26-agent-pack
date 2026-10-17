# Research sources and provenance

Checked during preparation on 17 October 2026. URLs are research references, not runtime network dependencies. Freeze actual files and revisions in the implementation. No video or dataset was inspected, and no inference benchmark was run for this specification.

## Models and runtime

| ID | Primary source | Used for |
|---|---|---|
| S01 | https://huggingface.co/Qwen/Qwen3-VL-4B-Instruct | Candidate model identity and Apache-2.0 model-card license |
| S02 | https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct | Optional larger candidate and model-card license |
| S03 | https://huggingface.co/docs/transformers/main/en/installation#offline-mode | Pre-download, offline setting and local-only loading |
| S04 | https://github.com/Blaizzy/mlx-vlm | Optional native Apple Silicon adapter; verify exact model compatibility |
| S05 | https://pyav.org/docs/stable/api/time.html | Presentation timestamps and rational time bases |
| S06 | https://github.com/QwenLM/Qwen3-VL | Model processing/video controls; pin actual tested implementation |
| S07 | https://huggingface.co/docs/huggingface_hub/package_reference/environment_variables | Offline and telemetry-related configuration |
| S08 | https://fastapi.tiangolo.com/tutorial/background-tasks/ | Heavy-work caveat; rationale for a separate durable worker |
| S09 | https://www.starlette.io/responses/ | Local file responses and range handling |
| S10 | https://sqlite.org/wal.html | WAL behavior; local single-machine database design |
| S11 | https://sqlite.org/lang_transaction.html | Transaction boundaries and writer constraints |

Model cards establish licensing and general capabilities, not cashier accuracy, five-minute throughput, sufficient VRAM for this exact pipeline or regulatory correctness. Hardware and sampling choices in the specification are engineering proposals to benchmark. Check license files for exact weights, quantized derivatives, model utilities, FFmpeg build and every redistributed component.

## Kazakhstan legal sources

| ID | Primary source | Role and retrieval limitation |
|---|---|---|
| L01 | https://adilet.zan.kz/rus/docs/V1900019680 | Core НБРК №231, 29.11.2019; official indexed clauses available, full consolidated retrieval inconsistent |
| L01m | https://adilet.zan.kz/rus/docs/V1900019680/info | Metadata; indexed research reported update 12.07.2026; recapture with official snapshot |
| L01h | https://adilet.zan.kz/rus/docs/V1900019680/history | Amendment history to verify before freezing edition |
| L02 | https://adilet.zan.kz/rus/docs/V2500037429 | НБРК №71, 10.11.2025; amendments including paragraphs 12 and 19 |
| L02a | https://old.adilet.zan.kz/rus/docs/V2500037429 | Legacy official text retrieved by research worker; later retries timed out |
| L03 | https://adilet.zan.kz/rus/docs/V2200027073 | НБРК №19, 28.02.2022; relevant counting/digital-document amendments |
| L04 | https://adilet.zan.kz/rus/docs/V2600038217 | НБРК №25, 16.03.2026; operative 2026 amendments need full capture |
| L05 | https://adilet.zan.kz/rus/docs/V2000020075 | НБРК №14, 24.02.2020; premises security research lead only |

A secondary text at https://zakon.uchet.kz/rus/docs/V1900019680 helped locate candidate clauses. It is not the final normative authority for enabled rules. The similarly named adilet.kz is not the official adilet.zan.kz source. The seed catalogue clearly separates candidate mapping from activation requirements.

The supplied user task and FAQ are the authoritative source for competition format, locality restrictions, no-face/no-audio requirements, jury JSON, deadline envelope and scoring. No external organizer page or dataset was supplied.
