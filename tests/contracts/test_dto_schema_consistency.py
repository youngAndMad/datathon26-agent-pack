"""Pydantic DTOs (src/cashwatch/dto.py) and the frozen JSON Schemas must agree: anything the DTO can
serialize validates against the schema, and the schema rejects what the DTO rejects."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from cashwatch import dto

from .conftest import CONTRACTS, FIXTURES, jpeg_b64, read_json


def _done(verdict, n):
    return dto.JuryDoneResult(job_id="job-x", verdict=verdict, violations=[
        dto.JuryViolation(type=f"T{i}", timecode_sec=dto.ts(1.0 + i), description="d",
                          evidence_frames=[dto.JuryEvidenceFrame(timestamp_sec=dto.ts(1.0 + i),
                                                                 image_base64=jpeg_b64())])
        for i in range(n)])


@pytest.mark.parametrize("verdict,n", [("violation", 1), ("violation", 3), ("no_violation", 0)])
def test_jury_done_dto_output_validates(qa, verdict, n):
    doc = json.loads(_done(verdict, n).model_dump_json())
    assert qa.schema_errors("jury-result.schema.json", doc) == []
    assert qa.schema_errors("job-response.schema.json", doc) == []
    assert set(doc) == {"job_id", "status", "verdict", "violations"}


def test_dto_verdict_mismatch_is_caught_by_schema(qa):
    """DTO alone does not enforce verdict<->violations; the schema and validator must."""
    doc = json.loads(_done("violation", 0).model_dump_json())
    assert qa.schema_errors("jury-result.schema.json", doc)
    doc = json.loads(_done("no_violation", 1).model_dump_json())
    assert qa.schema_errors("jury-result.schema.json", doc)


def test_dto_rejects_what_schema_rejects():
    with pytest.raises(ValidationError):
        dto.JuryEvidenceFrame(timestamp_sec=-1.0, image_base64=jpeg_b64())
    with pytest.raises(ValidationError):
        dto.JuryEvidenceFrame(timestamp_sec=1.0, image_base64="data:image/jpeg;base64,AAAA")
    with pytest.raises(ValidationError):
        dto.JuryViolation(type="T", timecode_sec=1.0, description="d", evidence_frames=[
            dto.JuryEvidenceFrame(timestamp_sec=1.0, image_base64=jpeg_b64())] * 4)
    with pytest.raises(ValidationError):
        dto.JuryDoneResult(job_id="j", verdict="violation", violations=[], confidence=1.0)
    with pytest.raises(ValidationError):
        dto.JuryDoneResult(job_id="j", verdict="unknown", violations=[])


def test_failed_and_processing_dtos_validate(qa):
    f = dto.FailedResponse(job_id="j", error=dto.ErrorInfo(code="worker_lost", message=dto.ERROR_MESSAGES_RU["worker_lost"]))
    p = dto.ProcessingResponse(job_id="j")
    for m in (f, p):
        assert qa.schema_errors("job-response.schema.json", json.loads(m.model_dump_json())) == []


def test_every_error_code_has_safe_message_and_matches_vocabulary(qa):
    codes = set(dto.ErrorCode.__args__)
    assert codes == set(dto.ERROR_MESSAGES_RU) == qa.KNOWN_ERROR_CODES
    ui_codes = set(read_json(CONTRACTS / "ui-report.schema.json")["$defs"]["ErrorInfo"]["properties"]["code"]["enum"])
    assert ui_codes == codes


def test_ui_report_schema_matches_dto():
    """contracts/ui-report.schema.json is generated from dto.UiReport; drift means regenerate."""
    assert read_json(CONTRACTS / "ui-report.schema.json") == dto.UiReport.model_json_schema()


@pytest.mark.parametrize("name", sorted(p.name for p in FIXTURES.glob("ui-report-*.json")))
def test_ui_fixtures_roundtrip_through_dto(qa, name):
    raw = read_json(FIXTURES / name)
    model = dto.UiReport.model_validate(raw)
    assert qa.schema_errors("ui-report.schema.json", json.loads(model.model_dump_json())) == []


def test_ui_report_never_contains_jury_only_or_private_fields():
    props = set(read_json(CONTRACTS / "ui-report.schema.json")["properties"])
    assert "violations" not in props  # jury object is never embedded in the UI report
    blob = json.dumps(read_json(CONTRACTS / "ui-report.schema.json"))
    for private in ("local_path", "jpeg_path", "prompt", "source_path"):
        assert private not in blob


def test_ts_precision_matches_validator(qa):
    assert dto.TIME_DECIMALS == qa.TIME_DECIMALS
    assert dto.ts(12.3449) == 12.345
