"""Operational resource summary; no command lines or media content."""
import json
import psutil

memory = psutil.virtual_memory()
print(json.dumps({"total_gib": round(memory.total / 2**30, 2),
                  "available_gib": round(memory.available / 2**30, 2),
                  "logical_cpus": psutil.cpu_count()}))
