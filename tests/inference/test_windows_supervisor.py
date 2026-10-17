"""Windows venv launcher regression: a timeout must stop its interpreter too."""
import os
from pathlib import Path
import unittest
import psutil
from cashwatch.inference.protocol import GenRequest
from cashwatch.inference.supervisor import ChildGenerator, GenTimeout


@unittest.skipUnless(os.name == 'nt', 'Windows launch tree')
class WindowsSupervisorTests(unittest.TestCase):
    def test_timeout_removes_descendants_and_can_restart(self):
        gen = ChildGenerator('scripted', Path.cwd(), options={'gen_sleep_sec': 30})
        request = GenRequest(system='synthetic', segments=[('text', 'test')], images=[], max_tokens=4)
        try:
            gen.start(20)
            root = psutil.Process(gen.pid)
            processes = [root] + root.children(recursive=True)
            self.assertGreaterEqual(len(processes), 2, 'Exercise the actual Windows venv interpreter child')
            with self.assertRaises(GenTimeout):
                gen.generate(request, 0.3)
            self.assertFalse(gen.alive())
            self.assertTrue(all(not p.is_running() for p in processes))
            gen.options = {'replies': ['synthetic-ok']}
            gen.start(20)
            self.assertEqual(gen.generate(request, 5).text, 'synthetic-ok')
        finally:
            gen.close()


if __name__ == '__main__':
    unittest.main()
