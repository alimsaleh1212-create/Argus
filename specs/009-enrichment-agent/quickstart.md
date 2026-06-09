# Quickstart — Enrichment Agent (#9)

How to run and verify the enrichment stage. Enrichment runs inside the existing `worker`; it adds no new
service. It consumes the corpus (#5), memory (#6), and LLM (#3) singletons already in the stack.

## Prerequisites

- The stack is up (`docker compose up`) with `migrate` + `seed-corpus` completed and `neo4j` healthy.
- An `LlmClient` provider is configured (Gemini or Ollama) — without it, the worker keeps the ADVANCE stub.
- (Optional) On-demand intel: set `SENTINEL__INTEL__ENABLED=true` and seed `secret/intel` with an `api_key`.
  Left disabled, enrichment runs on corpus + memory alone (intel verdicts come back `unknown`).

## Configuration (typed, `extra="forbid"`)

`EnrichmentSettings` (section `SENTINEL__ENRICHMENT__*`):

| Env | Default | Meaning |
|-----|---------|---------|
| `SENTINEL__ENRICHMENT__ADVANCE_MIN_CONFIDENCE` | `0.6` | below → ESCALATE |
| `SENTINEL__ENRICHMENT__RESOLVE_MIN_CONFIDENCE` | `0.7` | `benign` needs ≥ this to auto-resolve |
| `SENTINEL__ENRICHMENT__CORPUS_K` | `5` | top-k reference hits |
| `SENTINEL__ENRICHMENT__MEMORY_K` | `5` | top-k similar priors |
| `SENTINEL__ENRICHMENT__CONSULT_INTEL` | `true` | enrichment-side intel toggle |
| `SENTINEL__ENRICHMENT__MAX_INDICATORS` | `5` | cap on intel/`query_fact` calls |
| `SENTINEL__ENRICHMENT__PROMPT_VERSION` | `v1` | pinned system prompt |

## Verify (the three behaviors)

### 1. A real incident is enriched and advances (US1)

Replay a triage-`advance` incident whose indicator has a corpus mapping and a prior memory episode. Expect:

- the incident transitions `enriching → responding` with outcome `advance`;
- `incident.evidence["enrichment"]` carries a `correlation_summary` plus ≥1 `external_findings` and ≥1
  `internal_findings`;
- the trace shows one `supervisor.stage.enrichment` span with non-zero `tokens_consumed`.

```bash
# tail the worker logs while replaying the sample alert
docker compose logs -f worker | grep -E "supervisor_transition|enrichment"
```

### 2. Correlation downgrades or escalates (US2)

- Feed an incident whose correlated evidence exonerates it → transitions `enriching → resolved`
  (`auto_resolved_enrichment`); the response stage does **not** run.
- Feed an incident with conflicting external/internal signals (intel `benign` vs. a malicious reputation
  fact) and a sub-threshold confidence → transitions `enriching → escalated` (`escalated_enrichment`); the
  rationale states the conflict.

### 3. Graceful degradation + bounded (US3)

- Disable memory (`SENTINEL__MEMORY__ENABLED=false`) and intel → enrichment still produces a report from
  corpus-only context; the incident is not failed by the outage.
- Force a provider timeout / malformed output (in tests, via a faked driver) → the incident ESCALATEs after
  policy retries; the worker keeps consuming.

## Run the tests

```bash
# unit (retrievers + LLM mocked)
uv run pytest tests/unit/test_enrichment_report.py tests/unit/test_enrichment_decide.py \
              tests/unit/test_enrichment_builders.py tests/unit/test_enrichment_degrade.py \
              tests/unit/test_enrichment_errors.py -q

# integration (real corpus + memory + LlmClient, both providers)
uv run pytest tests/integration/test_enrichment_provider.py -q

# e2e (full-depth incident: triage → enrichment → responding/resolved/escalated)
uv run pytest tests/e2e/test_enrichment_e2e.py -q

# eval — the retrieval gate, now including the enrichment fixture set
uv run pytest tests/eval/test_retrieval_gate.py -q
```

## What this component does NOT touch

- `services/supervisor.py`, `repositories/incidents.py`, the DB schema — **unchanged** (#7 already wired the
  `ENRICHING` transitions and the `evidence_patch` merge; no migration).
- The response stage / approval interrupt (#10), the dashboard (#12), injection rails (#11), live feeds
  (roadmap §v2/v3).
