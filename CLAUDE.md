<!-- SPECKIT START -->
For additional context about technologies to be used, project structure,
shell commands, and other important information, read the current plan:

**Active component**: `007-incident-memory` (Component #6 — temporal incident memory; depends on #1, #2, #3;
unblocks enrichment #9 + corpus #5).
- Plan: `specs/007-incident-memory/plan.md`
- Spec: `specs/007-incident-memory/spec.md`
- Design: `specs/007-incident-memory/research.md`, `data-model.md`, `quickstart.md`, `contracts/`

Stack (this component): fills the reserved `infra/memory.py` seam with the **temporal incident-memory layer**
(this spec *is* Constitution VI). **Graphiti on Neo4j 5.26** (`graphiti-core[google-genai]`) behind a small
**`MemoryStore` Protocol** (`domain/memory.py`: `write_episode`/`search_similar`/`query_fact`) so the
**decided pgvector fallback** (`valid_from`/`valid_to`, MD9) is a config-toggle swap. The **worker** writes
one **redacted, idempotent** `IncidentEpisode` per incident **after** `run_incident` reaches terminal —
**off the synchronous path, best-effort** (a memory outage never blocks a disposition or crashes the worker,
FR-006); the **supervisor stays pure** (no memory dep). `search_similar` surfaces the closest prior incidents
+ dispositions (hit@k/MRR); `query_fact(as_of=…)` returns the **time-valid** state (current vs. superseded)
via Graphiti's native **invalidate-not-delete** edges → `FactState`. **Redaction before every write** (#2
`Redactor`; the `redaction` gate's `memory_write` boundary goes live). Graphiti uses its **native Gemini**
LLM+embedder (Vault key) — the one **documented Constitution VII deviation** (the #3 adapter is
`generate()`-only, no embeddings; recorded in `DECISIONS.md`/Complexity Tracking). `MemoryProvider` lifespan
singleton **degrades to `NullMemory`** if Neo4j is down; **worker-only** wiring (api/#12 read-wiring
deferred). New: `neo4j:5.26` compose service (creds via `vault-seed` → `secret/memory`), `MemorySettings`
(`backend` toggle / `retrieval_k` / timeout), dep `graphiti-core[google-genai]` + dev `testcontainers[neo4j]`.
**Big spec** → milestones **0 spike → a write → b retrieve → c temporal** (commit at each). Lands the
**retrieval** (hit@k/MRR) + **temporal-validity** eval gates (deterministic **store-logic**,
**provider-independent** like smoke/routing). §v2c feedback loop is roadmap (T2), out of v1.

Prior components (done): `006-triage-agent` — **first LLM stage**: replaces the triage stub with **one**
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
