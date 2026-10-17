"""Generate non-sensitive SYNTHETIC contract fixtures for parallel lane work.

Outputs (committed): contracts/ui-report.schema.json, contracts/fixtures/*.json, *.jpg.
Images are generated geometry with a visible "SYNTHETIC" label — not bank footage and not
evidence of any detection. Rule metadata in these fixtures is a placeholder marked
FIXTURE and must never be used as a real legal reference.
"""

from __future__ import annotations

import base64
import io
import json
from pathlib import Path

from PIL import Image, ImageDraw

from cashwatch.dto import (
    ERROR_MESSAGES_RU,
    EventReview,
    Interval,
    JobListResponse,
    JobSummary,
    RuleCoverage,
    JuryDoneResult,
    JuryEvidenceFrame,
    JuryViolation,
    StageProgress,
    UiEvent,
    UiEvidence,
    UiInterval,
    UiReport,
    UiSource,
)

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "contracts" / "fixtures"
JOB = "00000000-0000-4000-8000-000000000001"


def synthetic_jpeg(label: str, t: float) -> bytes:
    img = Image.new("RGB", (640, 360), (40, 44, 52))
    d = ImageDraw.Draw(img)
    for i in range(0, 640, 40):
        d.line([(i, 0), (i, 360)], fill=(60, 66, 78))
    d.rectangle([200, 120, 440, 240], outline=(230, 180, 60), width=4)
    d.text((16, 16), f"SYNTHETIC FIXTURE {label}", fill=(255, 255, 255))
    d.text((16, 330), f"t={t:.3f}s", fill=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (ROOT / "contracts" / "ui-report.schema.json").write_text(
        json.dumps(UiReport.model_json_schema(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    events = [("e1", 12.345, "FIXTURE-A"), ("e2", 47.0, "FIXTURE-B")]
    jury_violations = []
    ui_events = []
    for eid, t, rid in events:
        jpg = synthetic_jpeg(eid, t)
        (OUT / f"{eid}.jpg").write_bytes(jpg)
        desc = (f"[ТЕСТОВЫЕ ДАННЫЕ] На {t:.3f} с видно: синтетический объект. "
                "Требует проверки сотрудником. Источник: FIXTURE, не является нормой права.")
        jury_violations.append(JuryViolation(
            type=f"Тестовый тип {rid}", timecode_sec=t, description=desc,
            evidence_frames=[JuryEvidenceFrame(timestamp_sec=t, image_base64=base64.b64encode(jpg).decode())]))
        ui_events.append(UiEvent(
            event_id=eid, type=f"Тестовый тип {rid}", timecode_sec=t, description=desc,
            evidence=[UiEvidence(timestamp_sec=t, image_url=f"/api/v1/jobs/{JOB}/evidence/{eid}-0.jpg")],
            source=UiSource(rule_id=rid, document_title="FIXTURE — не является нормой права",
                            clause="0", official_url="https://adilet.zan.kz/rus/docs/FIXTURE",
                            local_excerpt="Синтетический фрагмент для проверки интерфейса."),
            interval=UiInterval(start_sec=t - 2, end_sec=t + 3),
            limitations_ru=["Синтетические данные для теста интерфейса"]))

    fixtures = {
        "jury-violation.json": JuryDoneResult(job_id=JOB, verdict="violation", violations=jury_violations),
        "jury-no-violation.json": JuryDoneResult(job_id=JOB, verdict="no_violation", violations=[]),
        "ui-report-processing.json": UiReport(
            job_id=JOB, status="processing", stage="observing", elapsed_sec=42.5, mode="test",
            progress=StageProgress(completed=5, total=16, unit="windows")),
        "ui-report-violation.json": UiReport(
            job_id=JOB, status="done", stage="complete", elapsed_sec=181.2, mode="test",
            verdict="violation", events=ui_events, media_available=True,
            coverage_notice_ru="В пределах видимых действий и проверяемых правил",
            rule_coverage=[
                RuleCoverage(rule_id="FIXTURE-A", type_ru="Тестовый тип FIXTURE-A", status="checked"),
                RuleCoverage(rule_id="FIXTURE-B", type_ru="Тестовый тип FIXTURE-B", status="partially_checked",
                             unverifiable_intervals=[Interval(start_sec=60.0, end_sec=72.0)],
                             note_ru="Зона работы с наличными перекрыта"),
                RuleCoverage(rule_id="FIXTURE-C", type_ru="Тестовый тип FIXTURE-C", status="not_checkable",
                             note_ru="Нужная зона не видна в кадре")],
            quality_notes_ru=["Часть кадров (3 из 120) слишком тёмная"], review_required=True,
            reviews=[EventReview(review_id="r1", event_id="e1", decision="confirmed", comment="Тестовый отзыв",
                                 reviewer="fixture", reviewed_at="2026-10-17T10:00:00.000+00:00")]),
        "ui-report-no-violation.json": UiReport(
            job_id=JOB, status="done", stage="complete", elapsed_sec=150.0, mode="test",
            verdict="no_violation", events=[], media_available=True,
            coverage_notice_ru="В пределах видимых действий и проверяемых правил"),
        "ui-report-failed.json": UiReport(
            job_id=JOB, status="failed", stage="observing", elapsed_sec=270.0, mode="test",
            error={"code": "deadline_exceeded", "message": ERROR_MESSAGES_RU["deadline_exceeded"]}),
    }
    fixtures["jobs-list.json"] = JobListResponse(jobs=[
        JobSummary(job_id=JOB, status="done", received_at="2026-10-17T09:57:00.000+00:00",
                   finished_at="2026-10-17T10:00:01.000+00:00", verdict="violation", event_count=2,
                   reviewed_event_count=1, review_required=True, mode="test"),
        JobSummary(job_id="00000000-0000-4000-8000-000000000002", status="failed",
                   received_at="2026-10-17T09:40:00.000+00:00", finished_at="2026-10-17T09:44:30.000+00:00",
                   mode="test", error_code="deadline_exceeded")])
    for name, model in fixtures.items():
        (OUT / name).write_text(model.model_dump_json(indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(fixtures)} fixtures to {OUT}")


if __name__ == "__main__":
    main()
