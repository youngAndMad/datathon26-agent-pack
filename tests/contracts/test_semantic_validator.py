"""Semantic checks of IMPLEMENTATION_SPEC §5.3 in the black-box validator (scripts/qa_jury_client.py).
These are the checks the schema cannot express; each crafted document must be caught."""

from __future__ import annotations

import base64

import pytest

from .conftest import FIXTURES, OFFICIAL_DESC, jpeg_b64, png_b64, read_json


def errs(qa, doc, **kw):
    kw.setdefault("duration_sec", 60.0)
    return qa.validate_job_response(doc, **kw).errors


def test_valid_violation_passes(qa, valid_violation):
    v = qa.validate_job_response(valid_violation, expected_job_id="job-1", duration_sec=60.0)
    assert v.errors == [] and v.warnings == []


def test_clean_and_failed_examples_pass(qa):
    assert errs(qa, read_json(FIXTURES / "jury-no-violation.json")) == []
    assert errs(qa, {"job_id": "j", "status": "failed",
                     "error": {"code": "deadline_exceeded", "message": "Анализ не уложился"}}) == []
    assert errs(qa, {"job_id": "j", "status": "processing"}) == []


def test_fixture_violation_is_placeholder_source(qa):
    """The G0 fixture deliberately carries 'Источник: FIXTURE' (no adilet URL). It is schema-valid but
    must fail the strict provenance check; with the relaxed flag everything else passes."""
    doc = read_json(FIXTURES / "jury-violation.json")
    strict = errs(qa, doc)
    assert strict and all("adilet.zan.kz" in e for e in strict)
    assert errs(qa, doc, require_official_source=False) == []


def test_job_id_mismatch(qa, valid_violation):
    assert any("job_id" in e for e in errs(qa, valid_violation, expected_job_id="other"))


def test_timecode_beyond_duration(qa, valid_violation):
    assert any("outside [0, duration]" in e for e in errs(qa, valid_violation, duration_sec=20.0))


def test_duration_unknown_is_warning_not_silent(qa, valid_violation):
    v = qa.validate_job_response(valid_violation, duration_sec=None)
    assert v.errors == [] and any("duration unknown" in w for w in v.warnings)


def test_anchor_must_equal_an_evidence_timestamp(qa, valid_violation, clone):
    d = clone(valid_violation)
    d["violations"][0]["timecode_sec"] = 12.3449  # rounds to 12.345 -> equal at 3 decimals
    assert errs(qa, d) == []
    d["violations"][0]["timecode_sec"] = 12.34  # differs at stored precision
    assert any("does not equal any evidence frame timestamp" in e for e in errs(qa, d))


def test_chronological_order(qa, valid_violation, clone):
    d = clone(valid_violation)
    d["violations"].reverse()
    assert any("chronological" in e for e in errs(qa, d))


@pytest.mark.parametrize("bad,needle", [
    ("data:image/jpeg;base64," + "A" * 8, "data: URI"),
    ("not base64!!", "strict base64"),
    (base64.b64encode(b"\xff\xd8garbage-not-a-jpeg").decode(), "does not decode"),
    (base64.b64encode(b"hello world, plain text").decode(), "not a JPEG"),
])
def test_bad_images(qa, valid_violation, clone, bad, needle):
    d = clone(valid_violation)
    d["violations"][1]["evidence_frames"][0]["image_base64"] = bad
    assert any(needle in e for e in errs(qa, d)), errs(qa, d)


def test_png_is_not_jpeg(qa, valid_violation, clone):
    d = clone(valid_violation)
    d["violations"][1]["evidence_frames"][0]["image_base64"] = png_b64()
    assert any("not a JPEG" in e for e in errs(qa, d))


def test_four_frames_and_verdict_mismatch(qa, valid_violation, clone):
    d = clone(valid_violation)
    d["violations"][1]["evidence_frames"] = [{"timestamp_sec": 47.0, "image_base64": jpeg_b64()}] * 4
    assert any("1-3 frames" in e for e in errs(qa, d))
    d = clone(valid_violation)
    d["verdict"] = "no_violation"
    assert any("mismatch" in e for e in errs(qa, d))


@pytest.mark.parametrize("desc", [
    "На 12.345 с видно: объект.",  # no source at all
    "Источник: документ без ссылки",  # no URL
    "Источник: https://adilet.kz/rus/docs/X",  # look-alike, not the official site
    "https://adilet.zan.kz/rus/docs/X без маркера",  # URL but no 'Источник:'
])
def test_description_provenance(qa, valid_violation, clone, desc):
    d = clone(valid_violation)
    d["violations"][0]["description"] = desc
    assert errs(qa, d)


def test_failed_with_leaky_message(qa):
    doc = {"job_id": "j", "status": "failed",
           "error": {"code": "internal_error", "message": "Traceback in /Users/x/app.py"}}
    assert any("leaks" in e for e in errs(qa, doc))


def test_unknown_error_code_is_warning(qa):
    v = qa.validate_job_response({"job_id": "j", "status": "failed", "error": {"code": "weird", "message": "m"}})
    assert v.errors == [] and v.warnings


def test_duplicate_event_warns(qa, valid_violation, clone):
    d = clone(valid_violation)
    d["violations"][1] = clone(d["violations"][0])
    v = qa.validate_job_response(d, duration_sec=60.0)
    assert any("duplicates" in w for w in v.warnings)


def test_official_desc_constant_shape():
    assert "Источник:" in OFFICIAL_DESC and "https://adilet.zan.kz/" in OFFICIAL_DESC
