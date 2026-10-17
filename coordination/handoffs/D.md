# Handoff: agent D (normative rules, predicates, explanations)

The agent is cloud-hosted. It used only public legal sources and synthetic DTO fixtures. No competition media, frames, scene descriptions or annotations were accessed (D004).

## Owned files changed (no git worktree; the orchestrator commits)
- `rules/registry.yaml`: production registry. Sources L01–L04, rules CW01–CW09, all `disabled`.
- `rules/registry.test.yaml`: `test_only: true`, labeled SYNTHETIC TEST. Rule TEST01 (red square leaves the yellow zone).
- `rules/sources/`: official snapshots captured 2026-10-17:
  - `L0{1..5}-2026-10-17.api.json` (raw)
  - `.txt` (derived)
  - `.old*.html` (legacy official corroboration)
  - `manifest.yaml`, `excerpts.yaml`
  - `TEST-fixture-synthetic.txt` and `excerpts.test.yaml` (fixture, not law)
- `src/cashwatch/rules/`:
  - `__init__.py`: `load_registry`, `create_rules`
  - `registry.py`: loader and lint
  - `schema.py`: registry file schema, `FORBIDDEN_RE`
  - `engine.py`: RulesPort
  - `sources.py`: offline hashing, html→text
  - `capture.py`: preparation-only network capture CLI
- `src/cashwatch/reporting/`:
  - `__init__.py`: `create_reporting`, `describe`, `coverage_notice_ru`
  - `templates.py`
- `evaluation/annotations/README.md`, `evaluation/annotations/annotation.template.yaml` (template only)
- `tests/rules/{__init__,synth,test_engine,test_registry}.py`, `tests/reporting/{__init__,test_reporting}.py`
- `coordination/requests/D.md`, this file.

## Contracts consumed
Frozen G0 code was not modified:
- `cashwatch.dto`: RuleRef, PredicateDefinition, CandidateEvent, AcceptedEvent, ReviewNote, VerificationRequest/Result, FrameManifest, ObservationBatch
- `cashwatch.ports`: RuleRegistry, RulesPort, ReportingPort
- `cashwatch.errors.PipelineError`

Factory signatures as required:
- `load_registry(path, *, require_approved, allow_test=False)`
- `create_rules(registry)` (one instance per job; `notes()` is per job)
- `create_reporting(registry)`

These match the existing calls in `pipeline/worker.py` and `pipeline/doctor.py`.

## Source verification (D01). Retrieved 2026-10-17 ~08:33–08:36Z, all HTTP 200
The SPA page `https://adilet.zan.kz/rus/docs/<NGR>` is a JS shell (1573 B, no text). The full operative text came from the official JSON endpoint that the SPA itself calls, `https://adilet.zan.kz/api/documents/by-ngr/<NGR>?language=rus`. The legacy official `https://old.adilet.zan.kz/rus/docs/<NGR>` returned the same clause text: pp. 12, 20, 22, 42 and 107 compared identical.

| ID | Act | Snapshot sha256 (raw api.json) | Operative text? | Edition / dates (official metadata) |
|---|---|---|---|---|
| L01 | НБРК №231, 29.11.2019, рег. МЮ №19680 03.12.2019 | 206d01dbdb549c539736fe0907dd7e18f206b94074c527cbf222ac27c88aca36 | yes, consolidated | «Дата изменения акта» 12.07.2026 (/info); history latest 12.07.2026; DB state 14.10.2026; status upd, is_actual |
| L02 | НБРК №71, 10.11.2025, рег. №37429 18.11.2025 | 63092cc26b977467e0295300bb11771124771e6c6d7b0b4d6e7fd9e3ec4002d7 | yes | published ЭКБ 20.11.2025; effective per its p.4 (10 days after first publication; some items from 01.01.2026). Exact date not computed |
| L03 | НБРК №19, 28.02.2022, №27073 | f722cd9f0230540e134e13ba14ff21a08488c71b8b0ac9df540501e55c037f8a | yes | published ЭКБ 14.03.2022; p.5 commencement |
| L04 | НБРК №25, 16.03.2026, №38217 | 2322daff2e5708ed719e224092d9c9b8e5084381f3fde11fd8f215da1db7a8be | yes | published ЭКБ 08.04.2026; p.6: some items from 12.07.2026; amended 30.06.2026 №78 |
| L05 | НБРК №14, 24.02.2020, №20075 | 5c160fd38adad53d505b9f4749cb02d09df6e13a40df2e741486a2856ee8bf7c | yes (captured only) | research lead; no rule uses it |

