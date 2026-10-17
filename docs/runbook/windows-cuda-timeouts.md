# Windows CUDA: generation timeouts on a 4 GiB GPU

The default evaluation profile is measured on Apple Silicon MLX. CUDA NF4 fits
quantized model weights on the RTX 3050 Laptop, but does not establish that its
multi-frame requests finish within the 60-second per-call cap.

The local dataset attempt repeatedly failed with `model_unavailable` and a
generation reply timeout. That interrupted run is not an accuracy measurement.
Its partial artifacts remain under `data/dev_eval/cuda-local-full-approved/`.

An optional experiment is available at
`deploy/runtime-profile.windows-cuda-4gb.json`. Shared configs, Python, dependency
versions, the global deadline and macOS defaults are unchanged. The experiment
limits observe/refine batches to 8 frames at 384 px, requests 128 neutral-output
tokens, disables optional motion proposals and uses at most 3 refined candidates.
All mandatory temporal windows still cover the clip, including its endpoints.
Long-video tiers sample more sparsely; this can miss short actions. Full interval
visibility requirements are not relaxed.

Synthetic checks on the local RTX 3050 Laptop, Qwen3-VL-2B CUDA NF4:

| Stage | Frames | Resolution | Output budget | Time | Result |
|---|---:|---:|---:|---:|---|
| Observe | 8 | 384 px | 128 | 40.922 s | Passed, repaired output |
| Verify | 8 | 384 px | 192 | 7.125 s | Passed |

One sample per stage; these are not banking accuracy results or a full-video
five-minute guarantee. The 50-second planning estimate is provisional.

Run the synthetic check without a competing worker:

```powershell
.tools\venv-cuda\Scripts\python.exe scripts\windows_cuda_bench.py
```

It stores synthetic frames and timings under `data/bench/windows-cuda-*/` and
returns a nonzero exit code for a failed model call. It uses a workspace folder
instead of an OS temporary directory, and closes each adapter in `finally`.

Local application experiment (still test mode and synthetic rules):

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File deploy\start_windows_cuda.ps1 -LowVram -Smoke
```

Dataset evaluation with bank rule candidates, after stopping the application:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File deploy\evaluate_windows_cuda.ps1 -Manifest evaluation\manifest\private\local-cuda-full\manifest.json -ExpectedClips 40 -RunName cuda-local-full-lowvram-v1 -Profile deploy\runtime-profile.windows-cuda-4gb.json
```

Use a new run name on repeat attempts. This local manifest has 40 unique clips
from 42 files after exact duplicate removal; it is not an independent holdout.
No timestamp accuracy is measured with this classification-only manifest.
Read completion/failure counts before interpreting completed-only accuracy.
Do not run evaluation and the application concurrently on this GPU.
