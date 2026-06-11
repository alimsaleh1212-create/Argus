# Phase 1 — Data Model: Incident Memory (Temporal)

**Component**: #6 `SPEC-memory` (branch `007-incident-memory`) · **Date**: 2026-06-09

Pure types live in **`backend/domain/memory.py`** (no outward imports; may import `Severity` from
`domain/incident.py` — domain→domain is allowed). The `MemoryStore` Protocol is the contract both backends
satisfy (see `contracts/memory-store-contract.md`). All text fields are **already redacted** before they
reach these types (FR-005, Constitution III).

---

## Domain types (`backend/domain/memory.py`)

### `EntityRef`
A reference to a tracked thing. Pulled from `normalized_event` at episode-build time.

| Field | Type | Notes |
|-------|------|-------|
| `kind` | `EntityKind` (StrEnum: `address` / `host` / `user` / `indicator`) | closed vocabulary |
| `value` | `str` | redacted/normalized identifier (e.g. an IP, hostname, username, hash) |

### `IncidentEpisode`
The unit of "what the system has seen" — one processed incident. Written by the worker at disposition.

| Field | Type | Notes |
|-------|------|-------|
| `incident_id` | `uuid.UUID` | **idempotency key** (FR-007) |
| `observed_at` | `datetime` | the episode's time anchor (incident `updated_at` at disposition) |
| `summary` | `str` | redacted one-line/paragraph synthesis (from `evidence.summary`) |
| `verdict` | `str` | detector verdict carried on the incident |
| `severity` | `Severity` | canonical severity (reused from `domain/incident.py`) |
| `disposition` | `str` | final disposition (e.g. `auto_resolved_triage`, `escalated_enrichment`) |
| `entities` | `list[EntityRef]` | extracted refs (src/dst address, host, user, indicators) |
| `fields` | `dict[str, Any]` | a bounded, redacted slice of `normalized_event.fields` |

### `TemporalFact`
A time-bounded fact about an entity. (Materialized by Graphiti edges; modeled explicitly for the fallback.)

| Field | Type | Notes |
|-------|------|-------|
| `entity` | `EntityRef` | subject |
| `fact_type` | `str` | e.g. `reputation`, `role`, `disposition` |
| `value` | `str` | redacted value (e.g. `malicious`, `payroll-server`) |
| `valid_from` | `datetime` | when this fact became valid |
| `valid_until` | `datetime \| None` | `None` = currently valid; set on invalidation (**never deleted**, FR-003) |

### `FactState`
The result of a time-scoped fact query.

| Field | Type | Notes |
|-------|------|-------|
| `fact` | `TemporalFact \| None` | the fact valid at the queried time; `None` if none |
| `is_current` | `bool` | whether the returned fact is the currently-valid one (vs. superseded) |
| `has_superseded` | `bool` | whether prior, now-superseded states exist for this entity/fact_type |

### `MemoryHit`
One ranked prior-incident result from similarity retrieval.

| Field | Type | Notes |
|-------|------|-------|
| `incident_id` | `uuid.UUID` | the prior incident |
| `summary` | `str` | redacted summary |
| `disposition` | `str` | how the prior was dispositioned (the actionable signal) |
| `observed_at` | `datetime` | when it was seen |
| `relevance` | `float` ∈ [0,1] | similarity/ranking score |

