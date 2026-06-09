# Contract — `CorpusRetriever` (reference-knowledge read API)

The read contract consumed by enrichment (#9). Defined as a Protocol in `domain/corpus.py`; implemented by
`repositories/corpus.py::CorpusRepository` over the `reference_corpus` Postgres table. Deterministic — **no
LLM, no embeddings** in v1 (CD1).

```python
@runtime_checkable
class CorpusRetriever(Protocol):
    async def search_reference(self, query: ReferenceQuery, *, k: int) -> list[ReferenceHit]: ...
```

## `search_reference(query, *, k) -> list[ReferenceHit]`

**Input**: `ReferenceQuery(technique_ids: list[str], terms: list[str])`, plus `k` (top-k; defaults to
`CorpusSettings.retrieval_k`).

**Behavior** — ranked union, deterministic:
1. **Technique-keyed match (highest rank)**: for each id in `technique_ids`, return the `TECHNIQUE` entry
   with that `key` and every `RUNBOOK` whose `tags` contain the id. `matched_on="technique"`,
   `relevance=1.0`.
2. **Tag match**: entries whose `tags` intersect the lowercased `terms`. `matched_on="tag"`, relevance
   scaled by overlap size (bounded `(0, 1)`).
3. **Term/lexical match**: entries whose `title`/`content` contain a `term` (simple case-insensitive
   substring/`ILIKE`). `matched_on="term"`, lower relevance band.
4. De-duplicate by `(kind, key)` keeping the highest relevance; sort by `relevance` desc then `key` asc
   (stable, deterministic); truncate to `k`.

**Guarantees**:
- **Empty in → empty out**: an empty query, or a query with no match, returns `[]` — never an error
  (FR-003, US1 scenario 3).
- **Cold-store competent**: a freshly seeded store returns relevant entries on the first incident
  (US1/SC-001).
- **Deterministic**: identical input → identical ordered output (no model, no randomness) — supports the
  `retrieval` eval and Principle IV.
- **Bounded**: indexed `(kind,key)` + GIN(`tags`) queries; honors `k`; adds no measurable latency to the
  disposition path (FR-008, the reader calls it off-path).

**Errors**: a DB error surfaces as an empty result to the *reader* (the reader treats knowledge as
best-effort), with the error logged — knowledge never blocks reasoning (FR-008). (The repository may raise
internally; the enrichment-side dependency wraps it. In #5's own tests the repository is exercised directly.)
