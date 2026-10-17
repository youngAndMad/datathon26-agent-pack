"""Public synthetic CUDA/NF4 kernel preflight; no videos or model weights."""
from __future__ import annotations

import json


def main():
    import torch
    if not torch.version.cuda or not torch.cuda.is_available():
        raise RuntimeError("CUDA unavailable: use .tools/venv-cuda and a compatible NVIDIA driver; no CPU fallback")
    import bitsandbytes as bnb
    device = torch.device("cuda:0")
    props = torch.cuda.get_device_properties(device)
    # Exercise the actual NF4 CUDA kernels, not just device discovery.
    layer = bnb.nn.Linear4bit(64, 32, bias=False, compute_dtype=torch.float16,
                            compress_statistics=True, quant_type="nf4")
    layer = layer.to(device)
    with torch.inference_mode():
        result = layer(torch.ones((1, 64), device=device, dtype=torch.float16))
    torch.cuda.synchronize(device)
    if not bool(torch.isfinite(result).all()) or result.device.type != "cuda":
        raise RuntimeError("NF4 CUDA kernel check failed")
    print(json.dumps({"device": props.name, "torch": torch.__version__,
                      "cuda": torch.version.cuda, "bitsandbytes": bnb.__version__,
                      "vram_total_mib": props.total_memory // 2**20,
                      "nf4_kernel": "passed", "scope": "tiny synthetic operation, not full model inference"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
