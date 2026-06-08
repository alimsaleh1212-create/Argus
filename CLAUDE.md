<!-- SPECKIT START -->
For additional context about technologies to be used, project structure,
shell commands, and other important information, read the current plan:

**Active component**: `006-triage-agent` (Component #8 — first LLM-backed stage; depends on #7, #4, #3).
- Plan: `specs/006-triage-agent/plan.md`
- Spec: `specs/006-triage-agent/spec.md`
- Design: `specs/006-triage-agent/research.md`, `data-model.md`, `quickstart.md`, `contracts/`

Stack (this component): replaces the #7 `agents/triage.py` **stub** with a real handler — the **first and
only LLM call in the pipeline so far** — runs **in the existing `worker`**, **no new service/dependency/
migration**. Triage fires **only** on the ambiguous incidents the supervisor fast-path routed to it
(`medium`/`high`/severity-undetermined). Makes **exactly one** structured-output call via the shared
`LlmClient` (#3, `response_schema`, no tools, no loop), validates the response into a pure `TriageJudgment`
(`domain/triage.py`: `TriageVerdict` real/noise/uncertain + `confidence` + `assessed_severity` +
evidence-citing `rationale` + `cited_evidence`), then a **pure, config-threshold-gated** `decide_outcome`
maps it to one `StageOutcome`: **ADVANCE** (real→enrichment), **RESOLVED** (confident noise→auto-close,
`auto_resolved_triage`, zero further stages), or **ESCALATE** (uncertain / `confidence<advance_min` / noise
`<resolve_min` → `escalated_triage`). Two config knobs (`TriageSettings`: `advance_min_confidence=0.6` ≤
`resolve_min_confidence=0.7` — auto-close is the higher-blast-radius bar). **Fail-closed everywhere** (TD7):
`LlmError`/malformed/OOV → typed `ToolError` (transient=retryable→supervisor retries then escalates;
permanent→escalate) — never auto-resolves on bad output, worker never crashes. **Structural Constitution III
boundary preserved**: DI by **closure factory** `make_triage_handler(llm, cfg)` keeps the frozen
`StageHandler` signature (no session, no action client, no write capability ever reaches triage); reasons
**only over already-redacted supplied evidence** (never priors, FR-005), reports `tokens_consumed` into the
supervisor cap (one call, SC-006). Records `assessed_severity` but **never** overwrites canonical severity.
One spec-scoped persistence extension (TD8): supervisor passes `StageResult.evidence_patch` to
`advance_status(evidence_patch=…)`, which **JSONB-merges** `{"triage": judgment}` into `evidence` in the same
guarded transition (single-writer; no migration). Wiring: `worker.py` registers `register_llm_provider()`
**before** `SupervisorProvider`; the provider builds the real triage handler from `container.llm`. Lands the
**triage real-vs-noise** eval gate (committed labeled set, macro-F1, abstention-bounded, **both providers** —
first eval with an LLM dimension).

Prior components (done): `005-incident-state-machine` — **deterministic supervisor** (`services/supervisor.py`,
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
