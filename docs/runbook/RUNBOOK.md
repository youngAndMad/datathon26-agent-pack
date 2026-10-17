# cashwatch runbook

Single machine, local inference only. Measured host so far: Apple M4 Pro, 14 cores, 48 GB unified
memory, macOS 26.6 (plan.md). Nothing here sends video, frames or descriptions off the machine.

Commands are run from the repository root. `make` targets are stable names (spec §14).

## 1. Preparation (online, once, explicit)

| Step | Command | Network |
|---|---|---|
| Python env + frontend deps | `make bootstrap` | yes (PyPI, npm) |
| Model weights (pinned revisions) | `make fetch-models` | yes (Hugging Face) |
| Frontend build | `make frontend` | no |
| Synthetic fixtures | `make fixtures` | no |
| Wheelhouse for another machine | `bash deploy/build_offline.sh --fetch-wheels [--with-models]` | yes (PyPI) |
| TLS certificate (public host only) | Caddy obtains it on first start | yes (ACME) |

After this, no command downloads anything. The worker sets `HF_HUB_OFFLINE=1`,
`TRANSFORMERS_OFFLINE=1` and `HF_HUB_DISABLE_TELEMETRY=1` itself; `deploy/start.sh` also exports
them and unsets cloud-provider credential variables.

## 2. Offline install on a target machine

1. `make build-offline` on the prepared machine writes `dist/offline-bundle/` (source tree without
   `data/`, `secrets/`, media or private evaluation files; `requirements.lock.txt` with hashes;
   `frontend/dist`; `models.sha256`; `BUILD_INFO.txt`; `MANIFEST.sha256`). Add `--with-models`
   to copy the model directory (APFS clone when possible), `--fetch-wheels` for a wheelhouse.
   Exit status 1 means the bundle is incomplete for release (e.g. `models/manifest.json` missing);
   the reasons are printed.
2. Copy the bundle; on the target: `shasum -a 256 -c MANIFEST.sha256`.
3. Python environment (see `BUNDLE_README.txt` in the bundle):
   * with a wheelhouse: `uv venv --python 3.12 .venv && uv pip install --offline --no-index --find-links wheelhouse --require-hashes -r requirements.lock.txt && uv pip install --offline --no-index --find-links wheelhouse --no-deps .`
   * same machine with the uv cache already populated: `uv sync --frozen --offline --extra mlx`
4. Models: copy into `models/`, then `cd models && shasum -a 256 -c ../models.sha256`.
5. `make doctor` — every FAIL must be fixed; BLOCKED rows are external decisions.

Limitations: the bundle targets the same OS/architecture (macOS arm64, CPython 3.12). FFmpeg/ffprobe
come from the OS package manager and are not bundled (license: see docs/submission/LICENSES.md).

## 3. Keys

* Generate: `bash deploy/gen_api_key.sh` → `secrets/jury_api_key`, 32 random bytes as 64 hex
  chars, mode 0600, directory 0700. Nothing secret is printed. Refuses to overwrite; rotate with
  `--force` (the old key stops working at the next API start).
* The key is only ever read from that file (`CASHWATCH_API_KEY_FILE`). Never put it in
  `deploy/cashwatch.env` (start.sh refuses inline secrets), the frontend, URLs, logs or chats.
* Deliver to the organizer only through their chosen private channel.
* Browser users type the key once; the UI keeps it in memory only.

## 4. Configuration

Copy `deploy/cashwatch.env.example` to `deploy/cashwatch.env` and edit. Production values:
`CASHWATCH_MODE=production`, `CASHWATCH_BACKEND=mlx`, `CASHWATCH_HOST=127.0.0.1`. Test mode
(`CASHWATCH_MODE=test`, optionally `CASHWATCH_BACKEND=fake`, `CASHWATCH_REGISTRY_PATH=rules/registry.test.yaml`)
is for synthetic plumbing only; results and the UI are labeled test.

## 5. Start / stop / status

```bash
make doctor      # environment, model, registry, storage, privacy diagnostics (no secrets)
make start       # bash deploy/start.sh: worker, then API; waits for /readyz (CASHWATCH_START_TIMEOUT, default 240 s)
make smoke       # real local inference via HTTP on a synthetic clip; refuses mock/test mode
make stop        # bash deploy/stop.sh: SIGTERM API then worker, SIGKILL after 20 s
```

`start.sh` refuses: production + fake backend; unknown mode/backend; missing or non-0600 key;
port in use; already running. On failure it prints the last log lines and stops what it started
(`CASHWATCH_KEEP_ON_FAIL=1` keeps processes for diagnosis). PID files: `data/run/{api,worker}.pid`.
Logs: `data/logs/{api,worker}.log` (request ids, durations, error codes; no keys, base64 or scene
text). Status: `curl -s localhost:8000/readyz`.

**Expected today:** production `make start` does not become ready because the production rule
registry has zero approved rules (D011) — this is the intended refusal, not a bug. A
production-ready start requires at least one approved rule (local visual validation, X1/X2).

### Keep running across reboots (macOS)

Templates in `deploy/launchd/`: replace `__REPO__` and `__DATA__`, copy to `~/Library/LaunchAgents/`,
`launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.cashwatch.worker.plist` (then api).
LaunchAgents (user session) are required for Metal/MLX. `KeepAlive.SuccessfulExit=false` restarts
crashes but not a clean refusal. Do not run launchd and `make start` at the same time.

### Prevent sleep and throttling during judging

* `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.cashwatch.caffeinate.plist`
  (runs `caffeinate -ims`) or simply `caffeinate -ims &` in a terminal that stays open.
