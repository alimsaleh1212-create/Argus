# Contract — Ingest Webhook API

**Owner**: #4 `SPEC-ingestion` · **Consumers**: the upstream Wazuh detector (and #14's rule detector,
which posts the *same* schema) · **Module**: `backend/routers/ingest.py`

The thin front door. Receipt is **validate → redact → dedup → persist → enqueue → `202`**; all heavy work
is the worker's. Mounted on `api_router` via `routers/__init__.py`.

---

## `POST /ingest/wazuh`

Accept one Wazuh-format alert.

**Auth**: `Authorization: Bearer <token>` — constant-time compared against the Vault `secret/ingest`
token resolved at startup (ID8). Missing/invalid ⇒ `401`, no side effects.

**Request headers**: `Content-Type: application/json`.

**Request body**: a `WazuhAlert` (see [data-model.md](../data-model.md)). Unknown fields tolerated.

**Processing order** (the handler delegates to `services/intake.accept()`):
1. **Size guard** — raw body > `ingest.max_alert_bytes` ⇒ `413` (before JSON parse).
2. **Validate** — body → `WazuhAlert`; failure ⇒ `422` with Pydantic error detail. No Incident, no enqueue.
3. **Redact** — redact the alert at the `SNAPSHOT`/`LOG` boundary (#2). Redaction error ⇒ fail closed
   (`500`, nothing persisted/logged/enqueued).
4. **Dedup** — `SET dedup:<fingerprint> <id> NX EX <window>`. Hit ⇒ return the existing incident
   (`deduplicated: true`), no new Incident, no enqueue.
5. **Persist** — insert `Incident(status=received)` (durable).
6. **Enqueue** — `LPUSH queue:incidents <id>`. Enqueue failure (Redis down) ⇒ roll back the insert,
   return `503`, **no orphan Incident**.

### Responses

| Status | When | Body |
|--------|------|------|
| `202 Accepted` | new alert accepted & enqueued | `IngestResult{ incident_id, status: "received", deduplicated: false }` |
| `200 OK` | duplicate within the dedup window | `IngestResult{ incident_id, status: <current>, deduplicated: true }` |
| `401 Unauthorized` | missing/invalid bearer token | error detail (no incident) |
| `413 Payload Too Large` | body exceeds `max_alert_bytes` | error detail (no incident) |
| `422 Unprocessable Entity` | body fails `WazuhAlert` validation | Pydantic error detail (no incident) |
| `503 Service Unavailable` | queue backend unreachable (enqueue failed) | error detail (no orphan incident) |

> **Acknowledgement budget**: `202`/`200` returned in **< 300 ms p95** (SC-001), independent of grounding.
> The response carries the incident id so the caller can correlate; it never blocks on the worker.

### Guarantees (map to FRs / SCs)

- Exactly **one Incident and one enqueued job** per accepted new alert (SC-002); **zero** of each on any
  rejection path (`401/413/422/503`) (SC-004).
- Duplicate within the window ⇒ **exactly one** Incident (SC-003).
- No unredacted secret/PII in the stored Incident, the queue message, or any log/span (SC-005, FR-004/013).
- Correlation id is bound for the request (#2 `bind_incident`) so all lines/spans share it (FR-013).

### Out of scope (seams)

- Roles / multi-user auth — `admin` role and richer authz are #12; this is a single machine-client guard.
- Injection/jailbreak screening of alert text — #11 (the redaction boundary is what #4 owes).
- `GET /incidents/...` read endpoints — #12 (the dashboard read side).
