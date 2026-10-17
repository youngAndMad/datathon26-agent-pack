#!/usr/bin/env python3
"""Runner behind tests/offline/verify_offline.sh (agent F). See that script for usage.

Items:
  sandbox_self_test   the deny profile blocks IP egress/DNS/UDP and allows loopback (proves the
                      enforcement mechanism works on this host before relying on it)
  t21_missing_model   doctor fails clearly and the service never becomes ready with a missing model
  t21_missing_registry doctor fails clearly with a missing rule registry
  t20_cold_start_1/2  fresh sandboxed worker+API reach /readyz and complete a real local job on a
                      synthetic clip; repeated after a full cold process restart
  t20_no_denials      no sandbox network-outbound denial was logged for the service processes
Results are printed as JSON; no media, scene text or secrets are emitted.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import math
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tests.integration.harness import (  # noqa: E402
    PY, TEST_REGISTRY, Service, load_qa_client, pending_components, synthetic_clip, tiny_mov,
)

AUDIT_DIR = Path(__file__).resolve().parent / "audit"

PROBE = r"""
import socket, json
res = {}
srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(1)
c = socket.socket(); c.settimeout(2)
try: c.connect(srv.getsockname()); res["loopback"] = "ok"
except OSError as e: res["loopback"] = f"err:{e.errno}"
c.close()
for host, port in (("1.1.1.1", 443), ("8.8.8.8", 53)):
    s = socket.socket(); s.settimeout(3)
    try: s.connect((host, port)); res[f"tcp_{host}"] = "CONNECTED"
    except PermissionError: res[f"tcp_{host}"] = "denied"
    except OSError as e: res[f"tcp_{host}"] = f"err:{e.errno}"
    s.close()
