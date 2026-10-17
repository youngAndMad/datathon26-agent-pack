"""Offline artifact integrity checks using tiny synthetic files, no weights."""
import importlib.util
import json
from pathlib import Path
import shutil
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("windows_model_2b", ROOT / "scripts/windows_model_2b.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ModelIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / ".tmp" / ("model-2b-test-" + uuid.uuid4().hex)
        folder = self.root / "models" / module.FOLDER
        folder.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.root)
        self.records = []
        for name in module.FILES:
            file = folder / name
            file.write_bytes(b"synthetic:" + name.encode())
            size, digest, _ = module.hashes(file)
            self.records.append({"path": name, "size_bytes": size, "sha256": digest})
        self.manifest = self.root / "manifest.json"
        self.entry = {"repo_id": module.REPO, "revision": module.REVISION,
                      "local_dir": "models/" + module.FOLDER, "adapter": "transformers",
                      "files": self.records}
        self.save()

    def save(self):
        self.manifest.write_text(json.dumps({"models": [self.entry]}), encoding="utf-8")

    def test_complete_artifacts(self):
        self.assertEqual(module.verify(self.manifest, self.root), len(module.FILES))

    def test_same_size_corruption_rejected(self):
        file = self.root / "models" / module.FOLDER / "model.safetensors"
        file.write_bytes(b"x" * file.stat().st_size)
        with self.assertRaisesRegex(ValueError, "checksum"):
            module.verify(self.manifest, self.root)

    def test_missing_record_and_traversal_rejected(self):
        for paths in [self.records[:-1], [{**self.records[0], "path": "../private"}, *self.records[1:]]]:
            self.entry["files"] = paths
            self.save()
            with self.assertRaisesRegex(ValueError, "Incomplete"):
                module.verify(self.manifest, self.root)

    def test_wrong_revision_rejected(self):
        self.entry["revision"] = "main"
        self.save()
        with self.assertRaisesRegex(ValueError, "identity"):
            module.verify(self.manifest, self.root)


if __name__ == "__main__":
    unittest.main()
