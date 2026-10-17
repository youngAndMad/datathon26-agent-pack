"""Run without third-party packages: python -m unittest discover -s tests -p test_platform_support.py."""

import importlib.util
from pathlib import Path
import unittest


class MemoryMetricTest(unittest.TestCase):
    def test_current_process_reports_real_peak_memory(self):
        path = Path(__file__).resolve().parents[1] / "src/cashwatch/media/platform_support.py"
        spec = importlib.util.spec_from_file_location("platform_support", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        before = module.peak_rss_mb()
        allocated = bytearray(8 * 1024 * 1024)
        for i in range(0, len(allocated), 4096):
            allocated[i] = 1
        after = module.peak_rss_mb()
        self.assertGreater(before, 0)
        self.assertGreaterEqual(after, before)


if __name__ == "__main__":
    unittest.main()