try: socket.getaddrinfo("example.com", 443); res["dns"] = "RESOLVED"
except OSError: res["dns"] = "failed"
u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
try: u.sendto(b"x", ("8.8.8.8", 53)); res["udp"] = "SENT"
except PermissionError: res["udp"] = "denied"
except OSError as e: res["udp"] = f"err:{e.errno}"
print(json.dumps(res))
"""


class Report:
    def __init__(self) -> None:
        self.items: list[dict] = []

    def add(self, item: str, status: str, note: str, **extra) -> None:
        self.items.append({"item": item, "status": status, "note": note, **extra})
        print(f"{status:8s} {item}: {note}", flush=True)

    def exit_code(self) -> int:
        st = {i["status"] for i in self.items}
        if "FAIL" in st:
            return 1
        if st & {"BLOCKED", "PENDING"}:
            return 2
        return 0


def sandbox_self_test(profile: Path, rep: Report) -> bool:
    r = subprocess.run(["sandbox-exec", "-f", str(profile), PY, "-c", PROBE],
                       capture_output=True, text=True, timeout=60)
    try:
        res = json.loads(r.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        rep.add("sandbox_self_test", "FAIL", f"probe did not run under sandbox-exec (rc={r.returncode})")
        return False
    ok = (res.get("loopback") == "ok" and res.get("tcp_1.1.1.1") == "denied"
          and res.get("tcp_8.8.8.8") == "denied" and res.get("dns") == "failed" and res.get("udp") == "denied")
    rep.add("sandbox_self_test", "PASS" if ok else "FAIL",
            "loopback allowed; TCP/UDP egress and DNS denied" if ok else f"unexpected probe result {res}",
            probe=res)
    return ok


def audit_self_test(profile: Path, tmp: Path, rep: Report) -> bool:
    """Positive control: the audit hook must record a sandboxed non-loopback attempt (and only that)."""
    import os
    f = tmp / "audit-selftest.jsonl"
    env = {**{k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "TMPDIR")},
           "PYTHONPATH": str(AUDIT_DIR), "CW_EGRESS_AUDIT": str(f)}
    code = ("import socket\nsrv=socket.socket(); srv.bind(('127.0.0.1',0)); srv.listen(1)\n"
            "socket.create_connection(srv.getsockname(), 1).close()\n"
            "try: socket.create_connection(('192.0.2.1', 443), 1)\nexcept OSError: pass\n")
    subprocess.run(["sandbox-exec", "-f", str(profile), PY, "-c", code], env=env, timeout=60,
                   capture_output=True)
    lines = f.read_text().splitlines() if f.exists() else []
    ok = bool(lines) and all("192.0.2.1" in ln for ln in lines)
    rep.add("egress_audit_self_test", "PASS" if ok else "FAIL",
            "audit hook records non-loopback attempts and ignores loopback" if ok
            else f"audit hook control failed ({len(lines)} records)")
    return ok


def doctor(env_over: dict[str, str], profile: Path | None) -> tuple[int, str]:
    import os
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "TMPDIR", "LANG")}
    env.update({"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"})
    env.update(env_over)
    cmd = ([ "sandbox-exec", "-f", str(profile)] if profile else []) + [PY, "-m", "cashwatch.pipeline.doctor"]
    r = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True, timeout=180)
    return r.returncode, (r.stdout + r.stderr)


def t21(profile: Path, backend: str, tmp: Path, rep: Report) -> None:
    missing = tmp / "does-not-exist"
    base = {"CASHWATCH_MODE": "test", "CASHWATCH_BACKEND": backend if backend != "fake" else "mlx",
            "CASHWATCH_DATA_DIR": str(tmp / "doctor-data"), "CASHWATCH_REGISTRY_PATH": str(TEST_REGISTRY)}
    # Positive control: with the real artifacts the same doctor line must NOT fail, otherwise a FAIL
    # under the missing artifact proves nothing (e.g. the loader itself is not importable yet).
    try:
        _, ctl = doctor(base, profile)
    except (subprocess.TimeoutExpired, OSError):
        ctl = ""
    ctl_model_ok = any("model" in ln.lower() and ln.lstrip().startswith("OK") for ln in ctl.splitlines())
    ctl_reg_ok = any("regist" in ln.lower() and ln.lstrip().startswith("OK") for ln in ctl.splitlines())
    try:
        rc, out = doctor({**base, "CASHWATCH_MODEL_PATH": str(missing / "model")}, profile)
    except (subprocess.TimeoutExpired, OSError) as e:
        rep.add("t21_missing_model_doctor", "FAIL", f"doctor did not finish ({type(e).__name__})")
    else:
        line = next((ln for ln in out.splitlines() if "model" in ln.lower() and "FAIL" in ln), "")
        if not ctl_model_ok:
            rep.add("t21_missing_model_doctor", "PENDING", "control run with the real model path does not "
                    "report model OK; missing-model result inconclusive")
        else:
            rep.add("t21_missing_model_doctor", "PASS" if rc != 0 and line else "FAIL",
                    f"doctor rc={rc}; {'clear model FAIL line' if line else 'no clear model diagnostic'}")
    try:
        rc, out = doctor({**base, "CASHWATCH_REGISTRY_PATH": str(missing / "registry.yaml")}, profile)
    except (subprocess.TimeoutExpired, OSError) as e:
        rep.add("t21_missing_registry_doctor", "FAIL", f"doctor did not finish ({type(e).__name__})")
    else:
        line = next((ln for ln in out.splitlines() if "regist" in ln.lower() and "FAIL" in ln), "")
        if not ctl_reg_ok:
            rep.add("t21_missing_registry_doctor", "PENDING", "control run with the test registry does not "
                    "report registry OK (loader pending?); missing-registry result inconclusive")
        else:
            rep.add("t21_missing_registry_doctor", "PASS" if rc != 0 and line else "FAIL",
                    f"doctor rc={rc}; {'clear registry FAIL line' if line else 'no clear registry diagnostic'}")

    if pending_components():
        rep.add("t21_missing_model_readyz", "PENDING", f"pending integration: {', '.join(pending_components())}")
        return
    svc = Service(tmp / "t21svc", mode="test", backend=backend if backend != "fake" else "mlx",
                  registry=TEST_REGISTRY, extra_env={"CASHWATCH_MODEL_PATH": str(missing / "model")})
    wrap = ["sandbox-exec", "-f", str(profile)]
    try:
        svc.start_worker(wrap)
        svc.start_api(wrap)
        ready = svc.wait_ready(30)
        rep.add("t21_missing_model_readyz", "FAIL" if ready else "PASS",
                "service became ready without model files" if ready else
                "readyz never 200 within 30 s with a missing model directory")
    finally:
        svc.stop()


def denials_since(start_epoch: float, pids: set[int]) -> int | None:
    """Count kernel Sandbox network-outbound denials since start_epoch for the service processes
    (by pid, or by a python process name: supervisor children have pids we never saw).
    Returns None if the unified log cannot be read."""
    import re
    since = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(math.ceil(start_epoch)))
    try:
        r = subprocess.run(["log", "show", "--start", since, "--style", "compact", "--predicate",
                            'sender == "Sandbox" AND eventMessage CONTAINS "network-outbound"'],
                           capture_output=True, text=True, timeout=120)
    except (subprocess.TimeoutExpired, OSError):
        return None
    if r.returncode != 0:
        return None
    rx = re.compile(r"Sandbox: (\S+)\((\d+)\) deny\(\d+\) network-outbound")
    n = 0
    for ln in r.stdout.splitlines():
        m = rx.search(ln)
        if m and (int(m.group(2)) in pids or m.group(1).lower().startswith("python")):
            n += 1
    return n


def cold_start_round(idx: int, svc: Service, profile: Path, clip: Path, qa, rep: Report,
                     ready_timeout: float, pids: set[int], require_source: bool) -> None:
    wrap = ["sandbox-exec", "-f", str(profile)]
    t0 = time.monotonic()
    w = svc.start_worker(wrap)
    a = svc.start_api(wrap)
    pids.update({w.pid, a.pid})
    if not svc.wait_ready(ready_timeout):
        rep.add(f"t20_cold_start_{idx}", "FAIL", f"not ready within {ready_timeout:.0f}s under denied egress",
                log_tail=svc.log_tail(10).replace(svc.key, "<redacted>"))
        svc.stop()
        return
    ready_s = round(time.monotonic() - t0, 1)
    res = qa.run_job(svc.base_url, svc.key, clip, timeout_sec=300, require_official_source=require_source)
    ok = res.terminal_status == "done" and not res.errors
    rep.add(f"t20_cold_start_{idx}", "PASS" if ok else "FAIL",
            f"ready in {ready_s}s; job {res.terminal_status} in {res.upload_to_terminal_sec}s"
            + ("" if ok else f"; errors={res.errors[:3]} code={res.error_code}"),
            ready_sec=ready_s, upload_to_terminal_sec=res.upload_to_terminal_sec)
    svc.stop()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", type=Path, required=True)
    ap.add_argument("--backend", default="mlx", choices=["mlx", "transformers", "fake"])
    ap.add_argument("--skip-model", action="store_true")
    ap.add_argument("--clip", default="marker_violation_40s")
    ap.add_argument("--ready-timeout", type=float, default=300.0)
    ap.add_argument("--json-out", type=Path)
    a = ap.parse_args(argv)
    rep = Report()
    started = time.time()
    with tempfile.TemporaryDirectory(prefix="cw-offline-") as td:
        tmp = Path(td)
        if not sandbox_self_test(a.profile, rep):
            rep.add("t20", "FAIL", "sandbox enforcement not demonstrated; denied-egress result would be meaningless")
        else:
            t21(a.profile, a.backend, tmp, rep)
            if a.skip_model:
                rep.add("t20_cold_start", "BLOCKED", "skipped by --skip-model")
            elif pending_components():
                rep.add("t20_cold_start", "PENDING", f"pending integration: {', '.join(pending_components())}")
            elif not TEST_REGISTRY.exists():
                rep.add("t20_cold_start", "PENDING", "rules/registry.test.yaml missing (production registry has "
                                                     "zero approved rules, D011)")
            else:
                qa = load_qa_client()
                clip = synthetic_clip(a.clip, tmp / "media") or tiny_mov(tmp / "media" / "tiny.mov")
                audit = tmp / "egress-audit.jsonl"
                audit_ok = audit_self_test(a.profile, tmp, rep)
                svc = Service(tmp / "svc", mode="test", backend=a.backend, registry=TEST_REGISTRY,
                              extra_env={"PYTHONPATH": str(AUDIT_DIR), "CW_EGRESS_AUDIT": str(audit)})
                pids: set[int] = set()
                time.sleep(1.5)  # `log show --start` has 1 s granularity: keep the self-tests out
                started = time.time()
                lock = contextlib.nullcontext()
                if a.backend != "fake":
                    from cashwatch.gpulock import gpu_reservation
                    lock = gpu_reservation("F-verify-offline", timeout_sec=1800)
                with lock:
                    for i in (1, 2):  # second round = full cold process restart
                        cold_start_round(i, svc, a.profile, clip, qa, rep, a.ready_timeout, pids,
                                         require_source=True)
                recs = audit.read_text().splitlines() if audit.exists() else []
                if not audit_ok:
                    rep.add("t20_no_egress_attempts", "FAIL", "audit hook not demonstrated; cannot certify")
                else:
                    targets = sorted({json.loads(r)["event"] + " " + json.loads(r)["target"] for r in recs})
                    rep.add("t20_no_egress_attempts", "PASS" if not recs else "FAIL",
                            "no Python-level non-loopback connect/sendto/DNS attempt by service processes"
                            if not recs else f"{len(recs)} attempt(s): {targets[:5]}")
                # Secondary, best effort: kernel sandbox denials (often not logged for sandbox-exec).
                n = denials_since(started, pids)
                if n:
                    rep.add("t20_kernel_denials", "FAIL", f"{n} kernel sandbox network-outbound denials for service processes")
                if a.backend == "fake":
                    rep.add("t20_real_inference", "BLOCKED", "fake backend: plumbing only, not a T20 pass")
    summary = {"items": rep.items, "exit_code": rep.exit_code(), "backend": a.backend}
    if a.json_out:
        a.json_out.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"exit_code": summary["exit_code"],
                      "counts": {s: sum(1 for i in rep.items if i["status"] == s)
                                 for s in ("PASS", "FAIL", "BLOCKED", "PENDING")}}))
    return rep.exit_code()


if __name__ == "__main__":
    sys.exit(main())
