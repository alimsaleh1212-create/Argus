<!-- SPECKIT START -->
For additional context about technologies to be used, project structure,
shell commands, and other important information, read the current plan:

**Active component**: `010-response-remediation` (Component #10 — the only **acting** stage + the
human-in-the-loop approval interrupt; **"big" spec, ~2 days**; depends on #7, #3 — all done;
**unblocks dashboard #12 approve/reject + audit**). Realizes the brief's "tiered remediation with a
human-in-the-loop interrupt" deliverable.
- Plan: `specs/010-response-remediation/plan.md`
- Spec: `specs/010-response-remediation/spec.md`
- Design: `specs/010-response-remediation/research.md`, `data-model.md`, `quickstart.md`, `contracts/`

Stack (this component): replaces the `run_response` stub with the only **acting** stage and **completes the
supervisor's reserved interrupt/resume seam** (#5 left `resume_incident` for #10: "timeout, audit rows, and
action execution are Component #10"). Forward path is **determinism-first** (clarification Q1, RD1): a
deterministic catalog match selects the playbook with **no** LLM call; only the ambiguous tail (multi-candidate
/ failed preconditions / conflict) makes **at most one** structured `LlmClient` call. A **pure config-backed
default-deny policy** classifies each action → **auto** (allowlist: add-to-watchlist/open-ticket/enrich-and-tag)
executes now via **mock executors** (`infra/executors.py`, `ActionExecutor` Protocol — the action tools, **this
stage only**, Constitution III) + an **audit row**; **destructive** (isolate-host/disable-user/block-IP) →
`NEEDS_APPROVAL` → supervisor parks `AWAITING_APPROVAL` (edge already exists) + writes a `pending` row. Approve
re-enters `RESPONDING` so the **response stage re-runs to execute** (RD3 — execution never leaves the
tool-holding stage; no LLM on resume) → `remediated`; reject → `rejected_by_human`; a worker **timeout sweeper**
expires past-deadline approvals → `ESCALATED`/`approval_expired` (RD7); all **idempotent** (RD6). Distinct
dispositions (Q2): `auto_remediated`/`remediated`/`rejected_by_human`/`approval_expired`/`escalated_response`.
**#10 owns the backend approvals endpoint** (Q3): `GET /approvals` + `POST /approvals/{id}/decision` → records +
resumes (drives `supervisor.resume_incident` synchronously; **API now registers `SupervisorProvider`**, RD4).
**New persistence** (the one schema change): `approval_requests` + `audit_log` (migration **0006**) via new
`repositories/approvals.py`/`audit.py`; **`incidents` table unchanged** (new disposition values are text) so the
supervisor stays its single writer. Pure types in `domain/response.py` (`RemediationPlan`/`RemediationAction`/
`ActionResult`); `ResponseSettings` (`extra="forbid"`). **v1 records *applied*, never *eliminated*** (Q4,
FR-020) — `ActionResult.verification` + `remediation_unverified` are **reserved**; the post-remediation
verification + feedback loop is the designed **§v2c** section, implemented at the **T2** checkpoint (Q5, layering
contract not traded). Eval: extends the existing **`supervisor-routing`** gate with response fixtures — **no new
gate** (RD12); remediation-rationale LLM-judge deferred to #13. Bounded supervisor edits only (RD2/RD8): finish
`resume_incident`, add `expire_incident`, `(RESPONDING,RESOLVED)` disposition passthrough — **not** new
routing/cap logic. Mock environment; rollback/per-action-granularity out of v1.

Prior components (done): `009-enrichment-agent` — **second LLM stage**: retrieval-only cross-correlation.
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