Full hashes (authoritative):
- `rules/sources/manifest.yaml`
- `rules/registry.yaml`:
  - L01 `206d01dbdb549c539736fe0907dd7e18f206b94074c527cbf222ac27c88aca36`
  - L02 `63092cc26b977467e0295300bb11771124771e6c6d7b0b4d6e7fd9e3ec4002d7`
  - L03 `f722cd9f0230540e134e13ba14ff21a08488c71b8b0ac9df540501e55c037f8a`
  - L04 `2322daff2e5708ed719e224092d9c9b8e5084381f3fde11fd8f215da1db7a8be`

Excerpts are verbatim slices of the derived text, with character offsets and text sha256. The loader re-derives the text from the raw JSON and re-checks each slice. Captured excerpts:
- L01 pp. 2(10), 5, 12 (+ footnote), 19 (+ footnote), 20, 22, 24, 25, 31, 32, 42, 107
- L02 p.4, plus its amendment wording for p.12 and pp.19(2),(3)
- L03 p.5 and its p.31 wording
- L04 p.6

Caveat: this is the IZPI consolidated text, not a certified official publication. Changes after 14.10.2026 were not checked.

## Rule status (candidate → approved changes: none)
| Rule | Clause | Clause text | Edition | Applicability | Local visual validation | State |
|---|---|---|---|---|---|---|
| CW01 | 22 | verified | verified | unverified | blocked | disabled |
| CW02 | 20 ч.2 | verified | verified | unverified (lock state invisible) | blocked | disabled |
| CW03 | 20 ч.1 | verified | verified | unverified | blocked | disabled |
| CW04 | 12 (ред. №71) | verified (= L02 amendment text) | verified | unverified (is the teller position part of a кассовый узел, p.5?) | blocked | disabled |
| CW05 | 19(2), 31(4); exc. 32 | verified | verified | unverified | blocked | disabled |
| CW06 | 24(3),(4) | verified | verified | unverified | blocked | disabled |
| CW07 | 25 | verified | verified | unverified | blocked | disabled |
| CW08 | 42 | verified | verified | unverified (likely inapplicable to counter service) | blocked | disabled |
| CW09 | 107(1),(2) | verified | verified | unverified | blocked | disabled |

Consequence: the production registry has 0 approved rules. `health_errors()` contains exactly one error, "no approved rules: …", and doctor prints `FAIL rule registry … approved=0`. Readiness fails honestly; nothing is silently turned into no_violation.

## Functionality
- **Registry lint.** An approved rule is exposed only if all of these hold:
  - official adilet.zan.kz URL (schema pattern)
  - snapshot and text hashes match
  - derived text equals the snapshot
  - corroboration hashes match
  - excerpts are verbatim
  - edition verified and in force
  - clause/edition/applicability verified and local validation "done"
  - approval owner/date and validation counts (pos ≥ 1, neg ≥ 1)
  - predicate ids unique and namespaced
  - exactly one anchor predicate consistent with the anchor convention
  - transition/omission structure valid
  - templates render, contain no braces or forbidden words, and include clause and URL

  The test registry is refused unless `allow_test=True`. TEST ids are not allowed in production, and production ids are not allowed in a test registry. A missing or malformed file raises `PipelineError(rule_registry_invalid)`.
