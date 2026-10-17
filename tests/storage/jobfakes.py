"""Test-only helpers (imported by tests/storage and tests/api). The fake result writer is TEST-ONLY plumbing: it copies synthetic
contract fixtures (contracts/fixtures) into the generation staging dir; it is not inference."""

from __future__ import annotations

import hashlib
import json
import secrets

from datetime import timedelta
from pathlib import Path

from cashwatch.deadline import iso_utc, utc_now
from cashwatch.dto import (
    AcceptedEvent,
    AnalysisReport,
    EvidenceFrame,
    ModelInfo,
    ReportEvent,
    RuleRef,
    RunVersions,
)
from cashwatch.settings import REPO_ROOT, Settings

FIXTURES = REPO_ROOT / "contracts" / "fixtures"


def make_settings(tmp: Path, **kw) -> Settings:
    key = tmp / "secrets" / "key"
    if not key.exists():
        key.parent.mkdir(parents=True, exist_ok=True)
        key.write_text(secrets.token_urlsafe(48))
    base = dict(mode="test", backend="fake", data_dir=tmp / "data", api_key_file=key,
                frontend_dist=tmp / "no-frontend")
    base.update(kw)
    return Settings(**base)


def enqueue(store, *, started=None, content=b"\x00\x00\x00\x08wide", job_id=None):
    import uuid
    started = started or utc_now()
    rid = store.reserve_capacity("jury")
    store.tmp_upload_path(rid).write_bytes(content)
    jid = job_id or str(uuid.uuid4())
    rel = store.persist_upload(rid, jid)
    jid, created = store.enqueue_job(
        job_id=jid, principal_id="jury", source_rel=rel, source_sha256=hashlib.sha256(content).hexdigest(),
        size_bytes=len(content), request_started_at=iso_utc(started), reservation_id=rid)
    assert created
    return jid


def write_fake_result(store, claimed, *, verdict: str = "violation", marker: str = "") -> Path:
    """Stage a synthetic result (jury.json + report.json + evidence) for the claimed job."""
    job = claimed.job
    out = store.output_dir(job)
    fx = "jury-violation.json" if verdict == "violation" else "jury-no-violation.json"
    jury = json.loads((FIXTURES / fx).read_text(encoding="utf-8"))
    jury["job_id"] = job.job_id
    events = []
    (out / "evidence").mkdir(exist_ok=True)
    for i, v in enumerate(jury["violations"]):
        if marker:
            v["description"] = v["description"] + marker
        eid = f"e{i + 1}"
        jpg = (FIXTURES / f"{eid}.jpg").read_bytes()
        (out / "evidence" / f"{eid}-0.jpg").write_bytes(jpg)
        t = v["timecode_sec"]
        rule = RuleRef(rule_id=f"FIXTURE-{chr(65 + i)}", rule_version="0", type_ru=v["type"],
                       document_title="FIXTURE — не является нормой права", authority="FIXTURE",
                       document_date="2000-01-01", document_number="0", clause="0",
                       official_url="https://adilet.zan.kz/rus/docs/FIXTURE",
                       local_excerpt="Синтетический фрагмент.", source_snapshot_sha256="0" * 64)
        ev = AcceptedEvent(event_id=eid, rule=rule, start_sec=max(0.0, t - 2), end_sec=t + 3,
                           anchor_frame_id=f"f{i}", evidence_frame_ids=[f"f{i}"], factual_text_ru="синтетика",
                           template_id="fixture", limitations_ru=["Синтетические данные"])
        fr = EvidenceFrame(evidence_id=f"{eid}-0", frame_id=f"f{i}", source_sha256=job.source_sha256, pts=int(t * 1000),
                           time_base_num=1, time_base_den=1000, timestamp_sec=t, jpeg_path=f"evidence/{eid}-0.jpg",
                           jpeg_sha256=hashlib.sha256(jpg).hexdigest(), width=640, height=360, jpeg_quality=85)
        events.append(ReportEvent(event=ev, evidence=[fr], description_ru=v["description"]))
    data = json.dumps(jury, ensure_ascii=False).encode("utf-8")
    (out / "jury.json").write_bytes(data)
    report = AnalysisReport(
        job_id=job.job_id, status="succeeded", mode="test", source=None,
        versions=RunVersions(contract_version="g0-1.0.0", code_version="test", config_version="test",
                             profile_id="test", registry_version="fixture",
                             model=ModelInfo(adapter="fake", model_id="fake", revision="0", local_path_label="fake",
                                             is_mock=True, warmed_up=True, device="cpu")),
        stage_timings_ms={}, upload_to_done_ms=1500, processing_ms=1000, coverage=None,
        completed_stages=["probing", "sampling", "observing", "verifying", "rendering_evidence", "committing"],
        visual_limitations_ru=["Синтетические данные для теста"], events=events,
        jury_result=jury)
    (out / "report.json").write_text(report.model_dump_json(), encoding="utf-8")
    return out



def later(sec: float) -> str:
    return iso_utc(utc_now() + timedelta(seconds=sec))
