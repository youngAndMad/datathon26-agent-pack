"""Rules lane (agent D): offline normative registry + deterministic tri-state rules engine.

Factories (frozen names, see cashwatch.ports):
  load_registry(path, *, require_approved, allow_test=False) -> Registry (RuleRegistry)
  create_rules(registry) -> Rules (RulesPort); create one per job, notes() are per instance.
"""

from __future__ import annotations

from cashwatch.ports import RuleRegistry, RulesPort
from cashwatch.rules.registry import Registry, load_registry

__all__ = ["Registry", "load_registry", "create_rules"]


def create_rules(registry: RuleRegistry) -> RulesPort:
    from cashwatch.rules.engine import Rules

    if not isinstance(registry, Registry):
        raise TypeError("create_rules requires a registry from cashwatch.rules.load_registry")
    return Rules(registry)
