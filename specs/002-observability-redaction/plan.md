# Implementation Plan: Observability & Redaction (Cross-Cutting Foundation)

**Branch**: `002-observability-redaction` | **Date**: 2026-06-07 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/002-observability-redaction/spec.md`

## Summary

Fill the redaction / logging / tracing seams that the platform foundation (#1) reserved as stubs,
delivering **one cross-cutting observability seam** every later component is wired through. Three
intertwined capabilities ship together: (1) **redaction** — a single `Redactor` (Microsoft Presidio
for PII + a deterministic regex/entropy secret scrubber) behind the existing `Redactor` Protocol,
**fail-closed**, applied at every exit boundary, with credentials scrubbed *everywhere* (incl. the
operational object and memory writes) and PII redacted at output boundaries while raw operational
identifiers stay internal for correlation (the FR-006a/b decision); (2) **structured, correlated
logging** — a redaction processor in the structlog chain plus a per-incident correlation id bound in
`contextvars` so every line is structured, scrubbed, and stitchable to one incident; (3) **tracing**
— an OpenTelemetry instrumentation layer where each agent step / tool call / retrieval is a span and
an incident is a trace tree, spans carrying tokens-in/out, model, latency, and redacted I/O, exported
**off the synchronous path** into Postgres as the queryable store the dashboard (#12) and eval (#13)
read. The component is "done" when unit + integration + e2e are green, the **redaction eval gate** is
wired into `eval_thresholds.yaml`, and the synchronous overhead is measured within budget. It adds
**no incident/business logic** and **no new external service** — Postgres and MinIO from #1 are reused.

## Technical Context

**Language/Version**: Python 3.12 (pinned `>=3.12,<3.13`); managed with `uv`.

**Primary Dependencies**: `structlog` (already present — the logging seam established in #1);
**OpenTelemetry** `opentelemetry-sdk` + `opentelemetry-api` (span model, async context propagation,
batched off-path export); **Microsoft Presidio** `presidio-analyzer` + `presidio-anonymizer` (PII) with
a `spaCy` `en_core_web_sm` model; reuse of FastAPI `Depends()` DI, SQLAlchemy 2.0 async + Alembic (the
Postgres trace store), and Pydantic v2 (span/log/policy models). The deterministic secret scrubber is
hand-rolled (regex pattern set + Shannon-entropy heuristic) — no extra dependency.

**Storage**: **PostgreSQL** (the #1 `pgvector/pgvector:pg16` instance) gains a `trace_spans` table (via
a new Alembic migration) as the **queryable trace store of record** the dashboard and eval read; **MinIO**
(`incident-snapshots`, `eval-reports` buckets from #1) receives only **redacted** stored snapshots.
**No new backing service** (no Jaeger/Tempo/OTLP collector) — scope discipline.

**Testing**: `pytest` + `pytest-asyncio` (`asyncio_mode=auto`). **Unit** = the redactor over seeded
secrets/PII (idempotency, nested traversal, fail-closed, entropy catch), the structlog redaction
processor, span-attribute setting with the LLM mocked, correlation-id binding. **Integration** = real
Presidio in-process + a real Postgres (testcontainers) for the trace store; correlation id consistent
across a simulated worker→agent chain; export-down resilience. **e2e** = a synthetic incident driven
through the seam produces one trace tree with no orphans and zero seeded-secret leaks at every boundary.

**Target Platform**: Linux containers under Docker Compose v2 on a single host (dev/CI). The
`deploy/api/Dockerfile` gains a `spacy download en_core_web_sm` build step; no compose service is added.

**Project Type**: Cross-cutting infrastructure layer inside the existing modular-monolith `backend/`
package — it *fills* reserved `infra/` seams and adds a thin tracing module, a trace repository, and
observability domain types. The React dashboard (#12) only **consumes** the shapes defined here.

**Performance Goals**: Synchronous observability overhead **≤ 5% (p95)** of per-incident disposition
time (SC-005); the Presidio engine + tracer are **lifespan singletons** (model loaded once, never
per-call); span/trace export is **batched and off the synchronous path** (`BatchSpanProcessor`); log
emission is JSON-to-stdout (cheap, on-path). Re-verified at the Tier-1 freeze (SC-008).

**Constraints**: Redaction **fails closed** (never emit raw on redactor error); **no bypass path** —
every log/span/prompt/snapshot/dashboard exit routes through the seam (FR-018); credentials scrubbed
everywhere incl. memory (FR-006a) while operational identifiers stay raw internally for correlation
(FR-006b); async all the way down; one typed settings section (`extra="forbid"`); 100% trace capture
(no sampling) for v1.

**Scale/Scope**: Demo-scale single-SOC workload; replayed alert volume; the trace store only needs to
hold per-incident trace trees the dashboard/eval query, and survive export-destination faults without
failing an incident.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design — still passing.*

Derived from `.specify/memory/constitution.md` (v1.0.0).

- [x] **I. Spec-Driven Delivery**: `spec.md` precedes code; "done" = unit + integration + e2e green and
      pushed. Internal milestones keep PRs ≤ ~400 lines: **(a)** redactor (Presidio + secret scrubber) +
      policy + fail-closed + unit/integration tests + seeded `redaction` eval gate; **(b)** structlog
      redaction processor + correlation-id propagation across worker/agents; **(c)** OTel tracing +
      span/trace-store (Alembic `trace_spans`) + per-step telemetry + off-path batched export; **(d)**
      the unified observability seam (provider + `Depends()` wiring) + e2e + overhead measurement.
- [x] **II. Test-First, Three-Tier, Eval-Gated (NON-NEGOTIABLE)**: three tiers planned, green daily,
      **≥80% on new code and higher on the redaction safety boundary**. The **`redaction` eval gate**
      (a seeded fake secret never appears unredacted in any log/trace/memory/dashboard view) is wired
      into `eval_thresholds.yaml` now; the full suite is `SPEC-eval` (#13). Observability is
      provider-agnostic; token accounting works for whichever provider #3 selects (seam recorded).
- [x] **III. Security Boundaries Are Structural, Not Prompted**: this component **delivers** the
      redaction clause of Principle III — "a redaction layer MUST run before anything leaves the
      service: every log line, trace span, memory write, and dashboard view; a redaction eval proves no
      secret ever appears unredacted." Fail-closed + no-bypass make it structural, not advisory. (Triage
      tool-gating itself is #8, enforced via the DI seam #1 established.)
- [x] **IV. Determinism First**: the hot-path scrubber is **deterministic** (regex + entropy, always
      on); Presidio NER may be disabled on deterministic paths (pattern-only) per the #1 seam docstring;
      observability makes **no LLM call**, so it introduces no nondeterminism.
- [x] **V. Human-in-the-Loop**: N/A — no remediation actions in this component.
- [x] **VI. Temporal Memory & Graceful Degradation**: this spec **fixes the memory-write redaction
      policy** consumed by #6/#9 — credentials are never persisted to the memory store (FR-006a); raw
      operational identifiers are retained so enrichment can correlate (FR-006b). Export-destination
      failure degrades gracefully (best-effort) and never fails an incident.
- [x] **VII. Production Engineering Standards**: this is the component that **realizes** "structured
      logging carries a trace ID on every line" and "observability MUST add negligible latency — span
      export and eval logging run off the synchronous incident path." Async off-path export; redactor +
      tracer as lifespan singletons via the provider seam; Pydantic span/log/policy models; a typed
      `observability` settings section (`extra="forbid"`); `uv`-pinned deps.
- [x] **Scope & Tiers**: strictly v1 / T1; **no new external service** (Postgres/MinIO reused; no
      Jaeger/Tempo/collector); no ML detector / multi-tenancy / widget / live capture / LLM supervisor /
      4th agent; respects the layering contract (a T1 cross-cutting concern every later T1 spec needs).

**Result: PASS — no violations.** Complexity Tracking intentionally empty. The heaviest choice — adopting
OpenTelemetry + Presidio/spaCy rather than hand-rolling — is a *dependency-weight* trade, not a
constitution violation; it is justified in [research.md](./research.md) (correct async context
propagation; Presidio is the brief-mandated PII engine) and will be carried into `DECISIONS.md`.

## Project Structure

### Documentation (this feature)

```text
specs/002-observability-redaction/
├── plan.md              # This file (/speckit-plan output)
├── research.md          # Phase 0 — decisions & rationale (OD1–OD10)
├── data-model.md        # Phase 1 — redaction policy, span/trace, log, telemetry models + trace_spans table
├── quickstart.md        # Phase 1 — how to verify redaction/logging/tracing + overhead
├── contracts/           # Phase 1 — the outward contracts later specs consume
│   ├── observability-seam.md   # the one seam (logger+tracer+redactor) via DI; no-bypass rule
│   ├── redaction-policy.md      # Redactor interface + class×boundary policy + fail-closed + scope A
│   ├── span-trace-schema.md     # Span/TraceTree shape + trace_spans table + telemetry aggregation
│   └── logging-contract.md      # structured log record shape + correlation id + redaction-in-chain
├── checklists/
│   └── requirements.md  # (already created by /speckit-specify)
└── tasks.md             # Phase 2 — created by /speckit-tasks (NOT here)
```

### Source Code (repository root)

> Fills reserved #1 seams and adds the minimum new files; **no restructuring**. New files marked `+`.

```text
backend/
├── infra/
│   ├── redaction.py          # FILL: Redactor impl (Presidio + secret scrubber), RedactionPolicy, RedactorProvider
│   ├── logging.py            # EDIT: add redaction processor + correlation-id contextvar binding to the chain
│   ├── tracing.py          + # NEW: OTel setup, tracer/span helpers, token/model/latency attrs, Postgres BatchSpanProcessor
│   ├── observability.py    + # NEW: the unified seam bundle (logger + tracer + redactor) + ObservabilityProvider
│   └── container.py / lifespan.py  # (unchanged) register RedactorProvider + tracer via the existing seam
├── dependencies.py           # EDIT: add get_redactor / get_tracer / get_obs Depends() providers
├── domain/
│   ├── redaction.py        + # NEW: SensitiveClass, Boundary enums, RedactionPolicy (pure types)
│   └── telemetry.py        + # NEW: Span, TraceTree, TelemetryRecord, LogContext (pure types)
├── repositories/
│   └── trace_repository.py + # NEW: persist/query spans (the trace store data-access layer)
└── db/migrations/versions/
    └── XXXX_trace_spans.py + # NEW: trace_spans table (+ indexes on correlation_id, parent_span_id)

