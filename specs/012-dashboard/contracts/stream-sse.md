# Contract — Live Stream (`GET /incidents/stream`, Server-Sent Events)

Realizes FR-004 + FR-023 + the spec's live-update clarification: queue and KPI views update by
**server push over a persistent stream**, with graceful reconnect and a fallback to on-demand refresh.
SSE is chosen over WebSocket because the push is one-directional (server → client); the only
client→server action, approve/reject, has its own REST endpoint (RD3). The event source is an **API-side
periodic snapshot poll** — the dashboard touches neither the worker nor the supervisor (RD4).

---

## Endpoint

`GET /incidents/stream` → `text/event-stream` (FastAPI `StreamingResponse`).

**Auth**: native `EventSource` cannot set headers, so the session token is passed as a query param:
`GET /incidents/stream?token=<jwt>`. Validated by the **same** `get_current_operator` logic; invalid /
expired → `401` (the SPA then re-authenticates). Served same-origin via the nginx reverse proxy (RD10),
so no CORS/preflight.

## Events

```
event: snapshot
data: { "queue": [ <IncidentSummary>, … ], "kpi_counters": { "active": 12, "awaiting_approval": 3,
                    "auto_resolved": 61, "escalated": 12 } }

event: delta
data: { "queue": [ <IncidentSummary changed since last tick> ], "kpi_counters": { … } }

event: heartbeat
data: { "ts": "2026-06-12T09:05:00Z" }
```

- On (re)connect the server first emits a **`snapshot`** (full current queue + counters) so the client
  **reconciles without loss or duplication** (FR-023). Subsequent ticks emit `delta` (rows whose
  `updated_at` advanced) — or `snapshot` again if delta tracking is unavailable; both are correct, delta
  is the optimization.
- A periodic `heartbeat` keeps the connection alive through proxies and lets the client detect silence.
- Producer cadence: `dashboard.stream_poll_seconds` (default 2.0s) — meets SC-010 (change visible ≤5s).

## Client behavior (TanStack Query + EventSource)

- Each `snapshot`/`delta` patches/invalidates the queue + KPI query caches → UI updates with no manual
  refresh (SC-010).
- On stream error/drop: show a **"reconnecting"** indicator; native `EventSource` auto-reconnects; while
  disconnected, fall back to **on-demand TanStack Query refetch** so the operator is never stuck on
  stale data (FR-023, edge case "live stream drops").
- On reconnect: the server's first `snapshot` reconciles any missed changes.

## Resilience & safety

- Read-only: the stream emits queue/KPI projections only — it executes nothing and mutates nothing.
- All emitted fields are redacted at write time (#2); the stream re-uses `IncidentSummary` (FR-017).
- Producer failure (DB blip) drops a tick and emits a heartbeat; it never crashes the stream or an
  incident (graceful degradation, Constitution VII).
- **Documented scale-up (not built in v1, RD4)**: replace the API-side poll with **Redis pub/sub**
  (supervisor/worker publish `incident_changed`) for event-driven push at higher scale — noted in
  DECISIONS.md; v1 takes the simplest sufficient path.

## Tests

- **unit**: snapshot/delta/heartbeat event serialization shape; token-as-query-param validated by the
  shared auth path; `401` on bad token.
- **integration**: connect with a valid token → receive an initial `snapshot`; create/advance an
  incident → a subsequent tick reflects it within the poll interval.
- **frontend**: simulate a drop → "reconnecting" indicator + refetch fallback; reconnect → snapshot
  reconciles the queue (no dupes).
