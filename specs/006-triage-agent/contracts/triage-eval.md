# Contract — Triage Eval Gate (`triage` gate in `eval_thresholds.yaml`)

**Component**: #8 activates the **triage real-vs-noise** gate the constitution requires per component
(Principle II; brief "Evaluation"). It is the **first eval gate with a real LLM dimension**, so unlike the
deterministic `supervisor_routing` gate it MUST run identically on **both** configured providers
(FR-013 / SC-002).

## What it measures

The triage agent's **real-vs-noise classification quality** on a committed, held-out **labeled alert set**,
scored by **macro-F1** (and per-class F1 reported), against a committed threshold. `uncertain` (abstain) is
handled per the scoring rule below so abstention is not punished as a misclassification.

## The labeled set (committed fixtures)

- Stored under `tests/fixtures/` (e.g. `tests/fixtures/triage_labeled/*.json`), each a grounded incident
  evidence slice + a gold label `real | noise`.
- Curated to cover the **ambiguous middle** triage actually sees (medium/high / severity-undetermined) —
  obvious-low and obvious-critical are the supervisor's fast-path, not triage's job.
- Small but balanced (both classes represented); each fixture's evidence is already-redacted, same shape the
  handler reads in production.

## Scoring rule (abstention-aware)

For each labeled incident, run the handler and compare the **verdict**:
- `real`/`noise` verdict vs gold label → counts toward the confusion matrix (macro-F1).
- `uncertain` (abstain) → counted as an **abstention**, reported separately; the gate enforces a
  **max abstention rate** so the agent can't trivially pass by abstaining on everything.

```yaml
# appended to config/eval_thresholds.yaml under gates:
  triage:
    description: >
      Triage real-vs-noise gate (SPEC-triage-agent #8). Runs the committed labeled alert set through the
      triage handler and scores macro-F1 on the real/noise decision. Abstentions (uncertain) are reported
      and bounded, not scored as errors. MUST pass on BOTH configured providers (Gemini and Ollama) — the
      first eval gate with an LLM dimension. Full harness owned by SPEC-eval (#13); seeded here so CI gates
      from this component.
    required: true
    providers: [gemini, ollama]
    threshold:
      min_macro_f1: 0.75          # committed bar; tune against the curated set before freeze
      max_abstention_rate: 0.30   # bounds "abstain on everything" gaming
      check_per_provider: true
```

> The exact `min_macro_f1` / `max_abstention_rate` numbers are committed when the labeled set is curated;
> they are seeded conservatively so CI gates immediately and are ratcheted, never loosened, before the day-9
> freeze.

## How it runs

- A `pytest` eval test (`tests/eval/test_triage_gate.py`) loads the fixtures, runs the handler per provider,
  computes macro-F1 + abstention rate, and asserts the thresholds. Required CI check alongside `smoke`,
  `redaction`, `supervisor_routing`, and `llm_provider`.
- **Determinism**: `temperature=0.0` keeps runs stable; the gate tolerates provider-level nondeterminism via
  the threshold margin (not exact-match).
- **Provider parity**: the same fixtures and scoring run for each entry in `providers`; a regression on
  *either* fails CI (the constitution's both-providers rule).

## Relationship to the other tiers

- **Unit** tests cover the *deterministic* parts (the `decide_outcome` mapping, judgment validation,
  error→`ToolError` mapping, fail-closed paths) with the LLM **mocked** — fast, provider-free, run every
  commit.
- **This eval gate** covers the *probabilistic* part (does the real LLM classify correctly) and is the
  committed quality contract.
- They are complementary: unit proves the wiring/safety is correct; the eval proves the judgment is good.
