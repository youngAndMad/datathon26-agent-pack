"""Only self-created sleeping processes are used; never touches app launch records."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest
import uuid
from unittest.mock import patch

import psutil

spec = importlib.util.spec_from_file_location(
    "windows_stop_tree", Path(__file__).resolve().parents[1] / "scripts/windows_stop_tree.py")
stop = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stop)


@unittest.skipUnless(os.name == "nt", "Windows process APIs")
class StopTreeTests(unittest.TestCase):
    def launch(self, tree=False):
        code = "import time; time.sleep(60)"
        if tree:
            code = f"import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',{code!r}]); time.sleep(60)"
            code = f"import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',{code!r}]); time.sleep(60)"
        process = subprocess.Popen([sys.executable, "-c", code],
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        self.addCleanup(self.cleanup, process)
        return process

    @staticmethod
    def cleanup(process):
        if process.poll() is not None:
            return
        p = psutil.Process(process.pid)
        children = p.children(recursive=True)
        for node in reversed([p] + children):
            try:
                node.kill()
                node.wait(timeout=5)
            except psutil.NoSuchProcess:
                pass
        process.wait(timeout=5)

    def test_stops_synthetic_tree_leaves_unrelated_process(self):
        unrelated = self.launch()
        root = self.launch(tree=True)
        until = time.monotonic() + 8
        while time.monotonic() < until:
            descendants = psutil.Process(root.pid).children(recursive=True)
            if len(descendants) >= 2:
                break
            time.sleep(0.05)
        self.assertGreaterEqual(len(descendants), 2)
        identities = [(p.pid, stop.start_ticks(p.pid)) for p in descendants]
        count = stop.stop_tree(root.pid, stop.start_ticks(root.pid))
        self.assertGreaterEqual(count, 3)
        root.wait(timeout=5)
        self.assertIsNone(unrelated.poll())
        for pid, ticks in identities:
            self.assertIsNone(stop.same_process(pid, ticks))

    def test_identity_mismatch_preserves_process(self):
        process = self.launch()
        with self.assertRaisesRegex(RuntimeError, "reused PID"):
            stop.stop_tree(process.pid, stop.start_ticks(process.pid) + 1)
        self.assertIsNone(process.poll())

    def test_enumeration_failure_resumes_root(self):
        process = self.launch()
        with patch.object(psutil.Process, "children", side_effect=psutil.AccessDenied(process.pid)):
            with self.assertRaises(psutil.AccessDenied):
                stop.stop_tree(process.pid, stop.start_ticks(process.pid))
        self.assertIsNone(process.poll())
        self.assertNotEqual(psutil.Process(process.pid).status(), psutil.STATUS_STOPPED)

    def test_missing_root_is_not_claimed_clean(self):
        process = self.launch()
        ticks = stop.start_ticks(process.pid)
        process.kill()
        process.wait(timeout=5)
        with self.assertRaisesRegex(RuntimeError, "absent"):
            stop.stop_tree(process.pid, ticks)

    def test_powershell_removes_only_successful_launch_record(self):
        project = Path(__file__).resolve().parents[1]
        directory = project / "tests" / ("windows-stop-" + uuid.uuid4().hex)
        directory.mkdir()
        record = directory / "api.windows.json"
        process = self.launch()
        powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        command = [str(powershell), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                   str(project / "deploy/stop.ps1"), "-RunDirectory", str(directory)]
        try:
            ticks = stop.start_ticks(process.pid)
            record.write_text(json.dumps({"id": process.pid, "start_ticks": str(ticks + 1)}), encoding="utf-8")
            failed = subprocess.run(command, capture_output=True, timeout=20)
            self.assertNotEqual(failed.returncode, 0)
            self.assertTrue(record.exists())
            self.assertIsNone(process.poll())
            record.write_text(json.dumps({"id": process.pid, "start_ticks": str(ticks)}), encoding="utf-8")
            done = subprocess.run(command, capture_output=True, timeout=20)
            self.assertEqual(done.returncode, 0, done.stderr.decode(errors="replace"))
            self.assertFalse(record.exists())
            process.wait(timeout=5)
        finally:
            record.unlink(missing_ok=True)
            directory.rmdir()


if __name__ == "__main__":
    unittest.main()
