"""Local perception (agent C). Factory: ``create_perception(settings) -> PerceptionPort``.

Backends: "mlx" (primary, Apple Silicon), "transformers" (reference/comparison, MPS/CUDA/CPU),
"fake" (explicit test mode only, is_mock=True; refused when settings.mode == "production").
The model always runs in a supervised child process (see supervisor.py); call
``warmup(timeout_sec=...)`` before use and after any failure that cleared ``ready()``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from cashwatch.errors import PipelineError

__all__ = ["create_perception", "resolve_model_entry", "adapter_config_from_profile"]


def _manifest_candidates(settings) -> list[Path]:
    from cashwatch.settings import REPO_ROOT
    out = [Path(settings.model_manifest)]
    proposed = REPO_ROOT / "models" / "manifest.proposed.json"
    if proposed not in out:
        out.append(proposed)
    return out


def resolve_model_entry(settings) -> dict[str, Any] | None:
    """Manifest entry whose local_dir resolves to settings.model_path (None if unlisted)."""
    from cashwatch.settings import REPO_ROOT
    target = Path(settings.model_path).resolve()
    for mpath in _manifest_candidates(settings):
        if not mpath.is_file():
            continue
        try:
            data = json.loads(mpath.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for entry in data.get("models", []):
            local = entry.get("local_dir")
            if local and (REPO_ROOT / local).resolve() == target:
                return dict(entry, _manifest=mpath.name)
    return None


def check_model_files(model_path: Path, entry: dict[str, Any]) -> list[str]:
    """Cheap startup check: every manifest file exists with the recorded size (full sha256
    verification: scripts/model_verify.py)."""
    errs = []
    for f in entry.get("files", []):
        p = model_path / f["path"]
        if not p.is_file():
            errs.append(f"missing model file {f['path']}")
        elif p.stat().st_size != f["size_bytes"]:
            errs.append(f"size mismatch for model file {f['path']}")
    return errs


def adapter_config_from_profile(profile: dict[str, Any] | None, **overrides):
    from cashwatch.inference.adapter import AdapterConfig
    s = (profile or {}).get("sampling", {})
    kw: dict[str, Any] = {}
    if s.get("model_long_side_px"):
        kw["max_long_side_px"] = int(s["model_long_side_px"])
    if s.get("max_visual_tokens"):
        kw["max_visual_tokens_per_call"] = int(s["max_visual_tokens"])
    kw.update(overrides)
    return AdapterConfig(**kw)


def create_perception(settings, *, config=None, responder=None, stderr_path: Path | None = None):
    """Build the perception adapter. Does not load the model: call ``warmup()``."""
    from cashwatch.inference.adapter import LocalPerception

    backend = settings.backend
    if backend == "fake":
        if settings.mode == "production":
            raise PipelineError("model_unavailable", "fake/mock perception is forbidden in production mode")
        from cashwatch.inference.fake import InProcessGenerator
        return LocalPerception(InProcessGenerator(responder), adapter="fake", model_id="mock",
                               revision="none", local_path_label="none (test mode mock)", is_mock=True,
                               config=config or adapter_config_from_profile(_profile(settings)))
    if backend not in ("mlx", "transformers"):
        raise PipelineError("model_unavailable", f"unsupported perception backend {backend!r}")
    if responder is not None:
        raise ValueError("responder hooks are only available for the fake backend")

    model_path = Path(settings.model_path)
    if not model_path.is_dir():
        raise PipelineError("model_unavailable", "model folder missing (pre-stage weights offline)")
    entry = resolve_model_entry(settings)
    if entry is None:
        if settings.mode == "production":
            raise PipelineError("model_unavailable", "model folder is not listed in the model manifest")
        model_id, revision = model_path.name, "unverified"
    else:
        if entry.get("adapter") != backend:
            raise PipelineError("model_unavailable",
                                f"manifest adapter {entry.get('adapter')!r} does not match backend {backend!r}")
        errs = check_model_files(model_path, entry)
        if errs:
            raise PipelineError("model_unavailable", "; ".join(errs[:3]))
        model_id, revision = entry["repo_id"], entry["revision"]

    from cashwatch.inference.supervisor import ChildGenerator
    options: dict[str, Any] = {"device": settings.device}
    # Optional Windows workstation launch profile; shared configs remain unchanged.
    if os.name == "nt" and os.environ.get("CASHWATCH_WINDOWS_CUDA_NF4") == "1":
        if (backend != "transformers" or settings.device != "cuda" or settings.mode != "test"
                or os.environ.get("CASHWATCH_WINDOWS_CPU_INT8") == "1"
                or os.environ.get("CASHWATCH_WINDOWS_CPU_LOW_MEMORY") == "1"):
            raise PipelineError("model_unavailable", "Experimental CUDA NF4 requires test mode, transformers CUDA and no CPU profile")
        options.update(device="cuda", cuda_nf4=True, dtype="float16")
    elif os.name == "nt" and os.environ.get("CASHWATCH_WINDOWS_CPU_INT8") == "1":
        if settings.device not in ("cpu", "auto"):
            raise PipelineError("model_unavailable", "Windows INT8 profile requires CPU")
        options.update(device="cpu", windows_int8=True)
        if config is None:
            config = adapter_config_from_profile(_profile(settings), observe_call_cap_sec=180.0)
    elif os.name == "nt" and os.environ.get("CASHWATCH_WINDOWS_CPU_LOW_MEMORY") == "1":
        if settings.device not in ("cpu", "auto"):
            raise PipelineError("model_unavailable", "low-memory Windows profile requires CPU")
        dtype = os.environ.get("CASHWATCH_WINDOWS_CPU_DTYPE", "bfloat16")
        if dtype not in ("bfloat16", "float32"):
            raise PipelineError("model_unavailable", "unsupported Windows CPU dtype")
        options.update(device="cpu", dtype=dtype, max_memory={"cpu": "2GiB"},
                       offload_dir=str(Path(settings.data_dir) / ("model-offload-" + dtype)))
    if stderr_path is None:
        stderr_path = Path(settings.data_dir) / "logs" / "model-child.log"
    gen = ChildGenerator(backend, model_path, options=options, stderr_path=stderr_path)
    return LocalPerception(gen, adapter=backend, model_id=model_id, revision=revision,
                           local_path_label=f"models/{model_path.name}", is_mock=False,
                           config=config or adapter_config_from_profile(_profile(settings)))


def _profile(settings) -> dict[str, Any] | None:
    try:
        return settings.profile
    except Exception:  # noqa: BLE001 - profile is optional for the adapter
        return None
