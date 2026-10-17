"""Test doubles for pipeline orchestration tests (TEST ONLY — never imported by production code).

The fake media layer writes generated synthetic JPEGs; timings follow a fixed 30 fps PTS grid
with time_base 1/30000 and a 1001 tick step (29.97 fps) so that frame times are not round
numbers and anchor equality is meaningful.
"""

from __future__ import annotations

import hashlib
import io
from fractions import Fraction
from pathlib import Path

from PIL import Image, ImageDraw

from cashwatch.dto import (
    AcceptedEvent,
    CandidateEvent,
    EvidenceFrame,
    FrameManifest,
    FrameRef,
    ModelInfo,
    MotionSample,
    ObservationBatch,
    PredicateAssessment,
    PredicateDefinition,
    ReviewNote,
    RuleRef,
    SamplingPlan,
    VerificationRequest,
    VerificationResult,
    VideoMetadata,
    ts,
)

SHA = "a" * 64
PH = "b" * 64
TB = Fraction(1, 30000)
STEP = 1001


def meta(duration: float = 40.0) -> VideoMetadata:
    n = int(duration / float(STEP * TB))
    last = float((n - 1) * STEP * TB)
    return VideoMetadata(
        source_sha256=SHA, size_bytes=1000, container="mov,mp4,m4a,3gp,3g2,mj2", codec="h264",
        width=640, height=360, display_rotation=0, display_width=640, display_height=360,
        duration_sec=float(n * STEP * TB), time_base_num=1, time_base_den=30000, origin_pts=0,
        origin_method="test", first_frame_sec=0.0, last_frame_sec=last, nominal_fps=29.97, frame_count=n)


