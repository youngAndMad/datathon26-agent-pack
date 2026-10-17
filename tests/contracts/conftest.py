"""Shared fixtures for contract tests (agent F). No server, no model, no media files."""

from __future__ import annotations

import base64
import copy
import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "contracts"
FIXTURES = CONTRACTS / "fixtures"


def load_qa_client():
    spec = importlib.util.spec_from_file_location("qa_jury_client", ROOT / "scripts" / "qa_jury_client.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["qa_jury_client"] = mod  # dataclasses resolve their module via sys.modules
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="session")
def qa():
    return load_qa_client()


def jpeg_b64(size=(64, 48), color=(10, 120, 200)) -> str:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode()


def png_b64() -> str:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), (1, 2, 3)).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


OFFICIAL_DESC = ("На 12.345 с видно: синтетический объект. Требует проверки сотрудником. "
                 "Источник: Синтетический документ, п. 0; https://adilet.zan.kz/rus/docs/SYNTHETIC")


@pytest.fixture()
def valid_violation():
    """A semantically valid synthetic done/violation document (not a real legal reference)."""
    return {
        "job_id": "job-1",
        "status": "done",
        "verdict": "violation",
        "violations": [
            {"type": "Тип A", "timecode_sec": 12.345, "description": OFFICIAL_DESC,
             "evidence_frames": [{"timestamp_sec": 12.345, "image_base64": jpeg_b64()},
                                 {"timestamp_sec": 13.0, "image_base64": jpeg_b64(color=(200, 1, 1))}]},
            {"type": "Тип B", "timecode_sec": 47.0, "description": OFFICIAL_DESC.replace("12.345", "47.000"),
             "evidence_frames": [{"timestamp_sec": 47.0, "image_base64": jpeg_b64()}]},
        ],
    }


@pytest.fixture()
def clone():
    return copy.deepcopy


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))
