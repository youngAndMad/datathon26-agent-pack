"""Synthetic DTO builders for rules/reporting tests (geometry fixtures only; no real media).

The "oracle" stands in for the perception model by reading a synthetic ground-truth timeline
(e.g. where a red square is relative to a yellow zone). It exercises the deterministic rule
gates, never model quality.
"""

from __future__ import annotations

import hashlib
import shutil
from collections.abc import Callable
from pathlib import Path

import yaml

from cashwatch.dto import (
    FrameManifest,
    FrameRef,
    Observation,
    ObservationBatch,
    PredicateAssessment,
    VerificationResult,
    VideoMetadata,
)

REPO = Path(__file__).resolve().parents[2]
RULES_DIR = REPO / "rules"
SRC_SHA = "a" * 64
PROMPT_SHA = "b" * 64

State = str  # "supported" | "contradicted" | "unknown"
Oracle = Callable[[str, float], State]


def fid(t: float) -> str:
    return f"f{int(round(t * 1000)):07d}"


def frame(t: float, src: str = SRC_SHA) -> FrameRef:
    return FrameRef(frame_id=fid(t), source_sha256=src, ordinal=int(round(t * 30)), pts=int(round(t * 30000)),
                    time_base_num=1, time_base_den=30000, relative_sec=t, local_path=f"work/{fid(t)}.jpg",
                    sha256=hashlib.sha256(fid(t).encode()).hexdigest(), width=640, height=360)


def video(duration: float) -> VideoMetadata:
    return VideoMetadata(source_sha256=SRC_SHA, size_bytes=1000, container="mov", codec="h264", width=640,
                         height=360, display_rotation=0, display_width=640, display_height=360,
                         duration_sec=duration, time_base_num=1, time_base_den=30000, origin_pts=0,
                         origin_method="first_decoded_pts", first_frame_sec=0.0, last_frame_sec=duration)


def manifest(duration: float, win: float = 12.0, overlap: float = 3.0, step: float = 1.0) -> FrameManifest:
    times = [round(i * step, 3) for i in range(int(duration / step) + 1)]
    frames = [frame(t) for t in times]
    wf: dict[str, list[str]] = {}
    start, k = 0.0, 0
    while True:
        end = min(duration, start + win)
        wf[f"w{k:02d}"] = [fid(t) for t in times if start - 1e-9 <= t <= end + 1e-9]
        if end >= duration:
            break
        start, k = end - overlap, k + 1
    return FrameManifest(video=video(duration), frames=frames, window_frames=wf,
                         coverage_first_sec=times[0], coverage_last_sec=times[-1])


def assess(pid: str, frames: list[FrameRef], oracle: Oracle, *, bad_frame: bool = False) -> PredicateAssessment:
    states = [(f, oracle(pid, f.relative_sec)) for f in frames]
    sup = [f.frame_id for f, s in states if s == "supported"]
    con = [f.frame_id for f, s in states if s == "contradicted"]
    if bad_frame and sup:
        sup = sup + ["f9999999"]
    if sup:
        st = "supported"
    elif con and len(con) == len(frames):
        st = "contradicted"
    else:
        st = "unknown"
    return PredicateAssessment(predicate_id=pid, state=st, supporting_frame_ids=sup,
                               contradicting_frame_ids=con, visibility="clear")


def batches(m: FrameManifest, cue_ids: list[str], oracle: Oracle, observations: bool = False) -> list[ObservationBatch]:
    by_id = {f.frame_id: f for f in m.frames}
    out = []
    for w, ids in m.window_frames.items():
        fr = [by_id[i] for i in ids]
        obs = [Observation(observation_id=f"{w}-o1", frame_ids=[ids[0]], action="a shape moves", visibility="clear")] \
            if observations else []
        out.append(ObservationBatch(window_id=w, observations=obs,
                                    cue_assessments=[assess(p, fr, oracle) for p in cue_ids],
                                    parse_status="ok", elapsed_ms=1, model_version="synthetic-oracle",
                                    prompt_sha256=PROMPT_SHA))
    return out


def refine(start: float, end: float, fps: float = 4.0, src: str = SRC_SHA, last: float | None = None) -> list[FrameRef]:
    n = int((end - start) * fps + 1e-9)
    ts = [round(start + i / fps, 3) for i in range(n + 1)]
    if last is not None:
        ts = [t for t in ts if t <= last]
    return [frame(t, src) for t in ts]


def verify(cand_id: str, pred_ids: list[str], frames: list[FrameRef], oracle: Oracle, *,
           interval_complete: bool = True, visibility_sufficient: bool = True,
           extra: list[PredicateAssessment] | None = None, bad_frame: bool = False) -> VerificationResult:
    ass = [assess(p, frames, oracle, bad_frame=bad_frame) for p in pred_ids] + list(extra or [])
    return VerificationResult(candidate_id=cand_id, assessments=ass, interval_complete=interval_complete,
                              visibility_sufficient=visibility_sufficient, schema_valid=True, parse_status="ok",
                              elapsed_ms=1, model_version="synthetic-oracle", prompt_sha256=PROMPT_SHA)


# ---------------------------------------------------------------- tmp registry copies

def copy_rules(tmp: Path) -> Path:
    dst = tmp / "rules"
    shutil.copytree(RULES_DIR, dst)
    return dst