def _jpeg(t: float, blank: bool) -> bytes:
    img = Image.new("RGB", (64, 36), (128, 128, 128))
    if not blank:
        d = ImageDraw.Draw(img)
        d.rectangle([5, 5, 30, 30], outline=(255, 255, 0))
        d.rectangle([int(t) % 50, 10, int(t) % 50 + 8, 18], fill=(255, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


class FakeMedia:
    def __init__(self, m: VideoMetadata, blank: bool = False):
        self.m, self.blank = m, blank

    def _frame(self, t: float, work_dir: Path) -> FrameRef:
        pts = min(int(-(-Fraction(t).limit_denominator(10**6) // (STEP * TB))) * STEP, (self.m.frame_count - 1) * STEP)
        rel = float(pts * TB)
        fid = f"p{pts}"
        p = work_dir / "frames" / f"{fid}.jpg"
        p.parent.mkdir(parents=True, exist_ok=True)
        data = _jpeg(rel, self.blank)
        p.write_bytes(data)
        return FrameRef(frame_id=fid, source_sha256=SHA, ordinal=pts // STEP, pts=pts, time_base_num=1,
                        time_base_den=30000, relative_sec=rel, local_path=f"frames/{fid}.jpg",
                        sha256=hashlib.sha256(data).hexdigest(), width=64, height=36)

    def probe_video(self, local_path, *, timeout_sec):
        return self.m

    def motion_profile(self, local_path, meta, *, fps, deadline):
        return [MotionSample(relative_sec=i / fps, score=1.0 if abs(i / fps - 20) < 0.3 else 0.1)
                for i in range(int(meta.duration_sec * fps))]

    def sample_video(self, local_path, meta, plan: SamplingPlan, work_dir, *, deadline):
        frames: dict[str, FrameRef] = {}
        wf = {}
        for w in plan.windows:
            ids = []
            for t in w.requested_sample_times:
                f = self._frame(t, work_dir)
                frames.setdefault(f.frame_id, f)
                if f.frame_id not in ids:
                    ids.append(f.frame_id)
            wf[w.window_id] = ids
        fl = sorted(frames.values(), key=lambda f: f.relative_sec)
        return FrameManifest(video=meta, frames=fl, window_frames=wf, coverage_first_sec=fl[0].relative_sec,
                             coverage_last_sec=fl[-1].relative_sec)

    def frames_for_times(self, local_path, meta, times_sec, work_dir, *, long_side_px, deadline):
        out = {}
        for t in times_sec:
            f = self._frame(t, work_dir)
            out[f.frame_id] = f
        return sorted(out.values(), key=lambda f: f.relative_sec)

    def extract_evidence(self, local_path, meta, frame_refs, out_dir, *, long_side_px, jpeg_quality, deadline):
        res = []
        for f in frame_refs:
            data = _jpeg(f.relative_sec, False)
            rel = f"evidence/{f.frame_id}.jpg"
            (out_dir / "evidence").mkdir(parents=True, exist_ok=True)
            (out_dir / rel).write_bytes(data)
            res.append(EvidenceFrame(evidence_id=f"e-{f.frame_id}", frame_id=f.frame_id, source_sha256=SHA, pts=f.pts,
                                     time_base_num=1, time_base_den=30000, timestamp_sec=ts(f.relative_sec),
                                     jpeg_path=rel, jpeg_sha256=hashlib.sha256(data).hexdigest(), width=64,
                                     height=36, jpeg_quality=jpeg_quality))
        return res

    def resolve_frame(self, frame, work_dir):
        return work_dir / frame.local_path

    def make_preview(self, *a, **k):
        return None


class FakePerception:
    """Scripted: cue 'marker_out' supported in windows whose interval contains `event_times`."""

    def __init__(self, event_times=(), fail_window=None, bad_frame_window=None, verify_fails=False,
                 verify_state="supported"):
        self.event_times = list(event_times)
        self.fail_window, self.bad_frame_window = fail_window, bad_frame_window
        self.verify_fails, self.verify_state = verify_fails, verify_state
        self.observed: list[str] = []

    def info(self):
        return ModelInfo(adapter="fake", model_id="fake", revision="0", local_path_label="-", is_mock=True,
                         warmed_up=True, device="cpu")

    def ready(self):
        return True

    def warmup(self, *, timeout_sec):
        pass

    def observe(self, req, work_dir, deadline):
        from cashwatch.errors import PipelineError
        self.observed.append(req.window_id)
        if req.window_id == self.fail_window:
            raise PipelineError("model_output_invalid", "scripted")
        hit = [f for f in req.frames if any(abs(f.relative_sec - t) < 0.6 for t in self.event_times)]
        cues = []
        for c in req.cue_predicates:
            cues.append(PredicateAssessment(
                predicate_id=c.predicate_id, state="supported" if hit else "unknown",
                supporting_frame_ids=[hit[0].frame_id] if hit else []))
        obs_frames = [req.frames[0].frame_id] if req.window_id != self.bad_frame_window else ["p999999999"]
        from cashwatch.dto import Observation
        return ObservationBatch(
            window_id=req.window_id, cue_assessments=cues, parse_status="ok", elapsed_ms=1, model_version="fake",
            prompt_sha256=PH,
            observations=[Observation(observation_id=f"{req.window_id}-o1", frame_ids=obs_frames,
                                      action="synthetic marker visible", visibility="clear")])

    def verify(self, req: VerificationRequest, work_dir, deadline):
        from cashwatch.errors import PipelineError
        if self.verify_fails:
            raise PipelineError("model_output_invalid", "scripted")
        hit = [f for f in req.frames if any(abs(f.relative_sec - t) < 0.15 for t in self.event_times)]
        anchor = hit[0] if hit else req.frames[len(req.frames) // 2]
        return VerificationResult(
            candidate_id=req.candidate_id, interval_complete=True, visibility_sufficient=True, schema_valid=True,
            parse_status="ok", elapsed_ms=1, model_version="fake", prompt_sha256=PH,
            assessments=[PredicateAssessment(predicate_id=p.predicate_id, state=self.verify_state,
                                             supporting_frame_ids=[anchor.frame_id]) for p in req.predicates])

    def close(self):
        pass


RULE = RuleRef(rule_id="TEST01", rule_version="1", type_ru="Синтетический тест", document_title="ТЕСТОВОЕ ПРАВИЛО",
               authority="test", document_date="2019-11-29", document_number="231", clause="0",
               official_url="https://adilet.zan.kz/rus/docs/V1900019680", local_excerpt="synthetic",
               source_snapshot_sha256="c" * 64)


class FakeRegistry:
    version = "test-1"

    def __init__(self, approved=True):
        self.approved = approved

    def approved_rule_ids(self):
        return ["TEST01"] if self.approved else []

    def rule_ref(self, rule_id):
        if rule_id != "TEST01" or not self.approved:
            raise KeyError(rule_id)
        return RULE

    def cue_predicates(self):
        return [PredicateDefinition(predicate_id="TEST01.marker_out", question_en="Is the red square outside the zone?")]

    def health_errors(self):
        return []


class FakeRules:
    """Minimal deterministic rules double: one candidate per window with a supported cue."""

    def __init__(self, registry):
        self.registry = registry
        self._notes: list[ReviewNote] = []

    def propose(self, batches, manifest):
        out = []
        for b in batches:
            for a in b.cue_assessments:
                if a.state == "supported":
                    f = next(fr for fr in manifest.frames if fr.frame_id == a.supporting_frame_ids[0])
                    out.append(CandidateEvent(candidate_id=f"c-{b.window_id}", rule_id="TEST01", rule_version="1",
                                              start_sec=f.relative_sec, end_sec=f.relative_sec,
                                              anchor_frame_id=None, assessments=[a]))
        return out

    def build_verification(self, cand, frames, *, job_id, max_output_tokens):
        return VerificationRequest(job_id=job_id, candidate_id=cand.candidate_id, rule_id=cand.rule_id,
                                   predicates=self.registry.cue_predicates(), interval_start_sec=cand.start_sec,
                                   interval_end_sec=cand.end_sec, frames=frames, task_version="t",
                                   max_output_tokens=max_output_tokens)

    def decide(self, cand, result, frames, manifest):
        ok = all(a.state == "supported" for a in result.assessments)
        if not ok:
            return cand.model_copy(update={"review_state": "rejected"})
        anchor = result.assessments[0].supporting_frame_ids[0]
        f = next(fr for fr in frames if fr.frame_id == anchor)
        after = [fr.frame_id for fr in frames if fr.relative_sec > f.relative_sec][:1]
        return cand.model_copy(update={"review_state": "accepted", "anchor_frame_id": anchor,
                                       "start_sec": f.relative_sec, "end_sec": f.relative_sec + 1,
                                       "evidence_frame_ids": [anchor, *after]})

    def merge(self, accepted, frames):
        accepted = sorted(accepted, key=lambda c: c.start_sec)
        out: list[CandidateEvent] = []
        for c in accepted:
            if out and c.start_sec <= out[-1].end_sec + 1.0:  # overlapping interval = same action
                continue
            out.append(c)
        return [AcceptedEvent(event_id=f"ev{i}", rule=RULE, start_sec=c.start_sec, end_sec=c.end_sec,
                              anchor_frame_id=c.anchor_frame_id, evidence_frame_ids=c.evidence_frame_ids,
                              factual_text_ru="красный квадрат вне жёлтой зоны", template_id="t1",
                              provenance_candidate_ids=[c.candidate_id]) for i, c in enumerate(out)]

    def notes(self):
        return self._notes


class FakeReporting:
    def describe(self, event, anchor_sec):
        r = event.rule
        return (f"На {anchor_sec:.3f} с видно: {event.factual_text_ru}. Требует проверки сотрудником. "
                f"Источник: {r.document_title}, п. {r.clause}; {r.official_url}")

    def coverage_notice_ru(self, verdict, limitations):
        return "В пределах видимых действий и проверяемых правил"
