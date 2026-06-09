# Contract — `MemoryStore` Protocol

**Component**: #6 `SPEC-memory` · **Owner of this contract**: `backend/domain/memory.py`

The single interface every memory backend satisfies and every consumer depends on. Defined as a
`typing.Protocol` so `GraphitiMemory`, `NullMemory`, and (decided fallback) `PgVectorMemory` are
interchangeable by config toggle (`MemorySettings.backend`). Consumers: the worker writer (this spec),
enrichment #9 (reader, later), the dashboard #12 (reader, later), the eval (#13 seed here).

All `str` inputs are **already redacted** by the caller (`services.memory.record_episode`) — the store never
re-redacts and never persists raw content (FR-005, Constitution III).

```python
class MemoryStore(Protocol):
    async def write_episode(self, episode: IncidentEpisode) -> None: ...
    async def search_similar(self, query: EpisodeQuery, *, k: int) -> list[MemoryHit]: ...
    async def query_fact(
        self, entity: EntityRef, fact_type: str, *, as_of: datetime | None = None
    ) -> FactState: ...
```

## `write_episode(episode)`

- **Records** one incident as a time-stamped episode (entities, verdict/severity, disposition, `observed_at`).
- **Idempotent on `episode.incident_id`** (FR-007): a repeat write (worker retry, reprocess) MUST NOT create
  a duplicate episode or double-count facts.
- **Temporal validity** (FR-003): if the episode contradicts a prior fact about an entity, the prior fact's
  validity is **ended** and the new fact recorded — the prior is **never deleted/overwritten**. (`GraphitiMemory`
  delegates to Graphiti's native invalidation; `PgVectorMemory` does the `UPDATE valid_until` + `INSERT`.)
- **Failure**: MUST NOT raise into the caller's disposition flow. The caller wraps it best-effort; a backend
  error is logged and swallowed (FR-006). `NullMemory.write_episode` is a no-op.

## `search_similar(query, *, k) -> list[MemoryHit]`

- Returns up to `k` prior incidents most similar to `query`, each with `disposition`, `observed_at`, and a
  `relevance` ∈ [0,1], **ordered by relevance desc** (FR-002, FR-009).
- **Empty store / read miss / outage → `[]`**, never an error (FR-006). `NullMemory` always returns `[]`.
- Bounded by `MemorySettings.retrieval_timeout_s`; a timeout returns `[]` (logged), not an exception.

## `query_fact(entity, fact_type, *, as_of=None) -> FactState`

- `as_of=None` → the **currently-valid** fact (`is_current=True` when found).
- `as_of=<time>` → the fact whose validity window `[valid_from, valid_until)` contains that time — the
  **time-valid** state, distinguished from "most recent" or "most similar" (FR-004, US2).
- `has_superseded=True` when prior, now-invalidated states exist for this `(entity, fact_type)`.
- No matching fact → `FactState(fact=None, is_current=False, has_superseded=…)`. Outage → empty `FactState`
  (logged), never an error. `NullMemory` always returns the empty `FactState`.

## Backend selection & lifecycle

- Built once by `MemoryProvider` (lifespan singleton) and exposed as `container.memory`.
- `MemorySettings.enabled=False` **or** a startup connection failure → `NullMemory` (graceful degradation,
  Constitution VI / FR-006); the worker keeps running.
- `MemorySettings.backend` selects `graphiti` (v1) or `pgvector` (decided fallback). The evals
  (`contracts/memory-eval.md`) run against whichever is configured — backend-agnostic by construction.