* `sudo pmset -c sleep 0 disksleep 0` (on AC power); disable Low Power Mode; keep on AC.
* Lid closed only in clamshell mode with external display + power; otherwise keep the lid open.
* Disable automatic macOS updates/restarts for the event window; keep Wi-Fi/Ethernet stable.

## 6. Public HTTPS (team-controlled host) — BLOCKED on owner decision (X3)

Plan: the same machine runs the API on 127.0.0.1:8000 and Caddy (`deploy/Caddyfile.example`) on
:443 with a valid certificate for a team-controlled DNS name. Caddy streams uploads (no proxy
buffering), allows 300 MB bodies, strips client timing headers, and does not log headers.

Required decisions/actions by the owner (agents must not provision or pay):
1. Which host serves judging (this laptop vs another team machine) and its stable address.
2. A DNS name the team controls pointing at that address; inbound TCP 443 (and 80 for ACME
   issuance) allowed by the network/firewall/router.
3. Confirm no third-party TLS-terminating tunnel/CDN is used unless the organizer approves it in
   writing (they would receive footage).
4. Then: `caddy run --config deploy/Caddyfile`; from **another network** run
   `.venv/bin/python scripts/qa_jury_client.py --base-url https://<name> --file tests/fixtures/generated/marker_violation_40s.mov`
   (synthetic clip) and record latency (T25). Check the certificate expiry date covers the event.

## 7. Offline verification (T20/T21) — `make verify-offline`

`tests/offline/verify_offline.sh` runs a sandbox self-test, missing-artifact checks (doctor and
readiness with a missing model directory / registry, each with a positive control) and two cold
starts of worker+API under `sandbox-exec -f tests/offline/deny-egress.sb` (outbound network denied
except loopback and unix sockets; the mDNSResponder socket is denied so DNS cannot resolve),
each completing a real local job on a synthetic clip. It holds the GPU reservation while the real
model runs. Options: `--skip-model`, `--backend fake` (plumbing only; never reported as T20 PASS).

What it **proves**: during the test, the service processes and their children could not open
TCP/UDP connections to non-loopback addresses or resolve names via the system resolver, and still
reached readiness and produced a valid result from fresh processes using only local files. The
self-test demonstrates the policy is actually enforced on this host. Attempts are audited two
ways: a Python audit hook (`tests/offline/audit/sitecustomize.py`, injected via `PYTHONPATH`,
inherited by child processes, positive-controlled before use) records every non-loopback
connect/sendto/DNS lookup even when the sandbox blocks it; and kernel Sandbox denials for python
processes in the unified log (best effort). Either kind of attempt fails the run.

What it **does not prove**: attempts from native code that bypasses Python's socket module are
blocked but only visible if the kernel logs them; that other processes on the machine (browser, Caddy, OS daemons) send
nothing; egress through XPC/mach services that perform networking on a process's behalf (e.g.
`nsurlsessiond`, `trustd` OCSP) — Python/MLX do not normally use them, but the profile does not
deny mach-lookup; behaviour on code paths not exercised by the synthetic job; a cold *OS* page
cache (files may still be cached in RAM — process restart ≠ reboot); anything on Linux (use a
network namespace/`--network none` container there). `sandbox-exec` is deprecated by Apple but
functional on macOS 26; keep the self-test as the guard.

## 8. Troubleshooting

| Symptom | Check |
|---|---|
| `/readyz` 503 | `make doctor`; `data/logs/worker.log`: model missing, warm-up failed, registry has no approved rules, storage not writable |
| start: "forbidden in production" | `CASHWATCH_BACKEND=fake` set with production mode; unset it |
| 401 on every call | header must be exactly `X-API-Key`; key file content vs the key you send; no query-string keys |
| 429 + `Retry-After` | one active analysis per accelerator, no backlog (`CASHWATCH_ACTIVE_JOBS=1`, `CASHWATCH_MAX_QUEUE_LENGTH=0`); retry after the given seconds |
| 413 | file > 256 MiB (`CASHWATCH_MAX_FILE_BYTES`) or Caddy `max_size` |
| job `failed: deadline_exceeded` | analysis exceeded the 270 s internal deadline; check per-stage timings in the local report |
| job `failed: worker_lost` | worker crashed/killed; the sweeper fails or retries once within the original deadline |
| stuck processing | must not happen (sweeper); collect `data/logs/*` locally and report |
| port in use | `lsof -nP -iTCP:8000 -sTCP:LISTEN` |

Never paste logs, reports or evidence images into cloud tools; they may contain restricted data.

## 9. Retention and final cleanup (T26)

* Retention is `CASHWATCH_RETENTION_HOURS` (default 24 h, only if organizer rules allow). The
  API's periodic sweeper removes expired jobs; it must never run shorter than the jury retrieval
  window — confirm the allowed retention with the organizer (X4) before judging.
* Exclude `data/` from Time Machine/cloud backup: `sudo tmutil addexclusion -p "$PWD/data"`.
* Final cleanup at the event end (after the retrieval window):
  ```bash
  make stop
  rm -rf data/jobs data/tmp data/db data/logs data/run      # footage, frames, reports, DB
  rm -rf evaluation/*/private dist/offline-bundle            # private evaluation + bundle copies
  rm -f secrets/jury_api_key                                 # revoke the jury key
  ```
  `rm` unlinks files; on an SSD this is **not** guaranteed physical erasure. FileVault (full-disk
  encryption) must be on for at-rest protection; for stronger assurance erase the volume/device.

## 10. Benchmark (BLOCKED: no authorized dataset)

Local human/agent only: `evaluation/manifest/build_manifest.py` → private manifest →
`make benchmark` (`scripts/qa_benchmark.py`) against a production-mode service. Reports go to
`evaluation/reports/private/` (git-ignored). See evaluation/reports/README.md for metric definitions.
