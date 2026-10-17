"""API test fixtures: tmp data_dir, test key file, tiny synthetic MOV (ffmpeg testsrc)."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "storage"))
from jobfakes import make_settings  # noqa: E402

from cashwatch.api import create_app  # noqa: E402
from cashwatch.settings import REPO_ROOT  # noqa: E402
from cashwatch.storage import create_store  # noqa: E402


@pytest.fixture(scope="session")
def mov_bytes(tmp_path_factory) -> bytes:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg CLI not available")
    out = tmp_path_factory.mktemp("media") / "synthetic.mov"
    subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=30", "-t", "2",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-f", "mov", str(out)], check=True, timeout=60)
    return out.read_bytes()


@pytest.fixture(scope="session")
def schemas():
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT202012

    c = REPO_ROOT / "contracts"
    docs = {n: json.loads((c / n).read_text(encoding="utf-8"))
            for n in ("job-response.schema.json", "jury-result.schema.json", "submit-response.schema.json",
                      "ui-report.schema.json")}
    registry = Registry().with_resources([(n, Resource.from_contents(d, default_specification=DRAFT202012)) for n, d in docs.items()])
    return docs, registry


@pytest.fixture
def validate(schemas):
    from jsonschema import Draft202012Validator

    docs, registry = schemas

    def _v(name: str, instance) -> None:
        Draft202012Validator(docs[name], registry=registry).validate(instance)

    return _v


class Env:
    def __init__(self, tmp_path: Path, **kw):
        from starlette.testclient import TestClient

        self.settings = make_settings(tmp_path, **kw)
        self.store = create_store(self.settings)
        self.app = create_app(self.settings, self.store, sweep_interval_sec=0)
        self.client = TestClient(self.app, raise_server_exceptions=False)
        self.key = self.settings.read_api_key()
        self.h = {"X-API-Key": self.key}

    def ready(self) -> None:
        self.store.worker_heartbeat("test-worker", ready=True, info={})

    def upload(self, data: bytes, *, name="clip.mov", ctype="application/octet-stream", headers=None):
        return self.client.post("/api/v1/analyze", files={"file": (name, data, ctype)},
                                headers={**self.h, **(headers or {})})


@pytest.fixture
def make_env(tmp_path):
    n = {"i": 0}

    def _make(**kw) -> Env:
        n["i"] += 1
        return Env(tmp_path / f"env{n['i']}", **kw)

    return _make


@pytest.fixture
def env(make_env) -> Env:
    e = make_env()
    e.ready()
    return e
