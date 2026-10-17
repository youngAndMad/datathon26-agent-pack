# Windows storage diagnostic

Owned changes: `scripts/windows_storage.py`, `src/cashwatch/storage/store.py`,
`tests/storage/test_windows_storage.py`, and this handoff. The diagnostic creates a fresh synthetic
directory inside the repository with inherited ACL, checks SQLite WAL writes
and reads, closes the connection, and removes only its synthetic files and
empty directory. It does not inspect video, secrets, existing database records,
or modify ACLs or application settings.

Verification: `.venv-windows\Scripts\python.exe scripts\windows_storage.py`
completed successfully: `Inherited workspace directory: SQLite WAL write/read
succeeded`.

`icacls .` showed inherited Full control for the current user, SYSTEM and
Administrators, and inherited Modify for two existing SID entries. No Everyone
or generic Users entry was shown. Those entries were only inspected.

The store creates root, database, temporary, job, work and output directories
using `mode=0o700`. On current Windows Python this requests a restrictive ACL;
this is relevant to restricted-process access failures. Precreating only the
database directory cannot fix later per-job directory creation. No private
directory ACL was relaxed.

The store now supports an explicit `CASHWATCH_WINDOWS_TEST_INHERIT_ACL=1`
opt-in. It refuses this flag outside Windows test mode and requires an existing
preprovisioned data root. Only under this flag, newly created synthetic runtime
subdirectories inherit the root ACL. Defaults, production and POSIX still request
0700; existing directories and their ACLs are never modified.

Integration requirement: launcher must provision a separate synthetic-only
runtime root, verify its inherited ACL has no broad grants, and set this flag
only in explicitly selected Windows test mode. Do not point it at existing
private data directories. This is not a production permissions fallback.

Verification: `.venv-windows\Scripts\python.exe -m unittest discover -s tests\storage
-p test_windows_storage.py -v` passed all 3 tests: production refusal,
missing-root refusal, and synthetic SQLite/upload/claim/work/output writes.
Test fixtures used inherited workspace ACLs and deleted their own synthetic
tree after containment checks. These tests are not full model inference.

No latency or memory benchmark was performed. No config, dependency version,
shared contract, or launch behavior was changed.
