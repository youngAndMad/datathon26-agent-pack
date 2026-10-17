"""Real subprocess/pipe checks using synthetic bytes, no model weights or pytest."""
import os
import subprocess
import sys
import shutil
import threading
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from cashwatch import gpulock
from cashwatch.inference.protocol import GenRequest, ProtocolTimeout, read_frame, write_frame
from cashwatch.inference.supervisor import ChildGenerator, GenCrashed, GenTimeout, child_env


class WindowsRuntimeTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[2]
        self.path = root / ".tmp" / ("runtime-test-" + uuid.uuid4().hex)
        self.path.mkdir(parents=True)
        # The generated path is strictly inside this checkout's scratch directory.
        self.addCleanup(shutil.rmtree, self.path)

    def test_binary_child_roundtrip_and_timeout_recovery(self):
        text = "binary\r\n\x1a\x00 текст"
        gen = ChildGenerator("scripted", self.path, options={"replies": [text]})
        self.addCleanup(gen.kill)
        request = GenRequest(system=text, segments=[("text", text)], images=[bytes(range(256))], max_tokens=4)
        gen.start(15)
        self.assertEqual(gen.generate(request, 5).text, text)
        proc = gen._proc
        gen.close()
        self.assertIsNotNone(proc.poll())
        gen.options = {"gen_sleep_sec": 10}
        gen.start(15)
        proc = gen._proc
        start = time.monotonic()
        with self.assertRaises(GenTimeout):
            gen.generate(request, 0.2)
        self.assertLess(time.monotonic() - start, 6)
        self.assertIsNotNone(proc.poll())
        self.assertFalse(gen.loaded())
        gen.options = {"replies": [text]}
        gen.start(15)
        self.assertEqual(gen.generate(request, 5).text, text)

    def test_pipe_read_deadline_and_binary_frame(self):
        reader, writer = os.pipe()
        self.addCleanup(os.close, reader)
        self.addCleanup(os.close, writer)
        with self.assertRaises(ProtocolTimeout):
            read_frame(reader, 0.05)
        with os.fdopen(writer, "wb", closefd=False) as fh:
            write_frame(fh, {"bytes": bytes(range(256))})
        self.assertEqual(read_frame(reader, 1), {"bytes": bytes(range(256))})

    def test_blocked_request_write_is_bounded(self):
        gen = ChildGenerator("scripted", self.path)
        self.addCleanup(gen.kill)
        gen._proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     start_new_session=os.name != "nt",
                                     creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        proc = gen._proc
        start = time.monotonic()
        with self.assertRaises(GenTimeout):
            gen._call({"op": "ping", "payload": b"x" * (2 * 1024 * 1024)}, 0.2)
        self.assertLess(time.monotonic() - start, 6)
        self.assertIsNotNone(proc.poll())
        self.assertFalse(any(t.name == "model-pipe" and t.is_alive() for t in threading.enumerate()))

    def test_crashed_child_reaped(self):
        gen = ChildGenerator("scripted", self.path, options={"crash": True})
        self.addCleanup(gen.kill)
        gen.start(15)
        proc = gen._proc
        with self.assertRaises(GenCrashed):
            gen.generate(GenRequest("s", [], [], 1), 5)
        self.assertIsNotNone(proc.poll())
        self.assertFalse(gen.loaded())

    def test_gpu_lock_excludes_other_process_and_releases(self):
        path = self.path / "gpu.lock"
        code = ("from cashwatch.gpulock import gpu_reservation\ntry:\n"
                " with gpu_reservation('child', timeout_sec=0.1): pass\n"
                "except TimeoutError: raise SystemExit(7)\n")
        env = child_env({"CASHWATCH_GPU_LOCK": str(path)})
        with patch.object(gpulock, "LOCK_PATH", path):
            with gpulock.gpu_reservation("parent", timeout_sec=1):
                self.assertEqual(subprocess.run([sys.executable, "-c", code], env=env, timeout=10).returncode, 7)
            self.assertEqual(subprocess.run([sys.executable, "-c", code], env=env, timeout=10).returncode, 0)


if __name__ == "__main__":
    unittest.main()
