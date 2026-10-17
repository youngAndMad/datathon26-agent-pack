#!/usr/bin/env python3
"""Black-box jury client and strict response validator (agent F).

Behaves like the organizer's jury: uploads one MOV via multipart field ``file`` with the
``X-API-Key`` header, polls ``GET /api/v1/jobs/{job_id}`` until a terminal state, validates every
response against ``contracts/job-response.schema.json`` (jury-result resolved through a
``referencing`` registry) and applies the semantic checks of IMPLEMENTATION_SPEC §5.3.

It never prints the API key, base64 payloads or description text (descriptions may contain scene
facts). Output is a JSON summary with checks, timings and error codes only.

Exit codes:
  0  terminal ``done`` and every check passed
  1  contract/semantic validation failure (any response)
  2  terminal ``failed`` (valid failed response; a processing failure, never a clean result)
  3  timeout: no terminal state within --timeout
  4  HTTP/transport error (incl. 401/429/503 on submit)
  5  usage error (missing file/key, bad arguments)

Importable: ``validate_job_response``, ``validate_submit_response``, ``probe_duration``,
``run_job`` are used by tests/contracts, tests/integration, qa_smoke and qa_benchmark.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import io
import json
import math
import re
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "contracts"
TIME_DECIMALS = 3  # cashwatch.dto.TIME_DECIMALS (D007); duplicated so the client stays black-box
OFFICIAL_URL_RE = re.compile(r"https://adilet\.zan\.kz/\S+")
SOURCE_MARK = "Источник:"
KNOWN_ERROR_CODES = {
    "invalid_media", "unsupported_container", "limits_exceeded", "timeline_unsupported",
    "decode_failed", "model_unavailable", "model_output_invalid", "gpu_oom", "storage_full",
    "worker_lost", "deadline_exceeded", "evidence_invalid", "rule_registry_invalid",
    "input_unusable", "internal_error",
}

EXIT_OK, EXIT_INVALID, EXIT_FAILED, EXIT_TIMEOUT, EXIT_HTTP, EXIT_USAGE = 0, 1, 2, 3, 4, 5


# --------------------------------------------------------------------------- schema handling

_VALIDATORS: dict[str, Any] = {}


def _load(name: str) -> dict:
    return json.loads((CONTRACTS / name).read_text(encoding="utf-8"))


def schema_validator(name: str):
    """Draft 2020-12 validator for a contract schema; relative $refs resolve to contracts/."""
    if name in _VALIDATORS:
        return _VALIDATORS[name]
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT202012

    registry = Registry()
    for p in sorted(CONTRACTS.glob("*.schema.json")):
        # Schemas without "$schema" (e.g. the generated ui-report schema) default to 2020-12.
        res = Resource.from_contents(json.loads(p.read_text("utf-8")), default_specification=DRAFT202012)
        registry = registry.with_resource(p.name, res)
    schema = _load(name)
    Draft202012Validator.check_schema(schema)
    v = Draft202012Validator(schema, registry=registry)
    _VALIDATORS[name] = v
    return v


def schema_errors(name: str, doc: Any) -> list[str]:
    v = schema_validator(name)
    out = []
    for e in sorted(v.iter_errors(doc), key=lambda e: list(e.absolute_path)):
        loc = "/".join(str(x) for x in e.absolute_path) or "<root>"
        out.append(f"schema[{name}] at {loc}: {e.message[:200]}")
    return out


# --------------------------------------------------------------------------- semantic checks

@dataclass
class Validation:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _finite_number(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(float(x))


def _decode_jpeg(b64: Any) -> tuple[bool, str]:
    if not isinstance(b64, str):
        return False, "image_base64 is not a string"
    if b64.startswith("data:"):
        return False, "image_base64 has a data: URI prefix"
    try:
        raw = base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError):
        return False, "image_base64 is not strict base64"
    if not raw.startswith(b"\xff\xd8"):
        return False, "decoded bytes are not a JPEG (missing SOI marker)"
    try:
        from PIL import Image
        with Image.open(io.BytesIO(raw)) as im:
            fmt = im.format
            im.verify()
        with Image.open(io.BytesIO(raw)) as im:
            im.load()
            w, h = im.size
    except Exception as e:  # noqa: BLE001 - any decoder failure is a contract failure
        return False, f"JPEG does not decode ({type(e).__name__})"
    if fmt != "JPEG":
        return False, f"image format is {fmt}, expected JPEG"
    if w < 16 or h < 16:
        return False, f"JPEG is implausibly small ({w}x{h})"
    return True, ""


def validate_job_response(
    doc: Any,
    *,
    expected_job_id: str | None = None,
    duration_sec: float | None = None,
    require_official_source: bool = True,
) -> Validation:
    """Schema + §5.3 semantic checks for one GET /api/v1/jobs/{id} body.

    ``duration_sec``: source presentation duration (ffprobe). When None, the upper time bound
    cannot be checked and a warning is recorded (never silently passed as an error-free check).
    ``require_official_source``: description must carry "Источник:" and an adilet.zan.kz URL.
    """
    v = Validation()
    v.errors += schema_errors("job-response.schema.json", doc)
    if not isinstance(doc, dict):
        v.errors.append("response is not a JSON object")
        return v
    if expected_job_id is not None and doc.get("job_id") != expected_job_id:
        v.errors.append("job_id does not match the submitted job")
    status = doc.get("status")
    if status == "processing":
        return v
    if status == "failed":
        if "verdict" in doc or "violations" in doc:
            v.errors.append("failed response must not contain verdict/violations")
        err = doc.get("error") if isinstance(doc.get("error"), dict) else {}
        code = err.get("code")
        if code not in KNOWN_ERROR_CODES:
            v.warnings.append(f"error code {code!r} is outside the frozen vocabulary")
        msg = str(err.get("message", ""))
        if re.search(r"(/Users/|/home/|/private/|Traceback|\.py\b)", msg):
            v.errors.append("failed message looks like it leaks paths or stack traces")
        return v
    if status != "done":
        v.errors.append(f"unknown status {status!r}")
        return v

    verdict = doc.get("verdict")
    violations = doc.get("violations")
    if not isinstance(violations, list):
        v.errors.append("violations is not a list")
        return v
    if (verdict == "violation") != (len(violations) > 0):
        v.errors.append("verdict/violations mismatch (violation iff nonempty list)")
    if duration_sec is None:
        v.warnings.append("source duration unknown: upper time bound not checked")
    elif not (_finite_number(duration_sec) and duration_sec > 0):
        v.errors.append("source duration is not a positive finite number")
        duration_sec = None

    upper = None if duration_sec is None else duration_sec + 10 ** -TIME_DECIMALS
    prev_tc = -math.inf
    seen: set[tuple[str, float]] = set()
    for i, ev in enumerate(violations):
        p = f"violations[{i}]"
        if not isinstance(ev, dict):
            v.errors.append(f"{p} is not an object")
            continue
        tc = ev.get("timecode_sec")
        if not _finite_number(tc):
            v.errors.append(f"{p}.timecode_sec is not a finite number")
            continue
        if tc < 0 or (upper is not None and tc > upper):
            v.errors.append(f"{p}.timecode_sec outside [0, duration]")
        if tc < prev_tc:
            v.errors.append(f"{p} is not in chronological order")
        prev_tc = tc
        key = (str(ev.get("type")), round(float(tc), TIME_DECIMALS))
        if key in seen:
            v.warnings.append(f"{p} duplicates type+timecode of an earlier event")
        seen.add(key)

        desc = ev.get("description")
        if not isinstance(desc, str) or not desc.strip():
            v.errors.append(f"{p}.description is empty")
        elif require_official_source:
            if SOURCE_MARK not in desc:
                v.errors.append(f"{p}.description lacks '{SOURCE_MARK}' provenance")
            elif not OFFICIAL_URL_RE.search(desc.split(SOURCE_MARK, 1)[1]):
                v.errors.append(f"{p}.description source lacks an https://adilet.zan.kz/ URL")

        frames = ev.get("evidence_frames")
        if not isinstance(frames, list) or not 1 <= len(frames) <= 3:
            v.errors.append(f"{p}.evidence_frames must contain 1-3 frames")
            frames = frames if isinstance(frames, list) else []
        stamps = []
        for j, fr in enumerate(frames):
            q = f"{p}.evidence_frames[{j}]"
            if not isinstance(fr, dict):
                v.errors.append(f"{q} is not an object")
                continue
            t = fr.get("timestamp_sec")
            if not _finite_number(t):
                v.errors.append(f"{q}.timestamp_sec is not a finite number")
            else:
                stamps.append(round(float(t), TIME_DECIMALS))
                if t < 0 or (upper is not None and t > upper):
                    v.errors.append(f"{q}.timestamp_sec outside [0, duration]")
            ok, why = _decode_jpeg(fr.get("image_base64"))
            if not ok:
                v.errors.append(f"{q}: {why}")
        if stamps and round(float(tc), TIME_DECIMALS) not in stamps:
            v.errors.append(f"{p}.timecode_sec does not equal any evidence frame timestamp "
                            f"at {TIME_DECIMALS}-decimal precision")
        if len(set(stamps)) != len(stamps):
            v.warnings.append(f"{p} has evidence frames with identical timestamps")
    return v


def validate_submit_response(status_code: int, doc: Any) -> Validation:
    v = Validation()
    if status_code != 202:
        v.errors.append(f"submit returned HTTP {status_code}, expected 202")
        return v
    v.errors += schema_errors("submit-response.schema.json", doc)
    return v


# --------------------------------------------------------------------------- media probe

def probe_duration(path: Path, *, timeout_sec: float = 30.0) -> float | None:
    """Presentation duration via local ffprobe (argument list, no shell). None if unavailable."""
    exe = shutil.which("ffprobe")
    if not exe:
        return None
    try:
        out = subprocess.run(
            [exe, "-v", "error", "-show_entries", "format=duration:stream=duration,codec_type",
             "-of", "json", "--", str(path)],
            capture_output=True, text=True, timeout=timeout_sec, check=False)
        data = json.loads(out.stdout or "{}")
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        return None
    cands = []
    for s in data.get("streams", []):
        if s.get("codec_type") == "video" and s.get("duration") not in (None, "N/A"):
            cands.append(float(s["duration"]))
    fmt = data.get("format", {}).get("duration")
    if fmt not in (None, "N/A"):
        cands.append(float(fmt))
    cands = [c for c in cands if math.isfinite(c) and c > 0]
    # The larger value is the conservative upper bound for "within the source duration".
    return max(cands) if cands else None


# --------------------------------------------------------------------------- HTTP client

@dataclass
class RunResult:
    file_label: str
    job_id: str | None = None
    terminal_status: str | None = None
    verdict: str | None = None
    violation_count: int | None = None
    timecodes_sec: list[float] = field(default_factory=list)
    violation_types: list[str] = field(default_factory=list)
    error_code: str | None = None
    submit_http: int | None = None
    retry_after: str | None = None
    upload_sec: float | None = None
    upload_to_terminal_sec: float | None = None
    accepted_to_terminal_sec: float | None = None
    polls: int = 0
    transient_errors: int = 0
    stable_on_repoll: bool | None = None
    duration_sec: float | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    exit_code: int = EXIT_OK

    def to_dict(self) -> dict:
        return asdict(self)


def run_job(
    base_url: str,
    api_key: str,
    file_path: Path,
    *,
    timeout_sec: float = 300.0,
    poll_interval_sec: float = 1.0,
    require_official_source: bool = True,
    duration_sec: float | None = None,
    file_label: str | None = None,
    client: Any = None,
) -> RunResult:
    """Upload, poll to terminal, validate every response. Never raises for HTTP problems."""
    import httpx

    res = RunResult(file_label=file_label or file_path.name)
    res.duration_sec = duration_sec if duration_sec is not None else probe_duration(file_path)
    base = base_url.rstrip("/")
    headers = {"X-API-Key": api_key}
    own = client is None
    c = client or httpx.Client(timeout=httpx.Timeout(connect=10.0, read=120.0, write=120.0, pool=10.0),
                               trust_env=False)
    try:
        t0 = time.monotonic()
        try:
            with open(file_path, "rb") as fh:
                r = c.post(f"{base}/api/v1/analyze", headers=headers,
                           files={"file": (file_path.name, fh, "application/octet-stream")})
        except httpx.HTTPError as e:
            res.errors.append(f"submit transport error: {type(e).__name__}")
            res.exit_code = EXIT_HTTP
            return res
        t_acc = time.monotonic()
        res.upload_sec = round(t_acc - t0, 3)
        res.submit_http = r.status_code
        res.retry_after = r.headers.get("Retry-After")
        try:
            body = r.json()
        except ValueError:
            body = None
        if r.status_code != 202:
            res.errors.append(f"submit HTTP {r.status_code}"
                              + (f" code={body.get('error', {}).get('code')}" if isinstance(body, dict)
                                 and isinstance(body.get("error"), dict) else ""))
            if r.status_code == 429 and not res.retry_after:
                res.errors.append("429 without Retry-After header")
            res.exit_code = EXIT_HTTP
            return res
        sv = validate_submit_response(r.status_code, body)
        if not sv.ok:
            res.errors += sv.errors
            res.exit_code = EXIT_INVALID
            return res
        res.job_id = body["job_id"]
        url = f"{base}/api/v1/jobs/{res.job_id}"

        last_raw: bytes | None = None
        deadline = t0 + timeout_sec
        backoff = poll_interval_sec
        while True:
            if time.monotonic() > deadline:
                res.errors.append(f"no terminal state within {timeout_sec:.0f}s")
                res.exit_code = EXIT_TIMEOUT
                return res
            try:
                g = c.get(url, headers=headers)
            except httpx.HTTPError:
                res.transient_errors += 1
                time.sleep(min(backoff, 5.0))
                backoff = min(backoff * 2, 5.0)
                continue
            res.polls += 1
            if g.status_code >= 500 or g.status_code == 429:
                res.transient_errors += 1
                time.sleep(min(backoff, 5.0))
                backoff = min(backoff * 2, 5.0)
                continue
            if g.status_code != 200:
                res.errors.append(f"status poll HTTP {g.status_code}")
                res.exit_code = EXIT_HTTP
                return res
            backoff = poll_interval_sec
            try:
                doc = g.json()
            except ValueError:
                res.errors.append("status body is not JSON")
                res.exit_code = EXIT_INVALID
                return res
            val = validate_job_response(doc, expected_job_id=res.job_id, duration_sec=res.duration_sec,
                                        require_official_source=require_official_source)
            res.errors += [e for e in val.errors if e not in res.errors]
            res.warnings += [w for w in val.warnings if w not in res.warnings]
            st = doc.get("status") if isinstance(doc, dict) else None
            if st in ("done", "failed"):
                t_end = time.monotonic()
                res.terminal_status = st
                res.upload_to_terminal_sec = round(t_end - t0, 3)
                res.accepted_to_terminal_sec = round(t_end - t_acc, 3)
                last_raw = g.content
                break
            time.sleep(poll_interval_sec)

        # Stable bytes on repeated polling of a terminal job (spec §6).
        try:
            g2 = c.get(url, headers=headers)
            res.stable_on_repoll = g2.status_code == 200 and g2.content == last_raw
            if not res.stable_on_repoll:
                res.errors.append("terminal response changed on repeated polling")
        except httpx.HTTPError:
            res.warnings.append("re-poll after terminal state failed (transport)")

        doc = json.loads(last_raw)
        if res.terminal_status == "done":
            res.verdict = doc.get("verdict")
            vs = doc.get("violations") or []
            res.violation_count = len(vs)
            res.timecodes_sec = [v.get("timecode_sec") for v in vs if isinstance(v, dict)]
            res.violation_types = [str(v.get("type")) for v in vs if isinstance(v, dict)]
        else:
            res.error_code = (doc.get("error") or {}).get("code")
        if res.errors:
            res.exit_code = EXIT_INVALID
        elif res.terminal_status == "failed":
            res.exit_code = EXIT_FAILED
        return res
    finally:
        if own:
            c.close()


def read_key(key_file: Path) -> str:
    key = key_file.read_text(encoding="utf-8").strip()
    if not key:
        raise ValueError("key file is empty")
    return key


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    ap.add_argument("--key-file", type=Path, default=ROOT / "secrets" / "jury_api_key")
    ap.add_argument("--file", type=Path, required=True, help="MOV file to upload")
    ap.add_argument("--timeout", type=float, default=300.0, help="upload-to-terminal limit, seconds")
    ap.add_argument("--poll-interval", type=float, default=1.0)
    ap.add_argument("--no-require-official-source", action="store_true",
                    help="do not require an adilet.zan.kz URL in descriptions (fixture runs only)")
    ap.add_argument("--json-out", type=Path, help="also write the summary JSON here")
    a = ap.parse_args(argv)
    if not a.file.is_file():
        print(json.dumps({"error": "file not found"}), file=sys.stderr)
        return EXIT_USAGE
    try:
        key = read_key(a.key_file)
    except (OSError, ValueError) as e:
        print(json.dumps({"error": f"cannot read key file ({type(e).__name__})"}), file=sys.stderr)
        return EXIT_USAGE
    res = run_job(a.base_url, key, a.file, timeout_sec=a.timeout, poll_interval_sec=a.poll_interval,
                  require_official_source=not a.no_require_official_source)
    out = json.dumps(res.to_dict(), ensure_ascii=False, indent=2)
    print(out)
    if a.json_out:
        a.json_out.write_text(out + "\n", encoding="utf-8")
    return res.exit_code


if __name__ == "__main__":
    sys.exit(main())
