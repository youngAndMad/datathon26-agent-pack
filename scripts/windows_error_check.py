"""Read only operational errors, without media, names or generated descriptions."""
import json
import re
import sqlite3
from pathlib import Path

root = Path(__file__).resolve().parents[1] / 'data/windows-local'
for name in ('worker.stderr.log', 'api.stderr.log', 'model-child.log'):
    path = root / 'logs' / name
    if not path.exists():
        continue
    text = path.read_text(encoding='utf-8', errors='replace')
    print(name, 'bytes=', path.stat().st_size)
    progress = re.findall(r'Loading weights:\s+(\d+)%', text)
    if progress:
        print('last_weight_load_percent=', int(progress[-1]))
    packing = re.findall(r'int8_packing layers=(\d+)', text)
    if packing:
        print('last_int8_packing_layers=', int(packing[-1]))
    generation = re.findall(r'int8_generation tokens=(\d+) elapsed_sec=([\d.]+)', text)
    for count, elapsed in generation[-3:]:
        print('int8_generation tokens=', count, 'elapsed_sec=', elapsed)
    failures = re.findall(r'generation_failure kind=(\w+) stage=([\w-]+) call_limit_sec=([\d.]+)', text)
    for kind, stage, cap in failures[-5:]:
        print('generation_failure', 'kind=', kind, 'stage=', stage, 'limit_sec=', cap)
    # Only known operational messages; never print arbitrary exceptions/prompts.
    matches = re.findall(r'[^\r\n]*(?:job failed code=[a-z_]+|worker core exited rc=[-\d]+; restarting|warmup failed: \w+|startup failed: \w+)', text)
    for line in matches[-12:]:
        print(line.lstrip('\x00'))
    for marker in ('MemoryError', 'out of memory', 'TimeoutError', 'PermissionError', 'RuntimeError'):
        count = text.count(marker)
        if count:
            print(marker, 'occurrences=', count)
db = root / 'db/cashwatch.sqlite3'
if db.exists():
    conn = sqlite3.connect(db.as_uri() + '?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    cols = {row[1] for row in conn.execute('PRAGMA table_info(jobs)')}
    allowed = ['job_id', 'state', 'created_at', 'updated_at', 'deadline_at', 'error_code', 'stage']
    selected = [col for col in allowed if col in cols]
    if selected:
        order = 'created_at' if 'created_at' in cols else 'rowid'
        for row in conn.execute('SELECT ' + ','.join(selected) + ' FROM jobs ORDER BY ' + order + ' DESC LIMIT 5'):
            print(json.dumps(dict(row)))
    print('job_column_names=', sorted(cols))
    conn.close()
