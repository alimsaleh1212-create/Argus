# Phase 1 — Data Model: Alert Ingestion Pipeline

**Component**: #4 `SPEC-ingestion` · **Date**: 2026-06-08

All types are **pure Pydantic v2** in `backend/domain/incident.py` (no outward imports — domain-isolation
`import-linter` contract), except `WazuhAlert` which is the untrusted inbound shape. The `incidents`
Postgres table (migration `0003`) persists these. This is the **single contract** imported by the
supervisor (#7), the agents (#8–#10), and the dashboard (#12) — defined once here.

---

## Enums

```python
class IncidentStatus(StrEnum):
    RECEIVED  = "received"    # persisted at intake, enqueued, awaiting the worker
    GROUNDING = "grounding"   # claimed by the worker, evidence being assembled
    GROUNDED  = "grounded"    # evidence ready; handed to the downstream pipeline seam (terminal for #4)
    FAILED    = "failed"      # processing exhausted bounded retries (terminal)
    # Extended by later specs: triaging / enriching / awaiting_approval / resolved / escalated …

class Severity(StrEnum):
    LOW = "low"; MEDIUM = "medium"; HIGH = "high"; CRITICAL = "critical"
```

`StrEnum` matches the project convention (`SpanKind`/`SpanStatus`); status is stored as text so later
specs add values with no migration.

**Wazuh `rule.level` → `Severity` band (ID6, deterministic):**

| `rule.level` | Severity |
|--------------|----------|
| 0–3 | `low` |
| 4–7 | `medium` |
| 8–11 | `high` |
| 12–15 | `critical` |
| missing / unparseable | `medium` (+ `evidence.flags += ["severity_defaulted"]`) |

---

## `WazuhAlert` — untrusted inbound payload (request body)

`model_config = ConfigDict(extra="ignore")` — tolerate unknown/extra Wazuh fields (ID6).

| Field | Type | Notes |
|-------|------|-------|
| `id` | `str \| None` | Wazuh alert id if present (`id` or `_id`) |
| `timestamp` | `str \| None` | Wazuh `timestamp` / `@timestamp`; parsed to UTC, else `None` |
| `rule` | `WazuhRule` | nested; required |
| `agent` | `WazuhAgent \| None` | nested; optional |
| `data` | `dict[str, Any]` | event payload; default `{}` |
| `full_log` | `str \| None` | raw log line |

`WazuhRule`: `level: int \| None`, `id: str \| None`, `description: str \| None`, `groups: list[str] = []`.
`WazuhAgent`: `id: str \| None`, `name: str \| None`, `ip: str \| None`.

> The webhook reads the **raw body** first for the size guard (ID10) and the dedup `content_signature`,
> then validates into `WazuhAlert`. Validation failure ⇒ `422` (FastAPI/Pydantic), no Incident, no enqueue.

---

## `NormalizedEvent` — the Wazuh adapter's output (lives in `evidence` / Incident)

The detector-supplied facts, normalized into one shape the rest of the system reads.

| Field | Type | Notes |
|-------|------|-------|
| `rule_id` | `str \| None` | from `rule.id` |
| `rule_level` | `int \| None` | from `rule.level` |
| `rule_description` | `str \| None` | from `rule.description` |
| `rule_groups` | `list[str]` | from `rule.groups` |
| `agent_id` | `str \| None` | from `agent.id` |
| `agent_name` | `str \| None` | from `agent.name` |
| `agent_ip` | `str \| None` | from `agent.ip` (operational identifier — preserved internally, redacted at output) |
| `event_time` | `datetime \| None` | parsed Wazuh timestamp (UTC) |
| `fields` | `dict[str, Any]` | salient `data.*` fields the adapter lifts (e.g. `srcip`, `dstuser`, `process`) |

---

## `Evidence` — the grounded evidence packet (what the triage agent #8 will reason over)

Produced by `grounding.ground()`. At #4 the retrieval slots are empty placeholders that #5/#6 fill later.

| Field | Type | Notes |
|-------|------|-------|
| `verdict` | `str` | the detector's standing verdict (Wazuh fired ⇒ `"rule_match"`) |
| `severity` | `Severity` | from the band table |
| `normalized_event` | `NormalizedEvent` | the structured facts |
| `summary` | `str` | one-line deterministic synthesis (`f"{rule_description} on {agent_name}"`) |
| `retrieved_context` | `list[dict] = []` | **reserved** — filled by corpus #5 / memory #6 |
| `flags` | `list[str] = []` | e.g. `severity_defaulted`, `agent_unknown` |

> `Evidence` deliberately carries **no triage decision** (real/noise, confidence, rationale) — those are
> #8's output fields. #4 only assembles the inputs.

---

## `Incident` — the canonical object (the downstream contract, persisted in `incidents`)

| Field | Type | Column | Notes |
|-------|------|--------|-------|
| `id` | `UUID` | `uuid PK` | generated at intake |
| `status` | `IncidentStatus` | `text, indexed` | source of truth; drives the worker/recovery |
| `severity` | `Severity` | `text` | from grounding (mirrors `evidence.severity` for cheap querying) |
| `correlation_id` | `str` | `text, indexed` | binds logs/spans (#2); == `str(id)` by default |
| `dedup_fingerprint` | `str` | `text, indexed` | SHA-256 of redacted `(rule_id, agent_id, content_signature)` |
| `source` | `str` | `text` | `"wazuh"` (the detector #14 will set its own) |
| `raw_alert` | `dict` | `jsonb` | the **redacted** inbound alert (`SNAPSHOT` boundary) |
| `normalized_event` | `dict \| None` | `jsonb` | set by grounding |
| `evidence` | `dict \| None` | `jsonb` | the `Evidence` packet; set by grounding |
| `attempts` | `int` | `int default 0` | worker retry counter (ID10) |
| `created_at` | `datetime` | `timestamptz default now()` | |
| `updated_at` | `datetime` | `timestamptz` | bumped on every status change |

**Validation rules**
- `raw_alert` MUST be the redacted form — the repository never writes an un-redacted alert (FR-004).
- `correlation_id` is non-empty and stable for the incident's life (bound via #2 `bind_incident`).
- `dedup_fingerprint` is non-empty and computed over redacted content (no secret in a Redis key).

**State transitions** (owned by this component; #7 extends)

```
            intake.accept()                 worker.claim()           grounding ok
   (none) ───────────────► received ─────────────────► grounding ─────────────────► grounded ─(stub handoff)─► [#7]
                              │                             │
                              │ enqueue fails (no commit)   │ exception, attempts ≥ max
                              ▼                             ▼
                          503, no incident               failed
```
Guarded claim (`UPDATE … SET status='grounding' WHERE id=:id AND status='received'`) makes the transition
atomic; grounding is idempotent (`status == 'grounded'` ⇒ skip), so at-least-once re-delivery is safe.

---

## `IngestResult` — the webhook response body

| Field | Type | Notes |
|-------|------|-------|
| `incident_id` | `UUID` | the new or (on dedup) existing incident |
| `status` | `IncidentStatus` | `received` (new) or current status (duplicate) |
| `deduplicated` | `bool` | `true` when this alert collapsed onto an existing incident |

HTTP: `202 Accepted` on accept; `200 OK` is also acceptable for a dedup hit (return the existing id). See
the webhook contract for the full status-code table.

---

## Settings additions (`infra/config.py`)

Two new typed sections (`extra="forbid"`), registered on `Settings`; `_KNOWN_ARGUS_SECTIONS` gains
`"redis"` and `"ingest"`. Env vars: `ARGUS__REDIS__URL`, `ARGUS__INGEST__MAX_ALERT_BYTES`, etc.

```python
class RedisSettings(BaseSettings):           # ARGUS__REDIS__*
    model_config = SettingsConfigDict(extra="forbid")
    url: str = "redis://redis:6379/0"
    queue_key: str = "queue:incidents"
    processing_key: str = "queue:processing"
    dedup_prefix: str = "dedup:"
    dequeue_block_s: float = 5.0             # BLMOVE block timeout

class IngestSettings(BaseSettings):          # ARGUS__INGEST__*
    model_config = SettingsConfigDict(extra="forbid")
    webhook_vault_path: str = "secret/ingest"   # added to vault.required_paths (fail-boot if absent)
    max_alert_bytes: int = 262_144              # 256 KiB pre-parse guard → 413
    dedup_window_s: int = 300                   # SET NX EX TTL
    max_attempts: int = 3                       # worker retry budget → failed
```

A `model_validator` appends `ingest.webhook_vault_path` to `vault.required_paths` (mirroring the existing
`_ensure_llm_vault_path_required`), so a missing webhook secret fails boot.

---

## Persistence — migration `0003_incidents`

`op.create_table("incidents", …)` with the columns above (`raw_alert`/`normalized_event`/`evidence` as
`JSONB`, `server_default "'{}'::jsonb"` where appropriate), plus:
- `ix_incidents_status` on `status` (worker recovery scans non-terminal rows),
- `ix_incidents_dedup_fingerprint` on `dedup_fingerprint`,
- `ix_incidents_correlation_id` on `correlation_id`.

Reversible: `downgrade()` drops the indexes then the table. Follows the `0002_trace_spans` shape exactly.
