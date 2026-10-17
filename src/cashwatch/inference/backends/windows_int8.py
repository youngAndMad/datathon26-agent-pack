"""Experimental resident CPU int8 loader using existing PyTorch/safetensors only.

No full float model is allocated. Unshared Linear layers are materialized and
packed one at a time. A tied output head gets a packed copy of the embedding
weights; embeddings use BF16 storage and return FP32 activations. Do not call
tie_weights after conversion.
"""
from __future__ import annotations

from collections import defaultdict
import json
import os
from pathlib import Path
from typing import Callable


class TensorFiles:
    def __init__(self, path: Path):
        from safetensors import safe_open

        self.root = path.resolve()
        index = self.root / "model.safetensors.index.json"
        if index.is_file():
            mapping = json.loads(index.read_text(encoding="utf-8")).get("weight_map")
            if not isinstance(mapping, dict) or not mapping:
                raise ValueError("Invalid safetensors index")
        else:
            with safe_open(str(self.root / "model.safetensors"), framework="pt", device="cpu") as fh:
                mapping = {key: "model.safetensors" for key in fh.keys()}
        self.files = {}
        for key, filename in mapping.items():
            if not isinstance(key, str) or not isinstance(filename, str):
                raise ValueError("Invalid safetensors tensor mapping")
            file = (self.root / filename).resolve()
            if not file.is_relative_to(self.root) or file.suffix != ".safetensors" or not file.is_file():
                raise ValueError("Safetensors shard is missing or outside model directory")
            self.files[key] = file

    def keys(self):
        return self.files.keys()

    def get(self, name):
        from safetensors import safe_open

        with safe_open(str(self.files[name]), framework="pt", device="cpu") as fh:
            return fh.get_tensor(name)


