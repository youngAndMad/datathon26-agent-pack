"""Strict parsing and validation of raw model text into frozen DTO fields (agent C).

Policy:
* Output must be one JSON object (code fences / surrounding prose are stripped).
* Unknown frame ids, unknown/duplicate predicate ids, unknown enum values, wrong types,
  oversized arrays and over-long strings are *rejected* (``OutputInvalid``) -> the adapter may
  do one repair call, then fails with ``model_output_invalid``.
* Extra keys emitted by the model (e.g. "confidence", "timestamp") are ignored: they never
  reach a DTO. Timestamps are resolved from the request FrameRefs only.
* Legal citations, verdict words, identity claims, URLs and monetary amounts inside free text
  are neutralized to ``[removed]`` (outputs are neutral observations only).
* A "supported"/"contradicted" state without any supporting/contradicting frame id is
  downgraded to "unknown" (never strengthened). Missing cue/predicate assessments are filled
  as "unknown" with a limitation note.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr, ValidationError

from cashwatch.dto import (
    FrameRef,
    Observation,
    PredicateAssessment,
    ts,
)
from cashwatch.inference import prompts as P


class OutputInvalid(ValueError):
    """Model text failed parsing/validation. Message is safe to echo back to the model."""


# --------------------------------------------------------------------------- JSON extraction

_FENCE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.S)


def extract_json_object(text: str) -> dict[str, Any]:
    if not isinstance(text, str) or not text.strip():
        raise OutputInvalid("empty output; expected one JSON object")
    candidates: list[str] = []
    for m in _FENCE.finditer(text):
        candidates.append(m.group(1))
    candidates.append(text)
    decoder = json.JSONDecoder()
    last_err = "no JSON object found"
    for cand in candidates:
        start = cand.find("{")
        while start != -1:
            try:
                obj, _end = decoder.raw_decode(cand, start)
            except json.JSONDecodeError as e:
                last_err = f"invalid JSON: {e.msg} at char {e.pos}"
                break  # first '{' is the outermost object; a later '{' would be a fragment
            if isinstance(obj, dict):
                return obj
            last_err = "top-level JSON value is not an object"
            start = cand.find("{", start + 1)
    raise OutputInvalid(last_err)


# --------------------------------------------------------------------------- neutralization

REDACTED = "[removed]"
_NEUTRALIZE_PATTERNS = [
    # URLs / legal databases
    r"https?://\S+", r"\bwww\.\S+", r"\badilet\S*",
    # legal citations (en)
    r"\b(?:article|art\.|clause|section|paragraph|subparagraph|item|chapter)\s*(?:no\.?|№|#)?\s*\d[\w.\-]*",
    r"§+\s*\d[\w.\-]*",
    r"\b(?:law|code|act|regulation|resolution|decree|order|rules?|standard|instruction)\s+(?:no\.?|№|#)\s*\S+",
    r"\b(?:labou?r|criminal|civil|administrative|tax)\s+code\b",
    # legal citations (ru/kk)
    r"(?i)(?:стать[яиюейё]\w*|ст\.|пункт\w*|подпункт\w*|п\.\s*\d|пп\.\s*\d|глав[аыеу]\w*)\s*№?\s*[\d.\-]*",
    r"(?:закон\w*|кодекс\w*|постановлени\w*|приказ\w*|правил\w*|инструкци\w*)(?:\s*№\s*\S+)?",
    # verdict / accusation vocabulary
    r"(?i)\b(?:violat\w*|infring\w*|breach\w*|non-?complian\w*|illegal\w*|unlawful\w*|illicit\w*)",
    r"(?i)\b(?:theft|thie(?:f|ves)|steal\w*|stole\w*|embezzl\w*|fraud\w*|guilt\w*|crim(?:e|es|inal)\w*|misconduct)",
    r"(?i)\b(?:unauthori[sz]ed|authori[sz]ed|authori[sz]ation|permission|permitted|forbidden|prohibited)\b",
    r"(?i)(?:нарушени\w*|наруша\w*|краж\w*|хищени\w*|мошенни\w*|виновн\w*|незаконн\w*)",
    # identity claims
    r"(?i)\b(?:named|whose name is|name is|called|identified as|identity of|recogni[sz]ed as|known as|face of|is (?:mr|mrs|ms|dr)\.?)\s+\S+",
    r"(?i)\b(?:mr|mrs|ms|miss|dr)\.?\s+[A-Z][a-z]+",
    # monetary amounts / discrepancies
    r"\b(?:shortage|surplus|discrepanc\w*|missing money)\b", r"(?:недостач\w*|излиш\w*|растрат\w*)",
    r"(?i)\d[\d\s,.]*\s*(?:tenge|тенге|тг|₸|kzt|usd|eur|rub|руб\w*|\$|€|dollars?)",
    r"(?i)(?:₸|\$|€)\s*\d[\d\s,.]*",
]
_NEUTRALIZE = [re.compile(p.replace("(?i)", ""), re.IGNORECASE) for p in _NEUTRALIZE_PATTERNS]


def neutralize(text: str) -> tuple[str, int]:
    """Return (neutral text, number of redactions)."""
    count = 0
    out = text
    for rx in _NEUTRALIZE:
        out, n = rx.subn(REDACTED, out)
        count += n
    if count:
        out = re.sub(r"(?:\[removed\][\s,;:]*){2,}", REDACTED + " ", out).strip()
    return out, count


# --------------------------------------------------------------------------- raw schemas

class _Raw(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)


_VIS = {"clear", "partial", "unusable"}
_STATES = {"supported", "contradicted", "unknown"}
_ACTOR_RX = re.compile(r"^[A-Za-z0-9_\-]{1,32}$")


class RawObservation(_Raw):
    observation_id: StrictStr | None = None
    frame_ids: list[StrictStr]
    actor_refs: list[StrictStr] = Field(default_factory=list)
    object_hypotheses: list[StrictStr] = Field(default_factory=list)
    action: StrictStr
    visibility: StrictStr
    uncertainty: StrictStr = ""


class RawAssessment(_Raw):
    predicate_id: StrictStr
    state: StrictStr
    supporting_frame_ids: list[StrictStr] = Field(default_factory=list)
    contradicting_frame_ids: list[StrictStr] = Field(default_factory=list)
    visibility: StrictStr = "partial"
    visible_reason: StrictStr = ""


class RawObservationOutput(_Raw):
    window_id: StrictStr
    observations: list[RawObservation]
    cue_assessments: list[RawAssessment] = Field(default_factory=list)
    limitations: list[StrictStr] = Field(default_factory=list)


class RawVerificationOutput(_Raw):
    candidate_id: StrictStr
    assessments: list[RawAssessment]
    plausible_alternatives: list[StrictStr] = Field(default_factory=list)
    interval_complete: StrictBool
    visibility_sufficient: StrictBool
    limitations: list[StrictStr] = Field(default_factory=list)


def _pydantic_msg(e: ValidationError) -> str:
    parts = []
    for err in e.errors()[:5]:
        loc = ".".join(str(x) for x in err["loc"])
        parts.append(f"{loc}: {err['msg']}")
    return "schema error: " + "; ".join(parts)


# --------------------------------------------------------------------------- checks

@dataclass
class _Ctx:
    frames: dict[str, FrameRef]
    redactions: int = 0
    notes: list[str] = field(default_factory=list)

    def text(self, value: str, limit: int, where: str) -> str:
        v = value.strip()
        if len(v) > limit:
            raise OutputInvalid(f"{where} longer than {limit} characters")
        out, n = neutralize(v)
        self.redactions += n
        return out

    def frame_ids(self, ids: list[str], where: str) -> list[str]:
        if len(ids) > len(self.frames):
            raise OutputInvalid(f"{where} has more ids than supplied frames")
        seen: list[str] = []
        for fid in ids:
            if fid not in self.frames:
                raise OutputInvalid(f"{where} contains unknown frame id {fid[:40]!r}")
            if fid not in seen:
                seen.append(fid)
        # canonical presentation order
        return sorted(seen, key=lambda f: (self.frames[f].relative_sec, self.frames[f].ordinal))


def _cap(items: list, n: int, where: str) -> None:
    if len(items) > n:
        raise OutputInvalid(f"{where} has {len(items)} items; at most {n} allowed")


def _enum(value: str, allowed: set[str], where: str) -> str:
    if value not in allowed:
        raise OutputInvalid(f"{where} must be one of {sorted(allowed)}, got {value[:30]!r}")
    return value


def _assessments(raw: list[RawAssessment], predicate_ids: list[str], ctx: _Ctx, where: str) -> list[PredicateAssessment]:
    allowed = set(predicate_ids)
    _cap(raw, max(len(predicate_ids), 0), where)
    by_id: dict[str, PredicateAssessment] = {}
    for i, a in enumerate(raw):
        w = f"{where}[{i}]"
        if a.predicate_id not in allowed:
            raise OutputInvalid(f"{w}.predicate_id unknown {a.predicate_id[:40]!r}")
        if a.predicate_id in by_id:
            raise OutputInvalid(f"{w}.predicate_id duplicated {a.predicate_id[:40]!r}")
        state = _enum(a.state, _STATES, f"{w}.state")
        vis = _enum(a.visibility, _VIS, f"{w}.visibility")
        sup = ctx.frame_ids(a.supporting_frame_ids, f"{w}.supporting_frame_ids")
        con = ctx.frame_ids(a.contradicting_frame_ids, f"{w}.contradicting_frame_ids")
        reason = ctx.text(a.visible_reason, P.MAX_REASON_CHARS, f"{w}.visible_reason")
        if set(sup) & set(con):
            state = "unknown"
            reason = "conflicting compact frame evidence"
            ctx.notes.append(f"{a.predicate_id}: conflicting frame evidence downgraded to unknown")
        if state == "supported" and not sup:
            state = "unknown"
            ctx.notes.append(f"{a.predicate_id}: supported without frame ids downgraded to unknown")
        if state == "contradicted" and not con:
            state = "unknown"
            ctx.notes.append(f"{a.predicate_id}: contradicted without frame ids downgraded to unknown")
        involved = sup + con
        if involved:
            times = [ctx.frames[f].relative_sec for f in involved]
            start, end = ts(min(times)), ts(max(times))
        else:
            start = end = None
        by_id[a.predicate_id] = PredicateAssessment(
            predicate_id=a.predicate_id, state=state, supporting_frame_ids=sup,
            contradicting_frame_ids=con, interval_start_sec=start, interval_end_sec=end,
            visibility=vis, reasoning_note=reason,
        )
    out: list[PredicateAssessment] = []
    for pid in predicate_ids:
        if pid in by_id:
            out.append(by_id[pid])
        else:
            ctx.notes.append(f"{pid}: not assessed by the model; recorded as unknown")
            out.append(PredicateAssessment(predicate_id=pid, state="unknown", visibility="partial",
                                           reasoning_note="not assessed by the model"))
    return out


def _limitations(raw: list[str], ctx: _Ctx, where: str) -> list[str]:
    _cap(raw, P.MAX_LIMITATIONS, where)
    return [ctx.text(s, P.MAX_LIMITATION_CHARS, f"{where}[{i}]") for i, s in enumerate(raw) if s.strip()]


def _finish_limitations(lims: list[str], ctx: _Ctx) -> list[str]:
    out = list(lims)
    if ctx.redactions:
        out.append(f"{ctx.redactions} non-visual term(s) (legal/identity/verdict/amount) removed from model text")
    out.extend(ctx.notes)
    return [s[:400] for s in out]


@dataclass(frozen=True)
class ParsedObservation:
    observations: list[Observation]
    cue_assessments: list[PredicateAssessment]
    limitations: list[str]


@dataclass(frozen=True)
class ParsedVerification:
    assessments: list[PredicateAssessment]
    plausible_alternatives: list[str]
    interval_complete: bool
    visibility_sufficient: bool
    limitations: list[str]


def _frame_map(frames: list[FrameRef]) -> dict[str, FrameRef]:
    return {f.frame_id: f for f in frames}


_RANGE = re.compile(r"^(\d{1,4})\s*[-–]\s*(\d{1,4})$")
_FRAME_KEYS = ("frame_ids", "supporting_frame_ids", "contradicting_frame_ids")


def _map_labels(obj: dict[str, Any], labels: dict[str, str] | None) -> dict[str, Any]:
    """Index label mode: integers / digit strings -> request frame ids. Unknown labels are kept
    as an unmistakable placeholder so validation rejects them."""
    if not labels:
        return obj

    def conv(v: Any) -> list[Any]:
        if isinstance(v, bool):
            return [v]
        if isinstance(v, int | str):
            key = str(v).strip()
            m = _RANGE.match(key)
            if m:
                a, b = int(m.group(1)), int(m.group(2))
                if a <= b and str(a) in labels and str(b) in labels:
                    return [labels[str(i)] for i in range(a, b + 1)]
                return [f"#unknown-range-{key[:20]}"]
            return [labels.get(key, f"#unknown-label-{key[:20]}")]
        return [v]

    for list_key in ("observations", "cue_assessments", "assessments"):
        items = obj.get(list_key)
        if isinstance(items, list):
            for it in items:
                if isinstance(it, dict):
                    for k in _FRAME_KEYS:
                        if isinstance(it.get(k), list):
                            it[k] = [x for v in it[k] for x in conv(v)]
    return obj


def parse_observation(text: str, *, window_id: str, frames: list[FrameRef],
                      predicate_ids: list[str], labels: dict[str, str] | None = None) -> ParsedObservation:
    obj = extract_json_object(text)
    if not predicate_ids and isinstance(obj, dict):
        obj.pop("cue_assessments", None)  # no cues were asked in this call; ignore extras
    obj = _map_labels(obj, labels)
    try:
        raw = RawObservationOutput.model_validate(obj)
    except ValidationError as e:
        raise OutputInvalid(_pydantic_msg(e)) from None
    if raw.window_id != window_id:
        raise OutputInvalid(f"window_id must be {window_id!r}")
    ctx = _Ctx(_frame_map(frames))
    _cap(raw.observations, P.MAX_OBSERVATIONS, "observations")
    observations: list[Observation] = []
    for i, o in enumerate(raw.observations):
        w = f"observations[{i}]"
        fids = ctx.frame_ids(o.frame_ids, f"{w}.frame_ids")
        if not fids:
            raise OutputInvalid(f"{w}.frame_ids must not be empty")
        _cap(o.actor_refs, P.MAX_ACTORS, f"{w}.actor_refs")
        _cap(o.object_hypotheses, P.MAX_OBJECTS, f"{w}.object_hypotheses")
        actors: list[str] = []
        for a in o.actor_refs:
            if not _ACTOR_RX.match(a):
                raise OutputInvalid(f"{w}.actor_refs must be short anonymous labels like A1")
            if a not in actors:
                actors.append(a)
        objects = [ctx.text(s, P.MAX_OBJECT_CHARS, f"{w}.object_hypotheses") for s in o.object_hypotheses]
        action = ctx.text(o.action, P.MAX_ACTION_CHARS, f"{w}.action")
        if not action:
            raise OutputInvalid(f"{w}.action must not be empty")
        observations.append(Observation(
            observation_id=f"{window_id}.o{i + 1}",  # application-assigned, stable
            frame_ids=fids,
            actor_refs=actors,
            object_hypotheses=[s for s in objects if s],
            action=action,
            visibility=_enum(o.visibility, _VIS, f"{w}.visibility"),
            uncertainty=ctx.text(o.uncertainty, P.MAX_UNCERTAINTY_CHARS, f"{w}.uncertainty"),
        ))
    cues = _assessments(raw.cue_assessments, predicate_ids, ctx, "cue_assessments")
    lims = _limitations(raw.limitations, ctx, "limitations")
    return ParsedObservation(observations, cues, _finish_limitations(lims, ctx))


def parse_verification(text: str, *, candidate_id: str, frames: list[FrameRef],
                       predicate_ids: list[str], labels: dict[str, str] | None = None) -> ParsedVerification:
    obj = _map_labels(extract_json_object(text), labels)
    try:
        raw = RawVerificationOutput.model_validate(obj)
    except ValidationError as e:
        raise OutputInvalid(_pydantic_msg(e)) from None
    if raw.candidate_id != candidate_id:
        raise OutputInvalid(f"candidate_id must be {candidate_id!r}")
    ctx = _Ctx(_frame_map(frames))
    assessments = _assessments(raw.assessments, predicate_ids, ctx, "assessments")
    _cap(raw.plausible_alternatives, P.MAX_ALTERNATIVES, "plausible_alternatives")
    alts = [ctx.text(s, P.MAX_ALTERNATIVE_CHARS, f"plausible_alternatives[{i}]")
            for i, s in enumerate(raw.plausible_alternatives) if s.strip()]
    lims = _limitations(raw.limitations, ctx, "limitations")
    return ParsedVerification(assessments, alts, raw.interval_complete, raw.visibility_sufficient,
                              _finish_limitations(lims, ctx))


_CUE_LINE = re.compile(r"^\s*\**\s*Q\s*(\d+)\s*\**\s*[:.)-]\s*(.*)$", re.IGNORECASE)


def _frame_numbers(text: str, n: int) -> list[int]:
    idx: list[int] = []
    for tok in re.findall(r"\d+\s*[-–]\s*\d+|\d+", text):
        if "-" in tok or "–" in tok:
            lo, hi = (int(x) for x in re.split(r"\s*[-–]\s*", tok))
            if lo <= hi and hi - lo <= n:
                idx.extend(range(lo, hi + 1))
        else:
            idx.append(int(tok))
    return sorted({i for i in idx if 1 <= i <= n})


def parse_cues(text: str, *, frames: list[FrameRef], predicate_ids: list[str],
               yes_without_frames_is_window: bool = False,
               strict: bool = False) -> list[PredicateAssessment]:
    """Parse compact answers: "Q1: yes 3-9", "Q2: no", "Q3: yes 5-9; no 1-4", "Q4: unclear".
    Coarse cues tolerate missing answers. Strict verification requires every answer,
    accepts explicit unclear, and rejects malformed or foreign frame references."""
    n = len(frames)
    answers: dict[int, tuple[list[int], list[int], bool, bool]] = {}
    for line in text.splitlines():
        m = _CUE_LINE.match(line)
        if not m:
            if strict and line.strip():
                raise OutputInvalid("unexpected compact answer text")
            continue
        q = int(m.group(1))
        if not 1 <= q <= len(predicate_ids) or q in answers:
            if strict:
                raise OutputInvalid("duplicate or unknown compact question")
            continue
        body = m.group(2).lower().strip()
        if strict and body != "unclear":
            seen = set()
            for part in re.split(r"[;|]", body):
                match = re.fullmatch(r"\s*(yes|no)(?:\s+(\d+(?:\s*[-–]\s*\d+)?(?:\s*,\s*\d+(?:\s*[-–]\s*\d+)?)*))?\s*", part)
                if not match or match.group(1) in seen:
                    raise OutputInvalid("malformed compact answer")
                seen.add(match.group(1))
                refs = match.group(2) or ""
                if not refs:
                    raise OutputInvalid("verification yes/no requires frame references")
                if any(not 1 <= int(x) <= n for x in re.findall(r"\d+", refs)):
                    raise OutputInvalid("compact frame reference outside request")
                for lo, hi in re.findall(r"(\d+)\s*[-–]\s*(\d+)", refs):
                    if int(lo) > int(hi):
                        raise OutputInvalid("reversed compact frame range")
        yes_part = no_part = ""
        said_yes = said_no = False
        for part in re.split(r"[;|]", body):
            part = part.strip()
            if part.startswith("yes"):
                said_yes, yes_part = True, part[3:]
            elif part.startswith("no"):
                said_no, no_part = True, part[2:]
        answers[q] = (_frame_numbers(yes_part, n), _frame_numbers(no_part, n), said_yes, said_no)
    if strict and len(answers) != len(predicate_ids):
        raise OutputInvalid("missing compact answers")
    out: list[PredicateAssessment] = []
    for q, pid in enumerate(predicate_ids, 1):
        yes, no, said_yes, said_no = answers.get(q, ([], [], False, False))
        if strict and (set(yes) & set(no) or (said_yes and said_no and not no)):
            out.append(PredicateAssessment(predicate_id=pid, state="unknown", visibility="partial",
                                           reasoning_note="conflicting compact frame evidence"))
            continue
        if said_yes and not yes and yes_without_frames_is_window:
            yes = list(range(1, n + 1))  # coarse proposal only; anchors come from verification
        yes_ids = [frames[i - 1].frame_id for i in yes]
        no_ids = [frames[i - 1].frame_id for i in no if i not in yes]
        if yes_ids:
            ts_ = [frames[i - 1].relative_sec for i in yes]
            out.append(PredicateAssessment(predicate_id=pid, state="supported", supporting_frame_ids=yes_ids,
                                           contradicting_frame_ids=no_ids, interval_start_sec=min(ts_),
                                           interval_end_sec=max(ts_), visibility="clear", reasoning_note="yes"))
        elif no_ids or (yes_without_frames_is_window and said_no and not said_yes):
            out.append(PredicateAssessment(predicate_id=pid, state="contradicted", contradicting_frame_ids=no_ids,
                                           visibility="clear", reasoning_note="no"))
        else:
            out.append(PredicateAssessment(predicate_id=pid, state="unknown", visibility="partial",
                                           reasoning_note="yes without frames" if said_yes else "unclear"))
    return out


def parse_partial_verification(text: str, *, frames: list[FrameRef],
                               predicate_ids: list[str]) -> list[PredicateAssessment] | None:
    """Salvage individually valid rows without inventing evidence for incomplete rows.

    Unlike coarse proposal parsing this enforces the verification grammar per row.
    Duplicate answers remain unknown, even if one of them happens to be valid.
    None means there was no valid answer at all; explicit unclear is a valid answer.
    """
    rows: dict[int, list[str]] = {}
    for line in text.splitlines():
        match = _CUE_LINE.match(line)
        if match and 1 <= int(match.group(1)) <= len(predicate_ids):
            rows.setdefault(int(match.group(1)), []).append(match.group(2))
    out = []
    valid = False
    for q, pid in enumerate(predicate_ids, 1):
        assessment = PredicateAssessment(predicate_id=pid, state="unknown", visibility="partial",
                                         reasoning_note="missing or invalid compact answer")
        answers = rows.get(q, [])
        if len(answers) == 1:
            try:
                assessment = parse_cues("Q1: " + answers[0], frames=frames,
                                        predicate_ids=[pid], strict=True)[0]
                valid = True
            except OutputInvalid:
                pass
        elif len(answers) > 1:
            assessment = assessment.model_copy(update={"reasoning_note": "conflicting compact frame evidence"})
        out.append(assessment)
    return out if valid else None
