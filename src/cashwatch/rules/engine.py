"""Deterministic rules engine (implements cashwatch.ports.RulesPort).

neutral perception -> tri-state predicates -> curated approved rules -> explicit gates.

* Tri-state: ``unknown`` is never coerced to supported/contradicted. Assessments that cite no
  frame or a frame id that was not supplied are downgraded to ``unknown`` (conservative), and
  predicate ids not defined by the rule are ignored and recorded (model-invented ids/clauses
  can never create or support an event).
* Only approved registry rules can produce candidates. Unmapped observations become review
  notes, never violations.
* Acceptance requires every gate: approved rule/version, every non-exclusion predicate in its
  required state, no supported exclusion (strict exclusions must be contradicted), visibility
  sufficient, complete and densely sampled interval for omission rules, a real anchor frame among
  the supplied frames per the rule's anchor convention, evidence frames from the same source.
"""

from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from collections.abc import Iterable

from cashwatch.dto import (
    AcceptedEvent,
    CandidateEvent,
    FrameManifest,
    FrameRef,
    ObservationBatch,
    PredicateAssessment,
    ReviewNote,
    VerificationRequest,
    VerificationResult,
)
from cashwatch.errors import PipelineError
from cashwatch.reporting.templates import sanitize_fact
from cashwatch.rules.registry import Registry
from cashwatch.rules.schema import RuleSpec

TASK_VERSION = "verify-v1"
EDGE_TOL_SEC = 0.5  # sampled frames must reach this close to an omission interval's ends
_ID_SAFE = re.compile(r"[^A-Za-z0-9_.:\-]")


def _safe_id(s: str) -> str:
    return _ID_SAFE.sub("_", s)[:128]


def _short(s: str, n: int = 300) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


