"""Runtime configuration (env prefix CASHWATCH_). Secrets are read from files, never logged.

Production mode refuses the fake perception adapter and an empty/unapproved registry.
Test mode is explicit and visibly labeled in reports/UI.
"""

from __future__ import annotations

import json
from functools import cached_property
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CASHWATCH_", extra="ignore", frozen=True)

    mode: Literal["production", "test"] = "production"
    data_dir: Path = REPO_ROOT / "data"
    api_key_file: Path = REPO_ROOT / "secrets" / "jury_api_key"
    host: str = "127.0.0.1"
    port: int = 8000

    # perception
    backend: Literal["mlx", "transformers", "fake"] = "mlx"
    model_path: Path = REPO_ROOT / "models" / "qwen3-vl-4b-instruct-mlx-8bit"
    model_manifest: Path = REPO_ROOT / "models" / "manifest.json"
    device: str = "auto"

    # profiles / registry
    profile_path: Path = REPO_ROOT / "configs" / "runtime-profile.json"
    registry_path: Path = REPO_ROOT / "rules" / "registry.yaml"

    # limits (engineering safety bounds; never below valid competition inputs)
    max_file_bytes: int = 256 * 1024 * 1024
    max_duration_sec: float = 300.0
    max_pixel_area: int = 3840 * 2160
    max_upload_sec: float = 60.0
    total_deadline_sec: float = 300.0
    internal_terminal_sec: float = 270.0  # hard internal deadline measured from request start
    active_jobs: int = 1
    max_queue_length: int = 0  # queued jobs beyond active ones
    retention_hours: float = 24.0
    lease_sec: float = 20.0
    worker_poll_sec: float = 0.5

    preview_enabled: bool = True
    frontend_dist: Path = REPO_ROOT / "frontend" / "dist"

    @cached_property
    def profile(self) -> dict:
        return json.loads(self.profile_path.read_text(encoding="utf-8"))

    def read_api_key(self) -> str:
        key = self.api_key_file.read_text(encoding="utf-8").strip()
        if len(key) < 32:
            raise RuntimeError("API key file must contain a strong key (>=32 chars)")
        return key

    def production_errors(self) -> list[str]:
        """Configuration errors that must block production startup (no secret values)."""
        errs: list[str] = []
        if self.mode == "production":
            if self.backend == "fake":
                errs.append("fake/mock perception adapter is forbidden in production mode")
            if not self.api_key_file.exists():
                errs.append("API key secret file missing")
        return errs


def get_settings() -> Settings:
    return Settings()
