<!-- SPECKIT START -->
For additional context about technologies to be used, project structure,
shell commands, and other important information, read the current plan:

**Active component**: `008-knowledge-corpus` (Component #5 — seeded reference corpus + optional on-demand
intel; depends on #1, #6; **unblocks enrichment #9**). Realizes Constitution VI's "seeded corpus makes the
agent competent on the first incident."
- Plan: `specs/008-knowledge-corpus/plan.md`
- Spec: `specs/008-knowledge-corpus/spec.md`
- Design: `specs/008-knowledge-corpus/research.md`, `data-model.md`, `quickstart.md`, `contracts/`

Stack (this component): gives the empty #6 store **knowledge to reason over**, kept deliberately small.
**Two stores, each for what it fits, no new service, no LLM in the retrieval path (CD1):** static
reference docs (MITRE technique→mitigation + runbooks) → **new Postgres `reference_corpus` table**
(`0006`, deterministic **keyed/lexical** retrieval, embeddings reserved-not-built); temporal reputation
(seed IOC set + on-demand intel verdicts) → **`TemporalFact`s in #6** via one minimal **anticipated**
`MemoryStore.write_fact` addition (CD2; intel is a *fact*, not an `IncidentEpisode` — keeps `search_similar`
clean), read via existing `query_fact(as_of=…)` → invalidate-not-delete supersession. New pure types
`domain/corpus.py` (`ReferenceCorpusEntry`/`ReferenceHit`/`ReferenceQuery`/`IntelVerdict` + `CorpusRetriever`
Protocol, consumed by #9). **On-demand intel** (`infra/intel.py`, `ThreatIntelClient`) is **optional /
config-gated / fail-closed / off-path**: single source, `httpx`, Redis-cached (negative caching), missing
creds → **disabled not fail-boot**, outage/timeout → `unknown` (CD3). **Untrusted-input boundary** —
redact (`Boundary.MEMORY_WRITE`) + the reserved `Guardrail` seam (#11) **before any write**; the seam
**no-ops gracefully until #11 lands** so #5 isn't blocked (CD5). **Idempotent one-shot** `seed-corpus`
(`python -m backend.seed_corpus`, mirrors `migrate`) loads `backend/data/corpus/*.json` after migrate +
neo4j healthy (CD4). New `CorpusSettings`/`IntelSettings` (`extra="forbid"`; intel key Vault path
**optional**). **No new external LLM path** (unlike #6). Eval: extends the existing **`retrieval`**
gate with a corpus fixture set (cold-start, provider-independent) — **no new gate** (CD7). Milestones
**(a) seed→retrieve → (b) intel→temporal-fact** (commit at each). Enrichment/dashboard wiring is #9/#12;
live/streaming feeds are roadmap §v2/v3, out of v1.

Prior components (done): `007-incident-memory` — **temporal incident-memory layer** (Constitution VI):
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
