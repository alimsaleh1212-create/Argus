# Phase 0 Research — Platform & Infrastructure Foundation

**Feature**: `001-platform-infra` | **Date**: 2026-06-06

The brief and constitution already fix most of the stack (Vault, MinIO, Alembic, `uv`, `ruff`,
`gitleaks`, docker-compose, async + DI + `pydantic-settings`). This document records the **open
decisions** that the spec/constitution left to implementation, each as Decision / Rationale /
Alternatives. All of these will be carried into `DECISIONS.md` at implementation time (Constitution
governance requires non-obvious choices be defended there).

---

## D1 — Python version pin

- **Decision**: Python **3.12**, pinned `requires-python = ">=3.12,<3.13"` (and a `.python-version`).
- **Rationale**: Broadest compatibility across the full Sentinel dependency set that lands in later
  specs (Graphiti, async SQLAlchemy 2.x, pydantic v2, asyncpg, aioboto3). 3.13 is still maturing in
  some C-extension wheels on the critical path; pinning one minor avoids "works on my machine" drift
  and keeps `uv.lock` reproducible (SC-008).
- **Alternatives**: 3.13 (newest, but earlier-adopter risk for graph/db wheels); 3.11 (very safe but
  forgoes 3.12 typing/async ergonomics). Rejected in favour of the 3.12 middle ground.

## D2 — Web framework & DI mechanism

- **Decision**: **FastAPI** (+ uvicorn). Dependency injection is **FastAPI-native**: a typed
  `AppContainer` built in the **lifespan** and held on `app.state`, exposed to handlers/tools through
  `Depends()` provider functions. No third-party DI container.
- **Rationale**: The brief explicitly says "via `Depends()`." FastAPI's lifespan gives exactly-once
  singleton construction/disposal (FR-011), `Depends()` gives the override seam that mocks the LLM and
  later enforces "triage has no action tools" (FR-012/FR-013, Constitution III/VII). Native OpenAPI
  also documents the health contract for free.
