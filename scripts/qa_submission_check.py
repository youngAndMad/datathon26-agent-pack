#!/usr/bin/env python3
"""Submission/release hygiene check (`make submission-check`, spec §12, §16). Read-only.

Prints PASS / FAIL / BLOCKED / WARN per item. Never prints secret values: a secret finding shows
only the file path, line number and rule name.

Exit: 0 all PASS/WARN; 1 any FAIL; 2 BLOCKED items but no FAIL.
"""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MEDIA_EXT = {".mov", ".mp4", ".m4v", ".avi", ".mkv", ".webm", ".heic", ".hevc", ".h264"}
WEIGHT_EXT = {".safetensors", ".gguf", ".bin", ".pt", ".pth", ".ckpt", ".npz", ".onnx", ".mlpackage"}
MAX_TRACKED_BYTES = 5 * 1024 * 1024
SECRET_RULES = [
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("hf_token", re.compile(r"\bhf_[A-Za-z0-9]{30,}\b")),
    ("openai_key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{20,}\b")),
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    ("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("assigned_secret", re.compile(
        r"(?i)\b[A-Z0-9_]*(?:api[_-]?key|secret|token|passw(?:or)?d)\b\s*[:=]\s*['\"]?([A-Za-z0-9+/=_\-]{24,})")),
]
# Values that are obviously placeholders/test material, not credentials.
PLACEHOLDER = re.compile(r"(?i)^(x+|k+|a+|0+|change[-_]?me.*|example.*|placeholder.*|your[-_].*|test.*|dummy.*|fake.*)$")


class Checks:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str]] = []

    def add(self, status: str, item: str, note: str) -> None:
        self.rows.append((status, item, note))
        print(f"{status:8s} {item:34s} {note}")

    def code(self) -> int:
        st = {r[0] for r in self.rows}
        return 1 if "FAIL" in st else 2 if "BLOCKED" in st else 0


def git_files() -> tuple[list[str], list[str]]:
    def ls(*args: str) -> list[str]:
        r = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z", *args], capture_output=True, check=True)
        return [p.decode() for p in r.stdout.split(b"\0") if p]
    return ls(), ls("--others", "--exclude-standard")


def scan_secrets(files: list[str], label: str, c: Checks) -> None:
    hits = []
    for rel in files:
        p = ROOT / rel
        name = Path(rel).name
        if name in (".env",) or rel.startswith("secrets/") or name == "jury_api_key" or name.endswith(".env"):
            hits.append(f"{rel} (secret-bearing filename)")
            continue
        if rel.startswith("contracts/fixtures/") or not p.is_file() or p.stat().st_size > MAX_TRACKED_BYTES:
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if rel == "uv.lock" or rel.endswith("package-lock.json"):
            continue  # integrity hashes, not secrets
        for n, line in enumerate(text.splitlines(), 1):
            for rule, rx in SECRET_RULES:
                m = rx.search(line)
                if not m:
                    continue
                if rule == "assigned_secret":
                    val = m.group(1)
                    if PLACEHOLDER.match(val) or re.fullmatch(r"[a-z_]+", val) or "{" in line:
                        continue
                hits.append(f"{rel}:{n} [{rule}]")
    c.add("FAIL" if hits else "PASS", f"secret scan ({label})",
          f"{len(hits)} finding(s): " + "; ".join(hits[:6]) if hits else f"{len(files)} files clean")


def check_media_weights(files: list[str], label: str, c: Checks) -> None:
    bad = []
    for rel in files:
        ext = Path(rel).suffix.lower()
        p = ROOT / rel
        if ext in MEDIA_EXT or ext in WEIGHT_EXT:
            bad.append(rel)
        elif rel.startswith(("data/", "models/")) and rel not in ("models/manifest.json", "models/README.md",
                                                                 "models/manifest.proposed.json"):
            bad.append(rel)
        elif p.is_file() and p.stat().st_size > MAX_TRACKED_BYTES and not rel.endswith(("uv.lock", "package-lock.json")):
            bad.append(f"{rel} (>{MAX_TRACKED_BYTES >> 20} MB)")
        elif re.match(r"evaluation/(annotations|manifest|reports)/private/", rel):
            bad.append(rel)
    c.add("FAIL" if bad else "PASS", f"no media/weights/private ({label})",
          "; ".join(bad[:6]) if bad else "none")


def check_gitignore(c: Checks) -> None:
    gi = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    need = ["data/", "secrets/", "*.mov", "*.MOV", "models/*", ".env", "evaluation/reports/private/",
            "evaluation/manifest/private/", "evaluation/annotations/private/"]
    missing = [n for n in need if n not in gi]
    c.add("FAIL" if missing else "PASS", ".gitignore restricted paths", ", ".join(missing) or "all present")
    if "deploy/cashwatch.env" not in gi and "*.env" not in gi:
        c.add("WARN", ".gitignore deploy env", "deploy/cashwatch.env not ignored (request F-R1)")


