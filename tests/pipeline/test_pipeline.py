"""Orchestration tests with explicit test doubles (plumbing only; not detection evidence)."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from cashwatch.deadline import Deadline, iso_utc, utc_now
from cashwatch.dto import JobInput, ts
from cashwatch.errors import PipelineError
from cashwatch.pipeline.analyze import LeaseLost, PipelineContext, analyze_job
from cashwatch.pipeline.planner import build_plan, coarse_windows
from cashwatch.pipeline.validate import validate_result
from cashwatch.settings import REPO_ROOT
from tests.pipeline import fakes

PROFILE = json.loads((REPO_ROOT / "configs" / "runtime-profile.json").read_text())


def _job() -> JobInput:
    now = iso_utc(utc_now())
    return JobInput(job_id="00000000-0000-4000-8000-0000000000aa", principal_id="jury", source_path="jobs/x/source.mov",
                    source_sha256=fakes.SHA, request_started_at=now, accepted_at=now, deadline_at=now, attempt=1,
                    lease_generation=1, config_version="t")


def _ctx(perception, *, media=None, approved=True, progress=None) -> PipelineContext:
    reg = fakes.FakeRegistry(approved)
    return PipelineContext(
        media=media or fakes.FakeMedia(fakes.meta()), perception=perception, registry=reg,
        rules_factory=fakes.FakeRules, reporting=fakes.FakeReporting(), profile=PROFILE, mode="test",
        limits={"max_duration_sec": 300, "max_pixel_area": 3840 * 2160},
        progress=progress or (lambda s, p: True))


def _run(tmp_path: Path, ctx, deadline_sec: float = 200):
    work, out = tmp_path / "work", tmp_path / "out"
    work.mkdir()
    out.mkdir()
    return analyze_job(_job(), tmp_path / "src.mov", work, out, ctx, Deadline.in_seconds(deadline_sec)), out


@pytest.mark.parametrize("duration", [0.5, 10.0, 12.0, 40.0, 145.0, 300.0])
def test_coarse_plan_covers_whole_clip(duration):
    m = fakes.meta(duration)
    ws = coarse_windows(m, PROFILE["sampling"])
    assert ws[0].start_sec == ts(m.first_frame_sec)
    assert ws[-1].end_sec == ts(m.last_frame_sec)
    assert ws[-1].requested_sample_times[-1] == ts(m.last_frame_sec)
    for a, b in zip(ws, ws[1:]):
        assert b.start_sec <= a.end_sec, "windows must overlap or touch (no gaps)"
    for w in ws:
        assert len(w.requested_sample_times) <= PROFILE["sampling"]["max_frames_per_observation"]
        gaps = [y - x for x, y in zip(w.requested_sample_times, w.requested_sample_times[1:])]
        assert all(g <= 1.0 / PROFILE["sampling"]["uniform_fps"] + 1e-3 for g in gaps)
    if duration == 145.0:
        assert 15 <= len(ws) <= 17  # spec §9.2 estimate (~16 windows)


def test_single_event_violation_validated(tmp_path):
    report, out = _run(tmp_path, _ctx(fakes.FakePerception(event_times=[20.0])))
    jury = report.jury_result
    assert jury.verdict == "violation" and len(jury.violations) == 1
    v = jury.violations[0]
    ev = report.events[0]
    assert v.timecode_sec == ev.evidence[0].timestamp_sec  # anchor == actual evidence frame time
    assert v.timecode_sec != 20.0 and abs(v.timecode_sec - 20.0) < 0.2  # actual PTS, not requested time
    assert base64.b64decode(v.evidence_frames[0].image_base64)[:3] == b"\xff\xd8\xff"
    assert "https://adilet.zan.kz/" in v.description
    assert json.loads((out / "jury.json").read_text())["verdict"] == "violation"
    assert set(report.coverage.required_window_ids) <= set(report.coverage.completed_window_ids)


def test_negative_clip_no_violation(tmp_path):
    report, out = _run(tmp_path, _ctx(fakes.FakePerception()))
    assert report.jury_result.verdict == "no_violation" and report.jury_result.violations == []
    assert (out / "jury.json").exists()


def test_two_separate_events_kept_and_overlap_duplicate_merged(tmp_path):
    report, _ = _run(tmp_path, _ctx(fakes.FakePerception(event_times=[10.0, 30.0])))
    times = [v.timecode_sec for v in report.jury_result.violations]
    assert len(times) == 2 and times == sorted(times)


def test_mandatory_window_failure_fails_job(tmp_path):
    with pytest.raises(PipelineError) as e:
        _run(tmp_path, _ctx(fakes.FakePerception(fail_window="w002")))
    assert e.value.code == "model_output_invalid"
    assert not (tmp_path / "out" / "jury.json").exists()


def test_hallucinated_frame_id_rejected(tmp_path):
    with pytest.raises(PipelineError) as e:
        _run(tmp_path, _ctx(fakes.FakePerception(bad_frame_window="w001")))
    assert e.value.code == "model_output_invalid"


def test_unverifiable_candidate_never_becomes_clean(tmp_path):
    with pytest.raises(PipelineError):
        _run(tmp_path, _ctx(fakes.FakePerception(event_times=[20.0], verify_fails=True)))
    assert not (tmp_path / "out" / "jury.json").exists()


def test_contradicted_verification_rejects_candidate(tmp_path):
    report, _ = _run(tmp_path, _ctx(fakes.FakePerception(event_times=[20.0], verify_state="contradicted")))
    assert report.jury_result.verdict == "no_violation"


def test_blank_video_is_failed_not_clean(tmp_path):
    media = fakes.FakeMedia(fakes.meta(), blank=True)
    # blank frames: overwrite generator so thumbnails are uniform
    with pytest.raises(PipelineError) as e:
        _run(tmp_path, _ctx(fakes.FakePerception(), media=_BlankMedia(fakes.meta())))
    assert e.value.code == "input_unusable"
    _ = media


class _BlankMedia(fakes.FakeMedia):
    def _frame(self, t, work_dir):
        f = super()._frame(t, work_dir)
        from PIL import Image
        Image.new("RGB", (64, 36), (0, 0, 0)).save(work_dir / f.local_path, format="JPEG")
        return f


def test_deadline_exceeded_is_failure(tmp_path):
    with pytest.raises(PipelineError) as e:
        _run(tmp_path, _ctx(fakes.FakePerception()), deadline_sec=5)
    assert e.value.code == "deadline_exceeded"


def test_lease_loss_stops_without_publishing(tmp_path):
    calls = {"n": 0}

    def progress(stage, p):
        calls["n"] += 1
        return stage != "observing"

    with pytest.raises(LeaseLost):
        _run(tmp_path, _ctx(fakes.FakePerception(event_times=[20.0]), progress=progress))
    assert not (tmp_path / "out" / "jury.json").exists()


def test_proposal_windows_do_not_replace_mandatory(tmp_path):
    p = fakes.FakePerception()
    report, _ = _run(tmp_path, _ctx(p))
    plan = build_plan(fakes.meta(), PROFILE)
    assert set(plan.mandatory_window_ids) <= set(p.observed)
    assert any(w.startswith("m") for w in p.observed)  # motion proposals added on top


def test_validator_rejects_tampering(tmp_path):
    report, out = _run(tmp_path, _ctx(fakes.FakePerception(event_times=[20.0])))
    reg = fakes.FakeRegistry()
    validate_result(report, out, reg)
    v = report.jury_result.violations[0]

    def tampered(**upd):
        j = report.jury_result.model_copy(update={"violations": [v.model_copy(update=upd)]})
        return report.model_copy(update={"jury_result": j})

    with pytest.raises(PipelineError):  # anchor not an evidence timestamp
        validate_result(tampered(timecode_sec=v.timecode_sec + 0.5), out, reg)
    with pytest.raises(PipelineError):  # description not from template
        validate_result(tampered(description=v.description + " кража"), out, reg)
    with pytest.raises(PipelineError):  # rule no longer approved
        validate_result(report, out, fakes.FakeRegistry(approved=False))
    with pytest.raises(PipelineError):  # verdict mismatch
        bad = report.jury_result.model_copy(update={"verdict": "no_violation"})
        validate_result(report.model_copy(update={"jury_result": bad}), out, reg)


def test_tier_selection_is_prevalidated_and_fits_budget():
    from cashwatch.pipeline.planner import count_coarse_windows, select_tier
    prof = {**PROFILE, "budget_sec": {"observe_total": 130}, "sampling_tiers": [
        {"tier_id": "dense", "est_window_sec": 20.0},
        {"tier_id": "sparse", "window_sec": 24, "uniform_fps": 0.67, "est_window_sec": 12.0},
    ]}
    pid_short, s_short = select_tier(fakes.meta(30.0), prof)
    pid_long, s_long = select_tier(fakes.meta(145.0), prof)
    assert pid_short.endswith("/dense") and pid_long.endswith("/sparse")
    assert count_coarse_windows(145.0, s_long) * 12.0 <= 130
    with pytest.raises(PipelineError):
        select_tier(fakes.meta(300.0), {**prof, "sampling_tiers": prof["sampling_tiers"][:1]})


def test_rule_coverage_and_review_flag(tmp_path):
    # FakePerception: cue supported only near events, "unknown" elsewhere -> unknown windows are unverifiable
    report, _ = _run(tmp_path, _ctx(fakes.FakePerception(event_times=[20.0])))
    (rc,) = report.rule_coverage
    assert rc.rule_id == "TEST01" and rc.status == "partially_checked" and rc.unverifiable_intervals
    assert report.review_required is True
    assert report.quality.sampled_frames > 0 and report.quality.dark_frames == 0


def test_rule_not_checkable_when_all_unknown(tmp_path):
    report, _ = _run(tmp_path, _ctx(fakes.FakePerception()))
    assert report.jury_result.verdict == "no_violation"
    assert report.rule_coverage[0].status == "not_checkable"
    assert report.review_required is True  # clean verdict, but a human should know the rule was not checkable


def test_dark_frames_reported(tmp_path):
    class Dark(fakes.FakeMedia):
        def _frame(self, t, work_dir):
            f = super()._frame(t, work_dir)
            from PIL import Image, ImageDraw
            im = Image.new("RGB", (64, 36), (5, 5, 5))
            ImageDraw.Draw(im).rectangle([10, 10, 20, 20], fill=(60, 60, 60))
            im.save(work_dir / f.local_path, format="JPEG")
            return f
    report, _ = _run(tmp_path, _ctx(fakes.FakePerception(), media=Dark(fakes.meta())))
    assert report.quality.dark_frames == report.quality.sampled_frames
    assert any("тёмн" in n for n in report.quality.notes_ru)


def test_model_limitation_text_not_in_reviewer_notice(tmp_path):
    class Chatty(fakes.FakePerception):
        def observe(self, req, work_dir, deadline):
            b = super().observe(req, work_dir, deadline)
            return b.model_copy(update={"limitations": ["MODEL-FREE-TEXT limitation"]})
    report, _ = _run(tmp_path, _ctx(Chatty()))
    assert "MODEL-FREE-TEXT" not in (report.coverage_notice_ru or "")
    assert "MODEL-FREE-TEXT limitation" in report.coverage.visual_limitations  # kept privately


def test_test_registry_clean_result_is_labeled_meaningless(tmp_path):
    ctx = _ctx(fakes.FakePerception())
    ctx.registry.test_only = True
    report, _ = _run(tmp_path, ctx)
    assert report.jury_result.verdict == "no_violation"
    assert "ТЕСТОВЫЙ РЕЕСТР" in report.coverage_notice_ru
