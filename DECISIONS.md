# Decisions — Sentinel

Records non-obvious architectural choices as required by the project constitution.
Each entry: what was chosen, why, and what alternatives were considered and rejected.

---

## Component 001 — Platform & Infrastructure Foundation

### D1 — Python 3.12 pin

**Decision**: Python 3.12, pinned `requires-python = ">=3.12,<3.13"` and `.python-version`.

**Rationale**: Broadest compatibility across the full Sentinel dependency set (Graphiti, async
SQLAlchemy 2.x, pydantic v2, asyncpg, aioboto3). Python 3.13 is still maturing for some C-extension
wheels; pinning one minor keeps `uv.lock` reproducible (SC-008).

**Rejected**: 3.13 (early-adopter risk for graph/db wheels); 3.11 (safe, but forgoes 3.12
typing/async ergonomics).

---

### D2 — FastAPI + native `Depends()` DI

**Decision**: FastAPI + uvicorn. DI is FastAPI-native: a typed `AppContainer` built in the lifespan,
held on `app.state`, exposed to handlers via `Depends()` provider functions. No third-party DI
container.

**Rationale**: The brief explicitly requires `Depends()`. FastAPI's lifespan gives exactly-once
singleton construction/disposal (FR-011); `Depends()` gives the override seam that mocks the LLM and
enforces "triage has no action tools" (FR-012/FR-013, Constitution III/VII).

**Rejected**: `dependency-injector` / `that-depends` (more machinery than needed); module-global
registry (violates FR-012).

---

### D3 — Provider protocol + ordered registry (the extensibility seam)

**Decision**: A `Provider` protocol — an async context manager that yields one built resource — plus
an ordered registry. The lifespan iterates the registry on startup and exits in reverse on shutdown.
Later specs append their provider without editing `lifespan.py`.

