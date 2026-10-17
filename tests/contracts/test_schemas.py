"""JSON Schema self-tests: every committed example/fixture validates; crafted invalid documents
are rejected by the schema alone (no application code involved)."""

from __future__ import annotations

import json

import pytest
from jsonschema import Draft202012Validator

from .conftest import CONTRACTS, FIXTURES, jpeg_b64, read_json

SCHEMAS = sorted(p.name for p in CONTRACTS.glob("*.schema.json"))


@pytest.mark.parametrize("name", SCHEMAS)
def test_schema_is_valid_draft_2020_12(name):
    Draft202012Validator.check_schema(read_json(CONTRACTS / name))


@pytest.mark.parametrize("name", ["example-processing.json", "example-no-violation.json", "example-failed.json"])
def test_contract_examples_validate_as_job_responses(qa, name):
    assert qa.schema_errors("job-response.schema.json", read_json(CONTRACTS / name)) == []


@pytest.mark.parametrize("name", ["jury-violation.json", "jury-no-violation.json"])
def test_jury_fixtures_validate(qa, name):
    doc = read_json(FIXTURES / name)
    assert qa.schema_errors("jury-result.schema.json", doc) == []
    assert qa.schema_errors("job-response.schema.json", doc) == []


@pytest.mark.parametrize("name", sorted(p.name for p in FIXTURES.glob("ui-report-*.json")))
def test_ui_report_fixtures_validate(qa, name):
    assert qa.schema_errors("ui-report.schema.json", read_json(FIXTURES / name)) == []


def test_submit_schema(qa):
    assert qa.schema_errors("submit-response.schema.json", {"job_id": "abc"}) == []
    assert qa.schema_errors("submit-response.schema.json", {"job_id": ""})
    assert qa.schema_errors("submit-response.schema.json", {"job_id": "a", "status": "processing"})


def test_openapi_refs_resolve_to_local_files():
    spec = read_json(CONTRACTS / "openapi.json")
    refs = []

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k == "$ref":
                    refs.append(v)
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(spec)
    assert refs, "openapi has no schema refs"
    for r in refs:
        if not r.startswith("#"):
            assert (CONTRACTS / r).is_file(), r
    assert spec["components"]["securitySchemes"]["JuryApiKey"] == {"type": "apiKey", "in": "header", "name": "X-API-Key"}
    assert set(spec["paths"]) == {"/api/v1/analyze", "/api/v1/jobs/{job_id}"}


# --------------------------------------------------------------------------- crafted invalid docs

def _base():
    return {"job_id": "j", "status": "done", "verdict": "violation", "violations": [
        {"type": "T", "timecode_sec": 1.0, "description": "d",
         "evidence_frames": [{"timestamp_sec": 1.0, "image_base64": jpeg_b64()}]}]}


def _mut(fn):
    d = _base()
    fn(d)
    return d


INVALID = {
    "extra_top_level_field": _mut(lambda d: d.update(confidence=0.9)),
    "extra_violation_field": _mut(lambda d: d["violations"][0].update(rule_id="CW01")),
    "extra_frame_field": _mut(lambda d: d["violations"][0]["evidence_frames"][0].update(bbox=[0, 0, 1, 1])),
    "violation_with_empty_list": _mut(lambda d: d.update(violations=[])),
    "no_violation_with_events": _mut(lambda d: d.update(verdict="no_violation")),
    "third_verdict": _mut(lambda d: d.update(verdict="insufficient_data")),
    "four_frames": _mut(lambda d: d["violations"][0]["evidence_frames"].extend(
        [{"timestamp_sec": 1.0, "image_base64": jpeg_b64()}] * 3)),
    "zero_frames": _mut(lambda d: d["violations"][0].update(evidence_frames=[])),
    "data_uri_prefix": _mut(lambda d: d["violations"][0]["evidence_frames"][0].update(
        image_base64="data:image/jpeg;base64," + jpeg_b64())),
    "negative_timecode": _mut(lambda d: d["violations"][0].update(timecode_sec=-0.5)),
    "negative_frame_time": _mut(lambda d: d["violations"][0]["evidence_frames"][0].update(timestamp_sec=-1)),
    "string_timecode": _mut(lambda d: d["violations"][0].update(timecode_sec="1.0")),
    "empty_description": _mut(lambda d: d["violations"][0].update(description="")),
    "missing_type": _mut(lambda d: d["violations"][0].pop("type")),
    "missing_violations": _mut(lambda d: d.pop("violations")),
    "status_completed": _mut(lambda d: d.update(status="completed")),
}


def test_crafted_base_is_valid(qa):
    assert qa.schema_errors("jury-result.schema.json", _base()) == []


@pytest.mark.parametrize("case", sorted(INVALID))
def test_crafted_invalid_done_rejected(qa, case):
    assert qa.schema_errors("jury-result.schema.json", INVALID[case]), case
    assert qa.schema_errors("job-response.schema.json", INVALID[case]), case


@pytest.mark.parametrize("doc", [
    {"job_id": "j", "status": "failed", "error": {"code": "x", "message": "m"}, "verdict": "no_violation"},
    {"job_id": "j", "status": "failed", "error": {"code": "x", "message": "m"}, "violations": []},
    {"job_id": "j", "status": "failed"},
    {"job_id": "j", "status": "failed", "error": {"code": "x", "message": "m", "trace": "..."}},
    {"job_id": "j", "status": "processing", "progress": 0.5},
    {"job_id": "j", "status": "queued"},
    {"status": "processing"},
    {"job_id": "", "status": "processing"},
])
def test_invalid_non_done_responses_rejected(qa, doc):
    assert qa.schema_errors("job-response.schema.json", doc), json.dumps(doc)


def test_nan_is_not_representable_in_strict_json():
    # Python's json allows NaN by default; the service must never emit it. Strict parsers reject it.
    with pytest.raises(ValueError):
        json.loads('{"x": NaN}', parse_constant=lambda c: (_ for _ in ()).throw(ValueError(c)))
