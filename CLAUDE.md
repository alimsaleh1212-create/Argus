<!-- SPECKIT START -->
For additional context about technologies to be used, project structure,
shell commands, and other important information, read the current plan:

**Active component**: `013-eval-harness` (Component #13 — `SPEC-eval`, the **consolidated evaluation
harness**; T1, *(big)*, the **day-9 freeze** spec; depends on all of #2–#12). Wires the already-seeded
eval gates into CI, runs both providers at the freeze, persists `eval_report.json` to MinIO, and adds the
one net-new LLM-judge **rationale** gate. **Red-team gate stays deferred to #11/v3b (VD1).**
- Plan: `specs/013-eval-harness/plan.md`
- Spec: `specs/013-eval-harness/spec.md`
- Design: `specs/013-eval-harness/research.md`, `data-model.md`, `quickstart.md`, `contracts/`

Stack (this component): a **backend-only consolidation** — no new service, no migration, no frontend
change. The harness is a top-level **`backend/eval/`** entrypoint package (peer to `worker.py`/
`seed_corpus.py`): `python -m backend.eval` reads **`config/eval_thresholds.yaml`** as the single source
of truth, runs every declared gate via a **registry** (declared⇔registered **orphan/stale check = hard
error**), and emits an `EvalReport` (pure DTOs in **`domain/eval.py`**). The seven already-seeded gates
(smoke, redaction, supervisor_routing, llm_provider, triage, retrieval, temporal_memory) are **consumed
unchanged** with thresholds read from the yaml, never hardcoded. **CI wiring is the core gap closed**
(today `tests/eval/` is not run by CI): a new **required `eval` job** in `ci.yml` runs the deterministic
gates + LLM gates on **Ollama only** per-PR (fork-safe, no Gemini key); a new **`eval-freeze.yml`**
(nightly + `v*` tag + dispatch) runs **both providers**, the pinned-judge **rationale** gate, and uploads
the report to the reserved **`eval-reports`** MinIO bucket keyed by commit/run (**history retained**).
The net-new **rationale** gate is **reported-only** (only a catastrophic floor blocks), judge **pinned to
Gemini**, scoring rationales from **both** producers, validated against a small hand-labeled set under
`tests/fixtures/rationale/`. New **`EvalSettings`** (`extra="forbid"`) holds wiring; `pyyaml` promoted to
a direct dep. Memory-safe via **`scripts/run-evals.sh`** (one gate per subprocess, mirrors
`run-tests.sh`). Ships **3 milestone PRs** (Constitution I): M1 harness+CI deterministic → M2
both-providers+MinIO → M3 rationale judge. The **red-team/injection gate is deferred to #11/v3b (VD1)** —
seam reserved, no v1 injection-coverage claim; the constitution amendment is a separate `/speckit-constitution` action.

Prior components (done): `012-dashboard` — the **React operations dashboard** (the human surface, graded
showcase). Separate-image React SPA (`frontend/`, Node 20) over **read-side** endpoints; **read-only
except approve/reject** (reuses #10's `/approvals/{id}/decision`; supervisor stays single writer —
Constitution III). Filled the reserved `routers/incidents.py` (queue/detail/audit/trace/kpis/stream) +
**admin auth** (username+password in Vault → HS256 JWT, `services/auth.py`/`get_current_operator`,
PyJWT + stdlib PBKDF2); registered the `incidents`+`approvals` routers. **No migration** (reads existing
tables); pure read DTOs in `domain/dashboard.py`; **SSE** push from an API-side 2s snapshot poll. Extends
the **redaction** gate with a dashboard-view check — no new gate. Plan: `specs/012-dashboard/plan.md`.
`010-response-remediation` — the only **acting** stage + the HITL approval
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
