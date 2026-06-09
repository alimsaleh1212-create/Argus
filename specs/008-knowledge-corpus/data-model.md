# Phase 1 — Data Model: Knowledge Corpus (#5)

Pure types live in `backend/domain/corpus.py` (no outward imports, mirroring `domain/memory.py`). The
temporal `TemporalFact`/`EntityRef`/`FactState` types are **reused from `domain/memory.py`** (#6), not
redefined. All text fields are **already redacted** before construction.

---

## New domain types (`backend/domain/corpus.py`)

### `ReferenceKind` (StrEnum)
`TECHNIQUE` · `RUNBOOK` — the two static reference-doc kinds in v1. (IOC reputation is **not** a reference
kind; it is a `TemporalFact` in the memory store — see CD1/CD2.)

### `ReferenceCorpusEntry`
The unit seeded into Postgres and returned (as a hit) from retrieval.

| Field | Type | Notes |
|-------|------|-------|
| `kind` | `ReferenceKind` | `TECHNIQUE` or `RUNBOOK`. |
| `key` | `str` | Stable natural key, unique within `kind` (e.g. `T1110` for a technique, `rb-brute-force` for a runbook). Idempotency key for seeding. |
| `title` | `str` | Short human title. |
| `content` | `str` | The reference body — mitigations text for a technique, the steps for a runbook. Redacted at the write boundary. |
| `tags` | `list[str]` | Lowercased tags for lexical match — technique ids, tactic names, keywords. Drives ranking. |

`model_config = ConfigDict(extra="forbid", frozen=True)`. Validation: `key` non-empty; `tags` lowercased
and de-duplicated.

### `ReferenceQuery`
What a reader (enrichment, #9) passes to retrieve reference knowledge for an incident.

| Field | Type | Notes |
|-------|------|-------|
| `technique_ids` | `list[str]` | MITRE technique ids present on the incident (exact-keyed match, highest rank). May be empty. |
| `terms` | `list[str]` | Free terms (tactic, keywords from the alert) for lexical/tag match. May be empty. |

### `ReferenceHit`
A ranked retrieval result.

| Field | Type | Notes |
|-------|------|-------|
| `entry` | `ReferenceCorpusEntry` | The matched reference doc. |
| `relevance` | `float` in `[0, 1]` | Validator-bounded (mirrors `MemoryHit.relevance`). |
| `matched_on` | `Literal["technique", "tag", "term"]` | Why it ranked — provenance for the dashboard/enrichment. |

### `IntelVerdict`
The result of an on-demand intel lookup (also the cached value).

| Field | Type | Notes |
|-------|------|-------|
| `indicator` | `str` | The looked-up indicator (redacted form for any persistence). |
| `verdict` | `Literal["benign", "malicious", "suspicious", "unknown"]` | `unknown` on disable/error/timeout (fail-closed). |
| `source` | `str` | Configured source name (provenance). |
| `observed_at` | `datetime` | Lookup time → the `valid_from` of the written fact. |

### `CorpusRetriever` (Protocol)
The read contract consumed by enrichment (#9). See `contracts/corpus-retrieval-contract.md`.

```python
@runtime_checkable
class CorpusRetriever(Protocol):
    async def search_reference(self, query: ReferenceQuery, *, k: int) -> list[ReferenceHit]: ...
```

---

## Reused / extended type (`backend/domain/memory.py`)

### `TemporalFact` (reused as-is)
Already defined for #6: `entity: EntityRef`, `fact_type: str`, `value: str`, `valid_from: datetime`,
`valid_until: datetime | None`. On-demand intel and seed reputation write **reputation** facts:
`fact_type="reputation"`, `value ∈ {benign, malicious, suspicious}`, `entity` = the indicator/address.

### `MemoryStore` Protocol — one added method (CD2)
```python
async def write_fact(self, fact: TemporalFact) -> None: ...
```
- `NullMemory.write_fact` → no-op (degradation preserved).
- `GraphitiMemory.write_fact` → write the fact as a time-bounded edge with `valid_from`; **end the validity
  of (invalidate, not delete) any current fact** of the same `(entity, fact_type)` whose `valid_until` is
  open. Read path unchanged: `query_fact(entity, "reputation", as_of=t)` returns the time-valid `FactState`.

---

## New settings (`backend/infra/config.py`)

### `CorpusSettings` (registered as `corpus`)
| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `enabled` | `bool` | `True` | Master switch for corpus retrieval. |
| `data_dir` | `str` | `backend/data/corpus` | Bundled snapshot location (seed source). |
| `retrieval_k` | `int > 0` | `5` | Default top-k for `search_reference`. |

### `IntelSettings` (registered as `intel`)
| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `enabled` | `bool` | `False` | On-demand intel is **off by default** (optional capability). |
| `source_name` | `str` | `"demo-intel"` | Provenance label. |
| `base_url` | `str` | `""` | External source base URL. |
| `api_key_vault_path` | `str` | `"secret/intel"` | **Optional** Vault path — absent → disabled, **not** fail-boot (CD3). |
| `timeout_s` | `float > 0` | `5.0` | Per-lookup timeout; on expiry → `unknown`. |
| `cache_ttl_s` | `int > 0` | `3600` | Redis TTL for verdicts (negative caching included). |

`model_config = SettingsConfigDict(extra="forbid")` on both. **Unlike** `memory.neo4j_vault_path`, the
intel key path is **not** force-added to `vault.required_paths` — it is optional by design.

---

## Storage — `reference_corpus` table (migration `0006_reference_corpus`)

| Column | Type | Notes |
|--------|------|-------|
| `id` | `bigint` PK | Surrogate. |
| `kind` | `text` not null | `ReferenceKind` value. |
| `key` | `text` not null | Natural key. |
| `title` | `text` not null | |
| `content` | `text` not null | Redacted. |
| `tags` | `text[]` not null default `'{}'` | Lowercased tags. |
| `embedding` | `vector` null | **Reserved, unused in v1** (CD1). |
| `created_at` / `updated_at` | `timestamptz` | |

- **Unique index** on `(kind, key)` → the idempotent-upsert target (`ON CONFLICT (kind, key) DO UPDATE`).
- **GIN index** on `tags` for lexical/tag match.
- No FK to `incidents` — reference knowledge is independent of any incident.

---

## Bundled corpus files (`backend/data/corpus/`)

Small, committed, curated. Shapes are specified in `contracts/corpus-data-schema.md`:
- `techniques.json` — `[{ "id": "T1110", "title": "...", "mitigations": "...", "tactic": "..." }, …]`
- `runbooks.json` — `[{ "key": "rb-brute-force", "title": "...", "steps": "...", "techniques": ["T1110"] }, …]`
- `ioc_reputation.json` — `[{ "indicator": "1.2.3.4", "kind": "address", "reputation": "malicious", "as_of": "2026-01-01T00:00:00Z" }, …]`

The seeder maps `techniques.json`/`runbooks.json` → `ReferenceCorpusEntry` rows and `ioc_reputation.json`
→ `TemporalFact`s (`fact_type="reputation"`) written via `store.write_fact`.