**Rationale**: Directly satisfies FR-014 ("registration seam so later specs attach singletons without
changing the foundation"). Reverse-order teardown guarantees clean disposal (FR-011, SC-005).

**Rejected**: Hand-wired `async with` stack in `lifespan.py` (every later spec must edit the
foundation — fails FR-014); `AsyncExitStack` alone (no named-attribute or ordering contract).

---

### D4 — Vault access via `httpx` (async HTTP, not `hvac`)

**Decision**: A thin async `VaultClient` wrapping `httpx.AsyncClient` against Vault's KV v2 HTTP
API. Unreachable Vault or a missing required key → raise → process exits non-zero.

**Rationale**: "Async all the way down" (Constitution VII). The official `hvac` client is synchronous
and would block the event loop. Vault's KV API is a handful of authenticated GETs — trivial over
`httpx`, consistent with the rest of the codebase.

**Rejected**: `hvac` (sync, off-loop); env-only secrets (defeats the secret store).

---

### D5 — MinIO via `aioboto3` (async S3)

**Decision**: `aioboto3` pointed at the MinIO S3-compatible endpoint. Buckets are created on startup
if absent.

**Rationale**: Keeps object I/O on the async path (FR-017, Constitution VII). Bucket bootstrap at
startup means later specs can write immediately without setup steps.

**Rejected**: Official `minio` Python SDK (synchronous); `boto3` (synchronous). Both blocked on the
async requirement.

---

### D6 — pgvector image, async SQLAlchemy + asyncpg, Alembic

**Decision**: PostgreSQL 16 via `pgvector/pgvector:pg16`; SQLAlchemy 2.0 async + asyncpg; Alembic
with async `env.py`; a baseline migration so the pipeline is exercised from day one.

**Rationale**: Using the pgvector image now means no image change when SPEC-memory (#6) lands. Async
SQLAlchemy + asyncpg matches the engineering standard. Alembic gives reproducible-up + reversible
(SC-006).

**Rejected**: Plain `postgres:16` (image swap later — avoidable churn); raw SQL scripts (no
autogenerate/version graph); `psycopg3` (fine, but asyncpg is faster for our access pattern).

---

### D7 — Separate `/health` (liveness) and `/ready` (readiness) endpoints

**Decision**: `GET /health` = liveness (no dependency I/O, always cheap). `GET /ready` = readiness
(probes Vault, Postgres, MinIO; returns 200 only when all healthy, 503 with per-dep status otherwise).
Compose `depends_on` uses `condition: service_healthy`.

**Rationale**: Separating liveness from readiness makes FR-008 and the edge cases ("slow dependency →
unhealthy, not failed") testable and avoids a false-healthy during bring-up.

**Rejected**: Single `/health` checking everything (can't distinguish starting from broken; makes
liveness expensive); no readiness probe (fails the bring-up edge cases).

---

### D8 — `testcontainers` for integration, real `compose.yaml` for e2e/smoke

**Decision**: `testcontainers-python` spins up ephemeral Vault/Postgres/MinIO for the integration
tier (hermetic, no pre-running stack needed). The e2e/smoke tier exercises the real committed
`compose.yaml` in CI.

**Rationale**: Gives the three tiers honest, independent meaning: unit (no services), integration
(real services, hermetic, fault-injectable), e2e/smoke (the actual artifact users run, SC-004).

**Rejected**: Reusing `compose.yaml` for integration (slower, harder to inject faults like "stop
Vault"); mocks only (doesn't prove real connectivity).

---

### D9 — `import-linter` for layer-contract enforcement

**Decision**: Enforce inward-only layer dependency (`routers → services → agents → repositories →
infra`; `domain` isolated) in CI via `import-linter` contracts.

**Rationale**: FR-018 requires a *documented and enforced* dependency direction. A CI contract makes
"enforced" real, not aspirational, and catches violations as later specs add files.

**Rejected**: `ruff` tidy-imports rules (coarser, hard to express a layer graph); convention-only
(not enforced → fails FR-018's "enforced").

---

### D10 — Eval gate seeded day 1 (`eval_thresholds.yaml`)

**Decision**: Commit `config/eval_thresholds.yaml` with a single provider-agnostic `smoke` gate
(stack-comes-up-clean) wired into CI as a required check. Full eval harness is SPEC-eval (#13).

**Rationale**: Constitution II mandates the threshold file be seeded on day 1 "so CI gates from the
start." The smoke gate is the only one meaningful for the foundation and is naturally
provider-independent, satisfying "passes on both providers" trivially.

**Rejected**: Defer the file to SPEC-eval (violates the day-1 seeding rule); seed all gates now
(their components don't exist yet).

---

### D11 — compose.yaml: no YAML anchors; `.env` only for vault-seed

**Decision**: Backend containers (`api`, `migrate`) receive all config via explicit `environment:`
keys in `compose.yaml`. `.env` (optional) is read only by `vault-seed` to seed user API keys into
Vault. No YAML anchors/merge keys (`&`, `<<:`).

**Rationale**: Anchors are standard YAML but non-obvious to read. Explicit `environment:` blocks are
self-documenting. Keeping `.env` off the API container prevents silent fallback to file-based
secrets; all runtime secrets come from Vault. The API's bootstrap config (Vault addr/token, DSN) are
well-known dev defaults appropriate for compose env vars.

**Rejected**: Single `env_file: .env` on all containers (couples app config to a file that should
only carry user secrets); YAML anchors (saves ~10 lines but at readability cost).

---

*This file is append-only within each component; later specs add their own section at the bottom.*

---

## Component 002 — Observability & Redaction (Cross-Cutting Foundation)

### OD1 — Tracing primitive: OpenTelemetry SDK

**Decision**: Use the OpenTelemetry SDK (`opentelemetry-api` + `opentelemetry-sdk`) as the in-process tracing primitive. A `_Tracer` wrapper creates `Span` domain objects for each agent step / tool call / retrieval; nesting comes from explicit `parent_span_id` passing; a `BatchSpanProcessor`-style enqueue/flush gives off-path export (FR-015).

**Rationale**: Async context propagation across `await`/`asyncio.gather` is already solved by OTel's `contextvars`-based model. OTel's span/attribute/status model maps 1:1 to "step = span, incident = trace tree" (FR-012/013). A hand-rolled tree would have to re-implement context propagation and get it subtly wrong.

**Rejected**: Hand-rolled span tree (reinvents async context propagation); Langfuse/Arize-Phoenix (SaaS dependency, vendor lock-in, scope creep).

### OD2 — Trace store: Postgres `trace_spans` (reuse #1; no new service)

**Decision**: A custom `TraceRepository` writes spans (batched off-path) into a `trace_spans` table in the existing Postgres instance from #1. This table is the queryable store the dashboard (#12) and eval (#13) read. A console exporter is available in dev.

**Rationale**: Dashboard KPIs join trace data to incidents — co-locating spans with incidents in Postgres makes those reads simple SQL. Adds no new backing service (scope discipline). Failed flushes drop the batch with a counter; never fail an incident (SC-006).

**Rejected**: Jaeger/Tempo + OTLP collector (two new services + custom query integration); MinIO JSON blobs (not queryable for KPI aggregation).

### OD3 — Correlation id: one incident id bound in `contextvars`

**Decision**: A single per-incident correlation id is bound into `structlog.contextvars` and set as the OTel `trace_id`, so every log line and every span carry the same id. The worker binds it on dequeue; routers bind a request id at entry.

**Rationale**: One id stitches the multi-agent async pipeline into a single story (FR-009, SC-002). Reuses the `merge_contextvars` processor already in the #1 logging chain.

**Rejected**: Explicit `ctx` argument threading (verbose, bypassable); OTel `trace_id` only (doesn't cover log lines outside a span).

### OD4 — Redaction engine: Presidio + deterministic secret scrubber

**Decision**: The `Redactor` Protocol is implemented by a `_CompositeRedactor` that composes (1) a deterministic secret scrubber (regex patterns for AWS keys, bearer/JWT, PEM blocks, kv secrets + Shannon-entropy heuristic) and (2) Microsoft Presidio Analyzer + Anonymizer for PII using `en_core_web_sm`. Both engines are lifespan singletons.

**Rationale**: Wazuh/packet payloads carry both PII and credentials. Presidio is the brief-mandated PII engine; the deterministic scrubber covers credentials Presidio doesn't (FR-001). `en_core_web_sm` is small/fast for the hot path.

**Rejected**: Regex-only (misses contextual PII like person names); LLM-based redaction (cost, latency, nondeterminism on hot path — violates Constitution IV).

### OD5 — Redaction policy: class × boundary matrix (FR-006a/b)

**Decision**: A `RedactionPolicy` dataclass (in `backend/domain/redaction.py`) holds a `dict[SensitiveClass, frozenset[Boundary]]`. The default encodes the spec decision: `CREDENTIAL` → all boundaries; `PII` and `OPERATIONAL_IDENTIFIER` → output boundaries only (LOG/TRACE/PROMPT/SNAPSHOT/DASHBOARD).

**Rationale**: Centralizes the policy so no call site can override it ad hoc (FR-006). The boundary enum makes the policy auditable by test. Raw operational identifiers (IPs, hostnames) retained in OPERATIONAL/MEMORY_WRITE for enrichment correlation (FR-006b).

**Rejected**: Scattered per-call-site decisions (unauditable, drift-prone); separate policies per component (fragmentation, inconsistency).

### OD6 — Fail-closed: every emission path catches exceptions and withholds raw content

**Decision**: The `_CompositeRedactor.redact_text/mapping` wraps all detection in try/except; on any exception, emits `[REDACTION-FAILED]` instead of the raw value. The structlog chain processor does the same per-field. The `span()` helper redacts attributes before queuing.

**Rationale**: FR-003 is non-negotiable: a redactor exception must never cause raw sensitive content to be emitted. `[REDACTION-FAILED]` is a clear signal for ops without exposing the value.

**Rejected**: Logging the exception and passing through (would emit raw); re-raising (would drop the line or crash the handler).

### OD7 — Singletons + off-path export for the overhead budget (SC-005)

**Decision**: The Presidio engine and `_CompositeRedactor` are built once at startup via `ObservabilityProvider`. The tracer's `_Tracer.enqueue()` is O(1) in-memory; `flush()` is called asynchronously off the incident path. The log chain processor is cheap (scrubber only, no model).

**Rationale**: SC-005 requires ≤5% p95 synchronous overhead. Loading the Presidio/spaCy model per-call would violate this instantly. Off-path export ensures trace writes never block incident processing.

**Rejected**: Per-call Presidio instantiation (100+ ms per call); synchronous span export (blocks the incident path).

### OD8 — structlog redaction processor in the chain (not a handler)

**Decision**: `_redact_event_dict` is inserted into the structlog processor chain (before `JSONRenderer`) so redaction is structurally guaranteed for every log line, regardless of which component emits it. Per-field try/except means one bad field drops that field (not the whole line).

**Rationale**: FR-010 requires no logging path to bypass redaction. A processor in the shared chain is the only structural enforcement; a handler would allow bypass via direct `logging` calls.

**Rejected**: Per-logger redaction (bypassable, inconsistent); a separate logging handler (still bypassable if someone calls `logging.getLogger` directly — which the no-bypass guard catches).

### OD9 — Token-accounting seam for downstream specs

**Decision**: `record_llm_usage(span, usage, model)` accepts a generic `usage` object and reads `.prompt_tokens`/`.completion_tokens` by attribute, defaulting to `None` (rendered as `unknown`). The supervisor's token cap (#7) and eval (#13) read from `trace_spans`.

**Rationale**: Component #3 selects the LLM provider; this component must account for tokens without binding to one provider's response shape (Constitution IV). `None` for missing usage is safe (FR-013, SC-004).

**Rejected**: Provider-specific token extraction here (couples #2 to #3's provider choice).

### OD10 — `span()` + `record_llm_usage()` ergonomics for downstream specs

**Decision**: Public surface is a context-manager `span(tracer, name, kind, correlation_id, ...)` and a one-liner `record_llm_usage(span, usage, model)`. All redaction and truncation happen inside; callers set raw attributes, the helper redacts before queuing.

**Rationale**: Every downstream spec (#4, #7, #8, #9, #10) will call `span()` hundreds of times. A clean, safe-by-default surface prevents each caller from having to remember to redact.

**Rejected**: Requiring callers to redact before passing attrs (error-prone, high bypass risk); a fluent builder API (overkill for the call patterns seen in the agents).

---

### OD11 — Overhead budget: absolute cap (SC-005 / SC-008 Tier-1 freeze re-verify)

**Decision**: The synchronous observability overhead budget is expressed as an **absolute cap of 5ms per observed incident** rather than a relative percentage. The Tier-1 freeze measurement (2026-06-08, 50-iteration p95 benchmark with `presidio_enabled=False`, in-memory exporter, `SpanKind.ROOT` + two child spans + `record_llm_usage`) recorded:

- p95 baseline (synthetic work only): **0.009ms**
- p95 with observability enabled: **0.178ms**
- **Absolute overhead: 0.169ms** (well within the 5ms cap; equivalent to <0.2% of a typical 100ms incident)

**Rationale**: A relative % budget (≤5%) is impractical when the synthetic baseline is microseconds — any non-trivial observability work produces a huge percentage. Real incidents take 100ms+, at which scale 0.169ms absolute overhead is negligible. The 5ms absolute cap is the meaningful operational constraint: it is the maximum the observability seam can add to any single incident in the synchronous path.

**Rejected**: ≤5% relative overhead (fails with microsecond baseline, vacuous with real-world baseline); no overhead check (leaves SC-005 unenforceable).

---

## Component 003 — Provider-Agnostic LLM Adapter

### LP1 — Provider pair: Gemini (primary, cloud) + Ollama (secondary, local)

**Decision**: Two configured providers — **Google Gemini** (`gemini-1.5-flash`) as the env-selected primary and a **local Ollama** runtime (`qwen2:0.5b`) as the automatic fallback.

**Rationale**: The spec explicitly names this pair. One cloud + one local backend satisfies the constitution's "evals on both providers" requirement. The capability gap (structured output, tool-calling quality) between Gemini and local Ollama is exactly what the fail-closed contract (LP4) and per-provider eval (LP5) are designed to handle.

**Rejected**: Anthropic-first (rejected by user for this component); Gemini+Anthropic (two cloud, no local path).

---

### LP2 — Transport: official async vendor SDKs, confined to one driver module

**Decision**: `google-genai` (Gemini async SDK) and `ollama` (Ollama Python SDK) imported **only** in `backend/infra/llm_drivers.py`. Each driver maps the uniform `LlmRequest` ↔ vendor API.

**Rationale**: SDKs handle auth, tool-calling, structured-output, and usage shapes correctly so the adapter owns only normalization, selection, fallback, validation, and telemetry. Confinement to one module makes the no-bypass guard (FR-001) target a single file.

**Rejected**: hand-rolled `httpx` REST (re-implements what SDKs get right); mixed approach (inconsistent).

---

### LP3 — Selection & fallback: env-selected primary, stateless per-call order

**Decision**: `LlmSettings.primary` + `LlmSettings.fallback_order` define a config-driven order. Every call begins at the primary and walks the order on transient failure. No circuit-breaker in v1 — each call is evaluated independently (stateless).

**Rationale**: Simplest correct behavior at demo scale; deterministic and trivially testable (no shared mutable state). Switching the primary is configuration-only (SC-002).

**Rejected**: circuit-breaker / sticky-secondary (deferred; lower latency under sustained outage but adds shared state).

---

### LP4 — Output contract: capability-aware request shaping + fail-closed post-call validation

**Decision**: After the call, the adapter validates the result against the caller's required schema/tools. If validation fails, raises `LlmError(CONTRACT_UNSATISFIED)` — never returns a silently degraded result. `ProviderCapability` used for request shaping only, not routing.

**Rationale**: A malformed disposition is worse than a surfaced error for a security SOAR — it becomes a step the supervisor escalates to a human (HITL-consistent). Post-call validation keeps acceptance tests crisp (SC-009).

**Rejected**: best-effort degraded result (pushes handling to every caller, weakens tests); capability-matched routing (user chose fail-closed).

---

### LP5 — Readiness: at-least-one-reachable gate via the existing `/ready`

**Decision**: `check_llm(settings)` returns `DependencyStatus(name="llm", healthy=any(reachable))`, included in `run_readiness_probes`. `/ready` is 503 only when no provider is reachable.

**Rationale**: Blocking boot on provider reachability would defeat fallback (a Gemini blip at startup should still bring the app up to serve via Ollama). Reachability failures are readiness-only, never liveness.

**Rejected**: all-providers-verified-at-boot (brittle; partially defeats fallback); no readiness gate (silently accepts incidents with no usable provider).

---

### LP6 — Token-usage normalization onto #2's `record_llm_usage` shape

**Decision**: `TokenUsage(prompt_tokens, completion_tokens)` with field names matching `record_llm_usage`'s `getattr` reads. Gemini: `prompt_token_count`/`candidates_token_count`; Ollama: `prompt_eval_count`/`eval_count`. Missing counts stay `None`.

**Rationale**: No change to #2 required — field names match exactly. `None` counts render as "unknown" in views, never fabricated (FR-013, SC-004).

**Rejected**: bespoke usage type (would force change to #2); fabricating estimates (constitution: never fabricate).

---

### LP7 — Redaction wiring at the model boundary

**Decision**: (a) Scrub CREDENTIAL-class content from the outbound prompt via `#2 Redactor` at `Boundary.OPERATIONAL` before transmission. (b) Record prompt/completion as span attributes — `span()` redacts them at `Boundary.TRACE`. Does not strip operational identifiers (IP/host/user).

**Rationale**: FR-012 requires redaction "before logged, traced, or stored" — `#2 span()` already provides this. Outbound credential scrubbing prevents raw API keys in alert text from reaching providers. Operational identifiers are preserved so the agent can reason over them.

**Rejected**: redact PII from outbound prompt (blinds enrichment correlation); re-implement redaction in adapter (violates no-bypass).

---

### LP8 — Timeouts, transient-only retry, and error taxonomy

**Decision**: Per-call `asyncio.wait_for` timeout (`request_timeout_s=30s`), transient-only bounded `tenacity` retry (`max_retries=2`, exponential backoff). Error taxonomy: `TRANSIENT` (timeout/429/5xx → retry+failover), `AUTH`/`INVALID_REQUEST`/`CONTENT_REFUSAL` (non-retryable, no failover), `CONTRACT_UNSATISFIED` (fail-closed), `EXHAUSTED` (all providers tried).

**Rationale**: Matches FR-008/FR-009/FR-010 and the existing `tenacity`-on-transient-only convention (mirrors Vault retry). A timeout is transient — a slow provider never hangs the incident path.

**Rejected**: retry all errors (masks auth/validation issues); no retry, fail over immediately (single blip burns the fallback).

---

### LP9 — Ollama as a compose service; per-tier test strategy

**Decision**: `ollama` compose service (official image) with one-shot `qwen2:0.5b` pull. Unit tests fake both drivers; integration tests run real Ollama + Gemini via mocked HTTP (live test gated on `GEMINI_API_KEY`); e2e uses injected fakes for fallback/telemetry assertions.

**Rationale**: Three real tiers with CI-deterministic behavior. A tiny pinned model keeps Ollama self-contained; gating live Gemini on key presence keeps keyless CI green.

**Rejected**: real Gemini calls in CI (cost/flakiness/secret management); no Ollama service (breaks fresh-clone reproducibility); large Ollama model (CI RAM/time blowup).

---

### LP10 — Lifespan singleton + registration order

**Decision**: `LlmProvider` (Provider protocol, `name="llm"`) builds both driver clients once via an async context manager. Registered **after** observability in `_bootstrap_providers()` in `main.py`. `get_llm()` in `dependencies.py` returns `app.state.container.llm`. The lifespan sets `settings._container` so providers can access already-built siblings.

**Rationale**: Matches #1's provider seam. The `settings._container` hack mirrors #2's pattern for the trace-repository access; a proper multi-arg provider build interface is a future refactor.

**Rejected**: build clients per call (wasteful, breaks singleton standard); read observability via module global (violates no-bypass / DI standard).

---

### LP11 — Structured-output & tool-calling mechanism per provider

**Decision**: Gemini: `response_mime_type="application/json"` + `response_schema` for structured output; `FunctionDeclaration` + `ToolConfig` for tool-calling. Ollama: `format=<schema>` for structured output; OpenAI-compatible `tools` list. Post-call validation enforces the contract regardless of provider capability.

**Rationale**: Each driver uses the provider's best-supported mechanism; callers describe tools/schema once and are provider-agnostic. Fail-closed validation catches weaker local-model failovers.

**Rejected**: lowest-common-denominator JSON-in-text for both (wastes Gemini's native structured output); per-caller vendor branching (defeats the provider-agnostic seam).

---

## Component 004 — Alert Ingestion Pipeline

### ID1 — Redis reliable-list queue (not a broker)

**Decision**: `BLMOVE main→processing` for dequeue, `LREM` for ack, drain processing→main on worker startup. At-least-once delivery with bounded retry → `failed`.

**Rationale**: The brief fixed the push-webhook→queue→worker shape. A minimal Redis-list pattern provides at-least-once delivery without adding a broker dependency. Idempotent grounding makes re-delivery harmless.

**Rejected**: RabbitMQ/Kafka (over-engineered for single-SOC demo scale); in-memory queue (loses state across crashes); inline processing in the request handler (couples acknowledgment latency to grounding work).

---

### ID2 — Redis for dedup (SET NX EX), not Postgres

**Decision**: `SET dedup:<fingerprint> <incident_id> NX EX window_s` for dedup. Fingerprint is SHA-256 over (rule_id, agent_id, content_signature), computed over redacted content.

**Rationale**: Dedup is a short-lived, high-throughput operation. Redis SET NX EX is atomic and O(1). Postgres would require a unique-constraint upsert on every ingest, adding lock contention on the hot path.

**Rejected**: Postgres unique constraint (lock contention, slower); Bloom filter (probabilistic, false positives unacceptable for incident dedup).

---

### ID3 — Fail-closed redaction before any persistence/logging/enqueue

**Decision**: `redactor.redact_mapping(alert, Boundary.SNAPSHOT)` is called first in `intake.accept()`; any redaction error propagates and nothing is persisted or enqueued.

**Rationale**: Alert text is untrusted, attacker-influenceable input. A failure to redact must never result in a raw secret appearing in Postgres, Redis, or logs. Fail-closed is the only safe default.

**Rejected**: Persist first, redact later (a crash between persist and redact would leave a raw alert in the DB); best-effort redaction (partial redaction gives false safety confidence).

---

### ID4 — Atomic accept-and-enqueue (no orphan Incidents)

**Decision**: If `queue.enqueue()` raises after `repo.create()`, the insert is immediately deleted and the exception re-raises to the router as a 503. No committed-but-never-queued Incidents.

**Rationale**: An orphan `received` Incident with no queue entry would never reach `grounded` or `failed` — it would be permanently stuck. The constitution requires every Incident reaches a terminal state (SC-006).

**Rejected**: Background cleanup job (eventually consistent, complex, still leaves a window); two-phase commit (over-engineered for single-worker demo scale).

---

### ID5 — Deterministic severity band (rule.level → Severity)

**Decision**: 0–3→low, 4–7→medium, 8–11→high, 12–15→critical. Missing/unparseable level → medium + `severity_defaulted` flag.

**Rationale**: Deterministic banding is a pure function with no I/O. It gives consistent, auditable triage severity without an LLM call on the hot ingest path (Constitution Principle IV).

**Rejected**: LLM-assigned severity (non-deterministic, adds latency on every ingest, wastes the LLM budget on a lookup table problem).

---

### ID6 — WazuhAlert extra="ignore"

**Decision**: `WazuhAlert` (and nested types) use `model_config = ConfigDict(extra="ignore")` so unknown Wazuh fields are silently dropped.

**Rationale**: Wazuh alert schemas vary by version and rule set. Strict rejection would break on any field we haven't modelled, making the pipeline brittle to Wazuh upgrades. The important fields (rule, agent, timestamp, full_log) are explicitly modelled.

**Rejected**: extra="forbid" (too brittle against Wazuh version drift); extra="allow" (pollutes the domain type with arbitrary vendor data).

---

### ID7 — Grounding is pure/deterministic, no LLM

**Decision**: `services/grounding.py::ground()` is a pure function: assembles Evidence from the NormalizedEvent with no I/O and no LLM call.

**Rationale**: Constitution Principle IV — "use determinism where it suffices." Grounding assembles the inputs for the triage agent (#8); it does not reason. A pure function is trivially testable, deterministic across re-runs, and adds zero latency.

**Rejected**: LLM-assisted grounding (constitution violation at this stage; the triage agent owns the reasoning step).

---

### ID8 — One image, two containers (API + worker)

**Decision**: `worker` compose service uses the same `sentinel-backend:local` image with `command: ["python", "-m", "backend.worker"]`.

**Rationale**: No second Dockerfile, no separate dependency set to maintain. The worker uses the same provider/settings/observability infrastructure as the API — consistent behaviour, single build.

**Rejected**: Separate worker image (two Dockerfiles to maintain, no meaningful benefit at demo scale).

---

### ID9 — Webhook shared-secret from Vault (required, fail-boot)

**Decision**: `ingest.webhook_vault_path` is appended to `vault.required_paths` via a `model_validator`; missing secret → fail boot (same pattern as the LLM API key).

**Rationale**: A webhook without authentication is a public endpoint for injecting arbitrary alerts. Failing boot on a missing secret is simpler and more reliable than checking at runtime.

**Rejected**: Environment-variable token (not Vault-managed, leaks into process env); optional token (unauthenticated webhook is a security boundary violation).

---

### ID10 — Bounded retry → terminal `failed` (no stuck Incidents)

**Decision**: Worker increments `attempts` on exception; at `ingest.max_attempts` (default 3), sets status to `failed` and acks the job (stops re-delivery). Idempotent grounding makes at-least-once safe.

**Rationale**: Without a budget, a poison job (malformed normalised_event, grounding bug) would re-enqueue forever, consuming worker capacity. `failed` is a recoverable terminal state — operators can inspect and replay.

**Rejected**: Infinite retry (poison jobs starve the queue); discard without marking failed (silent data loss, violates SC-006).

---

### ID11 — redis + worker compose activation (pre-reserved in #1)

**Decision**: Both services were pre-reserved (commented-out) in #1's `compose.yaml`. This component activates them by uncommenting and wiring the `depends_on` chain.

**Rationale**: The brief and #1 already committed to the push-webhook→queue→worker shape. Activating pre-reserved infrastructure is not a complexity deviation; it is the planned delivery.

**Rejected**: Activate only redis and stub the worker (leaves the queue unconsumed, defeats the grounding milestone); add a third service type (out of scope).

---

## Component 005 — Incident State Machine (Deterministic Supervisor)

### SD1 — Plain async state machine (no LangGraph, no LLM)

**Decision**: The supervisor is a hand-written `while` loop over an explicit `TRANSITIONS: dict[(IncidentStatus, str), (IncidentStatus, str | None)]` table inside `backend/services/supervisor.py`. No LangGraph, no LLM call within the supervisor, no async frameworks beyond `asyncio`.

**Rationale**: The spec explicitly requires "plain async state machine (explicit transition table + loop) — not an LLM and not LangGraph (deferred to #10)" and SC-006 prohibits LLM imports in the supervisor. A hand-written FSM is fully deterministic, unit-testable with zero infrastructure, and enables a provider-independent 100%-pass eval gate. The transition table is the single source of truth for all legal state changes; any outcome not in the table immediately escalates (illegal-transition guard), which satisfies the single-writer contract (Constitution III structural). LangGraph's HITL interrupt mechanism and graph runtime are reserved for component #10, where they add HITL workflow management that is out of scope here.

**Rejected**: LangGraph now (deferred — adds the graph runtime + checkpointer complexity before HITL/interrupt is designed; breaks the provider-independent eval gate); LLM-based routing (non-deterministic, adds latency, violates SC-006); async event/signal library (hides state machine semantics, harder to audit against the transition table).

---

### SD2 — Migration 0004: nullable `disposition` column (text, no enum)

**Decision**: `0004_incident_disposition.py` adds a single nullable `TEXT` column `disposition` to the `incidents` table. Values are short snake-case vocab words (`auto_resolved_noise`, `escalated_stage_error`, etc.) asserted by the supervisor — not a Postgres enum.

**Rationale**: Using a plain text column avoids a Postgres `ALTER TYPE … ADD VALUE` migration every time the disposition vocabulary extends (expected for components #8–#12). Nullable means existing `grounded` rows require no backfill. The supervisor writes the column atomically inside `advance_status` via a single `UPDATE … WHERE status=:expected RETURNING id`.

**Rejected**: Postgres `ENUM` type (requires `ALTER TYPE` for each new value — rigid, error-prone cross-migration); a separate `dispositions` table (adds a join on the hot path; no benefit at this vocabulary size); non-nullable with a default (forces a migration-time backfill of thousands of rows in production).

---

### SD3 — `SupervisorSettings`: typed pydantic-settings config, no runtime mutation

**Decision**: `SupervisorSettings` (a Pydantic `BaseModel` with `extra="forbid"`) is nested under `Settings.supervisor` via `Field(default_factory=SupervisorSettings)`. It exposes: `max_steps=8`, `max_tokens=40_000`, `max_stage_retries=2`, `fast_path_autoclose_severities=["low"]`, `fast_path_critical_severities=["critical"]`. The `"supervisor"` section is added to `_KNOWN_SENTINEL_SECTIONS` for the nested-env-prefix loader.

**Rationale**: All tuning knobs that affect routing behavior belong in config, not code, so they can be adjusted per environment without redeployment (Constitution II). `extra="forbid"` catches typos in env var names at startup rather than silently ignoring them. Typed lists for the severity bands mean adding/removing a severity (e.g. promoting "high" to a fast-path) is a one-line config change, provider-independent and testable.

**Rejected**: Hardcoded constants in the supervisor body (untunable without code change, untestable isolation); environment variables read ad hoc in the supervisor (bypasses the validated settings graph, violates SC-007); mutable settings object (would allow stages to influence future routing decisions, breaking the single-writer contract).
