"""Egress audit hook for tests/offline (agent F). Loaded via PYTHONPATH only by the offline check.

Records every Python-level attempt to reach a non-loopback network address (socket connect/sendto,
DNS lookups) into the file named by CW_EGRESS_AUDIT as JSON lines {pid, event, target}. It observes
attempts even when the sandbox denies them. Native code that bypasses Python's socket module is not
seen here (the sandbox still blocks it)."""

import json
import os
import sys

_PATH = os.environ.get("CW_EGRESS_AUDIT")
_LOOP_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0", "::", ""}


def _loopback(host) -> bool:
    if host is None:
        return True
    if isinstance(host, bytes):
        host = host.decode(errors="replace")
    h = str(host).strip("[]").lower()
    return h in _LOOP_HOSTS or h.startswith("127.") or h.startswith("::ffff:127.")


def _record(event, target):
    try:
        with open(_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"pid": os.getpid(), "event": event, "target": str(target)[:200]}) + "\n")
    except OSError:
        pass


def _hook(event, args):
    try:
        if event in ("socket.connect", "socket.sendto", "socket.sendmsg"):
            addr = args[1] if len(args) > 1 else None
            if isinstance(addr, (str, bytes)):  # AF_UNIX path
                return
            if isinstance(addr, tuple) and addr and not _loopback(addr[0]):
                _record(event, addr[:2])
        elif event in ("socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyname_ex",
                       "socket.gethostbyaddr"):
            host = args[0] if args else None
            if not _loopback(host):
                _record(event, host)
    except Exception:  # noqa: BLE001 - an audit hook must never break the audited program
        pass


if _PATH:
    sys.addaudithook(_hook)