- **Alternatives**: `dependency-injector` / `that-depends` (more machinery than needed; the brief
  already chose `Depends()`); a bare module-global registry (violates FR-012's "no module globals").

## D3 — Singleton registration seam (the extensibility contract)

- **Decision**: A `Provider` protocol — an async context manager that yields one built resource and
  tears it down — plus an **ordered provider registry**. The lifespan iterates the registry on
  startup (entering each, storing the result on `AppContainer`) and exits them in reverse on shutdown.
  Later specs **append** their provider (Neo4j driver, LLM client, Redis pool, guardrails client)
  without editing `lifespan.py`.
- **Rationale**: Directly satisfies FR-014 ("registration seam so later components attach their own
  startup-initialized singletons without changing the foundation") and the spec's ownership-seam
  assumption. Reverse-order teardown guarantees clean disposal (FR-011, SC-005).
- **Alternatives**: Hand-wired `async with` stack in `lifespan.py` (works but forces every later spec
  to edit the foundation — fails FR-014); `contextlib.AsyncExitStack` alone (used *internally* by the
  registry, but the registry adds the named-attribute + ordering contract).

## D4 — Vault access on the async path

- **Decision**: A thin async `VaultClient` wrapping `httpx.AsyncClient` against Vault's **KV v2 HTTP
  API**. At startup it reads the declared required secret paths; unreachable Vault **or** a missing
  required key → raise → process exits non-zero (refuse to boot).
- **Rationale**: "Async all the way down" (Constitution VII). The official `hvac` client is
  **synchronous** and would block the event loop or require thread-pool offloading. Vault's KV API is
  a couple of simple authenticated GETs — trivial over `httpx`, and it keeps one HTTP client style
  across the codebase. Implements FR-003/FR-004 and the "Vault refuses to boot if unreachable" rule.
- **Alternatives**: `hvac` (sync, off-loop); reading secrets from env only (defeats the point of a
  secret store and the day-1 Vault standard). Dev-mode Vault in compose with a fixed root token via
  `.env` keeps the demo one-command (SC-001) while the same client works against real Vault later.

## D5 — MinIO / object-store client

- **Decision**: **`aioboto3`** (async S3 client) pointed at the MinIO endpoint. On startup, ensure the
  `eval-reports` and `incident-snapshots` buckets exist (create-if-absent).
- **Rationale**: MinIO is S3-compatible; `aioboto3` keeps object I/O on the async path (FR-017,
  Constitution VII). Bucket bootstrap at startup means later specs (eval reports, incident snapshots)
  can write immediately.
- **Alternatives**: The official `minio` Python SDK (synchronous → off-loop); `boto3` (sync). Both
  rejected for the same async reason.

## D6 — Relational store, driver, and migrations

- **Decision**: **PostgreSQL 16** via the `pgvector/pgvector:pg16` image, accessed through
  **SQLAlchemy 2.0 async** + **`asyncpg`**. **Alembic** with an **async `env.py`** owns schema. A
  committed **baseline migration** creates the minimal foundation schema (e.g. a `schema_health` /
  infra marker table) so the migration pipeline is exercised end-to-end from day one (FR-015/FR-016).
- **Rationale**: Using the pgvector image now (even though vectors are `SPEC-memory`'s concern) means
  the relational + vector substrate and the documented Graphiti fallback (Constitution VI) need **no
  image change** later. Async SQLAlchemy + asyncpg matches the engineering standard. Alembic gives the
  reproducible-up + reversible guarantee (SC-006).
- **Alternatives**: Plain `postgres:16` (would need an image swap when pgvector lands — avoidable
  churn); raw SQL migration scripts (loses autogenerate/version graph); `psycopg3` async (fine, but
  `asyncpg` is the brief's implied choice and faster for our access pattern).

## D7 — Health vs readiness

- **Decision**: Two endpoints. `GET /health` = **liveness** (process up; no dependency checks; always
  cheap). `GET /ready` = **readiness** — actively probes Vault, Postgres, and MinIO and returns `200`
  only when **all required** dependencies are reachable, else `503` with a per-dependency status body.
  Compose `healthcheck`s gate start order via `depends_on: { condition: service_healthy }`.
- **Rationale**: Separating liveness from readiness is the standard that makes FR-008 ("healthy only
  when required dependencies are reachable") and the edge cases ("slow dependency → unhealthy, not
  failed") testable and avoids a false-healthy during bring-up. Drives the smoke check (FR-009).
- **Alternatives**: A single `/health` that checks everything (can't distinguish "starting" from
  "broken," and makes liveness expensive); no readiness probe (fails the bring-up edge cases).

## D8 — Integration-test infrastructure

- **Decision**: **`testcontainers`-python** spins up ephemeral Vault/Postgres/MinIO for the
  **integration** tier (hermetic, no pre-running stack needed in CI). The **e2e/smoke** tier exercises
  the real committed `compose.yaml` via a dedicated CI job (this is the artifact users actually run).
- **Rationale**: Gives the constitution's three tiers honest, independent meaning for an infra spec:
  unit (no services), integration (real services, hermetic), e2e/smoke (the real compose users run,
  SC-004). Keeps CI self-contained and fast while still proving the shipped compose file.
- **Alternatives**: Reuse `compose.yaml` for integration too (slower, couples unit-of-test to the full
  stack, harder to parametrise fault injection like "stop Vault"); mocks only (wouldn't prove real
  connectivity, the whole point of the integration tier).

## D9 — Import-direction enforcement

- **Decision**: Enforce the inward-only layer dependency (`api → services → repositories → infra`;
  `domain` depends on nothing) in CI using **`import-linter`** contracts (alongside `ruff` for
  lint/format).
- **Rationale**: FR-018 requires a *documented and enforced* dependency direction; a CI contract makes
  "enforced" real rather than aspirational, and catches accidental layering violations as later specs
  add files.
- **Alternatives**: `ruff`'s `flake8-tidy-imports` banned-API rules (coarser, harder to express
  layer-graph contracts); convention-only / code review (not enforced → fails FR-018's "enforced").

## D10 — Eval gate seeded on day 1

- **Decision**: Commit `eval_thresholds.yaml` now with a single provider-agnostic **`smoke`** gate
  (stack-comes-up-clean) wired into CI as a required check. The full eval harness, golden sets, and
  the remaining gates are `SPEC-eval` (#13), which extends this file.
- **Rationale**: Constitution II mandates the threshold file be seeded on day 1 "so CI gates from the
  start." The smoke gate is the only one meaningful for the foundation and is naturally provider-
  independent, satisfying the "passes on both providers" rule trivially.
- **Alternatives**: Defer the file to `SPEC-eval` (violates the day-1 seeding rule); seed all gates
  now (impossible — their components don't exist yet).

---

## Resolved unknowns

All Technical-Context items are concrete; **no `NEEDS CLARIFICATION` remain**. Open numeric defaults
(≤10-min bring-up, <5 s fail-fast) are inherited from the spec's Success Criteria and treated as
budgets to verify in the smoke/integration tiers, not as blockers.

## Carry-forward to later specs (seam reminders)

- `SPEC-observability` (#2) attaches structlog processors + tracing + redaction via the provider seam;
  this spec only guarantees no-secret-in-error and a logging entry point.
- `SPEC-ingestion` (#4) appends the **Redis** service to `compose.yaml` and a Redis pool provider.
- `SPEC-memory` (#6) appends **Neo4j** to compose + a driver provider, and uses the pgvector extension
  already available in the DB image.
- `SPEC-llm-provider` (#3) appends the LLM client provider (and is where the "both providers" eval
  dimension becomes real).
- `SPEC-safety` (#11) appends the **guardrails sidecar** service + a Vault-resolved service credential
  (using the `VaultClient` established here).
