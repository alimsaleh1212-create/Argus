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
