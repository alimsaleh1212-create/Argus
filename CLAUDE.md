<!-- SPECKIT START -->
For additional context about technologies to be used, project structure,
shell commands, and other important information, read the current plan:

**Active component**: `004-ingestion-pipeline` (Component #4 — Alert Ingestion Pipeline; depends on #1
and #2).
- Plan: `specs/004-ingestion-pipeline/plan.md`
- Spec: `specs/004-ingestion-pipeline/spec.md`
- Design: `specs/004-ingestion-pipeline/research.md`, `data-model.md`, `quickstart.md`, `contracts/`

Stack (this component): fills the #1-reserved `routers/ingest.py`, `infra/queue.py`, `infra/cache.py`,
and `worker.py` seams so a Wazuh alert flows **webhook → queue → worker → Incident**. Thin webhook
(`POST /ingest/wazuh`): **validate → redact → dedup → persist → enqueue → `202`** (`services/intake.py`);
the **async worker** runs a deterministic **grounding** step (`services/grounding.py`, no LLM) then a
**logging-stub handoff** (`services/pipeline.py`, filled by #7). **Postgres `incidents` table is the
source of truth** (migration `0003`); **Redis is transient** — reliable-list queue (`BLMOVE`
main→processing + `LREM` ack + startup `recover()`, at-least-once) and `SET NX EX` dedup on a redacted
content fingerprint. Owns the **single Incident schema** `domain/incident.py` (`Incident`,
`IncidentStatus` `received/grounding/grounded/failed`, `Severity`, `NormalizedEvent`, `Evidence`,
`WazuhAlert`) imported by #7/#8/#12. Deterministic Wazuh `rule.level`→severity band; **idempotent**
grounding; **atomic accept-and-enqueue** (enqueue fail → `503`, no orphan); **bounded retry → `failed`**.
`redis.asyncio` confined to `infra/` (no-bypass); redaction at `SNAPSHOT`/`LOG` (#2); webhook
shared-secret from Vault `secret/ingest` (required→fail boot); `check_redis` in `/ready`. Adds the
`redis` + `worker` compose services (pre-reserved in #1); typed `redis`/`ingest` settings sections.
No new eval gate — strengthens existing smoke + redaction gates.

Prior components (done): `003-llm-provider` — provider-agnostic async `LlmClient` (`Depends(get_llm)`),
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
