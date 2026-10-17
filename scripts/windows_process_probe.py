"""Read-only Toolhelp descendants of recorded Cashwatch launch identities."""
import ctypes
from ctypes import wintypes as w
import json
import importlib.util
from pathlib import Path


class Entry(ctypes.Structure):
    _fields_ = [("dwSize", w.DWORD), ("cntUsage", w.DWORD),
                ("th32ProcessID", w.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", w.DWORD), ("cntThreads", w.DWORD),
                ("th32ParentProcessID", w.DWORD), ("pcPriClassBase", w.LONG),
                ("dwFlags", w.DWORD), ("szExeFile", w.WCHAR * 260)]


k = ctypes.WinDLL("kernel32", use_last_error=True)
k.CreateToolhelp32Snapshot.argtypes = [w.DWORD, w.DWORD]
k.CreateToolhelp32Snapshot.restype = w.HANDLE
k.Process32FirstW.argtypes = [w.HANDLE, ctypes.POINTER(Entry)]
k.Process32NextW.argtypes = [w.HANDLE, ctypes.POINTER(Entry)]
k.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
k.OpenProcess.restype = w.HANDLE
k.GetProcessTimes.argtypes = [w.HANDLE] + [ctypes.POINTER(w.FILETIME)] * 4
k.CloseHandle.argtypes = [w.HANDLE]
k.QueryFullProcessImageNameW.argtypes = [w.HANDLE, w.DWORD, w.LPWSTR, ctypes.POINTER(w.DWORD)]


def identity(pid):
    handle = k.OpenProcess(0x1000, False, pid)
    if not handle:
        return {"error": ctypes.get_last_error()}
    try:
        times = [w.FILETIME() for _ in range(4)]
        if not k.GetProcessTimes(handle, *(ctypes.byref(x) for x in times)):
            return {"error": ctypes.get_last_error()}
        ticks = ((times[0].dwHighDateTime << 32) | times[0].dwLowDateTime) + 504911232000000000
        buf = ctypes.create_unicode_buffer(32768)
        size = w.DWORD(len(buf))
        exe = buf.value if k.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)) else None
        return {"start_ticks": str(ticks), "exe": exe}
    finally:
        k.CloseHandle(handle)


snapshot = k.CreateToolhelp32Snapshot(2, 0)
if snapshot == ctypes.c_void_p(-1).value:
    raise ctypes.WinError(ctypes.get_last_error())
entries = {}
try:
    row = Entry()
    row.dwSize = ctypes.sizeof(row)
    more = k.Process32FirstW(snapshot, ctypes.byref(row))
    while more:
        entries[row.th32ProcessID] = {"pid": row.th32ProcessID, "parent": row.th32ParentProcessID,
                                     "name": row.szExeFile}
        more = k.Process32NextW(snapshot, ctypes.byref(row))
finally:
    k.CloseHandle(snapshot)

root = Path(__file__).resolve().parents[1]
if importlib.util.find_spec("psutil"):
    import psutil
else:
    psutil = None
for entry in entries.values():
    if entry["name"].lower() not in ("python.exe", "pythonw.exe"):
        continue
    detail = identity(entry["pid"])
    exe = detail.get("exe")
    if not exe or not Path(exe).is_relative_to(root):
        continue
    module = "unclassified"
    if psutil:
        try:
            args = psutil.Process(entry["pid"]).cmdline()
            for target in ("cashwatch.pipeline.worker", "cashwatch.inference.child", "cashwatch.api.main"):
                if any(args[i] == "-m" and args[i + 1] == target for i in range(len(args) - 1)):
                    module = target
                    break
        except (psutil.Error, OSError):
            module = "access_denied"
    print(json.dumps({"project_python": True, "pid": entry["pid"], "parent": entry["parent"],
                      "start_ticks": detail.get("start_ticks"), "module": module,
                      "classification_available": psutil is not None}))
for name in ("api", "worker"):
    path = root / "data/windows-local/run" / f"{name}.windows.json"
    if not path.exists():
        continue
    saved = json.loads(path.read_text(encoding="utf-8-sig"))
    pending = [(saved["id"], int(saved["start_ticks"]), 0)]
    seen = set()
    print(json.dumps({"service": name, "saved": saved, "root_alive": saved["id"] in entries}))
    while pending:
        parent, parent_ticks, depth = pending.pop(0)
        if parent in seen:
            continue
        seen.add(parent)
        if parent in entries and depth == 0:
            print(json.dumps({**entries[parent], **identity(parent), "depth": depth}))
        for child in entries.values():
            if child["parent"] != parent:
                continue
            detail = identity(child["pid"])
            plausible = "start_ticks" in detail and int(detail["start_ticks"]) >= parent_ticks
            print(json.dumps({**child, **detail, "depth": depth + 1, "chronology_ok": plausible}))
            if plausible:
                pending.append((child["pid"], int(detail["start_ticks"]), depth + 1))
