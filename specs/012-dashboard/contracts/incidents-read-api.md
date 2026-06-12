# Contract — Incidents Read API (`routers/incidents.py`)

Fills the reserved `/incidents` router (docstring: "consumed by the dashboard (#12)"). **All endpoints
read-only and behind `get_current_operator`** (401 if unauthenticated). All values are redacted at write
time (#2) — these endpoints never expose raw secrets/PII and add no de-redaction path (FR-017). Prefix
`/incidents`. Approve/reject is **not** here — it reuses #10's `/approvals` (see `auth-api.md` wiring and
the #10 `approvals-api-contract.md`).

---

## `GET /incidents` — queue

**Query**: `view` (`active` | `resolved` | `all`, default `active`), `status` (repeatable; e.g.
`awaiting_approval`), `severity` (repeatable), `sort` (default `-updated_at`), `limit` (default 50,
max 200), `offset` (default 0).

**200 →** (`QueuePage`)
```json
{
  "items": [
    { "id": "f1e2…", "status": "awaiting_approval", "severity": "high",
      "disposition": null, "source": "wazuh", "summary": "<redacted grounded summary>",
      "is_awaiting_approval": true,
      "created_at": "2026-06-12T09:00:00Z", "updated_at": "2026-06-12T09:04:00Z" }
  ],
  "total": 137, "limit": 50, "offset": 0, "view": "active",
  "applied_filters": { "status": ["awaiting_approval"], "severity": [], "sort": "-updated_at" }
}
```
Backed by `IncidentRepository.list_for_queue(...)` + `count_for_queue(...)`. Default order = most-recent
activity first (FR-004). Empty system → `items: []`, `total: 0` (clear empty state). Must return < 2s for
≥200 incidents (SC-009).

## `GET /incidents/{id}` — detail

**200 →** (`IncidentDetailView`) incident summary fields + `evidence`, `normalized_event`,
`correlation_id`, embedded `pending_approval` (`ApprovalView | null`) and `audit` (list). `404` if
unknown.

## `GET /incidents/{id}/audit` — audit trail

**200 →** `{ "audit": [ { "actor": "admin", "action": "isolate_host", "target": "<redacted>",
"outcome": "applied", "created_at": "…" }, … ] }` via `AuditRepository.list_for_incident` (FR-008).

## `GET /incidents/{id}/trace` — trace inspector

**200 →** (`TraceTreeView`)
```json
{
  "correlation_id": "…",
  "root": { "span_id": "…", "parent_span_id": null, "name": "incident", "kind": "root",
            "status": "ok", "latency_ms": 8421, "llm_model": null,
            "tokens_in": null, "tokens_out": null, "attributes": {}, "error_message": null },
  "children": {
    "<root_id>": [
      { "span_id": "…", "name": "triage", "kind": "agent_step", "status": "ok",
        "llm_model": "gemini-…", "tokens_in": 1200, "tokens_out": 340, "latency_ms": 1100,
        "attributes": { "rationale": "<redacted>", "evidence": "<redacted>" }, "error_message": null }
    ]
  },
  "telemetry": { "total_tokens_in": 1530, "total_tokens_out": 410,
                 "end_to_end_ms": 8421, "step_count": 6, "error_steps": 1 }
}
```
Via `TraceRepository.get_trace_tree(correlation_id)` → serialized. **Null tokens stay `null`** (UI shows
"unknown", FR-015). Error spans carry `error_message` and their recovery shows as a following span
(FR-016). No spans yet (mid-pipeline) → `200` with an empty/partial tree, **not** an error (FR-021).

## `GET /incidents/kpis` — KPI snapshot

**200 →** (`KpiSnapshot`)
```json
{
  "volume_over_time": [ { "bucket": "2026-06-12T00:00:00Z", "count": 24 } ],
  "disposition_split": { "auto_resolved": 61, "escalated": 12, "awaiting_approval": 3 },
  "mean_time_to_disposition_ms": 742000,
  "memory_hit": { "enriched": 40, "hits": 27, "rate": 0.675 },
  "generated_at": "2026-06-12T09:05:00Z"
}
```
Computed by `services/kpis.py` from existing records; reconciles exactly with incident records (FR-019,
SC-006). `memory_hit.rate = hits / enriched`, `null` when `enriched == 0`; denominator = incidents that
reached enrichment (spec clarification).

## `GET /incidents/stream` — live push

Server-Sent Events. Full contract in `stream-sse.md` (FR-004, FR-023).

---

## Errors & resilience

- `401` (no/invalid/expired token) on every endpoint here.
- `404` (unknown incident) on detail/audit/trace.
- `422` (bad query param, e.g. `limit > 200`).
- Backend/DB failure → `503` with a clear body; the SPA shows an error state + retry (FR-021), never a
  blank/corrupted view.

## Wiring & tests

- `routers/incidents.py` depends on `get_current_operator`, `get_incident_repo`, `get_audit_repo`,
  `get_trace_repo`, and `services/kpis.py`.
- `routers/__init__.py` registers `incidents.router` (currently commented out).
- **integration**: each endpoint vs real Postgres — queue filter/sort/paginate, detail/audit/trace
  shapes, KPI reconciliation, 401-without-token, and the **redaction assertion** (seeded secret absent
  from every response). **e2e**: run one incident → it appears in the queue, detail/trace render with
  telemetry; an error-path incident shows the marked error span.
