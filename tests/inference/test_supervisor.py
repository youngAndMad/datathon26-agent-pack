"""Supervised child process boundary using the TEST-ONLY scripted backend (real subprocess,
no model weights): timeouts kill the child, crashes/OOM map to typed failures, readiness is
false until a fresh warmup succeeds."""

from __future__ import annotations

import os
import time

import pytest

from cashwatch.deadline import Deadline
from cashwatch.errors import PipelineError
from cashwatch.inference.adapter import AdapterConfig, LocalPerception
from cashwatch.inference.protocol import GenRequest
from cashwatch.inference.supervisor import ChildGenerator, GenTimeout, child_env
from inference_canned import good_observation


def _adapter(tmp_path, **options):
    gen = ChildGenerator("scripted", tmp_path, options=options)
    cfg = AdapterConfig(observe_call_cap_sec=1.5, verify_call_cap_sec=1.5, repair_call_cap_sec=1.5)
    return LocalPerception(gen, adapter="mlx", model_id="scripted", revision="test",
                           local_path_label="scripted", is_mock=True, config=cfg), gen


def _gone(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


def test_child_roundtrip_and_clean_close(tmp_path, obs_request, work_dir):
    p, gen = _adapter(tmp_path, replies=["ok", good_observation()])
    p.warmup(timeout_sec=30)
    assert p.ready() and gen.pid
    b = p.observe(obs_request, work_dir, Deadline.in_seconds(30))
    assert b.observations[0].frame_ids == ["f0001", "f0003"]
    pid = gen.pid
    p.close()
    assert not p.ready() and _gone(pid)


def test_hung_generation_is_killed_and_needs_warmup(tmp_path, obs_request, work_dir):
    p, gen = _adapter(tmp_path, gen_sleep_sec=30.0)
    gen.start(30)
    p._ready = True  # simulate a warmed-up adapter whose next call hangs
    pid = gen.pid
    t0 = time.monotonic()
    with pytest.raises(PipelineError) as ei:
        p.observe(obs_request, work_dir, Deadline.in_seconds(60))
    assert ei.value.code == "model_unavailable"  # per-call cap (1.5 s) hit, not the job deadline
    assert time.monotonic() - t0 < 10
    assert not p.ready() and _gone(pid)
    with pytest.raises(PipelineError) as ei:
        p.observe(obs_request, work_dir, Deadline.in_seconds(60))
    assert ei.value.code == "model_unavailable"


def test_timeout_bound_by_deadline_is_deadline_exceeded(tmp_path, obs_request, work_dir):
    p, gen = _adapter(tmp_path, gen_sleep_sec=30.0)
    gen.start(30)
    p._ready = True
    with pytest.raises(PipelineError) as ei:
        p.observe(obs_request, work_dir, Deadline.in_seconds(0.8))
    assert ei.value.code == "deadline_exceeded"
    assert not gen.alive()


def test_warmup_timeout_and_recovery(tmp_path):
    p, gen = _adapter(tmp_path, load_sleep_sec=30.0)
    with pytest.raises(PipelineError) as ei:
        p.warmup(timeout_sec=1.0)
    assert ei.value.code == "model_unavailable" and not p.ready() and not gen.alive()
    p2, _ = _adapter(tmp_path)
    p2.warmup(timeout_sec=30)
    assert p2.ready()
    p2.close()


def test_child_crash_maps_to_model_unavailable(tmp_path, obs_request, work_dir):
    p, gen = _adapter(tmp_path, crash=True)
    gen.start(30)
    p._ready = True
    with pytest.raises(PipelineError) as ei:
        p.observe(obs_request, work_dir, Deadline.in_seconds(30))
    assert ei.value.code == "model_unavailable" and not p.ready()


def test_oom_maps_to_gpu_oom_and_kills_child(tmp_path, obs_request, work_dir):
    p, gen = _adapter(tmp_path, fail_with="[metal::malloc] Unable to allocate 4096 MiB")
    gen.start(30)
    p._ready = True
    with pytest.raises(PipelineError) as ei:
        p.observe(obs_request, work_dir, Deadline.in_seconds(30))
    assert ei.value.code == "gpu_oom" and not gen.alive() and not p.ready()


def test_load_failure_is_model_unavailable(tmp_path):
    p, gen = _adapter(tmp_path, fail_load=True)
    with pytest.raises(PipelineError) as ei:
        p.warmup(timeout_sec=30)
    assert ei.value.code == "model_unavailable" and not gen.alive()


def test_generator_timeout_directly(tmp_path):
    gen = ChildGenerator("scripted", tmp_path, options={"gen_sleep_sec": 10})
    gen.start(30)
    with pytest.raises(GenTimeout):
        gen.generate(GenRequest(system="s", segments=[("text", "t")], images=[], max_tokens=4), 0.5)
    assert not gen.alive()


def test_child_env_is_offline_and_scrubbed(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    monkeypatch.setenv("HF_TOKEN", "y")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy")
    monkeypatch.setenv("HF_HUB_OFFLINE", "0")
    env = child_env()
    assert "OPENAI_API_KEY" not in env and "HF_TOKEN" not in env and "HTTPS_PROXY" not in env
    assert env["HF_HUB_OFFLINE"] == "1" and env["TRANSFORMERS_OFFLINE"] == "1"
    assert env["HF_HUB_DISABLE_TELEMETRY"] == "1"
