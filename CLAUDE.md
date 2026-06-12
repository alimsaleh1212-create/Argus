<!-- SPECKIT START -->
For additional context about technologies to be used, project structure,
shell commands, and other important information, read the current plan:

**Active component**: `012-dashboard` (Component #12 — the **React operations dashboard**, the human
surface; T1, **~1 day**, the final T1 component; depends on #10/#2/#3/#5/#7/#1 — all done; closes the
brief's "polished React operations dashboard" deliverable). The graded showcase surface.
- Plan: `specs/012-dashboard/plan.md`
- Spec: `specs/012-dashboard/spec.md`
- Design: `specs/012-dashboard/research.md`, `data-model.md`, `quickstart.md`, `contracts/`

Stack (this component): a **separate-image React SPA** (`frontend/`, Node 20 toolchain — the #1
second-runtime exception, never in the Python `uv` venv) backed by **read-side** backend endpoints; built
with the **`/ui-ux-pro-max`** skill (Dark-Mode OLED slate + green accent, Fira Sans/Code, app-shell;
Vite + React + TS + Tailwind + shadcn/ui + TanStack Query/Table + Recharts + native `EventSource`).
**Read-only except approve/reject** — the supervisor stays single writer; approve/reject **reuses #10's
`/approvals/{id}/decision`** (no direct state mutation, no action tools — Constitution III). Net-new
backend is small + additive: fill the reserved `routers/incidents.py` (`GET /incidents` queue +
`/{id}` detail + `/{id}/audit` + `/{id}/trace` + `/kpis` + `/stream`), add **admin auth** (none exists:
username+password in **Vault** → short-lived **HS256 JWT** w/ `role`, via `services/auth.py` +
`get_current_operator`; PyJWT + stdlib PBKDF2, **no native dep**), and **register** the `incidents` +
`approvals` routers (currently commented in `routers/__init__.py`) behind that dependency. **No migration**
— reads existing `incidents`/`approval_requests`/`audit_log`/`trace_spans`; auth is stateless (RD7). Pure
read DTOs in `domain/dashboard.py`; `IncidentRepository` gains **read** methods only (`list_for_queue`/
`count_for_queue` + KPI aggregates) — never a second writer. **Server push** = **SSE** (one-directional;
`GET /incidents/stream`) sourced by an **API-side 2s snapshot poll** (touches neither worker nor
supervisor; Redis pub/sub documented as the deferred scale-up — RD3/RD4), reconcile-on-reconnect + refetch
fallback. **Memory-hit KPI** = hits/enriched (denominator = incidents that reached enrichment), read from
the enrichment `evidence` signal. Redaction is **upstream** (#2) — dashboard asserts via tests, adds **no
de-redaction path** (RD8). **No new eval gate** — extends the **redaction** gate with a dashboard-view
check (deterministic UI). Ships as **milestone PRs** (Constitution I): P1 auth+shell+queue/detail → P2
approval+trace → P3 KPIs+SSE+polish. Packaging: `deploy/frontend/Dockerfile` (node build → nginx static +
reverse-proxy to `api`), `frontend` service uncommented in `compose.yaml`. Single `admin` role, one
console; no multi-tenancy/embeddable widget (v1 scope).

Prior components (done): `010-response-remediation` — the only **acting** stage + the HITL approval
interrupt. Determinism-first playbook select (catalog match, **no** LLM; ambiguous tail = **one**
`LlmClient` call); **config-backed default-deny** policy → **auto** allowlist executes via mock executors
(`infra/executors.py`, `ActionExecutor`) + **audit row**; **destructive** → `AWAITING_APPROVAL` +
`pending` row. Approve re-enters `RESPONDING` (re-runs to execute, no LLM on resume) → `remediated`;
reject → `rejected_by_human`; worker **timeout sweeper** → `approval_expired`; all **idempotent**.
**Owns `GET /approvals` + `POST /approvals/{id}/decision`** (drives `supervisor.resume_incident`; API
registers `SupervisorProvider`). New persistence `approval_requests` + `audit_log` (migration **0006**)
via `repositories/approvals.py`/`audit.py`; **`incidents` table unchanged**. Pure `domain/response.py`;
`ResponseSettings`. v1 records *applied*; `verification` reserved for **§v2c** (T2). Extends
**`supervisor-routing`** gate — no new gate. Plan: `specs/010-response-remediation/plan.md`.
`009-enrichment-agent` — **second LLM stage**: retrieval-only cross-correlation.
A **bounded retrieval fan-out (`asyncio.gather`) + exactly one** `LlmClient` call reads **both directions**
(external `CorpusRetriever`/`IntelVerdict` #5; internal `MemoryStore.search_similar` + `query_fact(as_of=…)`
#6) → validated `EnrichmentReport` (`domain/enrichment.py`) → pure `decide_outcome` → ADVANCE/RESOLVED/ESCALATE.
Closure-factory DI `make_enrichment_handler(...)`; **retrieval-only, no action tools, no write**; best-effort
retrieval + fail-closed reasoning; zero change to `supervisor.py`/`repositories/`/schema (#7 wired `ENRICHING`).
Extends the **`retrieval`** gate — no new gate. Plan: `specs/009-enrichment-agent/plan.md`.
`008-knowledge-corpus` — **seeded reference corpus + optional on-demand intel**
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
