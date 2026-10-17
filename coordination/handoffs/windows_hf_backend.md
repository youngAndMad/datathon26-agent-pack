# Windows HF backend handoff

Owned changes:
- `src/cashwatch/inference/backends/hf_backend.py`
- `tests/inference/test_hf_backend.py`

Completed: resident offline Qwen3-VL Transformers backend implementing existing
`load()` / `generate(GenRequest) -> GenResult` contracts. CPU uses FP32; CUDA uses
FP16 and SDPA. `auto` conservatively means CPU; explicit CUDA fails if unavailable.
No implicit OOM fallback/retry, no remote-code execution, local safetensors only,
offline environment forced before Transformers import. Generation is greedy and
bounded. Source timestamps stay in ordered text/image segments. Processor grids
and actual image-token IDs are cross-checked; prompt and output lengths come from
actual tensors. CPU memory metric remains unknown rather than fabricated.
Supervisor remains responsible for killing the child on deadlines/cancellation.

Verification:
- `set PYTHONPATH=src&& py -3.13 -m unittest discover -s tests\inference -p test_hf_backend.py -v`
  passed 9 synthetic contract tests, 0.046 seconds. This uses installed Python 3.13
  with its existing pydantic, not mixed virtualenv packages. Tests mock Transformers
  and tensors; this establishes adapter mechanics, not real inference.
- Same command with `.venv\Scripts\python.exe` failed import: pydantic unavailable.
- Initial filesystem fixtures hit sandbox ACL failures for newly created temporary
  directories (both system TEMP and Desktop); final tests mock model-folder check
  for load-path assertions and test missing config against an existing directory.

No model weights loaded; no accelerator reservation, real inference latency,
peak-memory measurement, denied-egress model execution, domain accuracy or
five-minute completion claim. Exact model revision/install remains integrator work.
CPU FP32 4B weights require approximately 16 GB before activations/overhead;
measure actual host headroom before starting. No quantization was added.

Primary source checked for API correctness:
https://raw.githubusercontent.com/huggingface/transformers/v4.57.1/src/transformers/models/qwen3_vl/processing_qwen3_vl.py
https://huggingface.co/docs/transformers/v5.3.0/model_doc/qwen3_vl

Requested integration: pin/install matching torch, transformers and accelerate;
download a reviewed Qwen3-VL checkpoint, set device=cpu for this 4 GB GPU host,
run a synthetic real-model smoke test, then measure resource use. Shared manifests,
settings, pipeline and protocol were not changed by this lane.
