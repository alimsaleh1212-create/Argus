# Contract — Episode / Entity / Fact shapes & the redaction-before-write boundary

**Component**: #6 `SPEC-memory` · **Owners**: `backend/domain/memory.py` (shapes),
`backend/services/memory.py` (assembly + redaction)

## What gets written (and what never does)

An `IncidentEpisode` is assembled by `services.memory.record_episode(incident, store, redactor)` from the
incident's **already-grounded evidence slice** — it never touches the raw alert:

| Source on the `Incident` | → Episode field | Redaction applied |
|--------------------------|-----------------|-------------------|
| `evidence.summary` | `summary` | yes (stored-snapshot boundary) |
| `evidence.verdict` | `verdict` | — (controlled vocabulary) |
| `severity` (canonical) | `severity` | — |
| `disposition` | `disposition` | — (controlled vocabulary) |
| `normalized_event.{agent_ip, agent_name, fields[user], fields[*_ip], indicators}` | `entities[]` (`EntityRef`) | yes — each `value` redacted/normalized |
| selected `normalized_event.fields` | `fields` (bounded dict) | yes |
| `updated_at` at disposition | `observed_at` | — |

**Never written:** the raw alert (`raw_alert`), any unredacted PII or secret, anything not in the bounded
slice above. This satisfies FR-005 and the `redaction` eval gate's **`memory_write`** boundary (FR-006a):
the secret-leak count into the memory store MUST be zero.

## Entity extraction (v1, deliberately simple)

`EntityRef`s are pulled from structured `normalized_event` fields by a small pure helper — **not** by an LLM
in our code. (Graphiti may extract *additional* relationships internally during `add_episode`; that is the
framework's concern and operates only on the already-redacted episode body.) v1 entity kinds:

- `address` — `agent.ip`, and any `*_ip` / `srcip` / `dstip` in `fields`
- `host` — `agent.name`
- `user` — `fields[user]` / `fields[dstuser]` / `fields[srcuser]`
- `indicator` — hashes / domains present in `fields` (best-effort, bounded)

Absent fields simply yield no entity of that kind — never an error.

## Temporal facts (what changes over time)

Facts are `(entity, fact_type, value)` with a validity window. v1 recognised `fact_type`s:

- `reputation` of an `address`/`indicator` (e.g. `benign` → `malicious`)
- `role` of a `host` (e.g. `honeypot`, `payroll-server`)
- `disposition` associated with an `entity` across incidents

On a contradicting episode, the prior fact is **invalidated, not deleted** (FR-003): `GraphitiMemory` relies
on Graphiti's native `invalid_at`; `PgVectorMemory` sets `valid_until=now()` and inserts the new row. This is
what powers `query_fact(..., as_of=…)` returning *what was true when* (US2).

## Idempotency

`write_episode` is keyed on `incident_id`. Re-running an episode write (worker retry, reprocess) is a no-op
for the episode and does not re-apply fact transitions (FR-007).
