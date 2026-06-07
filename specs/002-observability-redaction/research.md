# Phase 0 Research — Observability & Redaction

**Feature**: `002-observability-redaction` | **Date**: 2026-06-07

The brief and `DECISIONS.md` already fix the spine: `structlog` for structured logging; tracing where
each agent step/tool/retrieval is a span and an incident is a trace tree, with export off the
synchronous path; redaction as **Presidio (PII) + a deterministic secret/credential scrubber** behind
one `Redactor` interface, in-process, applied at the logs / LLM-prompts / stored-snapshots boundaries.
The spec adds the FR-006a/b scope decision (credentials everywhere; PII at output boundaries; raw
operational identifiers retained internally). This document records the **open decisions** left to
implementation, each as Decision / Rationale / Alternatives — to be carried into `DECISIONS.md`.

There were **no open `NEEDS CLARIFICATION` markers** entering planning (the one spec fork was resolved
during `/speckit-specify`). The decisions below resolve the implementation unknowns.

---

## OD1 — Tracing primitive: OpenTelemetry SDK (not hand-rolled, not a tracing backend)

- **Decision**: Use the **OpenTelemetry SDK** (`opentelemetry-api` + `opentelemetry-sdk`) as the
  in-process tracing primitive: a `Tracer` creates spans for each agent step / tool call / retrieval;
  nesting + parent/child come from OTel's `contextvars`-based context propagation; span attributes carry
  `llm.tokens.input/output`, `llm.model`, `llm.latency_ms`, status, and redacted I/O. **No OTLP
  collector / Jaeger / Tempo is run** (see OD2).
- **Rationale**: "Async all the way down" (Constitution VII) with `asyncio.gather` in enrichment means
  span parentage must survive `await` and task fan-out — OTel solves context propagation correctly via
  `contextvars`, which a hand-rolled tree would have to re-implement and get subtly wrong. OTel's
  span/attribute/status model maps 1:1 onto "step = span, incident = trace tree" (FR-012/FR-013), and a
  `BatchSpanProcessor` gives off-path export (FR-015) for free.
