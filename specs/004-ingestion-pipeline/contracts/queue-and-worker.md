# Contract — Queue Seam & Worker / Grounding

**Owner**: #4 `SPEC-ingestion` · **Consumers**: the API (enqueue side), the supervisor #7 (fills the
handoff stub) · **Modules**: `backend/infra/queue.py`, `backend/infra/cache.py`, `backend/worker.py`,
`backend/services/grounding.py`, `backend/services/pipeline.py`

Fills the reserved `infra/queue.py` / `infra/cache.py` seams and activates `worker.py`. Durability is in
Postgres (the Incident); Redis is transient dispatch + dedup only (ID1).

---

## Cache seam — `CacheProvider` (`infra/cache.py`)

Lifespan singleton (Provider protocol, like `DbEngineProvider`): builds one `redis.asyncio` connection
pool on startup, disposes it on shutdown. The **only** place (with `queue.py`) that imports `redis`.

```python
class CacheProvider:            # name = "cache"
    def build(self, settings) -> AbstractAsyncContextManager[Redis]: ...

# dedup helper (used by intake):
async def claim_fingerprint(redis, fp, incident_id, window_s) -> bool   # SET dedup:<fp> <id> NX EX window
async def lookup_fingerprint(redis, fp) -> str | None                   # GET dedup:<fp>
```

`claim_fingerprint` returns `True` if the key was set (first sighting) and `False` on a duplicate (ID3).

## Queue seam — `RedisTaskQueue` (`infra/queue.py`)

Reliable Redis-list pattern (ID2). Satisfies the reserved `TaskQueue` Protocol (`enqueue(topic, payload)`)
and adds the consumer side.

```python
class QueueProvider:            # name = "queue"  (reuses the cache pool)
    def build(self, settings) -> AbstractAsyncContextManager[RedisTaskQueue]: ...

class RedisTaskQueue:
    async def enqueue(self, incident_id: str) -> str          # LPUSH queue:incidents
    async def dequeue(self) -> str | None                     # BLMOVE queue→processing (block dequeue_block_s)
    async def ack(self, incident_id: str) -> None             # LREM queue:processing 1 <id>
    async def recover(self) -> int                            # drain processing → queue; returns count
```

**Delivery semantics**: at-least-once. A crash between `dequeue` and `ack` leaves the id in
`queue:processing`; the next worker's `recover()` (on startup) returns it to the main queue. Idempotent
grounding makes re-delivery harmless (SC-006).

> Enqueue is the only queue op the **API** calls (inside `intake.accept()`, after the durable insert).
> `dequeue/ack/recover` are the **worker's**.

## Worker — `backend/worker.py`

`python -m backend.worker` (same image as the API; activated `worker` compose container).

```text
main():
  build settings + the same providers (cache, queue, db, observability)   # reuse container/lifespan
  await queue.recover()                                                    # reclaim any crashed in-flight jobs
  loop forever:
      incident_id = await queue.dequeue()        # blocks up to dequeue_block_s; None → continue
      if not incident_id: continue
      bind_incident(correlation_id)              # #2 — all lines/spans share the id
      with span(tracer, "grounding", AGENT_STEP, correlation_id):
          try:
              if not await repo.claim_for_grounding(id):   # already grounding/grounded → skip (idempotent)
                  await queue.ack(id); continue
              incident = await repo.get(id)
              evidence = ground(incident)                    # deterministic, no LLM
              await repo.set_grounded(id, incident.normalized_event, evidence, evidence.severity)
              await dispatch_to_pipeline(incident)           # handoff stub (logs; filled by #7)
              await queue.ack(id)
          except Exception as exc:
              n = await repo.bump_attempt(id)
              if n >= settings.ingest.max_attempts:
                  await repo.mark_failed(id, reason=type(exc).__name__)
                  await queue.ack(id)                        # stop redelivering a poison job
              # else: leave unacked → recover()/redelivery retries it
```

**Guarantees**: every dequeued Incident reaches a terminal state (`grounded` or `failed`) — never lost,
never stuck (SC-006); grounding output is populated for 100% of `grounded` incidents (SC-007).

## Grounding — `backend/services/grounding.py`

```python
def ground(incident: Incident) -> Evidence: ...   # pure, deterministic, NO LLM (ID7)
```
Builds `Evidence` from `NormalizedEvent`: `verdict="rule_match"`, `severity` from the band table, a
one-line deterministic `summary`, empty `retrieved_context` (reserved for #5/#6), and any `flags`. Fully
unit-testable without any backing service.

## Downstream handoff — `backend/services/pipeline.py`

```python
async def dispatch_to_pipeline(incident: Incident) -> None:
    """Hand a grounded incident to the supervisor. STUB at #4: logs and returns.
    Filled by SPEC-incident-state-machine (#7); signature is the seam — do not change."""
```
A **logging no-op** now so the e2e pipeline goes green (`grounded` is clean for #4). #7 replaces the body
with the real supervisor entry; this signature is the contract #7 must honor.

## Readiness — `infra/health.py::check_redis`

`PING` the pool with the per-dependency timeout; redaction-safe detail (names the dep, never a value).
Added to the `/ready` aggregation (`run_readiness_probes`) so **Redis down ⇒ `/ready` 503** (FR-014).

## Compose activation

Uncomment/activate the reserved blocks in `compose.yaml`:
- **`redis`** (`redis:7`, healthcheck `redis-cli ping`) — add to `api` and `worker` `depends_on`.
- **`worker`** — same image, `command: ["python","-m","backend.worker"]`,
  `depends_on: redis (healthy), migrate (completed), vault-seed (completed)`.
- **`vault-seed`** — also write `secret/ingest` (the webhook token); add `secret/ingest` to the api/worker
  `ARGUS__VAULT__REQUIRED_PATHS`.