def check_mock_config(c: Checks) -> None:
    problems = []
    envf = ROOT / "deploy" / "cashwatch.env"
    for f in (envf, ROOT / "deploy" / "cashwatch.env.example", ROOT / ".env.example"):
        if not f.exists():
            continue
        kv = {}
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if "=" in line:
                k, v = line.split("=", 1)
                kv[k.strip().removeprefix("export ").strip()] = v.strip().strip("'\"")
        mode = kv.get("CASHWATCH_MODE", "production")
        if mode == "production" and kv.get("CASHWATCH_BACKEND") == "fake":
            problems.append(f"{f.relative_to(ROOT)}: production + fake backend")
        if f == envf and mode != "production":
            problems.append(f"{f.relative_to(ROOT)}: CASHWATCH_MODE={mode} (release config must be production)")
    for f in sorted((ROOT / "configs").glob("*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            problems.append(f"{f.relative_to(ROOT)}: invalid JSON")
            continue
        rt = d.get("runtime", {}) if isinstance(d, dict) else {}
        if rt.get("mock_mode") is True or d.get("mock_mode") is True:
            problems.append(f"{f.relative_to(ROOT)}: mock_mode true")
    try:
        sys.path.insert(0, str(ROOT / "src"))
        from cashwatch.settings import Settings
        s = Settings(_env_file=None)  # type: ignore[call-arg]
        if s.mode != "production" or s.backend == "fake":
            problems.append(f"Settings defaults mode={s.mode} backend={s.backend}")
        if not any("fake" in e for e in Settings(mode="production", backend="fake").production_errors()):
            problems.append("Settings.production_errors does not refuse fake backend")
    except Exception as e:  # noqa: BLE001
        problems.append(f"cannot import settings ({type(e).__name__})")
    for k in ("CASHWATCH_MODE", "CASHWATCH_BACKEND"):
        if k in os.environ:
            c.add("WARN", f"shell env {k}", f"set to {os.environ[k]!r} in this shell (check the service env)")
    c.add("FAIL" if problems else "PASS", "mock mode disabled in production", "; ".join(problems) or "ok")


def check_frontend(c: Checks) -> None:
    dist = ROOT / "frontend" / "dist"
    if not (dist / "index.html").is_file():
        c.add("FAIL", "frontend dist", "frontend/dist/index.html missing (make frontend)")
        return
    hard, domains = [], set()
    tag = re.compile(r"""<(?:script|link|img|iframe|source)[^>]+(?:src|href)\s*=\s*["']https?://""", re.I)
    css = re.compile(r"""(?:@import\s+(?:url\()?|url\()\s*["']?https?://""", re.I)
    js_fetch = re.compile(r"""(?:fetch|import|XMLHttpRequest|WebSocket|EventSource|sendBeacon)\s*\(\s*["'`](?:https?|wss?)://""")
    url = re.compile(r"""(?:https?|wss?)://([A-Za-z0-9.\-]+)""")
    allowed = {"www.w3.org", "adilet.zan.kz", "react.dev", "reactjs.org", "fb.me"}
    for p in dist.rglob("*"):
        if not p.is_file() or p.suffix.lower() not in (".html", ".js", ".css", ".mjs", ".json", ".svg", ".webmanifest"):
            continue
        t = p.read_text(encoding="utf-8", errors="replace")
        rel = str(p.relative_to(ROOT))
        if tag.search(t) or css.search(t) or js_fetch.search(t):
            hard.append(rel)
        for d in url.findall(t):
            if d not in allowed and not d.endswith(".zan.kz"):
                domains.add(d)
        if re.search(r"(?i)googleapis|gstatic|cdnjs|jsdelivr|unpkg|sentry|google-analytics|googletagmanager|segment\.io|mixpanel|hotjar", t):
            hard.append(f"{rel} (CDN/analytics reference)")
    if hard:
        c.add("FAIL", "frontend dist offline", "external loads: " + "; ".join(sorted(set(hard))[:5]))
    else:
        c.add("WARN" if domains else "PASS", "frontend dist offline",
              ("no external loads; URL strings for: " + ", ".join(sorted(domains)[:8])) if domains else "no external URLs")
    # key leak in bundle
    leaked = [str(p.relative_to(ROOT)) for p in dist.rglob("*.js")
              if re.search(r"VITE_[A-Z_]*KEY|X-API-Key['\"]?\s*:\s*['\"][A-Za-z0-9]{24,}", p.read_text(errors="replace"))]
    c.add("FAIL" if leaked else "PASS", "frontend has no embedded key", ", ".join(leaked) or "ok")


def check_models(c: Checks) -> None:
    man = ROOT / "models" / "manifest.json"
    if not man.is_file():
        c.add("FAIL", "models/manifest.json", "missing (orchestrator; agent C proposal in models/manifest.proposed.json)")
        return
    try:
        d = json.loads(man.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        c.add("FAIL", "models/manifest.json", "invalid JSON")
        return
    blob = json.dumps(d).lower()
    lic = "license" in blob
    rev = re.search(r"[0-9a-f]{40}", blob) is not None
    sha = re.search(r"[0-9a-f]{64}", blob) is not None
    c.add("PASS" if lic and rev and sha else "FAIL", "models/manifest.json",
          f"license={'yes' if lic else 'NO'} revision={'yes' if rev else 'NO'} file_hashes={'yes' if sha else 'NO'}")


def check_registry(c: Checks) -> None:
    reg = ROOT / "rules" / "registry.yaml"
    if not reg.is_file():
        c.add("FAIL", "rules/registry.yaml", "missing (agent D)")
        return
    try:
        import yaml
        d = yaml.safe_load(reg.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        c.add("FAIL", "rules/registry.yaml", f"cannot parse ({type(e).__name__})")
        return
    rules = d.get("rules", []) if isinstance(d, dict) else d or []
    approved = [r for r in rules if isinstance(r, dict) and r.get("state") == "approved"]
    bad = []
    import hashlib
    for r in approved:
        rid = r.get("rule_id", "?")
        h = r.get("source_snapshot_sha256") or (r.get("source") or {}).get("snapshot_sha256")
        snap = r.get("source_snapshot_path") or r.get("snapshot_path") or (r.get("source") or {}).get("snapshot_path")
        url = r.get("official_url") or (r.get("source") or {}).get("official_url") or ""
        if not h:
            bad.append(f"{rid}: no snapshot hash")
        if not str(url).startswith("https://adilet.zan.kz/"):
            bad.append(f"{rid}: official_url not adilet.zan.kz")
        if snap:
            sp = (ROOT / "rules" / snap) if not str(snap).startswith("rules/") else ROOT / snap
            if not sp.is_file():
                bad.append(f"{rid}: snapshot file missing")
            elif h and hashlib.sha256(sp.read_bytes()).hexdigest() != h:
                bad.append(f"{rid}: snapshot hash mismatch")
        elif h:
            bad.append(f"{rid}: snapshot path not recorded (hash unverifiable)")
    if bad:
        c.add("FAIL", "rule registry sources", "; ".join(bad[:6]))
    elif not approved:
        c.add("BLOCKED", "rule registry approved rules",
              f"0 approved of {len(rules)} (D011: local visual validation blocked, X1/X2)")
    else:
        c.add("PASS", "rule registry approved rules", f"{len(approved)} approved; snapshot hashes verified")
    test_reg = ROOT / "rules" / "registry.test.yaml"
    if test_reg.is_file():
        c.add("PASS", "test registry separate", "rules/registry.test.yaml present (test mode only)")


def check_docs(c: Checks) -> None:
    need = ["docs/runbook/RUNBOOK.md", "docs/limitations/LIMITATIONS.md", "docs/submission/CHECKLIST.md",
            "docs/submission/LICENSES.md", "README.md", "contracts/openapi.json"]
    missing = [n for n in need if not (ROOT / n).is_file()]
    c.add("FAIL" if missing else "PASS", "documentation present", ", ".join(missing) or "all present")


def check_secrets_perms(c: Checks) -> None:
    k = ROOT / "secrets" / "jury_api_key"
    if not k.exists():
        c.add("BLOCKED", "jury key file", "secrets/jury_api_key not generated yet (deploy/gen_api_key.sh at G4)")
        return
    mode = stat.S_IMODE(k.stat().st_mode)
    size = len(k.read_text(encoding="utf-8").strip())
    ok = mode in (0o600, 0o400) and size >= 32
    c.add("PASS" if ok else "FAIL", "jury key file", f"mode {oct(mode)}, length {'ok' if size >= 32 else 'TOO SHORT'}")


def check_deadline(c: Checks) -> None:
    v = os.environ.get("SUBMISSION_DEADLINE_ISO")
    c.add("PASS" if v else "BLOCKED", "SUBMISSION_DEADLINE_ISO",
          "set" if v else "not set: date/timezone of the 12:00 deadline unconfirmed (X4)")


def check_release_evidence(c: Checks) -> None:
    c.add("BLOCKED", "public HTTPS smoke (T25)", "host/DNS/TLS decision pending (X3)")
    c.add("BLOCKED", "real-data evaluation", "no authorized dataset (X1/X2, D004)")


def main() -> int:
    c = Checks()
    try:
        tracked, untracked = git_files()
    except (subprocess.CalledProcessError, FileNotFoundError):
        c.add("FAIL", "git", "not a git work tree")
        return 1
    scan_secrets(tracked, "tracked", c)
    scan_secrets(untracked, "untracked, not ignored", c)
    check_media_weights(tracked, "tracked", c)
    check_media_weights(untracked, "untracked, not ignored", c)
    check_gitignore(c)
    check_mock_config(c)
    check_frontend(c)
    check_models(c)
    check_registry(c)
    check_docs(c)
    check_secrets_perms(c)
    check_deadline(c)
    check_release_evidence(c)
    n = {s: sum(1 for r in c.rows if r[0] == s) for s in ("PASS", "WARN", "FAIL", "BLOCKED")}
    print(f"\n{n['PASS']} PASS, {n['WARN']} WARN, {n['FAIL']} FAIL, {n['BLOCKED']} BLOCKED")
    return c.code()


if __name__ == "__main__":
    sys.exit(main())