- **Engine.**
  - Tri-state assessments. A missing or undefined predicate is unknown. "Supported" without frames, or citing frames that were not supplied, is downgraded to unknown. Conflicting duplicates become unknown.
  - `propose`: candidates only for approved rules, from the window span plus per-rule context. Unmapped observations become `unmapped_behavior` notes. Unusable visibility becomes a `visibility` note.
  - `decide` gates:
    - approved rule and version
    - result/candidate id match
    - same-source frames
    - every non-exclusion predicate in its required state (unknown → unresolved; opposite state → rejected; completion not observed → unresolved)
    - a supported exclusion → rejected; a strict exclusion must be contradicted
    - visibility_sufficient
    - omission rules: interval_complete, dense end-to-end sampling (max gap), and completion visible before the clip end
    - anchor per convention: first_anchor_supported (after the precondition for transition rules), first_all_supported, or completion_transition

    Evidence is 1–3 frames: anchor first, then precondition context before and decisive context after. Non-accepted candidates also get a review note.
  - `merge`: same rule plus overlapping intervals and no reset between anchors means one event. A reset is the precondition re-supported, or the anchor predicate contradicted, between anchors. The earliest anchor is kept and provenance is the union. Events are sorted chronologically with ids `ev-NNN-<rule>`. RuleRef comes from the registry; `factual_text_ru` is the registry template.
- **Reporting.** `describe` checks that the event RuleRef equals the registry ref (a model-invented clause or unknown id gives `rule_registry_invalid`) and that template ids match. Output format:
  `На {mm:ss} ({sec:.3f} с) видно: {fact}. Это может указывать на {interpretation}, поскольку {predicate}. Требует проверки сотрудником. Источник: {title}, п. {clause}; {url}.`
  - `coverage_notice_ru`: clean → "Нарушение не обнаружено. В пределах видимых действий и проверяемых правил." plus at most 5 deduplicated limitations. Violation → a modest notice. Any other verdict raises ValueError.

## Verification
- `.venv/bin/python -m pytest tests/rules tests/reporting` → 48 passed, ~1.1 s.
- `.venv/bin/python -m pytest tests/pipeline tests/contracts` → 95 passed (no regressions from my packages).
- `.venv/bin/python -m cashwatch.pipeline.doctor` → `FAIL rule registry … approved=0; no approved rules …` (expected).
- T-ids covered with synthetic fixtures:
  - T08: negative, positive, no hard-coding
  - T09: two separate events, overlap dedup, persistent state = one event, close events with a reset stay separate
  - T10: complete omission accepted; cut-off, model-incomplete and sparse sampling unresolved; proposal suppressed when the action is seen
  - T11: occlusion exclusion, all-unknown, visibility, hallucinated frame id, supported-without-frames, foreign-source frame
  - T12: one supported and one obscured exit
  - T14: unknown rule id, invented predicate/clause, stale version, forged clause in describe, tampered snapshot/excerpt, missing hash, non-adilet URL, forbidden template words, test/production mixing, duplicate predicate ids, approval without local validation
  - Registry/reporting: exact description text, all CW templates render with source and without forbidden words, coverage notices
- Latency: production registry load (lint plus hashing about 1.9 MB) ≈ 53 ms mean over 5 loads on an Apple M4 Pro. Memory is negligible. The engine runs in milliseconds on synthetic inputs. No model was run.

## Assumptions / limitations
- Predicates and anchors are engineering proposals, not statutory thresholds. They are unvalidated on real footage.
- Coarse proposal is recall-oriented. A window where the decisive cue is supported, or a trigger is supported and the decisive cue is not contradicted, triggers one verification call per window. Duplicates are merged after verification, so cost scales with windows × approved rules.
- `plausible_alternatives` from the model are recorded but not gated on (free text). Disqualifying alternatives must be encoded as exclusion predicates.
- The TEST01 source is a fixture. Its URL is a placeholder required by the RuleRef pattern and is labeled «ТЕСТОВОЕ ПРАВИЛО — не является нормой права».

## Blocker (explicit)
D02/D04: local dataset audit, annotation and visual validation of CW01–CW09 cannot be done by a cloud agent (AGENTS.md rule 3, D004). A local reviewer needs to follow `evaluation/annotations/README.md`, confirm applicability, fill `approval.validation_counts`, and set state `approved`. Only the loader gates then remain. Until then the production registry correctly has zero approved rules.

## Requested integration actions
See `coordination/requests/D.md`:
- R-D1: test-mode env (`CASHWATCH_MODE=test`, `CASHWATCH_REGISTRY_PATH=rules/registry.test.yaml`) for the synthetic real-model e2e (red square leaves the yellow zone at t=20 s)
- R-D2: optionally unify forbidden terms
- R-D3: capture module is prep-only in the egress audit
- R-D4: commit `rules/sources`
- R-D5: local validation blocker
