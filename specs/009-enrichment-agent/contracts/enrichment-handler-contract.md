# Contract — Enrichment Stage Handler

The handler that replaces the `run_enrichment` stub. Built by a closure factory and registered as the
`ENRICHMENT` stage in the supervisor's `stages` map. Preserves the frozen `StageHandler` signature
(`Incident` → `StageResult`, or raises `ToolError`) — the seam that enforces the Principle III boundary.

```python
def make_enrichment_handler(
    llm: LlmClient,
    corpus: CorpusRetriever | None,
    memory: MemoryStore | None,
    intel: ThreatIntelClient | None,
    cfg: EnrichmentSettings,
) -> StageHandler: ...
```

Only **read-only** retrievers are injected — there is no DB session and no action client, so enrichment
**cannot** write incident state or execute a remediation. Any of `corpus`/`memory`/`intel` may be `None`.

## Flow (one incident)

1. **Build queries (deterministic, pure)** from the incident's already-redacted `evidence.normalized_event`:
   - `query = build_reference_query(evidence)` → `ReferenceQuery(technique_ids, terms)`.
   - `entities = extract_entities(evidence)` → `list[EntityRef]` (capped at `cfg.max_indicators` for the
     entity-keyed calls).
2. **Fan out retrieval concurrently** with `asyncio.gather`, **each call individually guarded** (any
   exception/timeout → empty result for that source, logged at debug, never fails the stage — FR-008):
   - `corpus.search_reference(query, k=cfg.corpus_k)` if `corpus` else `[]`.
   - `memory.search_similar(EpisodeQuery(text=summary, entities=entities), k=cfg.memory_k)` if `memory` else `[]`.
   - `memory.query_fact(e, "reputation", as_of=None)` for each entity (bounded) if `memory` else empty states.
   - `intel.lookup(ind.value, ind.kind)` for indicator/address entities if `intel is not None and cfg.consult_intel`.
3. **Assemble the reasoning input** — a compact, structured bundle: the incident's own evidence (verdict,
   severity, normalized fields, summary, triage judgment) + the external block (corpus hits, intel verdicts)
   + the internal block (similar priors, time-valid facts with their `is_current`/`has_superseded` flags).
4. **One structured-output call**: `await llm.generate(request, correlation_id=incident.correlation_id)` with
   `response_schema = ENRICHMENT_REPORT_SCHEMA`, `max_tokens=cfg.max_output_tokens`,
   `temperature=cfg.temperature`, system prompt pinned by `cfg.prompt_version`.
5. **Validate** → `EnrichmentReport.model_validate(json.loads(response.content))`. Any parse/validation
   failure → `ToolError(retryable=False, kind="malformed_output")` (fail-closed → ESCALATE).
6. **Map** → `outcome, _ = decide_outcome(report, cfg)`; `tokens = prompt+completion usage`.
7. **Return** `StageResult(stage=ENRICHMENT, outcome=outcome, tokens_consumed=tokens,
   evidence_patch={"enrichment": report.model_dump(mode="json")}, note=…)`.

## `decide_outcome(report, cfg) -> tuple[StageOutcome, str | None]` (pure, top-to-bottom precedence)

```
if report.assessment == INCONCLUSIVE:            return ESCALATE, "escalated_enrichment"
if report.confidence < cfg.advance_min_confidence: return ESCALATE, "escalated_enrichment"
if report.assessment == CONFIRMED:               return ADVANCE, None
# assessment == BENIGN from here
if report.confidence >= cfg.resolve_min_confidence: return RESOLVED, "auto_resolved_enrichment"
return ESCALATE, "escalated_enrichment"
```

(The disposition string is advisory; the `ENRICHING` transition table in #7 already supplies the canonical
disposition for RESOLVED/ESCALATE, and `final_disp = table_disp or result.disposition`.)

## Error mapping (mirrors triage, fail-closed)

- `LlmError` with `kind ∈ {TRANSIENT, EXHAUSTED}` → `ToolError(retryable=True, kind="llm_<kind>")` → the
  supervisor retries within `max_stage_retries`, then ESCALATEs.
- any other `LlmError` → `ToolError(retryable=False, kind="llm_<kind>")` → ESCALATE.
- any unexpected exception around the call → `ToolError(retryable=False, kind="llm_unexpected")` → ESCALATE.
- a malformed/invalid report → `ToolError(retryable=False, kind="malformed_output")` → ESCALATE.
- **Retrieval errors never become `ToolError`** — they degrade to empty context (FR-008); only the reasoning
  call's failures are surfaced to the supervisor.

## Guarantees

- **At most one** LLM call per incident; retrieval fans out concurrently (FR-011 / SC-006).
- **No incident-state write, no action** — returns a `StageResult`; the supervisor persists everything
  (single writer). Only the `MemoryStore` **read** methods are called (`search_similar`/`query_fact`); never
  `write_episode` (SC-004).
- **Best-effort retrieval**: memory unavailable / corpus empty / intel disabled or `unknown` → the stage still
  produces a report from whatever context exists (FR-008 / SC-005).
- **Fail-closed**: every reasoning failure ESCALATEs; the worker never crashes (SC-005).
- **Untrusted input**: all retrieved/intel/feed text is treated as data; the structural no-tools/no-write
  boundary bounds the worst case to a wrong assessment (SC-004). Injection rails are #11.
