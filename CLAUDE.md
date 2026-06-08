<!-- SPECKIT START -->
For additional context about technologies to be used, project structure,
shell commands, and other important information, read the current plan:

**Active component**: `003-llm-provider` (Component #3 — Provider-Agnostic LLM Adapter, a cross-cutting
concern; depends on #1 and #2).
- Plan: `specs/003-llm-provider/plan.md`
- Spec: `specs/003-llm-provider/spec.md`
- Design: `specs/003-llm-provider/research.md`, `data-model.md`, `quickstart.md`, `contracts/`

Stack (this component): fills the #1-reserved `backend/infra/llm.py` seam with one **provider-agnostic
async LLM adapter** (`LlmClient` via `Depends(get_llm)`, lifespan singleton). Two configured providers —
**Google Gemini (cloud, primary)** + **local Ollama (fallback)**, the brief-named pair (no Anthropic in
this component) — behind official async SDKs (`google-genai`, `ollama`) confined to a new
`infra/llm_drivers.py` (no-bypass). **Env-selected primary; stateless per-call fallback** (no
circuit-breaker); **fail-closed** output contract (validate structured-output/tools or raise
`CONTRACT_UNSATISFIED`, never degrade); per-call **timeout + transient-only retry**. Wires the #2
seam: opens an `LLM_CALL` span, `record_llm_usage` (tokens/model/latency; `TokenUsage.prompt_tokens`/
`completion_tokens` match #2's hook), redacts recorded prompt/completion, scrubs credentials from the
outbound prompt. **At-least-one-reachable** `/ready` gate (`check_llm`); Gemini key from Vault
(`secret/llm`, required→fail boot). Adds pure types `domain/llm.py`, a typed `llm` settings section, a
new **`ollama` compose service** (tiny model), and seeds the `llm_provider` both-providers eval gate.

Prior components (done): `002-observability-redaction` — `structlog` redaction + correlation-id,
**OpenTelemetry** tracing → Postgres `trace_spans` (off-path `BatchSpanProcessor`), **Presidio + secret
scrubber** redaction; the unified `infra/observability.py` seam (`span()`, `record_llm_usage`,
`Redactor`) #3 consumes. Plan: `specs/002-observability-redaction/plan.md`.
`001-platform-infra` — compose stack, Vault, MinIO, async SQLAlchemy/Alembic, typed `pydantic-settings`
(`extra="forbid"`, `SecretStr`), layered `backend/` with `import-linter`, lifespan singletons via the
provider seam in `backend/infra/container.py`. Plan: `specs/001-platform-infra/plan.md`.
<!-- SPECKIT END -->