- **Alternatives**: **Hand-rolled span tree** (full control of the persisted shape, but reinvents async
  context propagation and standard semantics — rejected as avoidable risk on the critical path);
  **Langfuse / Arize-Phoenix** (LLM-tracing SaaS/!services — adds a dependency/service and couples us to
  one vendor's incident model; rejected for scope discipline and "own every line").

## OD2 — Trace store of record: Postgres `trace_spans` (reuse #1; no new service)

- **Decision**: A custom OTel `SpanExporter` writes spans (batched, off-path) into a **`trace_spans`
  table in the existing Postgres** (`pgvector/pgvector:pg16` from #1). That table is the **queryable
  store of record** the dashboard trace inspector (#12) and the eval/temporal gates (#13) read. A
  console/JSON exporter is available in dev for local inspection.
- **Rationale**: The dashboard must render the full trace tree + per-agent tokens/latency and the KPIs
  (memory-hit rate, MTTD) join trace data to incidents — co-locating spans with incidents in Postgres
  makes those reads simple SQL and adds **no new backing service** (scope discipline; the brief avoids
  unnecessary services). Survives the "export destination down" edge case because writes are batched
  off-path and a failed flush is dropped-with-counter, never an incident failure (SC-006).
- **Alternatives**: **Jaeger/Tempo + OTLP collector** (the "real" backend, but two new services and a
  query API the dashboard would have to integrate — rejected: scope creep for a single-host demo);
  **MinIO JSON blobs per trace** (cheap to write but not queryable for KPI aggregation — rejected).

## OD3 — Correlation id: one incident id bound in `contextvars`, shared by logs and spans

- **Decision**: A single per-incident correlation id (the incident id; a request id for synchronous
  API calls) is bound into **`structlog.contextvars`** (already merged by the configured chain) **and**
  set on the OTel trace context, so every log line and every span for one incident carry the same id.
  The worker binds it when it dequeues an incident; routers bind a request id at entry. Propagation
  across `await`/`asyncio.gather` relies on `contextvars` (explicit context copy for spawned tasks).
- **Rationale**: One id is what stitches the multi-agent, async pipeline into a single story
  (FR-009, SC-002) and what lets the trace tree be rooted per incident. `structlog`'s
  `merge_contextvars` processor is **already** in the #1 logging chain — we reuse it rather than invent
  a new mechanism.
- **Alternatives**: Thread an explicit `ctx` argument through every call (noisy, easy to forget, fails
  the "no bypass" intent); use OTel's `trace_id` as the only id (works for spans but not for log lines
  emitted outside a span — rejected in favour of one explicit id bound in both).

## OD4 — Redaction engine: Presidio (PII) + a deterministic secret scrubber, behind the #1 `Redactor`

- **Decision**: Implement the reserved `Redactor` Protocol with **two composed strategies**: (1) a
  **deterministic secret scrubber** — an explicit regex pattern set (AWS-style keys, bearer/JWT tokens,
  PEM private-key blocks, `password=`/`token=`/`apikey=` key-values) **plus** a Shannon-entropy
  heuristic for high-entropy tokens the patterns miss (FR-005) — always on, deterministic; (2)
  **Presidio Analyzer + Anonymizer** for PII entities (email, IP, credit card, IBAN, phone, person)
  using the lightweight **`en_core_web_sm`** spaCy model. The engines are built **once** as a lifespan
  singleton (OD7).
- **Rationale**: Wazuh/packet payloads carry **both** PII and credentials; Presidio is the
  brief-mandated PII engine and ships recognizers that handle structured PII deterministically
  regardless of the NER model, while the deterministic scrubber covers credentials Presidio does not
  (FR-001). `en_core_web_sm` keeps the model small/fast for the hot path; recognizer-based PII (IP,
  email, cards) does not depend on NER size.
- **Alternatives**: **Regex-only** (misses contextual PII like person names — weaker than mandated);
  **LLM-based redaction** (cost, latency, and nondeterminism on the hot path — rejected, violates
  Constitution IV); **`en_core_web_lg`** (more accurate NER but larger image / slower load — noted as a
  later accuracy upgrade, not v1 default).

## OD5 — Redaction scope policy (encodes the FR-006a/b decision)

- **Decision**: A centralized, declarative `RedactionPolicy` maps **sensitive class × boundary** to an
  action: **CREDENTIAL → redact at every boundary AND before any operational/memory write** (scrubbed
  everywhere); **PII → redact at output boundaries** (log, trace, prompt, snapshot, dashboard) only;
  **OPERATIONAL_IDENTIFIER** (IP/host/user) → **retained raw** in the operational object and memory
  store, **redacted whenever crossing an output boundary**. No call site decides policy locally.
- **Rationale**: Directly implements the spec's resolved fork (FR-006a/b) and the seam to #6/#9: the
  memory store never holds a raw credential, but enrichment can still correlate on raw identifiers
  internally. Centralizing the matrix keeps "what is sensitive where" auditable and testable (FR-006).
- **Alternatives**: Scrub everything everywhere incl. identifiers (breaks enrichment correlation —
  rejected per the spec decision); per-call-site redaction choices (un-auditable, drift-prone —
  rejected by FR-006).

## OD6 — Fail-closed enforcement at the boundary

- **Decision**: Redaction is invoked **inside** each emission path — a structlog **processor** (OD8), a
  span-attribute setter, the snapshot writer, and the prompt assembler — and each path is wrapped so
  that **if the redactor raises, the content is withheld** (dropped or replaced with a safe
  `"[REDACTION-FAILED]"` placeholder), never emitted raw (FR-003, SC-006).
- **Rationale**: A SOAR leaking a credential because redaction errored is worse than losing a log line;
  fail-closed makes the safety guarantee structural, not best-effort.
- **Alternatives**: Fail-open / emit-then-warn (unacceptable security posture — rejected).

## OD7 — Singletons & off-path export (the no-latency NFR)

- **Decision**: Register an **`ObservabilityProvider`** (or `RedactorProvider` + tracer setup) in the
  **existing #1 provider seam** so the Presidio engine and the OTel `TracerProvider` are built **once**
  on startup and disposed (force-flush spans) on shutdown. Span export uses a **`BatchSpanProcessor`**
  whose exporter writes to Postgres on a background task; the per-incident telemetry/eval persistence is
  likewise off-path. **Log emission** (JSON → stdout) stays on-path (cheap, 12-factor).
- **Rationale**: Loading the spaCy model per call would dominate latency; a singleton makes redaction
  cheap (Constitution VII). Batched off-path export is exactly the brief's "span export and eval logging
  are asynchronous / off the synchronous incident path" and underpins SC-005/SC-006/SC-008.
- **Alternatives**: Build the redactor per request (catastrophic latency — rejected); `SimpleSpanProcessor`
  / synchronous per-span DB write (taxes the hot path and couples incident success to export health —
  rejected).

## OD8 — Where redaction meets logging: a structlog processor (no bypass)

- **Decision**: Insert a **redaction processor** into the existing `configure_logging` chain (before
  `JSONRenderer`) that runs the secret scrubber (and PII redaction, since logs are an output boundary)
  over event values. Because it is in the shared chain, **every** `get_logger(...)` call is redacted —
  there is no raw logging path (FR-010, FR-018).
- **Rationale**: Putting redaction in the processor chain — not at call sites — is what makes
  "no logging path bypasses redaction" structural. The #1 chain already uses `merge_contextvars`; we
  add the redaction processor alongside it.
- **Alternatives**: Redact at each `log.info(...)` call (forgettable, fails FR-010 — rejected); a custom
  log handler outside structlog (re-implements the chain — rejected).

## OD9 — Token / model / latency accounting (seam with `SPEC-llm-provider` #3)

- **Decision**: The model-call span records `tokens-in`, `tokens-out`, `model`, and `latency`. Token
  counts come from the **usage object the #3 LLM adapter returns**; latency is the span duration. When
  the provider omits usage, the span marks the counts **`unknown`** rather than estimating (FR-013,
  SC-004). This binds a small seam: the #3 adapter MUST surface `usage` (input/output tokens + model id)
  on its result; the observability layer reads and records it.
- **Rationale**: Authoritative counts come from the provider, not a tokenizer guess; recording
  `unknown` keeps the data honest. Naming the seam now prevents a gap when #3 lands.
- **Alternatives**: Client-side token estimation via a tokenizer (drifts from provider billing; wrong
  for some providers — rejected as the source of truth, acceptable only as a future fallback).

## OD10 — Instrumentation ergonomics for downstream specs

- **Decision**: Expose thin helpers from `infra/observability.py` — an `async with span(name, **attrs)`
  context manager and a `record_llm_usage(span, usage)` helper — plus the `Depends()` providers
  (`get_obs`, `get_redactor`, `get_tracer`). Downstream specs (#7 supervisor, #8/#9/#10 agents,
  #4 ingestion) wrap each step with the helper; they never touch OTel or Presidio directly.
- **Rationale**: One ergonomic seam keeps the "everything → observability, no bypass" contract usable
  (FR-018) and means the agents' code reads uniformly. Auto-instrumentation is deliberately **not** used
  — we want the explicit incident=trace tree, not HTTP-framework spans.
- **Alternatives**: Let each agent import OTel/Presidio directly (leaks the implementation, invites
  bypass — rejected); OTel auto-instrumentation (produces transport-level spans, not the incident model
  the dashboard needs — rejected).

---

## Resolved unknowns

All Technical-Context items are concrete; **no `NEEDS CLARIFICATION` remain**. The one open numeric
default — **≤ 5% p95 synchronous overhead** (SC-005) — is inherited from the spec and treated as a
budget to verify in the e2e/overhead tier and re-verify at the day-8 freeze (SC-008), not a blocker.

## Carry-forward to later specs (seam reminders)

- **`SPEC-llm-provider` (#3)** — the LLM adapter MUST return a `usage` (input/output tokens + model id)
  on its call result so the model-call span can record OD9; observability stays provider-agnostic.
- **`SPEC-ingestion` (#4)** — the worker binds the incident correlation id (OD3) on dequeue; the alert
  intake path redacts on ingest reusing this `Redactor`.
- **`SPEC-memory` (#6)** — honors OD5: scrub credentials before any episode write; retain raw
  operational identifiers for correlation; redact them only at output.
- **`SPEC-incident-state-machine` (#7)** / **agents (#8/#9/#10)** — wrap each step with the OD10 span
  helper; the supervisor's step/token cap reads the per-span token telemetry recorded here.
- **`SPEC-dashboard` (#12)** — reads the `trace_spans` store (OD2) for the trace inspector + per-agent
  telemetry; renders only redacted values.
- **`SPEC-eval` (#13)** — extends `eval_thresholds.yaml` with the full redaction/red-team/temporal
  gates; this spec seeds the `redaction` gate so CI enforces it from day one.
