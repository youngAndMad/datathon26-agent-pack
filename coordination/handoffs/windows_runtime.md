# Windows runtime handoff

Owned files changed:
- src/cashwatch/inference/protocol.py
- src/cashwatch/inference/supervisor.py
- src/cashwatch/inference/child.py
- src/cashwatch/gpulock.py
- tests/inference/test_windows_runtime.py

Completed Windows support: binary CRT stdin/stdout for pickle frames; PeekNamedPipe
deadline reads instead of socket-only Windows select; platform-specific child spawning
without a console window; bounded full exchange including blocked request writes;
Windows taskkill process-tree cleanup with direct kill fallback; msvcrt byte locking
for the shared accelerator reservation while retaining flock on POSIX.
The lock file is never truncated/replaced while held (owner text no longer persisted).
Supervisor/lock root discovery no longer requires importing Settings.

Contracts consumed: agents/C_LOCAL_INFERENCE.md, contracts/INTERNAL_CONTRACTS.md,
existing GenRequest/GenResult and child wire protocol. External schemas unchanged.

Verification on the actual Windows host:
`py -3.13 -m unittest discover -s tests/inference -p test_windows_runtime.py -v`
PASS: 5 tests, 3.892 seconds. Actual child processes and OS locks, scripted synthetic
backend only. Covers binary CR/LF/Ctrl-Z/NUL/Unicode, timeout and recovery, blocked
2 MiB pipe write timeout with no lingering transport thread, crash reaping, pipe
read timeout and competing-process reservation/release.

Earlier Python 3.11 attempt could not import pydantic. Python 3.13 default
TemporaryDirectory created restrictive ACLs inaccessible in this sandbox; tests
now create unique scratch folders inside checkout .tmp with ordinary mkdir and
remove only their own generated directory. Full existing pytest suite unavailable
because pytest is not installed. Existing test_supervisor.py `_gone` uses POSIX
`os.kill(pid, 0)` and should be ported before using that suite on Windows.

No GPU/model/video benchmark, latency guarantee, domain validation or peak-memory
measurement performed; test duration is test-harness timing only. No restricted
media used. Windows descendant cleanup is best effort via taskkill (bounded to
3 seconds with direct child kill fallback); no Job Object containment claimed.
POSIX path preserved but not executed on this Windows host.

Integration: run focused suite in the final installed runtime and full pytest
suite once dependencies are available. No manifest/API/backend changes requested.
