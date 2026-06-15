# Contract — `rationale` gate block (added to `config/eval_thresholds.yaml`)

The one net-new gate. **Reported-only** (FR-004): scores are recorded; only the `catastrophic_floor`
blocks CI. Runs at **freeze/nightly only**, judge pinned to the cloud primary (Gemini), over rationales
produced by **both** providers. Extends — never replaces — the existing file.

```yaml
  rationale:
    description: >
      LLM-judge rationale-quality gate (SPEC-eval #13). A single pinned judge (the cloud primary)
      scores triage/enrichment/response rationales on an ordinal scale (grounded / partially_grounded /
      ungrounded) plus a 'cites only supplied evidence' check. The judge is first validated against a
      small hand-labeled reference set (tests/fixtures/rationale/{stage}.json); per-stage grounded-rate
      and judge↔human agreement are RECORDED in eval_report.json. REPORTED-ONLY: ordinary below-target
      scores do NOT block merge — only the catastrophic_floor does. Freeze/nightly only; judge pinned
      regardless of which provider produced the rationale.
    required: false          # reported-only — harness maps this to kind=reported_only
    run_modes: [freeze, nightly]
    judge_provider: gemini   # pinned (R6); informational — authoritative value is EvalSettings.judge_provider
    stages: [triage, enrichment, response]
    fixture_dir: tests/fixtures/rationale
    target:                  # reported, NOT blocking
      min_grounded_rate: 0.70
      min_judge_agreement: 0.70
    catastrophic_floor:      # the ONLY blocking condition for this gate
      min_grounded_rate: 0.50
      min_judge_agreement: 0.50
```

## Semantics
- **grounded_rate** = (`#grounded` + 0.5·`#partially_grounded`) / `n`, per stage per producer-provider.
- **judge_human_agreement** = exact-match rate of judge ordinal label vs `human_label` over the fixture
  set (κ reported separately, not gated — R2/R3).
- **Blocking rule**: if any stage's `grounded_rate < catastrophic_floor.min_grounded_rate` OR
  `judge_human_agreement < catastrophic_floor.min_judge_agreement`, the harness promotes the result to
  blocking → run `not_certifiable` (exit 1). Otherwise the gate is informational.
- **Redaction**: the judge prompt (incident context + rationale text) and every recorded field pass the
  `Redactor` first (FR-014) — the eval system obeys the redaction guarantee it verifies.

## Anti-gap note
Adding this block is what makes the gate **declared** (FR-002): the harness registry MUST have a
`rationale` runner, and this block MUST exist, or the orphan/stale check aborts (exit 2).
