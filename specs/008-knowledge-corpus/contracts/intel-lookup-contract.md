# Contract — `ThreatIntelClient` (optional on-demand intel)

`infra/intel.py`, behind an `IntelProvider` (DI lifespan singleton). **Optional, config-gated,
fail-closed, off-path.** Realizes FR-004/FR-005/FR-006/FR-007/FR-008 and CD3/CD5.

```python
class ThreatIntelClient:
    async def lookup(self, indicator: str, kind: EntityKind) -> IntelVerdict: ...
```

## `lookup(indicator, kind) -> IntelVerdict`

**Disabled fast-path**: if `IntelSettings.enabled is False` **or** the API key is absent (optional Vault
path unseeded), return `IntelVerdict(verdict="unknown", …)` immediately — **no external call, no error**
(CD3). This is the "intel disabled → corpus-only" path (US2 scenario 4, FR-004).

**Enabled path**:
1. **Cache read** — `GET intel:<redacted-indicator>` via the existing Redis `CacheProvider`. On hit, return
   the cached `IntelVerdict` (including a cached `unknown`/negative) — **no external call** (US2 scenario 2,
   FR-005).
2. **External call** — one async `httpx` GET to `IntelSettings.base_url`, bounded by `timeout_s`. Any
   exception or timeout → `verdict="unknown"` (**fail-closed**, FR-008).
3. **Untrusted-input handling (CD5)** — the response text is **redacted** (`Boundary.MEMORY_WRITE`) and
   passed through the reserved `Guardrail` seam (#11) **before** any persistence; the seam **no-ops
   gracefully** if #11 is not yet configured (never raises into this path).
4. **Cache write** — `SET intel:<indicator> <verdict> EX cache_ttl_s` (negative/unknown cached too) to
   bound cost and protect the source (FR-005).
5. **Persist to memory (CD2)** — for a non-`unknown` verdict, build a `TemporalFact`
   (`fact_type="reputation"`, `value=verdict`, `valid_from=observed_at`) and call
   `MemoryStore.write_fact(fact)`. This **supersedes** any prior reputation for the indicator
   (invalidate-not-delete) so the next appearance carries history (US2 scenario 1 & 3, FR-006). A
   memory-write failure is swallowed (best-effort, FR-008) — the verdict is still returned to the caller.

**Guarantees**:
- Never blocks or crashes the pipeline: disabled/error/timeout all yield `unknown`; memory/guardrail
  failures are swallowed (FR-008).
- Idempotent enough for v1: a repeat within TTL is a cache hit; a fact write of an unchanged current
  reputation is a no-op supersession.
- Missing credentials **disable**, they do **not** fail boot (CD3) — distinct from substrate creds.

**Out of scope**: multiple/federated sources, ret/ failover, streaming feeds (roadmap §v2/v3).

---

## Added `MemoryStore` method (the #6-seam touch, CD2)

```python
# domain/memory.py — MemoryStore Protocol
async def write_fact(self, fact: TemporalFact) -> None: ...
```
- `NullMemory.write_fact` → no-op (degradation preserved; FR-008).
- `GraphitiMemory.write_fact` → write a time-bounded reputation edge with `valid_from`; end the validity of
  the current fact of the same `(entity, fact_type)` (invalidate, not delete). Read unchanged via
  `query_fact(entity, "reputation", as_of=…) -> FactState` (`is_current` / `has_superseded`).
- The decided pgvector fallback (`PgVectorMemory`, MD9) gains the same method when/if built (a `valid_until`
  update + insert).
