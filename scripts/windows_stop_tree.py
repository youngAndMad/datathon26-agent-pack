"""Stop only an identity-verified Windows launch tree; never hunt orphan processes."""
from __future__ import annotations
import argparse
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import sys
import psutil


def start_ticks(pid: int) -> int:
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        times = [wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
            raise ctypes.WinError(ctypes.get_last_error())
        return ((times[0].dwHighDateTime << 32) | times[0].dwLowDateTime) + 504911232000000000
    finally:
        kernel.CloseHandle(handle)


def same_process(pid: int, ticks: int) -> psutil.Process | None:
    if not psutil.pid_exists(pid):
        return None
    process = psutil.Process(pid)
    try:
        actual = start_ticks(pid)
    except (OSError, psutil.NoSuchProcess):
        if not psutil.pid_exists(pid):
            return None
        raise
    if actual != ticks:
        raise RuntimeError(f"Refusing reused PID {pid}")
    return process


def stop_tree(pid: int, ticks: int) -> int:
    if os.name != "nt":
        raise RuntimeError("Windows only")
    if pid == os.getpid():
        raise RuntimeError("Refusing to stop this helper")
    if same_process(pid, ticks) is None:
        raise RuntimeError("Tracked root is absent; descendant cleanup cannot be verified")
    pending = [(pid, ticks)]
    captured: list[tuple[int, int]] = []
    seen: set[int] = set()
    try:
        # Freeze ancestors before enumerating immediate children. Complete the
        # entire snapshot before terminating anything, so supervisors cannot respawn.
        while pending:
            node_pid, node_ticks = pending.pop(0)
            if node_pid in seen:
                continue
            seen.add(node_pid)
            process = same_process(node_pid, node_ticks)
            if process is None:
                raise RuntimeError(f"PID {node_pid} exited before tree enumeration completed")
            process.suspend()
            captured.append((node_pid, node_ticks))
            for child in process.children(recursive=False):
                try:
                    child_ticks = start_ticks(child.pid)
                except (OSError, psutil.NoSuchProcess):
                    if not psutil.pid_exists(child.pid):
                        continue
                    raise
                if child_ticks < node_ticks:
                    raise RuntimeError("Descendant chronology mismatch")
                pending.append((child.pid, child_ticks))
        stopped = 0
        for node_pid, node_ticks in reversed(captured):
            process = same_process(node_pid, node_ticks)
            if process is None:
                continue
            process.kill()
            process.wait(timeout=10)
            stopped += 1
        return stopped
    finally:
        # A failed enumeration or stop must not strand suspended survivors.
        for node_pid, node_ticks in reversed(captured):
            try:
                process = same_process(node_pid, node_ticks)
                if process is not None:
                    process.resume()
            except (OSError, psutil.Error, RuntimeError):
                pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record", required=True, type=Path)
    args = parser.parse_args()
    try:
        saved = json.loads(args.record.read_text(encoding="utf-8-sig"))
        pid, ticks = int(saved["id"]), int(saved["start_ticks"])
        if pid <= 0 or ticks <= 0:
            raise ValueError("Invalid launch identity")
        count = stop_tree(pid, ticks)
    except (OSError, psutil.Error, RuntimeError, ValueError, KeyError) as exc:
        print(f"Tree cleanup failed: {exc}", file=sys.stderr)
        return 1
    print(f"Stopped {count} identity-verified processes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
