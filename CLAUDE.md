<!-- SPECKIT START -->
For additional context about technologies to be used, project structure,
shell commands, and other important information, read the current plan:

**Active component**: `002-observability-redaction` (Component #2 — Observability & Redaction, the
first cross-cutting concern; depends only on #1).
- Plan: `specs/002-observability-redaction/plan.md`
- Spec: `specs/002-observability-redaction/spec.md`
- Design: `specs/002-observability-redaction/research.md`, `data-model.md`, `quickstart.md`, `contracts/`

Stack (this component): builds on #1; adds `structlog` redaction processor + correlation-id binding,
**OpenTelemetry** (`opentelemetry-sdk`/`-api`) tracing with a **Postgres `trace_spans`** store
(reusing #1's pgvector instance; no new service) exported off the synchronous path via
`BatchSpanProcessor`, and **redaction** = Microsoft **Presidio** (PII, `en_core_web_sm`) + a
deterministic regex/entropy **secret scrubber** behind the reserved `Redactor` Protocol. Fail-closed,
no-bypass; **credentials scrubbed everywhere** (incl. memory writes), **PII redacted at output
boundaries**, raw operational identifiers (IP/host/user) kept internally for correlation (FR-006a/b).
Fills the reserved seams `backend/infra/redaction.py` + `logging.py`; adds `infra/tracing.py`,
`infra/observability.py` (the one seam via `Depends()`), `domain/{redaction,telemetry}.py`,
`repositories/trace_repository.py`, and one Alembic migration. Seeds the `redaction` eval gate.

Prior component (done): `001-platform-infra` — compose stack, Vault, MinIO, async SQLAlchemy/Alembic,
typed `pydantic-settings` (`extra="forbid"`, `SecretStr`), layered `backend/`
(routers/services/agents/repositories/domain/infra) with `import-linter` inward-only imports, lifespan
singletons via the provider seam in `backend/infra/container.py`. Plan: `specs/001-platform-infra/plan.md`.
<!-- SPECKIT END -->
