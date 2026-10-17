"""Projection of stored job state / AnalysisReport onto the UI report DTO (dto.UiReport).

Only public fields are copied: no local paths, prompts, model internals or hashes.
"""

from __future__ import annotations

from datetime import datetime

from cashwatch.deadline import parse_utc
from cashwatch.dto import (
    AnalysisReport,
    UiEvent,
    UiEvidence,
    UiInterval,
    UiReport,
    UiSource,
)
from cashwatch.storage import JobView


def evidence_url(job_id: str, evidence_id: str) -> str:
    return f"/api/v1/jobs/{job_id}/evidence/{evidence_id}.jpg"


def _elapsed(start: str, end: datetime) -> float:
    return round(max(0.0, (end - parse_utc(start)).total_seconds()), 3)


def project_events(job_id: str, report: AnalysisReport) -> list[UiEvent]:
    out: list[UiEvent] = []
    for re_ in report.events:
        ev = re_.event
        anchor = next((f for f in re_.evidence if f.frame_id == ev.anchor_frame_id), re_.evidence[0])
        out.append(UiEvent(
            event_id=ev.event_id,
            type=ev.rule.type_ru,
            timecode_sec=anchor.timestamp_sec,
            description=re_.description_ru,
            evidence=[UiEvidence(timestamp_sec=f.timestamp_sec, image_url=evidence_url(job_id, f.evidence_id))
                      for f in re_.evidence],
            source=UiSource(rule_id=ev.rule.rule_id, document_title=ev.rule.document_title,
                            clause=ev.rule.clause, official_url=ev.rule.official_url,
                            local_excerpt=ev.rule.local_excerpt),
            interval=UiInterval(start_sec=ev.start_sec, end_sec=ev.end_sec),
            limitations_ru=list(ev.limitations_ru),
        ))
    return out


def coverage_notice(report: AnalysisReport) -> str | None:
    # AnalysisReport carries no notice field yet (see coordination/requests/A.md); expose the
    # stored visual limitations verbatim rather than inventing text.
    if report.coverage_notice_ru:
        return report.coverage_notice_ru
    lim = list(report.visual_limitations_ru)
    if report.coverage is not None:
        lim.extend(x for x in report.coverage.visual_limitations if x not in lim)
    return "; ".join(lim) if lim else None


def build_ui_report(view: JobView, report: AnalysisReport | None, *, now: datetime, default_mode: str,
                    media_available: bool) -> UiReport:
    mode = report.mode if report is not None else default_mode
    if view.state == "succeeded":
        if report is None or report.jury_result is None:
            raise ValueError("succeeded job without a readable report")
        if report.upload_to_done_ms is not None:
            elapsed = round(report.upload_to_done_ms / 1000.0, 3)
        else:
            elapsed = _elapsed(view.received_at, parse_utc(view.finished_at) if view.finished_at else now)
        return UiReport(
            job_id=view.id, status="done", stage="complete", elapsed_sec=elapsed, progress=None, mode=mode,
            verdict=report.jury_result.verdict, coverage_notice_ru=coverage_notice(report),
            events=project_events(view.id, report), media_available=media_available, error=None)
    if view.state == "failed":
        end = parse_utc(view.finished_at) if view.finished_at else now
        return UiReport(
            job_id=view.id, status="failed", stage=view.stage or "receiving",
            elapsed_sec=_elapsed(view.received_at, end), progress=None, mode=mode,
            media_available=media_available, error=view.error())
    return UiReport(
        job_id=view.id, status="processing", stage=view.stage or "receiving",
        elapsed_sec=_elapsed(view.received_at, now), progress=view.progress, mode=mode,
        media_available=media_available)
