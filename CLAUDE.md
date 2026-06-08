<!-- SPECKIT START -->
For additional context about technologies to be used, project structure,
shell commands, and other important information, read the current plan:

**Active component**: `005-incident-state-machine` (Component #7 — deterministic supervisor; depends on #4
and #3).
- Plan: `specs/005-incident-state-machine/plan.md`
- Spec: `specs/005-incident-state-machine/spec.md`
- Design: `specs/005-incident-state-machine/research.md`, `data-model.md`, `quickstart.md`, `contracts/`

Stack (this component): fills the #4-reserved `services/pipeline.py:dispatch_to_pipeline` seam and the
#1-reserved `agents/` stubs with a **deterministic supervisor** (`services/supervisor.py`) — a **plain
async state machine** (explicit transition table + loop), **not** an LLM and **not** LangGraph (deferred to
#10). Runs **in the existing `worker` container** — no new service, no new dependency, **no LLM call**
(SC-006). Drives a grounded Incident `grounded → {resolved | escalated | failed}` (or parks in
`awaiting_approval`): **determinism-first routing** (config-backed fast-path — `severity=low` ⇒
auto-`resolved` with **zero** stage calls; `severity=critical` ⇒ straight to `responding`; `medium`/`high`
⇒ ambiguous full depth triage→enrichment→response, **adaptive** — enrichment only if triage `ADVANCE`s);
**hard step+token cap** → `escalated`; **graceful degradation** (retryable `ToolError` retried, else
`escalated`; worker never crashes). **Single-writer**: stages are **pure handlers** returning a
`StageResult` (or raising `ToolError`) — the supervisor persists all state, so triage/enrichment get no
DB-write/action capability (Constitution III structural). New pure types `domain/pipeline.py`
(`StageName`, `StageOutcome`, `StageResult`, `ToolError`) imported by #8/#9/#10/#12; **extends**
`IncidentStatus` (+`triaging/enriching/responding/awaiting_approval/resolved/escalated`, no migration — text)
and adds nullable `disposition` (migration `0004`). Guarded `advance_status` transitions ⇒ idempotent /
resumable (at-least-once). `awaiting_approval` **park + resume edges** owned here; interrupt mechanism /
timeout / audit are #10. Typed `supervisor` settings; spans per step via #2 (redacted). Lands the
**supervisor-routing** eval gate (deterministic fixtures, provider-independent).

Prior components (done): `004-ingestion-pipeline` — Wazuh **webhook → queue → worker → Incident**; thin
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
