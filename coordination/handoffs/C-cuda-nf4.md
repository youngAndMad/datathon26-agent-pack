# Optional CUDA NF4 backend

Changed files: `src/cashwatch/inference/backends/hf_backend.py`,
`tests/inference/test_cuda_nf4.py`, this handoff. No commits made.

The explicit `cuda_nf4=True` option loads the same local Qwen checkpoint through
Transformers BitsAndBytesConfig using NF4, double quantization and FP16 compute.
The fixed CUDA device map disallows automatic CPU/disk spilling. The loader rejects
Windows CPU INT8, disk offload and conflicting compute dtypes before loading a
processor or model. Missing CUDA, loading errors and OOM propagate without retrying
on another device. Loaded models must advertise 4-bit loading and their reported
device map must contain only CUDA targets. Load metadata distinguishes CUDA NF4
from Windows CPU INT8. Existing defaults are preserved.

Contracts consumed: AGENTS.md, agents/C_LOCAL_INFERENCE.md,
IMPLEMENTATION_SPEC.md, contracts/INTERNAL_CONTRACTS.md and inference/protocol.py.
GenRequest/GenResult, offline environment flags, source image/token accounting and
public schemas are unchanged.

Verification:

```
.venv-windows\Scripts\python.exe -m unittest tests.inference.test_cuda_nf4 tests.inference.test_hf_backend
```

Result: 23 tests passed (7 new CUDA contract tests and 16 existing HF tests).
Tests use synthetic mocks, no GPU, model weights or private footage. They establish
loader options, rejection before loading, truthful mode metadata and no retry after
OOM; they do not establish real bitsandbytes compatibility, memory fit, speed or
recognition quality.

Integration required: pass `device="cuda:0", cuda_nf4=True` through the Windows
experimental environment launcher. Install a compatible CUDA Torch/bitsandbytes
stack in a separate environment; do not alter shared locks or the CPU environment.
The orchestrator must run the real synthetic smoke once with exclusive GPU access.
No latency or memory measurements were taken in this lane (sample count zero).