### `EpisodeQuery`
The query shape for similarity retrieval (built from a new incident's evidence).

| Field | Type | Notes |
|-------|------|-------|
| `text` | `str` | redacted query text (summary + key fields) |
| `entities` | `list[EntityRef]` | optional entity filters/boosts |

### `MemoryStore` (Protocol)
`write_episode(episode) -> None` · `search_similar(query, *, k) -> list[MemoryHit]` ·
`query_fact(entity, fact_type, *, as_of=None) -> FactState`. Full semantics in
`contracts/memory-store-contract.md`. Implementations: `GraphitiMemory`, `NullMemory` (v1);
`PgVectorMemory` (decided fallback, MD9).

---

## Configuration (`backend/infra/config.py`)

### `MemorySettings` (env `ARGUS__MEMORY__*`, `extra="forbid"`)

| Field | Type / default | Notes |
|-------|----------------|-------|
| `enabled` | `bool = True` | `False` → worker uses `NullMemory` (unit-only CI, no Neo4j) |
| `backend` | `Literal["graphiti","pgvector"] = "graphiti"` | the MD1/MD9 fallback toggle |
| `neo4j_uri` | `str = "bolt://neo4j:7687"` | bolt endpoint |
| `neo4j_vault_path` | `str = "secret/memory"` | Vault KV path for `username`/`password` (required-path, fail-boot) |
| `retrieval_k` | `int = 5` (`gt=0`) | top-k (FR-009) |
| `retrieval_timeout_s` | `float = 5.0` (`gt=0`) | latency budget (SC-006) |
| `embedding_model` | `str = "text-embedding-004"` | Gemini embedder model |

Registered in `_KNOWN_ARGUS_SECTIONS` (`"memory"`) and added as `memory: MemorySettings` on `Settings`.
A `model_validator` appends `neo4j_vault_path` to `vault.required_paths` (fail-boot if unseeded) — mirrors
`_ensure_llm_vault_path_required`.

---

## Graphiti mapping (v1 primary)

- `write_episode(episode)` → `graphiti.add_episode(name=str(incident_id), episode_body=<redacted JSON of
  summary+fields+verdict+severity+disposition+entities>, reference_time=observed_at, source=…)`. Graphiti
  extracts entities/edges and **manages time-validity natively** (sets `invalid_at` on contradicted edges).
  Idempotency: keyed on `incident_id` (skip/no-op if already present).
- `search_similar(query, k)` → Graphiti hybrid search; map results → `MemoryHit` (incident_id from episode
  name, disposition/summary from episode attrs, relevance from score).
- `query_fact(entity, fact_type, as_of)` → read edges for the entity; select the edge whose
  `[valid_at, invalid_at)` window contains `as_of` (or the open one if `as_of` is None) → `FactState`
  (`is_current` = `invalid_at is None`; `has_superseded` = any edge with non-null `invalid_at`).

---

## pgvector fallback schema (`0005_memory_fallback` — built only on spike "no-go", MD9)

```sql
CREATE TABLE incident_episodes (
    incident_id   uuid PRIMARY KEY,              -- idempotency key (FR-007)
    observed_at   timestamptz NOT NULL,
    summary       text NOT NULL,                 -- redacted
    verdict       text NOT NULL,
    severity      text NOT NULL,
    disposition   text NOT NULL,
    fields        jsonb NOT NULL DEFAULT '{}',   -- redacted slice
    embedding     vector(768)                    -- Gemini text-embedding-004 dim
);
CREATE INDEX ix_episodes_embedding ON incident_episodes
    USING ivfflat (embedding vector_cosine_ops);

CREATE TABLE entity_facts (
    id          bigserial PRIMARY KEY,
    entity_kind text NOT NULL,
    entity_val  text NOT NULL,                   -- redacted
    fact_type   text NOT NULL,
    value       text NOT NULL,                   -- redacted
    valid_from  timestamptz NOT NULL,
    valid_until timestamptz                      -- NULL = current; set on invalidation, NEVER deleted
);
CREATE INDEX ix_facts_lookup ON entity_facts (entity_kind, entity_val, fact_type, valid_from);
```

- **similarity** → `ORDER BY embedding <=> :q LIMIT :k` → `MemoryHit`.
- **invalidate** → `UPDATE entity_facts SET valid_until = :now WHERE … AND valid_until IS NULL`, then
  `INSERT` the new fact with `valid_from = :now` (both rows retained — FR-003).
- **time-scoped read** →
  `SELECT … WHERE entity_kind=:k AND entity_val=:v AND fact_type=:t AND valid_from <= :as_of
   AND (valid_until IS NULL OR valid_until > :as_of) ORDER BY valid_from DESC LIMIT 1`.

---

## Redaction boundary (Constitution III / FR-005 / FR-006a)

`services.memory.record_episode` applies the #2 `Redactor` (Boundary = stored-snapshot) to `summary`,
`fields`, and every `EntityRef.value`/`TemporalFact.value` **before** constructing the `IncidentEpisode`, so
no unredacted PII or secret is ever passed to Graphiti's LLM/embedder, persisted to Neo4j/Postgres, or
returned in a `MemoryHit`/`FactState`. The `redaction` eval gate's `memory_write` boundary asserts this.
