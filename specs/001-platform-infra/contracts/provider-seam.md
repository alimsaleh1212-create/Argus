# Contract — Singleton Provider Seam

**Feature**: `001-platform-infra` | Consumed by: **every** later spec that needs a startup-built
singleton (observability, ingestion/Redis, memory/Neo4j, llm-provider, safety/guardrails).

This is the foundation's most important outward contract: how a later component attaches a long-lived
shared resource **without modifying the foundation** (FR-014). It is the same seam that enforces the
"triage has no action tools" boundary later (Constitution III) by controlling what each consumer can
be handed.

---

## The `Provider` protocol

```python
class Provider(Protocol):
    name: str  # attribute name it will occupy on AppContainer (unique)

    def build(self, settings: Settings) -> AbstractAsyncContextManager[Any]:
        """Yield exactly one constructed resource; dispose it on context exit."""
```

- `build()` is an **async context manager**: setup before `yield`, teardown after (FR-011).
- `name` MUST be unique across the registry; duplicate registration is a startup error.

## Registration

```python
# foundation registers, in dependency order:
register_provider(DbEngineProvider())     # name="db_engine"
register_provider(VaultClientProvider())  # name="vault_client"
register_provider(BlobClientProvider())   # name="blob_client"

# a LATER spec (e.g. SPEC-memory) adds, with NO edit to foundation files:
register_provider(Neo4jDriverProvider())  # name="neo4j_driver"
```

- Registration order = **build order**; **teardown is reverse order** (SC-005, no leaks).
- Providers are registered at import time of each component's `infra` module; the foundation's
  lifespan iterates whatever is registered.

## Lifespan behaviour (guarantees)

1. On startup, enter each provider's `build()` in order; assign the yielded value to
   `container.<name>`. Build each **exactly once** (FR-011).
2. If any `build()` raises (e.g. Vault unreachable), **abort**: exit already-entered providers in
   reverse, then exit the process non-zero — never serve in a half-built state (SC-003, FR-003).
3. On shutdown, exit all entered providers in reverse order; assert no resource remains open (SC-005).

## Consumption (DI)

```python
async def get_db_session(request: Request) -> AsyncIterator[AsyncSession]:
    container: AppContainer = request.app.state.container
    async with container.session_factory() as session:
        yield session

# handler / tool:
async def handler(session: AsyncSession = Depends(get_db_session)): ...
```

- Consumers obtain resources **only** through `Depends()` providers reading `app.state.container`
  (FR-012) — never module globals.
- In tests, `app.dependency_overrides[get_db_session] = fake` substitutes a double without touching
  consumer code (FR-013).

### Contract tests (must exist)
- Each registered provider builds exactly once and is reachable as `container.<name>` (integration).
- Reverse-order teardown closes every resource; a leak probe reports zero open connections (integration).
- A provider whose `build()` raises causes a non-zero process exit and **no** served requests (integration).
- A duplicate `name` registration fails fast at startup (unit).
- `dependency_overrides` replaces a provider in a handler without code change (unit).
