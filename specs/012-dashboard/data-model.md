# Phase 1 — Data Model: React Operations Dashboard (#12)

The dashboard is **read-only except approve/reject** and adds **no migration**. This document defines
the **read DTOs** the API returns (pure Pydantic in `backend/domain/dashboard.py`), the existing tables
they project from, and the read methods added to `IncidentRepository`. No table is created or altered;
the supervisor remains the single writer of incident state.

---

## Source tables (existing — read only)

| Table | Owner | Read by #12 for |
|-------|-------|-----------------|
| `incidents` | #4 schema, #5/#7 writer | queue, detail, KPIs (status/severity/disposition/source/timestamps/evidence) |
| `approval_requests` | #10 | approval panel (pending_actions, rationale, deadline, status) |
| `audit_log` | #10 | audit trail (actor, action, target, outcome, timestamp) |
| `trace_spans` | #2 | trace inspector (span tree, tokens/model/latency/status, redacted attributes) |

Vault (KV v2) holds the admin password hash + JWT signing secret (no DB). **No new table, no
migration** (RD7).

---

## Read DTOs (`backend/domain/dashboard.py` — pure, `extra="forbid"` on inputs)

### IncidentSummary (queue row)
Projection of one `incidents` row for the queue.
- `id: UUID`, `status: IncidentStatus`, `severity: Severity`, `disposition: str | None`
- `source: str`, `summary: str | None` (grounded summary, redacted), `created_at`, `updated_at`
- Derived `is_awaiting_approval: bool` (status == `awaiting_approval`) for badge/quick-filter.

### QueuePage (paginated queue response)
- `items: list[IncidentSummary]`
- `total: int` (matching the active filter), `limit: int`, `offset: int`
- `view: "active" | "resolved" | "all"`, `applied_filters: dict` (echoed status/severity/sort).

### IncidentDetailView
- All `IncidentSummary` fields **plus** `evidence: dict | None` (redacted grounded evidence),
  `normalized_event: dict | None`, `correlation_id: str`.
- `pending_approval: ApprovalView | None` (present iff parked) and `audit: list[AuditView]`.

### ApprovalView (projection of `approval_requests`, reusing #10 shapes)
- `id: int`, `incident_id: UUID`, `plan_id: str`
- `pending_actions: list[dict]` (each `{type, target, risk}` — already redacted)
- `rationale: str`, `status: ApprovalStatus`, `deadline_at: datetime | None`, `created_at`
- Derived `is_actionable: bool` (status == `pending` and not past `deadline_at`) — gates the UI's
  approve/reject buttons (server still enforces via 409).

> The approval **list** + **decision** are served by #10's existing `/approvals` endpoints; `ApprovalView`
> mirrors that response so the detail view can embed the parked approval without a second contract.

### AuditView (projection of `audit_log` `AuditRow`)
- `actor: str`, `action: str`, `target: str | None`, `outcome: str`, `created_at: datetime`.

### TraceTreeView / SpanView (projection of `TraceTree`/`Span` from `domain/telemetry.py`)
- `SpanView`: `span_id`, `parent_span_id: str | None`, `name`, `kind: SpanKind`,
  `status: SpanStatus`, `started_at`, `ended_at: datetime | None`, `latency_ms: int | None`,
  `llm_model: str | None`, `tokens_in: int | None`, `tokens_out: int | None`,
  `attributes: dict` (pre-redacted evidence/rationale), `error_message: str | None`.
- `TraceTreeView`: `correlation_id`, `root: SpanView`, `children: dict[str, list[SpanView]]`,
  plus a `telemetry: TelemetryView` rollup (`total_tokens_in/out: int | None`, `end_to_end_ms`,
  `step_count`, `error_steps`) from `TelemetryRecord.from_trace_tree`.
- **Null token usage is preserved as `null`** and rendered "unknown" in the UI (FR-015) — never coerced
  to `0`.
- A span with `status == error` carries `error_message`; its recovery is visible as a following
  sibling/child span (FR-016) — the UI marks it, the data already expresses it.

