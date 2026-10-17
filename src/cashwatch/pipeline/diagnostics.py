"""Aggregate pipeline attrition; no frame IDs, scene text or label inference."""
from collections import Counter
from copy import deepcopy


class DiagnosticCounters:
    def __init__(self, callback):
        self.callback = callback
        self.total = Counter()
        self.per_rule = {}

    def bump(self, name, count=1, rule=None):
        self.total[name] += count
        if rule is not None:
            self.per_rule.setdefault(rule, Counter())[name] += count

    def emit(self, phase):
        self.callback(deepcopy({"phase": phase, "counters": dict(self.total),
                                "per_rule": {rule: dict(counts) for rule, counts in self.per_rule.items()}}))