class Rules:
    """One instance per job; ``notes()`` returns that job's review notes."""

    def __init__(self, registry: Registry) -> None:
        self._reg = registry
        self._notes: list[ReviewNote] = []

    # ------------------------------------------------------------------ notes
    def notes(self) -> list[ReviewNote]:
        return list(self._notes)

    def _note(self, kind: str, start: float, end: float, text_ru: str) -> None:
        nid = f"note-{len(self._notes) + 1:04d}-{kind}"
        s, e = max(0.0, float(start)), max(0.0, float(end))
        self._notes.append(ReviewNote(note_id=nid, start_sec=min(s, e), end_sec=max(s, e), kind=kind,
                                      text_ru=_short(text_ru, 400)))

    # ------------------------------------------------------------------ predicate hygiene
    def _clean(
        self, rule: RuleSpec, assessments: Iterable[PredicateAssessment], valid_frames: set[str],
    ) -> tuple[dict[str, PredicateAssessment], list[str]]:
        defined = {p.predicate_id for p in rule.predicates}
        out: dict[str, PredicateAssessment] = {}
        issues: list[str] = []
        for a in assessments:
            if a.predicate_id not in defined:
                issues.append(f"undefined predicate id ignored: {a.predicate_id}"[:300])
                continue
            fixed = a
            cited = set(a.supporting_frame_ids) | set(a.contradicting_frame_ids)
            if not cited <= valid_frames:
                issues.append(f"{a.predicate_id}: cites frame ids that were not supplied -> unknown")
                fixed = _unknown(a)
            elif a.state == "supported" and not a.supporting_frame_ids:
                issues.append(f"{a.predicate_id}: supported without supporting frames -> unknown")
                fixed = _unknown(a)
            prev = out.get(a.predicate_id)
            if prev is not None and prev.state != fixed.state:
                issues.append(f"{a.predicate_id}: conflicting duplicate assessments -> unknown")
                fixed = _unknown(a)
            out[a.predicate_id] = fixed
        return out, issues

    # ------------------------------------------------------------------ propose
    def propose(self, batches: list[ObservationBatch], manifest: FrameManifest) -> list[CandidateEvent]:
        times = {f.frame_id: f.relative_sec for f in manifest.frames}
        video = manifest.video
        approved = self._reg.approved_rule_ids()
        out: list[CandidateEvent] = []
        used_ids: set[str] = set()
        for b in batches:
            wf = [i for i in manifest.window_frames.get(b.window_id, []) if i in times]
            if not wf:
                self._note("technical", 0.0, 0.0, f"Окно {b.window_id}: нет сопоставимых кадров; наблюдения не использованы")
                continue
            w_start = min(times[i] for i in wf)
            w_end = max(times[i] for i in wf)
            by_rule: dict[str, list[PredicateAssessment]] = defaultdict(list)
            for a in b.cue_assessments:
                rid = self._reg.predicate_rule(a.predicate_id)
                if rid is None or rid not in approved:
                    self._note("technical", w_start, w_end,
                               f"Окно {b.window_id}: оценка неизвестного или неутверждённого предиката проигнорирована")
                    continue
                by_rule[rid].append(a)
            mapped_obs: set[str] = set()
            for rid in approved:
                if rid not in by_rule:
                    continue
                rule = self._reg.rule(rid)
                ass, _issues = self._clean(rule, by_rule[rid], set(wf))
                if not _worth_verifying(rule, ass):
                    continue
                sup_frames = {f for a in ass.values() if a.state == "supported" for f in a.supporting_frame_ids}
                prov = [o.observation_id for o in b.observations if sup_frames & set(o.frame_ids)]
                mapped_obs.update(prov)
                t = rule.temporal
                start = max(video.first_frame_sec, w_start - t.context_before_sec)
                end = min(max(video.last_frame_sec, w_end), w_end + t.context_after_sec)
                anchor = None
                ap = rule.anchor_predicate
                if ap and ap.predicate_id in ass and ass[ap.predicate_id].state == "supported":
                    anchor = min(ass[ap.predicate_id].supporting_frame_ids, key=lambda i: (times[i], i))
                cid = _safe_id(f"cand-{rid}-{b.window_id}")
                k = 2
                while cid in used_ids:
                    cid = _safe_id(f"cand-{rid}-{b.window_id}-{k}")
                    k += 1
                used_ids.add(cid)
                out.append(CandidateEvent(
                    candidate_id=cid, rule_id=rid, rule_version=rule.version, start_sec=start,
                    end_sec=max(start, end), anchor_frame_id=anchor, assessments=_ordered(rule, ass),
                    counterexamples_considered=[_short(c, 400) for c in rule.counterexamples_en],
                    provenance_observation_ids=prov, review_state="proposed"))
            for o in b.observations:
                if o.observation_id in mapped_obs:
                    continue
                ot = [times[i] for i in o.frame_ids if i in times] or [w_start, w_end]
                if o.visibility == "unusable":
                    self._note("visibility", min(ot), max(ot),
                               f"Наблюдение {o.observation_id} (окно {b.window_id}): видимость недостаточна")
                else:
                    self._note("unmapped_behavior", min(ot), max(ot),
                               f"Наблюдение {o.observation_id} (окно {b.window_id}) не сопоставлено ни с одним "
                               "утверждённым правилом; не является нарушением, требуется ручной просмотр")
        out.sort(key=lambda c: (c.start_sec, c.candidate_id))
        return out

    # ------------------------------------------------------------------ verification request
    def build_verification(
        self, candidate: CandidateEvent, frames: list[FrameRef], *, job_id: str, max_output_tokens: int,
    ) -> VerificationRequest:
        try:
            rule = self._reg.rule(candidate.rule_id)
        except KeyError as e:
            raise PipelineError("rule_registry_invalid", "candidate rule not approved") from e
        if candidate.rule_version != rule.version:
            raise PipelineError("rule_registry_invalid", "candidate rule version mismatch")
        if not frames:
            raise PipelineError("internal_error", "no frames supplied for verification")
        ordered = sorted(frames, key=lambda f: (f.relative_sec, f.frame_id))
        scope = [_short(s) for s in rule.applicability_en]
        scope += [_short("Not visible; never assume: " + s) for s in rule.nonvisual_prerequisites_en]
        notes = [
            "Answer every predicate with supported, contradicted or unknown. Use unknown whenever it is not clearly visible.",
            "Cite only the supplied frame ids. Supporting frames must directly show the predicate.",
            "Do not identify people, infer intent or authorization, or state legal conclusions.",
        ]
        t = rule.temporal
        if t.kind == "transition" and t.transition_from:
            notes.append(_short(f"The decisive change counts only after '{t.transition_from}' was visible in an earlier frame."))
        if t.requires_complete_interval:
            notes.append(_short(
                f"Set interval_complete=true only if the whole interval {candidate.start_sec:.1f}-"
                f"{candidate.end_sec:.1f} s is visible without cuts, gaps or occlusion of the relevant area."))
        notes += [_short(s) for s in rule.boundary_notes_en]
        notes += [_short("Visibility requirement: " + s) for s in rule.visibility_requirements_en]
        notes += [_short("Compliant alternative to check: " + s) for s in rule.counterexamples_en]
        return VerificationRequest(
            job_id=job_id, candidate_id=candidate.candidate_id, rule_id=rule.rule_id,
            predicates=self._reg.definitions(rule.rule_id), scope_conditions_en=scope,
            boundary_notes_en=notes, interval_start_sec=candidate.start_sec,
            interval_end_sec=max(candidate.start_sec, candidate.end_sec), frames=ordered,
            requires_complete_interval=t.requires_complete_interval,
            task_version=f"{TASK_VERSION}:{self._reg.version}:{rule.rule_id}@{rule.version}",
            max_output_tokens=max_output_tokens)

    # ------------------------------------------------------------------ decide
    def decide(
        self, candidate: CandidateEvent, result: VerificationResult, frames: list[FrameRef],
        manifest: FrameManifest,
    ) -> CandidateEvent:
        rejected: list[str] = []
        unresolved: list[str] = []
        try:
            rule = self._reg.rule(candidate.rule_id)
        except KeyError:
            return self._finish(candidate, "rejected", ["rule is unknown or not approved"])
        if candidate.rule_version != rule.version:
            return self._finish(candidate, "rejected", ["rule version differs from approved registry"])
        if result.candidate_id != candidate.candidate_id:
            return self._finish(candidate, "rejected", ["verification result belongs to another candidate"])
        src = manifest.video.source_sha256
        fmap = {f.frame_id: f for f in frames if f.source_sha256 == src}
        if len(fmap) != len({f.frame_id for f in frames}):
            rejected.append("supplied frame from a different source")
        times = {i: f.relative_sec for i, f in fmap.items()}
        ass, issues = self._clean(rule, result.assessments, set(times))
        ass = _augment_transition(rule, ass, times)
        if not result.schema_valid:
            unresolved.append("verification output schema invalid")

        t = rule.temporal
        trans_from = t.transition_from if t.kind == "transition" else None
        for p in rule.predicates:
            a = ass.get(p.predicate_id)
            st = a.state if a else "unknown"
            if p.predicate_id == trans_from and st == "contradicted" and a and a.supporting_frame_ids:
                # A state that held before the decisive change is time-varying by definition;
                # its earlier supporting frames are checked against the anchor below.
                st = "supported"
            if p.kind == "exclusion":
                if st == "supported":
                    rejected.append(f"exclusion supported: {p.predicate_id}")
                elif p.required_state == "contradicted" and st != "contradicted":
                    unresolved.append(f"exclusion not ruled out: {p.predicate_id}")
            elif st == p.required_state:
                if st == "contradicted" and not (a and a.contradicting_frame_ids):
                    unresolved.append(f"absence not grounded in frames: {p.predicate_id}")
            elif st == "unknown":
                unresolved.append(f"required predicate unknown: {p.predicate_id}")
            elif p.kind == "completion":
                # completion not (yet) seen: truncated/ongoing opportunity -> unknown, not a negative
                unresolved.append(f"completion not observed: {p.predicate_id}")
            else:
                rejected.append(f"required predicate {st}: {p.predicate_id}")

        if not result.visibility_sufficient:
            unresolved.append("visibility insufficient")
        t = rule.temporal
        if t.requires_complete_interval:
            if not result.interval_complete:
                unresolved.append("opportunity interval incomplete")
            elif not _interval_sampled(sorted(times.values()), candidate.start_sec, candidate.end_sec,
                                       t.max_frame_gap_sec or 1.0):
                unresolved.append("opportunity interval not densely sampled end to end")
            if candidate.end_sec >= manifest.video.last_frame_sec - EDGE_TOL_SEC and not _completion_inside(
                    rule, ass, times, manifest.video.last_frame_sec):
                unresolved.append("clip ends before the opportunity interval is complete")

        anchor_id = _anchor(rule, ass, times)
        if anchor_id is None:
            unresolved.append("no anchor frame per rule anchor convention")

        state = "rejected" if rejected else ("unresolved" if unresolved else "accepted")
        reasons = rejected + unresolved + issues
        upd: dict = {
            "assessments": _ordered(rule, ass),
            "interval_complete": bool(result.interval_complete),
            "visibility_sufficient": bool(result.visibility_sufficient),
            "counterexamples_considered": [_short(c, 400) for c in
                                           (list(rule.counterexamples_en) + list(result.plausible_alternatives))[:20]],
        }
        if state == "accepted":
            at = times[anchor_id]
            start, end = _event_interval(rule, ass, times, at)
            upd.update(anchor_frame_id=anchor_id, start_sec=start, end_sec=end,
                       evidence_frame_ids=_evidence(rule, ass, times, anchor_id))
        return self._finish(candidate.model_copy(update=upd), state, reasons)

    def _finish(self, cand: CandidateEvent, state: str, reasons: list[str]) -> CandidateEvent:
        out = cand.model_copy(update={"review_state": state, "rejection_reasons": [_short(r, 400) for r in reasons][:30]})
        trace = os.environ.get("CASHWATCH_DEBUG_DECISIONS")  # dev evaluation only (local file)
        if trace:
            with open(trace, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"candidate": out.candidate_id, "rule": out.rule_id, "state": state,
                                     "start": out.start_sec, "end": out.end_sec, "reasons": reasons[:10],
                                     "assessments": [(a.predicate_id, a.state, len(a.supporting_frame_ids),
                                                      len(a.contradicting_frame_ids)) for a in out.assessments]},
                                    ensure_ascii=False) + "\n")
        if state != "accepted":
            word = "отклонён" if state == "rejected" else "не подтверждён (неопределённость)"
            self._note("unresolved_candidate", out.start_sec, out.end_sec,
                       f"Кандидат {out.candidate_id} по правилу {out.rule_id} {word}; причин: {len(reasons)}")
        return out

    # ------------------------------------------------------------------ merge
    def merge(self, accepted: list[CandidateEvent], frames: list[FrameRef]) -> list[AcceptedEvent]:
        times = {f.frame_id: f.relative_sec for f in frames}
        approved = set(self._reg.approved_rule_ids())
        items: list[CandidateEvent] = []
        for c in accepted:
            if (c.review_state != "accepted" or c.rule_id not in approved or c.anchor_frame_id not in times
                    or not c.evidence_frame_ids or any(i not in times for i in c.evidence_frame_ids)
                    or c.rule_version != self._reg.rule(c.rule_id).version):
                self._note("technical", c.start_sec, c.end_sec,
                           f"Кандидат {c.candidate_id} исключён при объединении: не прошёл повторную проверку")
                continue
            items.append(c)
        clusters: list[list[CandidateEvent]] = []
        for c in sorted(items, key=lambda c: (c.rule_id, times[c.anchor_frame_id], c.candidate_id)):
            rule = self._reg.rule(c.rule_id)
            target = None
            for cl in clusters:
                if cl[0].rule_id != c.rule_id:
                    continue
                s, e = min(x.start_sec for x in cl), max(x.end_sec for x in cl)
                if c.start_sec <= e and s <= c.end_sec and not _reset_between(rule, cl + [c], times):
                    target = cl
                    break
            if target is None:
                clusters.append([c])
            else:
                target.append(c)

        events: list[tuple[float, AcceptedEvent]] = []
        for cl in clusters:
            rule = self._reg.rule(cl[0].rule_id)
            first = min(cl, key=lambda x: (times[x.anchor_frame_id], x.candidate_id))
            anchor = first.anchor_frame_id
            ev_ids: list[str] = [anchor]
            for x in [first] + [y for y in cl if y is not first]:
                for i in x.evidence_frame_ids:
                    if i not in ev_ids and len(ev_ids) < 3:
                        ev_ids.append(i)
            at = times[anchor]
            start = min([x.start_sec for x in cl] + [at])
            end = max([x.end_sec for x in cl] + [at])
            events.append((at, AcceptedEvent(
                event_id="pending", rule=self._reg.rule_ref(rule.rule_id), start_sec=start, end_sec=end,
                anchor_frame_id=anchor, evidence_frame_ids=ev_ids,
                factual_text_ru=sanitize_fact(rule.explanation.fact_ru),
                template_id=rule.explanation.template_id, limitations_ru=list(rule.limitations_ru),
                provenance_candidate_ids=sorted(x.candidate_id for x in cl))))
        events.sort(key=lambda p: (p[0], p[1].rule.rule_id))
        return [ev.model_copy(update={"event_id": f"ev-{n:03d}-{ev.rule.rule_id}"})
                for n, (_, ev) in enumerate(events, start=1)]


