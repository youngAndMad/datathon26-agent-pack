"""Result projection and the mandatory pre-publication validator (spec §5.3 checks 1-9).

Nothing is published unless ``validate_result`` returns without raising. Every failure is a
``PipelineError("evidence_invalid" | "rule_registry_invalid" | "internal_error")``.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import math
import re
from functools import lru_cache
from pathlib import Path

import jsonschema
from PIL import Image
from referencing import Registry, Resource

from cashwatch.dto import (
    STAGES,
    AnalysisReport,
    JuryDoneResult,
    JuryEvidenceFrame,
    JuryViolation,
    ReportEvent,
    ts,
)
from cashwatch.errors import PipelineError
from cashwatch.ports import RuleRegistry
from cashwatch.settings import REPO_ROOT

FORBIDDEN_TERMS = re.compile(
    r"\b(кража|краж[аиуе]|ворует|украл|мошенни\w*|виновн\w*|преступ\w*|несанкционированн\w* лиц\w*|"
    r"theft|fraud|guilty|unauthori[sz]ed person)\b",
    re.IGNORECASE,
)
REQUIRED_STAGES = [s for s in STAGES if s not in ("committing", "complete")]


@lru_cache(maxsize=1)
def _jury_validator() -> jsonschema.Draft202012Validator:
    cdir = REPO_ROOT / "contracts"
    schema = json.loads((cdir / "jury-result.schema.json").read_text(encoding="utf-8"))
    reg = Registry().with_resource("jury-result.schema.json", Resource.from_contents(schema))
    return jsonschema.Draft202012Validator(schema, registry=reg)


def read_jpeg_b64(out_dir: Path, rel: str) -> str:
    path = (out_dir / rel).resolve()
    if not path.is_relative_to(out_dir.resolve()) or path.is_symlink():
        raise PipelineError("evidence_invalid", "evidence path escapes output dir")
    return base64.b64encode(path.read_bytes()).decode("ascii")


def project_jury_result(job_id: str, events: list[ReportEvent], out_dir: Path) -> JuryDoneResult:
    violations = []
    for re_ in events:
        anchor = next(e for e in re_.evidence if e.frame_id == re_.event.anchor_frame_id)
        violations.append(JuryViolation(
            type=re_.event.rule.type_ru,
            timecode_sec=anchor.timestamp_sec,
            description=re_.description_ru,
            evidence_frames=[
                JuryEvidenceFrame(timestamp_sec=e.timestamp_sec, image_base64=read_jpeg_b64(out_dir, e.jpeg_path))
                for e in re_.evidence
            ],
        ))
    return JuryDoneResult(
        job_id=job_id, verdict="violation" if violations else "no_violation", violations=violations)


def _fail(msg: str, code: str = "evidence_invalid") -> None:
    raise PipelineError(code, msg)  # type: ignore[arg-type]


def validate_result(report: AnalysisReport, out_dir: Path, registry: RuleRegistry) -> None:
    jury = report.jury_result
    if report.status != "succeeded" or jury is None or report.source is None or report.coverage is None:
        _fail("report not in a publishable state", "internal_error")
    assert jury is not None and report.source is not None and report.coverage is not None
    meta = report.source
    duration = meta.duration_sec

    # Exact external schema.
    doc = json.loads(jury.model_dump_json())
    errs = list(_jury_validator().iter_errors(doc))
    if errs:
        _fail(f"jury schema: {errs[0].message[:120]}", "internal_error")

    # (9) required stages and complete declared coverage.
    missing = [s for s in REQUIRED_STAGES if s not in report.completed_stages]
    if missing:
        _fail(f"stages not completed: {missing}", "internal_error")
    cov = report.coverage
    if set(cov.required_window_ids) - set(cov.completed_window_ids) or set(cov.failed_window_ids) & set(cov.required_window_ids):
        _fail("mandatory coverage incomplete", "internal_error")

    # (1) verdict iff non-empty.
    if (jury.verdict == "violation") != bool(jury.violations):
        _fail("verdict/violations mismatch", "internal_error")
    if len(jury.violations) != len(report.events):
        _fail("jury/report event count mismatch", "internal_error")

    prev_t = -1.0
    seen: set[tuple[str, float]] = set()
    for jv, rev in zip(jury.violations, report.events, strict=True):
        ev = rev.event
        # (5) rule resolves, enabled and approved; provenance identical to registry.
        try:
            ref = registry.rule_ref(ev.rule.rule_id)
        except KeyError:
            _fail("unknown or unapproved rule id", "rule_registry_invalid")
        if ref != ev.rule:
            _fail("rule provenance differs from registry", "rule_registry_invalid")
        # (2) finite and in range.
        for v in [jv.timecode_sec, ev.start_sec, ev.end_sec, *[f.timestamp_sec for f in jv.evidence_frames]]:
            if not math.isfinite(v) or v < 0 or v > duration + 1e-6:
                _fail("timestamp out of source range")
        if not (ts(ev.start_sec) <= jv.timecode_sec <= ts(ev.end_sec)):
            _fail("anchor outside event interval")
        # (4) 1-3 real JPEGs from the same source, bytes match recorded hashes.
        if not 1 <= len(rev.evidence) <= 3 or len(rev.evidence) != len(jv.evidence_frames):
            _fail("evidence count")
        if [e.frame_id for e in rev.evidence] != list(ev.evidence_frame_ids):
            _fail("evidence ids differ from accepted event")
        for e, jf in zip(rev.evidence, jv.evidence_frames, strict=True):
            if e.source_sha256 != meta.source_sha256:
                _fail("evidence from a different source")
            raw = base64.b64decode(jf.image_base64, validate=True)
            if hashlib.sha256(raw).hexdigest() != e.jpeg_sha256:
                _fail("evidence bytes hash mismatch")
            if raw[:3] != b"\xff\xd8\xff":
                _fail("evidence is not JPEG")
            try:
                with Image.open(io.BytesIO(raw)) as im:
                    if im.format != "JPEG":
                        _fail("evidence is not JPEG")
                    im.verify()
            except PipelineError:
                raise
            except Exception:
                _fail("evidence JPEG does not decode")
            if jf.timestamp_sec != ts(e.timestamp_sec):
                _fail("evidence timestamp serialization")
        # (3) anchor equals an actual evidence-frame timestamp at stored precision.
        anchor = [e for e in rev.evidence if e.frame_id == ev.anchor_frame_id]
        if len(anchor) != 1 or ts(anchor[0].timestamp_sec) != jv.timecode_sec:
            _fail("anchor does not equal its evidence frame timestamp")
        if jv.evidence_frames[0].timestamp_sec != jv.timecode_sec:
            _fail("anchor evidence must be first")
        # (6)/(8) description assembled from registry; no forbidden assertions.
        if jv.description != rev.description_ru or jv.type != ref.type_ru:
            _fail("description/type not from template", "internal_error")
        if ref.official_url not in jv.description or ref.clause not in jv.description:
            _fail("description lacks registry source", "rule_registry_invalid")
        if FORBIDDEN_TERMS.search(jv.description) or FORBIDDEN_TERMS.search(ev.factual_text_ru):
            _fail("forbidden assertion in description", "internal_error")
        # (7) chronological order, duplicates suppressed.
        if jv.timecode_sec < prev_t:
            _fail("events not chronological", "internal_error")
        prev_t = jv.timecode_sec
        key = (ref.rule_id, jv.timecode_sec)
        if key in seen:
            _fail("duplicate event", "internal_error")
        seen.add(key)
