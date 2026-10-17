"""Offline checks for upstream rules/reporting integration on Windows."""
from pathlib import Path
import unittest

from cashwatch.rules import load_registry
from cashwatch.reporting import create_reporting
from cashwatch.errors import PipelineError

ROOT = Path(__file__).resolve().parents[1]


class MergedRulesTests(unittest.TestCase):
    def test_synthetic_registry_is_ready_only_when_explicitly_allowed(self):
        registry = load_registry(ROOT / 'rules/registry.test.yaml', require_approved=False, allow_test=True)
        self.assertEqual(registry.health_errors(), [])
        self.assertTrue(registry.approved_rule_ids())
        self.assertIsNotNone(create_reporting(registry))
        with self.assertRaises(PipelineError):
            load_registry(ROOT / 'rules/registry.test.yaml', require_approved=True, allow_test=False)

    def test_production_still_requires_human_approval(self):
        registry = load_registry(ROOT / 'rules/registry.yaml', require_approved=True, allow_test=False)
        self.assertFalse(registry.approved_rule_ids())
        self.assertTrue(any('no approved rules' in e for e in registry.health_errors()))
        self.assertFalse(any('sha256 mismatch' in e for e in registry.health_errors()), registry.health_errors())


if __name__ == '__main__':
    unittest.main()