# ---------------------------------------------------------------------- helpers

def _unknown(a: PredicateAssessment) -> PredicateAssessment:
    return a.model_copy(update={"state": "unknown"})


def _ordered(rule: RuleSpec, ass: dict[str, PredicateAssessment]) -> list[PredicateAssessment]:
    return [ass.get(p.predicate_id) or PredicateAssessment(predicate_id=p.predicate_id, state="unknown")
            for p in rule.predicates]


def _st(ass: dict[str, PredicateAssessment], pid: str) -> str:
    a = ass.get(pid)
    return a.state if a else "unknown"


def _worth_verifying(rule: RuleSpec, ass: dict[str, PredicateAssessment]) -> bool:
    """Coarse-pass proposal heuristic (recall-oriented; acceptance is decided by gates later)."""
    triggers = rule.by_kind("trigger")
    any_trig = any(_st(ass, p.predicate_id) == "supported" for p in triggers)
    decisive_pos = [p for p in rule.by_kind("decisive", "completion") if p.required_state == "supported"]
    decisive_neg = [p for p in rule.by_kind("decisive") if p.required_state == "contradicted"]
    if rule.temporal.kind == "omission":
        if any(_st(ass, p.predicate_id) == "supported" for p in decisive_neg):
            return False  # the required action was seen
        return all(_st(ass, p.predicate_id) == "supported" for p in triggers)
    if any(_st(ass, p.predicate_id) == "supported" for p in decisive_pos):
        return True
    all_contra = bool(decisive_pos) and all(_st(ass, p.predicate_id) == "contradicted" for p in decisive_pos)
    return any_trig and not all_contra