def _fill_model(model, source, progress: Callable[[dict], None] | None = None):
    """Fill a meta-parameter/CPU-buffer skeleton; also usable with tiny test models."""
    import torch
    from torch.ao.nn.quantized.dynamic import Linear as DynamicLinear
    from torch.ao.quantization import default_dynamic_qconfig

    if torch.get_default_dtype() != torch.float32:
        raise ValueError("Windows int8 loader requires default float32 dtype")
    available = set(source.keys())
    consumed = set()
    parameters = list(model.named_parameters(remove_duplicate=False))
    aliases = defaultdict(list)
    for name, parameter in parameters:
        aliases[id(parameter)].append(name)
    modules = dict(model.named_modules())

    class FloatOutputEmbedding(torch.nn.Embedding):
        def forward(self, input):
            return super().forward(input).float()

    def read(name, expected, dtype=torch.float32):
        if name not in available:
            raise ValueError(f"Checkpoint tensor missing: {name}")
        value = source.get(name)
        if tuple(value.shape) != tuple(expected.shape):
            raise ValueError(f"Checkpoint tensor shape mismatch: {name}")
        if not value.is_floating_point() or not expected.is_floating_point():
            if value.dtype != expected.dtype:
                raise ValueError(f"Non-floating tensor dtype mismatch: {name}")
        value = value.to(device="cpu", dtype=dtype if value.is_floating_point() else value.dtype)
        consumed.add(name)
        return value

    converted = set()
    count = 0
    # Pack the largest matrices (normally the output head) before retaining the
    # rest of the int8 model, reducing peak FP32 conversion memory.
    linear_layers = [(name, layer) for name, layer in modules.items()
                     if name and type(layer) is torch.nn.Linear]
    for name, layer in sorted(linear_layers, key=lambda pair: pair[1].weight.numel(), reverse=True):
        weight_name = name + ".weight"
        sources = [key for key in aliases[id(layer.weight)] if key in available]
        if weight_name in available:
            source_name = weight_name
        elif len(sources) == 1:
            source_name = sources[0]
        else:
            raise ValueError(f"Missing or ambiguous tied Linear weight: {weight_name}")
        weight = read(source_name, layer.weight)
        # Construct on meta so no redundant initialized float matrix is allocated.
        with torch.device("meta"):
            floating = torch.nn.Linear(layer.in_features, layer.out_features, bias=layer.bias is not None)
        floating.weight = torch.nn.Parameter(weight, requires_grad=False)
        if layer.bias is not None:
            floating.bias = torch.nn.Parameter(read(name + ".bias", layer.bias), requires_grad=False)
        floating.qconfig = default_dynamic_qconfig
        packed = DynamicLinear.from_float(floating)
        parent_name, _, attr = name.rpartition(".")
        setattr(modules[parent_name], attr, packed)
        converted.add(weight_name)
        if layer.bias is not None:
            converted.add(name + ".bias")
        count += 1
        if progress:
            progress({"stage": "packing_int8", "linear_layers": count})
        del weight, floating, packed

    done = set()
    for name, parameter in parameters:
        identity = id(parameter)
        if identity in done or name in converted:
            continue
        names = [alias for alias in aliases[identity] if alias not in converted]
        sources = [key for key in names if key in available]
        if len(sources) != 1:
            raise ValueError(f"Expected exactly one checkpoint tensor for shared group: {names}")
        embedding_names = [alias.rpartition(".")[0] for alias in names
                           if type(modules[alias.rpartition(".")[0]]) is torch.nn.Embedding]
        if embedding_names and len(embedding_names) != len(names):
            raise ValueError("Unsupported embedding tied to a non-embedding floating parameter")
        dtype = torch.bfloat16 if embedding_names else torch.float32
        value = torch.nn.Parameter(read(sources[0], parameter, dtype=dtype), requires_grad=False)
        for embedding_name in embedding_names:
            old = modules[embedding_name]
            with torch.device("meta"):
                replacement = FloatOutputEmbedding(old.num_embeddings, old.embedding_dim,
                    padding_idx=old.padding_idx, max_norm=old.max_norm, norm_type=old.norm_type,
                    scale_grad_by_freq=old.scale_grad_by_freq, sparse=old.sparse, dtype=torch.bfloat16)
            parent_name, _, attr = embedding_name.rpartition(".")
            setattr(modules[parent_name], attr, replacement)
            modules[embedding_name] = replacement
        for alias in names:
            parent_name, _, attr = alias.rpartition(".")
            setattr(modules[parent_name], attr, value)
        done.add(identity)

    # Persistent buffers may be stored; nonpersistent rotary buffers were created
    # on CPU by init_empty_weights(include_buffers=False), not on meta.
    for name, buffer in list(model.named_buffers()):
        if name in available:
            parent_name, _, attr = name.rpartition(".")
            setattr(dict(model.named_modules())[parent_name], attr, read(name, buffer))
        elif buffer.is_meta:
            raise ValueError(f"Unmaterialized buffer: {name}")
    if available != consumed:
        raise ValueError(f"Unexpected checkpoint tensors: {sorted(available - consumed)[:8]}")
    if any(p.is_meta for p in model.parameters()) or any(b.is_meta for b in model.buffers()):
        raise ValueError("Unmaterialized meta tensors remain")
    if not count:
        raise ValueError("No supported unshared Linear layers found")
    model.eval()
    model.requires_grad_(False)
    model._cashwatch_int8_linear_count = count
    return model


def load_windows_int8(path: Path | str, progress: Callable[[dict], None] | None = None):
    """Return local Qwen3-VL with resident packed int8 Linear weights (CPU only)."""
    if os.name != "nt":
        raise RuntimeError("Experimental int8 loader is Windows-only")
    import torch
    from accelerate import init_empty_weights
    from transformers import AutoConfig, Qwen3VLForConditionalGeneration

    config = AutoConfig.from_pretrained(str(path), local_files_only=True, trust_remote_code=False)
    if config.model_type != "qwen3_vl":
        raise ValueError("Windows int8 loader only supports Qwen3-VL")
    if torch.backends.quantized.engine not in ("x86", "fbgemm", "onednn"):
        raise RuntimeError("No supported x86 dynamic quantization engine selected")
    config._attn_implementation = "sdpa"
    with init_empty_weights(include_buffers=False):
        model = Qwen3VLForConditionalGeneration(config)
    # Accelerate's parameter registration patch may separate tied aliases. Retie
    # while weights are still meta, BEFORE replacing any head with packed int8.
    model.tie_weights()
    model = _fill_model(model, TensorFiles(Path(path)), progress)
    # Re-tying would overwrite the packed head with a Parameter; the independent
    # int8 head is intentional. This loader is inference-only.
    return model.eval()
