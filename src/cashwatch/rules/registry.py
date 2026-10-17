"""Offline rule registry loader and lint (implements cashwatch.ports.RuleRegistry).

Hard guarantees:
* Only rules in state ``approved`` that pass *every* gate below are exposed through
  ``approved_rule_ids`` / ``rule_ref`` / ``cue_predicates``. Anything else raises KeyError.
* A production registry with zero approved rules loads fine but reports
  ``health_errors() == ["no approved rules ..."]`` when ``require_approved`` — readiness fails
  honestly instead of the service returning ``no_violation`` for everything.
* A ``test_only`` registry is refused (PipelineError rule_registry_invalid) unless the caller
  explicitly passes ``allow_test=True`` (explicit, visibly labeled test mode).
* Legal provenance (title, clause, URL, excerpt, snapshot hash) comes only from this file and the
  hashed offline snapshots in ``rules/sources``; never from a model.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from pydantic import ValidationError

from cashwatch.dto import PredicateDefinition, RuleRef
from cashwatch.errors import PipelineError
from cashwatch.rules.schema import (
    FORBIDDEN_RE,
    ExcerptFile,
    PredicateSpec,
    RegistryFile,
    RuleSpec,
    SourceSpec,
)
from cashwatch.rules.sources import html_to_text, read_text, sha256_file

_CONVENTION_ANCHOR_KIND = {
    "first_anchor_supported": "decisive",
    "first_all_supported": "decisive",
    "completion_transition": "completion",
}


class Registry:
    """Loaded, linted registry. Construct via ``load_registry``."""

    def __init__(self, data: RegistryFile, base: Path, *, require_approved: bool,
                 candidate_rules: frozenset[str] = frozenset()) -> None:
        self._data = data
        # Evaluation-only: these draft rules are exposed if every SOURCE gate passes, skipping only
        # the approval/local-validation gate (that is what the evaluation produces). Never used by
        # the production worker; reports carry evaluation_only=True.
        self.candidate_rules = candidate_rules
        self.evaluation_only = bool(candidate_rules)
        self._base = base
        self.version: str = data.registry_version
        self.test_only: bool = data.test_only
        self.label: str = data.label
        self._errors: list[str] = []
        self._specs: dict[str, RuleSpec] = {}
        self._pred_owner: dict[str, tuple[str, PredicateSpec]] = {}
        self._approved: dict[str, RuleSpec] = {}
        self._refs: dict[str, RuleRef] = {}
        self._texts: dict[str, str] = {}
        self._source_ok: dict[str, bool] = {}
        self._lint(require_approved=require_approved)

    # ------------------------------------------------------------------ RuleRegistry port
    def approved_rule_ids(self) -> list[str]:
        return sorted(self._approved)

    def rule_ref(self, rule_id: str) -> RuleRef:
        if rule_id not in self._refs:
            raise KeyError(rule_id)
        return self._refs[rule_id]

    def cue_predicates(self) -> list[PredicateDefinition]:
        out: list[PredicateDefinition] = []
        for rid in self.approved_rule_ids():
            for p in self._approved[rid].predicates:
                # Coarse pass asks only triggers (one short question per rule keeps a small model
                # accurate); decisive/exclusion predicates are evaluated in verification.
                if p.kind == "trigger":
                    out.append(_definition(p))
        return out

    def health_errors(self) -> list[str]:
        return list(self._errors)

    # ------------------------------------------------------------------ internal API
    def rule(self, rule_id: str) -> RuleSpec:
        """Approved rule spec; KeyError otherwise."""
        return self._approved[rule_id]

    def spec(self, rule_id: str) -> RuleSpec:
        """Any rule spec regardless of state (diagnostics/tests only)."""
        return self._specs[rule_id]

    def all_rule_ids(self) -> list[str]:
        return list(self._specs)

    def predicate_rule(self, predicate_id: str) -> str | None:
        hit = self._pred_owner.get(predicate_id)
        return hit[0] if hit else None

    def definitions(self, rule_id: str) -> list[PredicateDefinition]:
        return [_definition(p) for p in self.rule(rule_id).predicates]

    # ------------------------------------------------------------------ lint
    def _err(self, msg: str) -> None:
        self._errors.append(msg)

    def _path(self, rel: str) -> Path | None:
        p = (self._base / rel).resolve()
        try:
            p.relative_to(self._base.resolve())
        except ValueError:
            return None
        return p

    def _check_source(self, sid: str, src: SourceSpec) -> bool:
        ok = True
        if src.kind == "fixture" and not self.test_only:
            self._err(f"source {sid}: fixture source in a production registry")
            ok = False
        if src.kind == "official" and self.test_only:
            # allowed (reference), but never needed; keep strict separation
            pass
        snap = self._path(src.snapshot_file)
        if snap is None or not snap.is_file():
            self._err(f"source {sid}: snapshot file missing")
            return False
        if sha256_file(snap) != src.snapshot_sha256:
            self._err(f"source {sid}: snapshot sha256 mismatch")
            ok = False
        txt = self._path(src.text_file)
        if txt is None or not txt.is_file():
            self._err(f"source {sid}: text file missing")
            return False
        if sha256_file(txt) != src.text_sha256:
            self._err(f"source {sid}: text sha256 mismatch")
            ok = False
        text = read_text(txt)
        if src.kind == "official":
            try:
                doc = json.loads(snap.read_bytes().decode("utf-8"))["document"]
                if html_to_text(doc["text_content"]) != text:
                    self._err(f"source {sid}: derived text does not match snapshot")
                    ok = False
            except Exception:
                self._err(f"source {sid}: snapshot is not an official API document")
                ok = False
        for c in src.corroboration_files:
            cp = self._path(c.file)
            if cp is None or not cp.is_file() or sha256_file(cp) != c.sha256:
                self._err(f"source {sid}: corroboration file {c.file} missing or hash mismatch")
                ok = False
        if ok:
            self._texts[sid] = text
        return ok

    def _lint(self, *, require_approved: bool) -> None:
        d = self._data
        exc_path = self._path(d.excerpts_file)
        excerpts = {}
        if exc_path is None or not exc_path.is_file():
            self._err("excerpts file missing")
        else:
            try:
                ef = ExcerptFile.model_validate(yaml.safe_load(exc_path.read_text(encoding="utf-8")))
                excerpts = {e.excerpt_id: e for e in ef.excerpts}
            except (ValidationError, yaml.YAMLError) as e:
                self._err(f"excerpts file invalid ({type(e).__name__})")

        for sid, src in d.sources.items():
            self._source_ok[sid] = self._check_source(sid, src)

        seen_rules: set[str] = set()
        for r in d.rules:
            problems: list[str] = []
            if r.rule_id in seen_rules:
                self._err(f"duplicate rule id {r.rule_id}")
                continue
            seen_rules.add(r.rule_id)
            self._specs[r.rule_id] = r
            if self.test_only != r.rule_id.startswith("TEST"):
                problems.append("test/production rule id namespace mismatch")
            for p in r.predicates:
                if p.predicate_id in self._pred_owner:
                    problems.append(f"duplicate predicate id {p.predicate_id}")
                else:
                    self._pred_owner[p.predicate_id] = (r.rule_id, p)
                if not p.predicate_id.startswith(r.rule_id + "."):
                    problems.append(f"predicate {p.predicate_id} not namespaced by rule id")
                if p.kind == "exclusion" and p.required_state == "supported":
                    problems.append(f"exclusion {p.predicate_id} cannot require supported")
                if p.kind != "exclusion" and p.required_state == "not_supported":
                    problems.append(f"{p.predicate_id}: not_supported only valid for exclusions")
            anchor = r.anchor_predicate
            if anchor is None:
                problems.append("rule needs exactly one anchor predicate")
            elif anchor.kind != _CONVENTION_ANCHOR_KIND[r.anchor_convention] or anchor.required_state != "supported":
                problems.append("anchor predicate kind/state does not match anchor convention")
            if not r.by_kind("trigger"):
                problems.append("rule needs at least one trigger predicate")
            if not r.by_kind("decisive"):
                problems.append("rule needs at least one decisive predicate")
            if not r.by_kind("exclusion") and not r.counterexamples_en:
                problems.append("rule needs exclusions or counterexamples")
            t = r.temporal
            if t.kind == "transition":
                ids = {p.predicate_id: p for p in r.predicates}
                if not t.transition_from or t.transition_from not in ids or ids[t.transition_from].kind != "trigger":
                    problems.append("transition_from must name a trigger predicate of the rule")
            elif t.transition_from:
                problems.append("transition_from only valid for transition rules")
            if t.kind == "omission":
                if not t.requires_complete_interval or t.max_frame_gap_sec is None:
                    problems.append("omission rule must require a complete interval and a max frame gap")
                if r.anchor_convention != "completion_transition" or not r.by_kind("completion"):
                    problems.append("omission rule must anchor on a completion predicate")
                if not any(p.required_state == "contradicted" for p in r.by_kind("decisive")):
                    problems.append("omission rule needs a decisive predicate required contradicted")
            for text in (r.type_ru, r.explanation.fact_ru, r.explanation.interpretation_ru,
                         r.explanation.predicate_ru, *r.limitations_ru):
                if FORBIDDEN_RE.search(text):
                    problems.append("forbidden term in Russian template text")
                if "{" in text or "}" in text:
                    problems.append("template fragments must not contain format braces")
            if r.source_id not in d.sources:
                problems.append(f"unknown source {r.source_id}")
            for eid in r.excerpt_ids:
                e = excerpts.get(eid)
                if e is None:
                    problems.append(f"excerpt {eid} missing")
                elif e.source_id not in d.sources:
                    problems.append(f"excerpt {eid} from unknown source")
            for msg in problems:
                self._err(f"{r.rule_id}: {msg}")
            candidate = r.rule_id in self.candidate_rules
            if r.state != "approved" and not candidate:
                continue
            # ------------- additional activation gates for approved rules
            gates: list[str] = []
            src = d.sources.get(r.source_id)
            if src is None:
                gates.append("source missing")
            else:
                if not self._source_ok.get(r.source_id, False):
                    gates.append("source snapshot not verified")
                if not src.official_url.startswith("https://adilet.zan.kz/"):
                    gates.append("official_url must be https://adilet.zan.kz/")
                if not self.test_only:
                    if src.kind != "official":
                        gates.append("non-official source")
                    if not (src.edition_verified and src.in_force):
                        gates.append("edition not verified / not in force")
            ex_texts: list[str] = []
            for eid in r.excerpt_ids:
                e = excerpts.get(eid)
                if e is None:
                    gates.append(f"excerpt {eid} missing")
                    continue
                es = d.sources.get(e.source_id)
                text = self._texts.get(e.source_id)
                if es is None or text is None or e.text_sha256 != es.text_sha256 or e.text_file != Path(es.text_file).name:
                    gates.append(f"excerpt {eid} not bound to a verified snapshot")
                    continue
                if not (0 <= e.start < e.end <= len(text)) or text[e.start:e.end] != e.text:
                    gates.append(f"excerpt {eid} is not a verbatim slice of the snapshot")
                    continue
                ex_texts.append(e.text)
            ss = r.source_status
            a = r.approval
            if not self.test_only:
                if not (ss.clause_text == ss.edition == "verified"):
                    gates.append("clause/edition not verified")
                if not candidate:
                    if ss.applicability != "verified":
                        gates.append("applicability not verified")
                    if ss.local_visual_validation != "done":
                        gates.append("local visual validation not done")
                    vc = a.validation_counts
                    if not (a.owner and a.date and vc and vc.positive >= 1 and vc.negative >= 1):
                        gates.append("approval owner/date/validation counts missing")
            elif not (a.owner and a.date):
                gates.append("approval owner/date missing")
            if problems:
                gates.append("rule lint failed")
            ref = None
            if not gates:
                try:
                    ref = RuleRef(
                        rule_id=r.rule_id, rule_version=r.version, type_ru=r.type_ru,
                        document_title=src.document_title, authority=src.authority,
                        document_date=src.document_date, document_number=src.document_number,
                        clause=r.clause, official_url=src.official_url,
                        local_excerpt="\n\n".join(ex_texts), source_snapshot_sha256=src.snapshot_sha256)
                    _render_check(r, ref)
                except (ValidationError, ValueError) as e:
                    gates.append(f"rule ref/template invalid ({type(e).__name__})")
            if gates:
                for g in gates:
                    self._err(f"{r.rule_id}: approved rule rejected: {g}")
                continue
            self._approved[r.rule_id] = r
            self._refs[r.rule_id] = ref
        if require_approved and not self._approved:
            self._err("no approved rules: production readiness requires at least one approved, "
                      "validated rule (local visual validation pending)")


def _definition(p: PredicateSpec) -> PredicateDefinition:
    return PredicateDefinition(predicate_id=p.predicate_id, question_en=p.question_en,
                               counterexamples_en=list(p.counterexamples_en))


def _render_check(r: RuleSpec, ref: RuleRef) -> None:
    from cashwatch.reporting.templates import render_description

    s = render_description(r, ref, r.explanation.fact_ru, 0.0)
    if ref.clause not in s or ref.official_url not in s:
        raise ValueError("template does not include source")


def load_registry(path: Path | str, *, require_approved: bool, allow_test: bool = False,
                  candidate_rules: frozenset[str] | set[str] | None = None) -> Registry:
    """Load and lint a registry file.

    Raises PipelineError("rule_registry_invalid") when the file is missing/malformed or when a
    ``test_only`` registry is loaded without ``allow_test=True``. Lint failures of individual
    rules do not raise; they appear in ``health_errors()`` and the rule is not exposed.
    """
    p = Path(path)
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
        data = RegistryFile.model_validate(raw)
    except FileNotFoundError as e:
        raise PipelineError("rule_registry_invalid", "registry file missing") from e
    except (yaml.YAMLError, ValidationError, UnicodeDecodeError, TypeError) as e:
        raise PipelineError("rule_registry_invalid", f"registry file invalid ({type(e).__name__})") from e
    if data.test_only and not allow_test:
        raise PipelineError("rule_registry_invalid",
                            "test-only (SYNTHETIC TEST) registry refused outside explicit test mode")
    if candidate_rules and require_approved:
        raise PipelineError("rule_registry_invalid", "candidate (evaluation-only) rules refused in production")
    return Registry(data, p.parent, require_approved=require_approved,
                    candidate_rules=frozenset(candidate_rules or ()))