def _sup_times(ass: dict[str, PredicateAssessment], pid: str, times: dict[str, float]) -> list[float]:
    a = ass.get(pid)
    if not a or a.state != "supported":
        return []
    return sorted(times[i] for i in a.supporting_frame_ids if i in times)


def _augment_transition(rule: RuleSpec, ass: dict[str, PredicateAssessment],
                        times: dict[str, float]) -> dict[str, PredicateAssessment]:
    t = rule.temporal
    ap = rule.anchor_predicate
    if not (t.kind == "transition" and t.transition_from and t.transition_from_negates_anchor and ap):
        return ass
    dec = ass.get(ap.predicate_id)
    if not dec or dec.state != "supported":
        return ass
    first_sup = min((times[i] for i in dec.supporting_frame_ids if i in times), default=None)
    if first_sup is None:
        return ass
    prior = [i for i in dec.contradicting_frame_ids if i in times and times[i] < first_sup]
    if not prior:
        return ass
    cur = ass.get(t.transition_from)
    sup = sorted(set(prior) | set(cur.supporting_frame_ids if cur else []), key=lambda i: (times.get(i, 0.0), i))
    out = dict(ass)
    out[t.transition_from] = PredicateAssessment(
        predicate_id=t.transition_from, state="supported", supporting_frame_ids=sup,
        contradicting_frame_ids=[i for i in (cur.contradicting_frame_ids if cur else []) if i not in sup],
        visibility=dec.visibility, reasoning_note="derived: decisive predicate contradicted before its first support")
    return out


