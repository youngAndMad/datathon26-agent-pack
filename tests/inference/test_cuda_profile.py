"""Factory wiring checks without loading weights or touching private data."""
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from cashwatch.errors import PipelineError
import cashwatch.inference.supervisor  # noqa: F401  (patched by attribute path; not auto-imported on macOS)
from cashwatch.inference import create_perception


class CudaProfileTests(unittest.TestCase):
    def setUp(self):
        self.settings = SimpleNamespace(backend="transformers", mode="test", device="cuda",
                                        model_path=Path(__file__).parent, data_dir=Path(__file__).parent,
                                        profile={})
        for patcher in (
            patch("cashwatch.inference.os.name", "nt"),
            patch.dict(os.environ, {"CASHWATCH_WINDOWS_CUDA_NF4": "1",
                                    "CASHWATCH_WINDOWS_CPU_INT8": "0",
                                    "CASHWATCH_WINDOWS_CPU_LOW_MEMORY": "0"}),
            patch("cashwatch.inference.resolve_model_entry", return_value=None),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        child = patch("cashwatch.inference.supervisor.ChildGenerator")
        self.child = child.start()
        self.addCleanup(child.stop)

    def test_cuda_profile_reaches_child_without_cpu_offload(self):
        create_perception(self.settings)
        self.assertEqual(self.child.call_args.kwargs["options"],
                         {"device": "cuda", "cuda_nf4": True, "dtype": "float16"})

    def test_cpu_device_is_not_silently_selected(self):
        self.settings.device = "cpu"
        with self.assertRaises(PipelineError):
            create_perception(self.settings)
        self.child.assert_not_called()

    def test_conflicting_profiles_fail_before_child_start(self):
        for name in ("CASHWATCH_WINDOWS_CPU_INT8", "CASHWATCH_WINDOWS_CPU_LOW_MEMORY"):
            with self.subTest(name=name), patch.dict(os.environ, {name: "1"}):
                with self.assertRaises(PipelineError):
                    create_perception(self.settings)
        self.child.assert_not_called()

    @unittest.skipIf(os.name != "nt", "patches os.name globally; pathlib breaks off Windows")
    def test_default_cpu_path_remains_unquantized(self):
        self.settings.device = "cpu"
        with patch.dict(os.environ, {"CASHWATCH_WINDOWS_CUDA_NF4": "0"}):
            create_perception(self.settings)
        self.assertEqual(self.child.call_args.kwargs["options"], {"device": "cpu"})


if __name__ == "__main__":
    unittest.main()