### KpiSnapshot
- `volume_over_time: list[{ bucket: datetime, count: int }]`
- `disposition_split: { auto_resolved: int, escalated: int, awaiting_approval: int, ... }`
- `mean_time_to_disposition_ms: int | None`
- `memory_hit: { enriched: int, hits: int, rate: float | None }` (rate = hits/enriched; `None` if
  `enriched == 0`) — denominator is incidents that reached enrichment (spec clarification).
- `generated_at: datetime`.

### Auth DTOs
- `LoginRequest`: `{ username: str, password: SecretStr }` (`extra="forbid"`).
- `TokenResponse`: `{ access_token: str, token_type: "bearer", expires_in: int, role: str }`.
- `OperatorSession`: `{ subject: str, role: str, expires_at: datetime }` (returned by
  `get_current_operator`; its `subject` is the audit `decided_by` actor).

### Stream event (SSE payload — see `contracts/stream-sse.md`)
- `event: "snapshot" | "delta" | "heartbeat"`, `data: { queue: list[IncidentSummary], kpi_counters:
  {...} }` for snapshot/delta; heartbeat carries only a timestamp.

---

## Repository read methods (added to `IncidentRepository` — read only)

`IncidentRepository` stays the **sole** module touching `incidents`; we add reads, never a writer:

- `list_for_queue(*, view, statuses, severities, sort, limit, offset) -> list[Incident]` — filtered,
  sorted, paginated. `view=active` ⇒ non-terminal statuses; `resolved` ⇒ terminal; `all` ⇒ no status
  bound (filters still apply). Default sort: most-recent activity (`updated_at DESC`).
- `count_for_queue(*, view, statuses, severities) -> int` — total for pagination (`QueuePage.total`).
- KPI aggregate reads (or thin helpers consumed by `services/kpis.py`): volume buckets by `created_at`;
  disposition/status counts; MTTD over terminal rows; enrichment-reached + memory-hit counts from the
  `evidence` JSONB. (Cross-table/derived math lives in `services/kpis.py`; raw counts come from the
  repo.)

Audit + trace already expose what's needed: `AuditRepository.list_for_incident`,
`TraceRepository.get_trace_tree(correlation_id)`.

---

## Validation & state rules

- **Auth gate**: every DTO above is returned **only** to an authenticated operator
  (`get_current_operator`); unauthenticated/expired → `401`, no body (FR-001, SC-007).
- **Approve/reject** is the lone state transition the dashboard triggers, and only via #10's
  `/approvals/{id}/decision`: `pending → approved` resumes the response stage (`→ remediated`);
  `pending → rejected` → `rejected_by_human`. A non-`pending` (already-decided/expired by the sweeper)
  approval → `409`, surfaced as "already decided / expired"; **no second remediation** (FR-012, SC-008).
- **Disposition reflection**: after a decision the detail/queue re-reads the incident and shows the new
  disposition within 3s (SC-003) — driven by the decision response + an SSE delta.
- **Redaction invariant**: every DTO field originates from data redacted at write time (#2); the
  dashboard never requests or exposes raw values (FR-017, SC-004) — asserted by test (RD8).
- **Missing telemetry**: `tokens_in/out == null` ⇒ "unknown", not `0` (FR-015).
- **Empty/partial**: no incidents ⇒ empty `QueuePage`/`KpiSnapshot` (clear empty state); an in-flight
  incident with no spans ⇒ `GET /incidents/{id}/trace` returns an empty/partial tree, not an error
  (FR-021, edge cases).

---

## Entity → requirement / story map

| DTO / method | Requirements | Story |
|--------------|--------------|-------|
| `IncidentSummary`, `QueuePage`, `list/count_for_queue` | FR-004, FR-005, FR-006 | US1 (P1) |
| `IncidentDetailView`, `AuditView` | FR-007, FR-008 | US1 (P1) |
| `ApprovalView` + reused `/approvals` | FR-009–FR-013 | US2 (P2) |
| `TraceTreeView`, `SpanView`, `TelemetryView` | FR-014, FR-015, FR-016 | US3 (P2) |
| `KpiSnapshot` | FR-018, FR-019 | US4 (P3) |
| `LoginRequest`, `TokenResponse`, `OperatorSession` | FR-001, FR-002, FR-003 | all stories |
| Stream event | FR-004, FR-023 | US1/US4 |
| (redaction passthrough) | FR-017 | US3 + all |