def edit_registry(path: Path, fn: Callable[[dict], None]) -> Path:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    fn(data)
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def approve_fully(rule: dict) -> None:
    """Simulate a *future* completed local validation (test-only; never committed)."""
    rule["state"] = "approved"
    rule["source_status"].update(applicability="verified", local_visual_validation="done")
    rule["approval"] = {"owner": "local-reviewer", "date": "2026-10-18",
                        "validation_counts": {"positive": 3, "negative": 5, "unknown": 1}, "notes": "synthetic"}


OMISSION_RULE = {
    "rule_id": "TEST02", "version": "1.0.0", "state": "approved", "disabled_reason": None,
    "symbol": "SYNTHETIC_TOKEN_NOT_TAPPED", "type_ru": "Синтетический тест: жетон убран без касания",
    "source_id": "TEST", "clause": "TEST02 (синтетический тест)", "excerpt_ids": ["TEST-fixture-1"],
    "source_status": {"clause_text": "fixture", "edition": "fixture", "applicability": "fixture",
                      "local_visual_validation": "fixture", "notes": ""},
    "last_verified": "2026-10-17", "paraphrase_ru": "Синтетика.",
    "applicability_en": ["Generated test video only."], "nonvisual_prerequisites_en": [],
    "predicates": [
        {"predicate_id": "TEST02.token_placed", "kind": "trigger", "required_state": "supported",
         "question_en": "Is a blue token placed on the table?", "counterexamples_en": []},
        {"predicate_id": "TEST02.token_tapped", "kind": "decisive", "required_state": "contradicted",
         "question_en": "Is the blue token tapped at any time?", "counterexamples_en": []},
        {"predicate_id": "TEST02.token_removed", "kind": "completion", "anchor": True, "required_state": "supported",
         "question_en": "Is the blue token removed from the table?", "counterexamples_en": []},
        {"predicate_id": "TEST02.table_visible", "kind": "visibility", "required_state": "supported",
         "question_en": "Is the table fully visible?", "counterexamples_en": []},
        {"predicate_id": "TEST02.excl_unseen", "kind": "exclusion", "required_state": "contradicted",
         "question_en": "Could a tap have happened unseen?", "counterexamples_en": []},
    ],
    "counterexamples_en": ["Tap between frames."], "boundary_notes_en": [], "visibility_requirements_en": [],
    "temporal": {"kind": "omission", "transition_from": None, "context_before_sec": 2.0, "context_after_sec": 2.0,
                 "requires_complete_interval": True, "max_frame_gap_sec": 1.0},
    "anchor_convention": "completion_transition", "evidence_strategy": "anchor_then_context",
    "explanation": {"template_id": "TEST02.v1", "fact_ru": "синий жетон убран со стола без видимого касания",
                    "interpretation_ru": "срабатывание синтетического теста пропуска действия",
                    "predicate_ru": "на всём видимом интервале касание не видно"},
    "limitations_ru": ["СИНТЕТИЧЕСКИЙ ТЕСТ."],
    "approval": {"owner": "agent-D-synthetic-fixture", "date": "2026-10-17", "validation_counts": None, "notes": ""},
}


# ---------------------------------------------------------------- TEST01 geometry oracle

def square_oracle(segments: list[tuple[float, float, str]]) -> Oracle:
    """segments: (start, end, 'in'|'out'|'occluded'); position of the red square over time."""
    def pos(t: float) -> str:
        for s, e, p in segments:
            if s - 1e-9 <= t < e - 1e-9:
                return p
        return segments[-1][2]

    def oracle(pid: str, t: float) -> State:
        p = pos(t)
        if pid == "TEST01.red_visible":
            return "unknown" if p == "occluded" else "supported"
        if pid == "TEST01.red_inside_zone":
            return {"in": "supported", "out": "contradicted"}.get(p, "unknown")
        if pid == "TEST01.red_outside_zone":
            return {"out": "supported", "in": "contradicted"}.get(p, "unknown")
        if pid == "TEST01.excl_ambiguous":
            return "supported" if p == "occluded" else "contradicted"
        return "unknown"

    return oracle


def run_pipeline(reg, m: FrameManifest, oracle: Oracle, *, last: float | None = None):
    """Mirror of the orchestrator's verifying stage with a synthetic oracle (no model)."""
    from cashwatch.rules import create_rules

    rules = create_rules(reg)
    cue_ids = [p.predicate_id for p in reg.cue_predicates()]
    cands = rules.propose(batches(m, cue_ids, oracle), m)
    all_frames = {f.frame_id: f for f in m.frames}
    decided = []
    for c in cands:
        rf = refine(max(0.0, c.start_sec - 3), min(m.video.last_frame_sec, c.end_sec + 3), last=last)
        for f in rf:
            all_frames.setdefault(f.frame_id, f)
        req = rules.build_verification(c, rf, job_id="job1", max_output_tokens=512)
        res = verify(c.candidate_id, [p.predicate_id for p in req.predicates], rf, oracle)
        decided.append(rules.decide(c, res, rf, m))
    acc = [d for d in decided if d.review_state == "accepted"]
    events = rules.merge(acc, list(all_frames.values())) if acc else []
    return events, decided, cands, rules, all_frames
