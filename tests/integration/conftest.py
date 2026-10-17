"""Integration fixtures (agent F). Black-box: real API + worker processes, HTTP only.

Backend selection: CASHWATCH_IT_BACKEND=fake (default; plumbing only — NOT detection evidence) or
mlx (real local model; holds the shared GPU reservation for the whole session and is marked
``model``). Service tests are marked ``slow`` (they spawn processes)."""

from __future__ import annotations

import contextlib
import os
from pathlib import Path

import pytest

from .harness import TEST_REGISTRY, Service, load_qa_client, pending_components, synthetic_clip, tiny_mov

IT_BACKEND = os.environ.get("CASHWATCH_IT_BACKEND", "fake")


def require_components(only: tuple[str, ...] | None = None, registry: bool = True) -> None:
    missing = [m for m in pending_components() if only is None or m in only]
    if missing:
        pytest.skip(f"pending integration: cannot import {', '.join(missing)}")
    if registry and not TEST_REGISTRY.exists():
        pytest.skip("pending integration: rules/registry.test.yaml (agent D) missing")


@pytest.fixture(scope="session")
def qa():
    return load_qa_client()


@pytest.fixture(scope="session")
def gpu_session():
    if IT_BACKEND == "fake":
        yield None
        return
    from cashwatch.gpulock import gpu_reservation
    with gpu_reservation("F-integration", timeout_sec=1800):
        yield IT_BACKEND


@pytest.fixture(scope="session")
def media_dir(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("synthetic-media")


@pytest.fixture(scope="session")
def clip(media_dir):
    """Return a callable name -> path of a synthetic clip (skips if it cannot be produced)."""
    def get(name: str) -> Path:
        p = synthetic_clip(name, media_dir)
        if p is None:
            if name == "tiny":
                return tiny_mov(media_dir / "tiny.mov")
            pytest.skip(f"synthetic fixture {name} unavailable (agent B generator)")
        return p
    return get


def _make_service(tmp: Path, **kw) -> Service:
    kw.setdefault("backend", IT_BACKEND)
    kw.setdefault("registry", TEST_REGISTRY)
    return Service(tmp, **kw)


@pytest.fixture(scope="module")
def service(tmp_path_factory, gpu_session):
    require_components()
    svc = _make_service(tmp_path_factory.mktemp("svc"))
    try:
        svc.start(timeout=240 if IT_BACKEND != "fake" else 90)
    except RuntimeError as e:
        svc.stop()
        pytest.fail(str(e))
    yield svc
    svc.stop()


@pytest.fixture(scope="module")
def api_only(tmp_path_factory):
    """API process alone (no worker, not ready): enough for auth/ingress tests (T01)."""
    require_components(only=("api", "storage"), registry=False)
    svc = Service(tmp_path_factory.mktemp("api-only"), backend="fake", registry=None)
    svc.start_api()
    if not svc.wait_healthy(30):
        svc.stop()
        pytest.fail("api did not answer /healthz:\n" + svc.log_tail())
    yield svc
    svc.stop()


@pytest.fixture()
def make_service(tmp_path, gpu_session):
    """Factory for a dedicated service instance (destructive tests)."""
    made: list[Service] = []

    def make(*, only: tuple[str, ...] | None = None, **kw) -> Service:
        require_components(only=only, registry=only is None)
        s = _make_service(tmp_path / f"svc{len(made)}", **kw)
        made.append(s)
        return s
    yield make
    for s in made:
        with contextlib.suppress(Exception):
            s.stop()
