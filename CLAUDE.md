<!-- SPECKIT START -->
For additional context about technologies to be used, project structure,
shell commands, and other important information, read the current plan:

**Active component**: `009-enrichment-agent` (Component #9 — retrieval-only cross-correlation stage;
depends on #5, #6, #3 — all done; **unblocks response #10 + dashboard #12 evidence**). Realizes the brief's
"assemble both directions and correlate" deliverable.
- Plan: `specs/009-enrichment-agent/plan.md`
- Spec: `specs/009-enrichment-agent/spec.md`
- Design: `specs/009-enrichment-agent/research.md`, `data-model.md`, `quickstart.md`, `contracts/`

Stack (this component): replaces the `run_enrichment` ADVANCE stub with the **second LLM stage**, mirroring
triage — a **bounded retrieval fan-out (`asyncio.gather`) + exactly one** structured `LlmClient` call (ED1).
Reads **both directions** via existing contracts: external = `CorpusRetriever.search_reference` (#5) +
optional `ThreatIntelClient.lookup`→`IntelVerdict` (#5); internal = `MemoryStore.search_similar` (priors) +
`query_fact(as_of=…)` time-valid facts (#6, the temporal differentiator). One call **cross-correlates** →
validated `EnrichmentReport` (`domain/enrichment.py`: `assessment` confirmed/benign/inconclusive + confidence
+ `correlation_summary` + external/internal findings + cited evidence) → pure `decide_outcome` → ADVANCE
(→response, primary) / RESOLVED (exonerated) / ESCALATE (conflict/low-confidence) (ED3). **Closure-factory
DI** `make_enrichment_handler(llm, corpus, memory, intel, cfg)` (retrievers may be `None` → best-effort)
preserves the frozen `StageHandler`; **retrieval-only, no action tools, no incident-state write** (calls only
memory READ methods; ED5). **Best-effort retrieval** — backend down/empty, intel disabled/`unknown`/timeout →
partial context, never blocks (FR-008); **fail-closed** reasoning → ESCALATE. Reads already-redacted evidence
+ retrieved text → **no in-stage redactor**. **Zero change to `services/supervisor.py`/`repositories/`/
schema** — #7 already wired the `ENRICHING` transitions + `evidence_patch` merge (ED6); only wiring: register
worker-absent `CorpusProvider`/`IntelProvider` + order memory/corpus/intel before `SupervisorProvider`. New
`EnrichmentSettings` (`extra="forbid"`). Eval: extends the existing **`retrieval`** gate with an enrichment
fixture set — **no new gate** (ED7); correlation LLM-judge deferred to #13. Live feeds roadmap §v2/v3.

Prior components (done): `008-knowledge-corpus` — **seeded reference corpus + optional on-demand intel**
(Constitution VI cold-start): static MITRE technique→mitigation + runbooks → Postgres `reference_corpus`
(`0006`, deterministic keyed/lexical, embeddings reserved); temporal reputation (seed IOC + intel verdicts) →
`TemporalFact`s in #6 via the anticipated `MemoryStore.write_fact` (intel is a *fact*, not an episode — keeps
`search_similar` clean). Pure `domain/corpus.py` (`CorpusRetriever` Protocol, consumed by #9);
`infra/intel.py` `ThreatIntelClient` optional/config-gated/fail-closed (missing creds → disabled not
fail-boot; outage → `unknown`). Idempotent one-shot `seed-corpus`. Extends the **`retrieval`** gate with
corpus fixtures — no new gate. Plan: `specs/008-knowledge-corpus/plan.md`.
`007-incident-memory` — **temporal incident-memory layer** (Constitution VI):
**Graphiti on Neo4j 5.26** behind the `MemoryStore` Protocol (`domain/memory.py`:
`write_episode`/`search_similar`/`query_fact`), decided pgvector fallback (MD9). The **worker** writes one
redacted, idempotent `IncidentEpisode` per incident after terminal — off-path, best-effort (memory outage
never blocks disposition); supervisor stays pure. `query_fact(as_of=…)` → time-valid `FactState` via
invalidate-not-delete. Graphiti's native Gemini LLM+embedder is the one documented VII deviation.
`MemoryProvider` degrades to `NullMemory`. Lands **retrieval** (hit@k/MRR) + **temporal_memory** gates
(provider-independent). Plan: `specs/007-incident-memory/plan.md`.
`006-triage-agent` — **first LLM stage**: replaces the triage stub with **one**
structured-output `LlmClient` call → validated `TriageJudgment` (`domain/triage.py`: real/noise/uncertain +
confidence + evidence-cited rationale) → pure config-gated `decide_outcome` → ADVANCE/RESOLVED/ESCALATE;
**fail-closed** (bad output → escalate, worker never crashes); **no tools / no write** (closure-factory DI
preserves the frozen `StageHandler`); supervisor JSONB-merges `evidence_patch`; **triage F1** gate on both
providers. Plan: `specs/006-triage-agent/plan.md`.
`005-incident-state-machine` — **deterministic supervisor** (`services/supervisor.py`,
plain async state machine, no LLM/LangGraph); config-backed fast-path routing + adaptive depth; hard
step+token cap → `escalated`; graceful degradation; **single-writer** over pure stage handlers; pure types
`domain/pipeline.py` (`StageName`/`StageOutcome`/`StageResult`/`ToolError`); extends `IncidentStatus` (text,
no migration) + nullable `disposition` (`0004`); guarded `advance_status` (idempotent/resumable);
`awaiting_approval` park + resume edges (#10 owns mechanism/timeout/audit); **supervisor-routing** eval gate.
Plan: `specs/005-incident-state-machine/plan.md`.
`004-ingestion-pipeline` — Wazuh **webhook → queue → worker → Incident**; thin
`POST /ingest/wazuh` (**validate → redact → dedup → persist → enqueue → `202`**); async **worker** grounds
(`services/grounding.py`, no LLM) then hands to `services/pipeline.py` (the seam #7 now fills). **Postgres
`incidents` source of truth** (migration `0003`); **Redis transient** (reliable-list queue + `SET NX EX`
dedup). Owns the **Incident schema** `domain/incident.py`. Plan: `specs/004-ingestion-pipeline/plan.md`.
`003-llm-provider` — provider-agnostic async `LlmClient` (`Depends(get_llm)`),
Gemini primary + Ollama fallback behind SDKs confined to `infra/llm_drivers.py`, fail-closed contract,
`domain/llm.py`, `ollama` compose service. Plan: `specs/003-llm-provider/plan.md`.
`002-observability-redaction` — `structlog` redaction + correlation-id,
**OpenTelemetry** tracing → Postgres `trace_spans` (off-path `BatchSpanProcessor`), **Presidio + secret
scrubber** redaction; the unified `infra/observability.py` seam (`span()`, `record_llm_usage`,
`Redactor`) #3 consumes. Plan: `specs/002-observability-redaction/plan.md`.
`001-platform-infra` — compose stack, Vault, MinIO, async SQLAlchemy/Alembic, typed `pydantic-settings`
(`extra="forbid"`, `SecretStr`), layered `backend/` with `import-linter`, lifespan singletons via the
provider seam in `backend/infra/container.py`. Plan: `specs/001-platform-infra/plan.md`.
<!-- SPECKIT END -->
