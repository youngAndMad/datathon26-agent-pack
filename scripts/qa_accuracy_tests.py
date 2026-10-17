"""Run synthetic accuracy regression suites using workspace temporary folders.

The temporary directories inherit ordinary workspace ACLs on Windows. No media,
private dataset, model weights or service endpoint is used.
"""
from pathlib import Path
import os
import shutil
import uuid
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    os.chdir(ROOT)
    root = ROOT / ".tmp" / ("accuracy-pytest-" + uuid.uuid4().hex)
    root.mkdir(parents=True)

    class Factory:
        def mktemp(self, basename, numbered=True):
            folder = root / ("fixture-" + uuid.uuid4().hex)
            folder.mkdir()
            return folder

        def getbasetemp(self):
            return root

    class WorkspaceTemps:
        @pytest.fixture(scope="session")
        def tmp_path_factory(self):
            return Factory()

        @pytest.fixture
        def tmp_path(self, tmp_path_factory):
            return tmp_path_factory.mktemp("test")

    try:
        return pytest.main(["tests/inference/test_validation.py", "tests/inference/test_adapter.py",
                            "tests/inference/test_accuracy_contract.py",
                            "tests/rules/test_engine.py", "tests/pipeline/test_pipeline.py",
                            "tests/rules/test_event_dedup.py", "tests/rules/test_service_item_conditions.py",
                            "tests/pipeline/test_anchor_refinement.py",
                            "tests/pipeline/test_verification_frame_selection.py",
                            "-q", "-p", "no:cacheprovider"], plugins=[WorkspaceTemps()])
    finally:
        # Delete only this invocation's UUID directory inside the workspace.
        if root.resolve().parent != (ROOT / ".tmp").resolve():
            raise RuntimeError("Unexpected test cleanup path")
        shutil.rmtree(root)


if __name__ == "__main__":
    raise SystemExit(main())
