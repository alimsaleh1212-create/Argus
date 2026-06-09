# Contract — Enrichment Eval (extends the existing `retrieval` gate)

Enrichment introduces **no new gate** (ED7). Its eval is the retrieval half the plan/brief specify — "does
memory surface the right prior incidents? hit@k / MRR" — scored through the existing provider-independent
**`retrieval`** gate in `config/eval_thresholds.yaml`, extended with an **enrichment fixture set**.

## What is added

Under `gates.retrieval`, an `enrichment_fixtures` block (alongside the existing `corpus_fixtures`):

```yaml
  retrieval:
    # … existing memory + corpus_fixtures …
    enrichment_fixtures:
      fixture_dir: tests/fixtures/enrichment
      cases_file: cases.json      # each case: a grounded incident + expected prior incident id(s)
                                  #   and expected corpus mapping key(s)
      min_hit_at_k: 0.80
      k: 5
```

Each fixture case is a grounded, triage-`advance` incident plus its labels:
- the **prior incident(s)** that `extract_entities` + `search_similar` should surface within top-k;
- the **corpus mapping key(s)** that `build_reference_query` + `search_reference` should return.

The gate runs enrichment's **deterministic retrieval assembly** (the `build_reference_query` /
`extract_entities` + retriever calls — *not* the LLM call) against a freshly seeded corpus + a pre-seeded
prior-incident set, and scores hit@k / MRR on the union of expected priors and mappings.

## Why provider-independent

The scored surface is deterministic store logic (keyed/lexical corpus retrieval + memory `search_similar`),
exactly like #6's `retrieval` and #5's `corpus_fixtures` — no chat-LLM judgment, so no per-provider dimension.
This keeps the gate stable and fast, and tests the *core deliverable* (the right context is assembled to
correlate over) without coupling to model nondeterminism.

## What is deliberately deferred to SPEC-eval (#13)

The **correlation-quality** judge — the brief's "hand-label a few, report judge agreement" on the
`correlation_summary`/`assessment` — is an LLM-judge gate validated against hand-labels, owned by #13's
harness. #9 instead validates the correlation call **functionally** on **both** providers in the integration
tier (a real `LlmClient` produces a schema-valid `EnrichmentReport` whose `assessment` matches the labeled
expectation on a small set), so the stage is proven on both providers without a new judged CI gate here.

## Pass criteria

- Enrichment fixture cases meet `min_hit_at_k` (≥ 0.80 at k=5) on the union of expected priors + mappings.
- Cases whose expected context legitimately does not exist in a cold store (empty-by-design) are excluded
  from scoring, consistent with the corpus-fixtures convention.