def _sup_frames(ass: dict[str, PredicateAssessment], pid: str, times: dict[str, float]) -> list[str]:
    a = ass.get(pid)
    if not a or a.state != "supported":
        return []
    return sorted((i for i in a.supporting_frame_ids if i in times), key=lambda i: (times[i], i))


def _anchor(rule: RuleSpec, ass: dict[str, PredicateAssessment], times: dict[str, float]) -> str | None:
    ap = rule.anchor_predicate
    if ap is None:
        return None
    cands = _sup_frames(ass, ap.predicate_id, times)
    if not cands:
        return None
    t = rule.temporal
    if t.kind == "transition" and t.transition_from:
        before = _sup_times(ass, t.transition_from, times)
        for fid in cands:
            if before and before[0] < times[fid]:
                return fid
        return None
    if rule.anchor_convention == "first_all_supported":
        req = [p for p in rule.required if p.required_state == "supported" and p.kind != "visibility"]
        firsts = []
        for p in req:
            st = _sup_times(ass, p.predicate_id, times)
            if not st:
                return None
            firsts.append(st[0])
        threshold = max(firsts)
        for fid in cands:
            if times[fid] >= threshold:
                return fid
        return None
    return cands[0]  # first_anchor_supported / completion_transition


def _event_interval(rule: RuleSpec, ass, times, anchor_t: float) -> tuple[float, float]:
    starts = [anchor_t]
    for p in rule.required:
        if p.required_state == "supported":
            starts += [x for x in _sup_times(ass, p.predicate_id, times) if x <= anchor_t]
    if rule.temporal.kind == "transition" and rule.temporal.transition_from:
        prev = [x for x in _sup_times(ass, rule.temporal.transition_from, times) if x < anchor_t]
        starts = [max(prev)] if prev else [anchor_t]
    ap = rule.anchor_predicate
    ends = [anchor_t] + [x for x in _sup_times(ass, ap.predicate_id, times) if x >= anchor_t]
    return min(starts), max(ends)