config/
└── eval_thresholds.yaml      # EDIT: add the `redaction` gate (seeded; enforced by #13's harness)

deploy/api/Dockerfile         # EDIT: `python -m spacy download en_core_web_sm`
pyproject.toml                # EDIT: add otel + presidio + spacy deps; drop redaction.py from coverage omit; add tracing/observability

tests/
├── unit/                     # redactor (secrets/PII/nested/idempotent/fail-closed/entropy), log processor, span attrs (LLM mocked), correlation binding
├── integration/             # real Presidio + Postgres trace store (testcontainers); correlation across worker→agent; export-down resilience
└── e2e/                      # synthetic incident → one trace tree, no orphans, zero seeded-secret leaks at every boundary; overhead measurement
```

**Structure Decision**: Stay inside the established modular-monolith `backend/` package and **fill the
reserved infra seams** rather than restructure. `redaction.py` and `logging.py` already exist as the
#1-reserved stubs; this spec implements them and adds `tracing.py` + `observability.py` (the unified
seam) in `infra/` (the foundation's home), pure observability types in `domain/`, a `trace_repository`
in `repositories/`, and one Alembic migration for the `trace_spans` store. Import direction stays
inward-only and is enforced by the existing `import-linter` contracts. The redactor and tracer are
registered as **lifespan singletons via the existing provider seam** (`container.py`) so the Presidio
model loads once; consumers obtain the seam **only** through `Depends()` (no module globals, no bypass).
Spans export **off the synchronous path** via a `BatchSpanProcessor` into Postgres, the queryable trace
store the dashboard (#12) and eval (#13) consume. No new compose service is introduced.

## Complexity Tracking

> No constitution violations — table intentionally empty.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| —         | —          | —                                   |
