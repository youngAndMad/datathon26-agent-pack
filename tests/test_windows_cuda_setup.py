"""Preparation safety checks; no downloads, driver changes or model inference."""
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

from scripts import windows_cuda_setup as setup


class CudaSetupTests(unittest.TestCase):
    def driver(self, version):
        return patch.object(setup.subprocess, "run", return_value=SimpleNamespace(stdout=version))

    def test_old_driver_rejected(self):
        with self.driver("527.54\n"):
            with self.assertRaisesRegex(RuntimeError, "below 560.76"):
                setup.check_driver()

    def test_minimum_and_newer_driver_accepted(self):
        with self.driver("560.76\n581.80\n"):
            self.assertEqual(setup.check_driver(), ["560.76", "581.80"])

    def test_missing_or_unparseable_driver_rejected(self):
        for value in ("", "N/A", "560.x"):
            with self.subTest(value=value), self.driver(value):
                with self.assertRaises(RuntimeError):
                    setup.check_driver()

    def test_snapshot_pins_preserved_except_gpu_packages(self):
        def dist(name, version):
            return SimpleNamespace(metadata={"Name": name}, version=version)
        pins = setup.dependency_pins([
            dist("torch", "2.14.1+cpu"), dist("torchvision", "0.29.1+cpu"),
            dist("cashwatch", "0.1"), dist("bitsandbytes", "0.1"),
            dist("transformers", "5.20.1"), dist("accelerate", "1.15.0"),
        ])
        self.assertEqual(pins, ["accelerate==1.15.0", "transformers==5.20.1", setup.BNB])

    def test_bad_package_name_rejected(self):
        with self.assertRaises(ValueError):
            setup.dependency_pins([SimpleNamespace(metadata={"Name": "--index-url"}, version="1")])

    def test_driver_failure_precedes_any_install_or_file_creation(self):
        root = setup.ROOT / ".tmp" / ("cuda-never-created-" + uuid.uuid4().hex)
        with patch.object(setup, "check_driver", side_effect=RuntimeError("old driver")), \
                patch.object(setup.subprocess, "run") as run:
            with self.assertRaisesRegex(RuntimeError, "old driver"):
                setup.prepare(root)
        run.assert_not_called()
        self.assertFalse(root.exists())

    def test_prepare_uses_separate_environment_and_pinned_indexes(self):
        root = setup.ROOT / ".tmp" / ("cuda-setup-test-" + uuid.uuid4().hex)
        root.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, root)
        target = root / ".tools" / "venv-cuda"
        original_python = str(root / ".venv-windows" / "Scripts" / "python.exe")
        calls = []

        def run(command, **kwargs):
            calls.append(command)
            self.assertTrue(kwargs["check"])
            if "venv" in command:
                (target / "Lib" / "site-packages").mkdir(parents=True)
            return SimpleNamespace(returncode=0)

        with patch.object(setup, "check_driver", return_value=["560.76"]), \
                patch.object(setup, "dependency_pins", return_value=["transformers==5.20.1", setup.BNB]), \
                patch.object(sys, "platform", "win32"), patch.object(sys, "version_info", (3, 12, 0)), \
                patch.object(sys, "prefix", str(root / ".venv-windows")), \
                patch.object(sys, "executable", original_python), \
                patch.object(setup.subprocess, "run", side_effect=run):
            python = setup.prepare(root)
        self.assertEqual(calls[0], [original_python, "-m", "venv", str(target)])
        self.assertTrue(all(command[0] == str(python) for command in calls[1:]))
        self.assertIn(setup.CUDA_INDEX, calls[1])
        self.assertIn(setup.TORCH, calls[1])
        self.assertIn(setup.TORCHVISION, calls[1])
        self.assertIn("https://pypi.org/simple", calls[2])
        self.assertIn("--constraint", calls[2])
        self.assertEqual((target / "cpu-snapshot-requirements.txt").read_text(),
                         "transformers==5.20.1\nbitsandbytes==0.50.2\n")
        self.assertEqual((target / "cuda-constraints.txt").read_text(),
                         setup.TORCH + "\n" + setup.TORCHVISION + "\n")
        self.assertEqual((target / "Lib" / "site-packages" / "cashwatch-local.pth").read_text().strip(),
                         str((root / "src").resolve()))

    def test_install_error_is_not_retried_or_hidden(self):
        with patch.object(setup, "prepare", side_effect=subprocess.CalledProcessError(1, ["pip"])), \
                patch("sys.stderr"):
            self.assertEqual(setup.main(["--prepare"]), 1)


if __name__ == "__main__":
    unittest.main()