def _evidence(rule: RuleSpec, ass, times: dict[str, float], anchor: str) -> list[str]:
    at = times[anchor]
    ctx_ids: list[str] = []
    t = rule.temporal
    pre_preds = [t.transition_from] if (t.kind == "transition" and t.transition_from) else \
        [p.predicate_id for p in rule.by_kind("trigger")]
    before = [i for pid in pre_preds for i in _sup_frames(ass, pid, times) if times[i] < at]
    if not before:
        before = [i for i in times if times[i] < at]
    if before:
        ctx_ids.append(max(before, key=lambda i: (times[i], i)))
    ap = rule.anchor_predicate
    after = [i for i in _sup_frames(ass, ap.predicate_id, times) if times[i] > at]
    if not after:
        after = [i for i in times if times[i] > at]
    if after:
        ctx_ids.append(min(after, key=lambda i: (times[i], i)))
    return [anchor] + [i for i in ctx_ids if i != anchor][:2]


def _interval_sampled(ts_sorted: list[float], start: float, end: float, max_gap: float) -> bool:
    inside = [x for x in ts_sorted if start - EDGE_TOL_SEC <= x <= end + EDGE_TOL_SEC]
    if not inside or inside[0] > start + EDGE_TOL_SEC or inside[-1] < end - EDGE_TOL_SEC:
        return False
    return all(b - a <= max_gap + 1e-9 for a, b in zip(inside, inside[1:]))


def _completion_inside(rule: RuleSpec, ass, times, last_sec: float) -> bool:
    """For an omission near the clip end, the completion must be visible before the last frame."""
    ap = rule.anchor_predicate
    st = _sup_times(ass, ap.predicate_id, times) if ap else []
    return bool(st) and st[0] < last_sec - 1e-6


def _reset_between(rule: RuleSpec, cl: list[CandidateEvent], times: dict[str, float]) -> bool:
    """True when the members are distinct occurrences: between two anchors the rule's
    precondition was re-established or the anchor state was visibly contradicted."""
    anchors = sorted(times[c.anchor_frame_id] for c in cl)
    lo, hi = anchors[0], anchors[-1]
    if hi - lo <= 1e-9:
        return False
    t = rule.temporal
    if t.kind == "transition" and t.transition_from:
        reset_preds = [t.transition_from]
    elif t.kind == "omission":
        reset_preds = [p.predicate_id for p in rule.by_kind("trigger")]
    else:
        reset_preds = []
    ap = rule.anchor_predicate.predicate_id if rule.anchor_predicate else None
    for c in cl:
        for a in c.assessments:
            if a.predicate_id in reset_preds and a.state == "supported":
                if any(lo < times[i] < hi for i in a.supporting_frame_ids if i in times):
                    return True
            if a.predicate_id == ap and a.contradicting_frame_ids:
                if any(lo < times[i] < hi for i in a.contradicting_frame_ids if i in times):
                    return True
    return False
