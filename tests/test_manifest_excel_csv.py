"""Synthetic Excel UTF-8 BOM compatibility; no organizer data."""
import importlib.util
from pathlib import Path
import shutil
import unittest
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("build_manifest_test", ROOT / "evaluation/manifest/build_manifest.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ExcelCsvTests(unittest.TestCase):
    def test_bom_header_preserves_label_and_timestamp(self):
        root = ROOT / ".tmp" / ("csv-" + uuid.uuid4().hex)
        root.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, root)
        (root / "synthetic.mov").write_bytes(b"synthetic-not-real-video")
        labels = root / "labels.csv"
        labels.write_text("file,label_verdict,label_timestamps_sec,group,split\nsynthetic.mov,violation,1.25,g1,dev\n", encoding="utf-8-sig")
        with patch.object(module, "duration", return_value=2.0):
            result = module.build(root, labels)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["label_verdict"], "violation")
        self.assertEqual(result[0]["label_timestamps_sec"], [1.25])


if __name__ == "__main__":
    unittest.main()
