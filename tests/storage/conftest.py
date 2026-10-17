"""Storage test fixtures."""

from __future__ import annotations

import pytest
from jobfakes import make_settings

from cashwatch.storage import create_store


@pytest.fixture
def settings(tmp_path):
    return make_settings(tmp_path)


@pytest.fixture
def store(settings):
    return create_store(settings)
