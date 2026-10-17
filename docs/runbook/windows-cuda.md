# Experimental Windows CUDA profile

This profile tries Qwen3-VL 2B with 4-bit bitsandbytes quantization on the NVIDIA
RTX 3050 Laptop (4 GiB VRAM). Fitting the complete video workload into 4 GiB,
latency, accuracy and peak memory have not yet been measured. A successful
installation or synthetic smoke check is not a real-data quality evaluation.

## Preparation and isolation

The existing `.venv-windows` remains the CPU fallback. Preparation creates
`.tools/venv-cuda` with the same Python 3.12 interpreter and snapshots the existing
installed dependency versions. Only these packages are replaced/added:

- `torch==2.14.1+cu126` and `torchvision==0.29.1+cu126`, from the official PyTorch CUDA 12.6 index;
- `bitsandbytes==0.50.2`, from PyPI.

The pinned snapshot and CUDA constraints are saved inside the new environment.
Other installed dependency versions remain pinned; a dependency conflict fails
preparation instead of silently upgrading them. Project code is linked locally
through a `.pth` file. Shared manifests, Python versions, CPU dependencies and
macOS startup are unchanged. Downloads happen only with explicit `--prepare`;
the script preserves network and proxy settings and never installs a driver.

From the repository root in ordinary PowerShell:

```powershell
.\.venv-windows\Scripts\python.exe scripts\windows_cuda_setup.py --check-driver
.\.venv-windows\Scripts\python.exe scripts\windows_cuda_setup.py --prepare
powershell -NoProfile -ExecutionPolicy Bypass -File deploy\start_windows_cuda.ps1 -Smoke
```

The launcher can combine preparation and a synthetic smoke check:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File deploy\start_windows_cuda.ps1 -Prepare -Smoke
```

If the CPU service is running, first stop its tracked processes from the same
ordinary Windows terminal:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File deploy\stop.ps1 -WindowsLocalTest
```

The GPU launcher uses the same local port 8000 and test storage; it refuses
existing launch records. The same stop command handles the GPU service.
`-Smoke` generates only a synthetic one-second MOV and writes
`data/windows-local/2b-smoke-summary.json` and `2b-smoke-memory.json`.
The memory file samples whole-system RAM during analysis, not GPU VRAM.
Use `nvidia-smi` to check GPU activity. The backend also measures CUDA peak
allocated memory for each generation; this is not total system GPU usage.

Use the dedicated launcher rather than adding `cuda` to the CPU INT8 profile:
CPU INT8 and GPU 4-bit loading use different implementations. The GPU experiment
remains explicit test mode and does not approve bank rules or enable production.

## Current blockers

The observed driver is **527.54**. This profile conservatively requires **560.76
or later**, the native Windows CUDA 12.6 driver baseline. Driver verification
runs before creating an environment or installing packages. Update the NVIDIA
driver locally and reboot before retrying. This repository does not modify the
system driver. CUDA toolkit installation is not part of this procedure; the
PyTorch wheels supply their CUDA runtime dependencies.

The coding environment also cannot reach package indexes through its configured
proxy. No proxy bypass or installation attempt is used to work around that
restriction. After the driver update, run preparation from the user's ordinary
terminal with permitted network access. A failed preparation leaves the CPU
environment intact and starts no service.

**Status:** mocked setup tests cover isolation, version pins and driver gating;
actual GPU inference and memory/time measurements remain blocked. The service
must still return a real result within the original five-minute job deadline;
out-of-memory or unavailable-model errors must remain failures.

References: [PyTorch CUDA 12.6 wheels](https://download.pytorch.org/whl/cu126/),
[NVIDIA CUDA 12.6 release notes](https://docs.nvidia.com/cuda/archive/12.6.0/cuda-toolkit-release-notes/index.html),
[Transformers bitsandbytes quantization](https://huggingface.co/docs/transformers/quantization/bitsandbytes).
