# Phase 1 Data Model — Observability & Redaction

**Feature**: `002-observability-redaction` | **Date**: 2026-06-07

Pure types live in `backend/domain/` (no outward deps, Pydantic v2). The persisted trace store is one
new Alembic-managed Postgres table. Nothing here is incident/business logic — these are the shapes the
seam produces and the dashboard/eval consume.

---

## Enumerations

### `SensitiveClass` (`backend/domain/redaction.py`)

| Value | Meaning | Examples |
|-------|---------|----------|
| `CREDENTIAL` | A secret with no legitimate downstream use | API key, bearer/JWT token, password, PEM private key |
| `PII` | Personal data to protect at output | person name, email, credit card, IBAN, phone |
| `OPERATIONAL_IDENTIFIER` | PII-adjacent value the pipeline must correlate on | IP address, hostname, username |

### `Boundary` (`backend/domain/redaction.py`)

| Value | Kind | Notes |
|-------|------|-------|
| `LOG` | output | structured log lines |
| `TRACE` | output | span attributes / recorded I/O |
| `PROMPT` | output | text assembled for a model call |
| `SNAPSHOT` | output | stored incident/report snapshot (MinIO/Postgres) |
| `DASHBOARD` | output | values rendered in the UI |
| `MEMORY_WRITE` | internal | episode/edge written to the memory store (#6) |
| `OPERATIONAL` | internal | the in-memory incident object the agents reason over |

### `SpanStatus` (`backend/domain/telemetry.py`)

`OK` · `ERROR` · `UNSET` — mirrors OTel status; `ERROR` carries a redacted message.

---

## Redaction model

### `RedactionPolicy` (`backend/domain/redaction.py`)

The centralized class×boundary matrix (FR-006/006a/006b). Declarative; no call site overrides it.

| Field | Type | Rule |
|-------|------|------|
| `rules` | `dict[SensitiveClass, set[Boundary]]` | the boundaries at which each class is redacted |
| `default_placeholder` | `str` | e.g. `"[REDACTED:{class}]"` |
| `fail_closed_placeholder` | `str` | e.g. `"[REDACTION-FAILED]"` (OD6) |

**Default policy (encodes the spec decision):**

```
CREDENTIAL              -> { LOG, TRACE, PROMPT, SNAPSHOT, DASHBOARD, MEMORY_WRITE, OPERATIONAL }  # everywhere
PII                     -> { LOG, TRACE, PROMPT, SNAPSHOT, DASHBOARD }                              # output only
OPERATIONAL_IDENTIFIER  -> { LOG, TRACE, PROMPT, SNAPSHOT, DASHBOARD }                              # output only; raw internally
```

**Validation rules**: `CREDENTIAL` MUST include every `Boundary` (invariant test); the two internal
boundaries (`MEMORY_WRITE`, `OPERATIONAL`) MUST NOT appear for `PII`/`OPERATIONAL_IDENTIFIER`.

### `Redactor` (interface already in `backend/infra/redaction.py`, Protocol)

```python
class Redactor(Protocol):
    def redact_text(self, text: str, boundary: Boundary) -> str: ...
    def redact_mapping(self, data: dict, boundary: Boundary) -> dict: ...
```

> Note: the #1 stub Protocol has `redact_text(text)`/`redact_mapping(data)`. This spec **extends** the
> signatures with a `boundary` argument so one redactor enforces the class×boundary policy. Behaviour:
> idempotent (re-redacting a placeholder is a no-op, FR-004), recursive over nested mappings/lists
> (any depth, FR-004), never mutates input (returns a copy), fail-closed (FR-003).

**Detection findings** (internal, not persisted): `(class, span_start, span_end, detector)` where
`detector ∈ {presidio, pattern, entropy}` — used by tests to assert *why* a value was caught.

---

## Telemetry model

### `Span` (`backend/domain/telemetry.py`) ↔ persisted `trace_spans`

| Field | Type | Notes |
|-------|------|-------|
| `span_id` | `str` (uuid/otel id) | unique |
| `trace_id` | `str` | == the incident correlation id (OD3); roots the tree |
| `parent_span_id` | `str \| None` | `None` for the incident root span |
| `correlation_id` | `str` | the incident/request id (indexed) |
| `name` | `str` | e.g. `triage.step`, `tool.virustotal_lookup`, `retrieval.memory` |
| `kind` | `str` | `agent_step` \| `tool_call` \| `retrieval` \| `llm_call` \| `root` |
| `status` | `SpanStatus` | `ERROR` carries `error_message` (redacted) |
| `started_at` / `ended_at` | `datetime` | UTC; `latency_ms` derived |
| `attributes` | `dict` | **redacted** inputs/outputs/evidence (TRACE boundary), bounded by truncation |
| `llm_model` | `str \| None` | model id for `llm_call` |
| `tokens_in` / `tokens_out` | `int \| None` | `None` ⇒ rendered as `unknown` (FR-013) |

### `trace_spans` table (Alembic migration)

```
trace_spans(
  span_id        text primary key,
  trace_id       text not null,
  parent_span_id text null,
  correlation_id text not null,
  name           text not null,
  kind           text not null,
  status         text not null,
  started_at     timestamptz not null,
  ended_at       timestamptz null,
  latency_ms     integer null,
  llm_model      text null,
  tokens_in      integer null,
  tokens_out     integer null,
  attributes     jsonb not null default '{}'::jsonb,   -- redacted
  error_message  text null                              -- redacted
)
-- indexes: (correlation_id), (trace_id), (trace_id, parent_span_id)
```

Reversible (down-migration drops the table) — honors #1's SC-006 (migrations up/down clean).

### `TraceTree` (derived, not stored)

`root: Span` + children resolved by `parent_span_id`. Built on read from `trace_spans` filtered by
`trace_id`. **Invariant**: exactly one root per incident; every non-root span's `parent_span_id`
resolves within the same `trace_id` (no orphans — SC-003).

### `TelemetryRecord` (derived)

Per-incident aggregate the dashboard KPIs read: `total_tokens_in`, `total_tokens_out`,
`end_to_end_latency_ms`, `step_count`, `error_steps` — all derivable from the incident's spans (FR-016).

### `LogContext` (`backend/domain/telemetry.py`)

The fields bound into `structlog.contextvars` for every line: `correlation_id` (or explicit
`correlation_id="-"` / `no_incident=true` when outside an incident — FR-011), `component`, `trace_id`.

---

## State / lifecycle

- **Span lifecycle**: `open` (on `span()` enter) → attributes set (redacted) → `close` (status +
  `ended_at`) → **queued** to the `BatchSpanProcessor` → **exported** to `trace_spans` off-path.
  Export failure ⇒ batch dropped with a counter; the incident is unaffected (SC-006).
- **Redaction**: stateless per call; engines (Presidio, scrubber) are process singletons (OD7).
- No approval/HITL state here (Constitution V N/A for this component).

## Settings additions (`backend/infra/config.py`, `extra="forbid"`)

A new `ObservabilitySettings` section (registered on `Settings`, keeping the `_KNOWN_ARGUS_SECTIONS`
set in sync):

| Field | Default | Purpose |
|-------|---------|---------|
| `presidio_enabled` | `True` | toggle PII NER off for deterministic paths (Constitution IV) |
| `spacy_model` | `"en_core_web_sm"` | PII NER model |
| `entropy_threshold` | `4.0` | secret-scrubber entropy cutoff (bits/char) |
| `span_attr_max_bytes` | `8192` | truncation bound (FR-017) |
| `export_batch_size` / `export_interval_ms` | `512` / `2000` | `BatchSpanProcessor` tuning |
| `trace_to_stdout` | `False` | dev console exporter alongside the Postgres exporter |
