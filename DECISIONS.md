# Decisions — Sentinel

Records non-obvious architectural choices as required by the project constitution.
Each entry: what was chosen, why, and what alternatives were considered and rejected.

---

## Component 001 — Platform & Infrastructure Foundation

### D1 — Python 3.12 pin

**Decision**: Python 3.12, pinned `requires-python = ">=3.12,<3.13"` and `.python-version`.

**Rationale**: Broadest compatibility across the full Sentinel dependency set (Graphiti, async
SQLAlchemy 2.x, pydantic v2, asyncpg, aioboto3). Python 3.13 is still maturing for some C-extension
wheels; pinning one minor keeps `uv.lock` reproducible (SC-008).

**Rejected**: 3.13 (early-adopter risk for graph/db wheels); 3.11 (safe, but forgoes 3.12
typing/async ergonomics).

---

### D2 — FastAPI + native `Depends()` DI

**Decision**: FastAPI + uvicorn. DI is FastAPI-native: a typed `AppContainer` built in the lifespan,
held on `app.state`, exposed to handlers via `Depends()` provider functions. No third-party DI
container.

**Rationale**: The brief explicitly requires `Depends()`. FastAPI's lifespan gives exactly-once
singleton construction/disposal (FR-011); `Depends()` gives the override seam that mocks the LLM and
enforces "triage has no action tools" (FR-012/FR-013, Constitution III/VII).

**Rejected**: `dependency-injector` / `that-depends` (more machinery than needed); module-global
registry (violates FR-012).

---

### D3 — Provider protocol + ordered registry (the extensibility seam)

**Decision**: A `Provider` protocol — an async context manager that yields one built resource — plus
an ordered registry. The lifespan iterates the registry on startup and exits in reverse on shutdown.
Later specs append their provider without editing `lifespan.py`.

**Rationale**: Directly satisfies FR-014 ("registration seam so later specs attach singletons without
changing the foundation"). Reverse-order teardown guarantees clean disposal (FR-011, SC-005).

**Rejected**: Hand-wired `async with` stack in `lifespan.py` (every later spec must edit the
foundation — fails FR-014); `AsyncExitStack` alone (no named-attribute or ordering contract).

---

### D4 — Vault access via `httpx` (async HTTP, not `hvac`)

**Decision**: A thin async `VaultClient` wrapping `httpx.AsyncClient` against Vault's KV v2 HTTP
API. Unreachable Vault or a missing required key → raise → process exits non-zero.

**Rationale**: "Async all the way down" (Constitution VII). The official `hvac` client is synchronous
and would block the event loop. Vault's KV API is a handful of authenticated GETs — trivial over
`httpx`, consistent with the rest of the codebase.

**Rejected**: `hvac` (sync, off-loop); env-only secrets (defeats the secret store).

---

### D5 — MinIO via `aioboto3` (async S3)

**Decision**: `aioboto3` pointed at the MinIO S3-compatible endpoint. Buckets are created on startup
if absent.

**Rationale**: Keeps object I/O on the async path (FR-017, Constitution VII). Bucket bootstrap at
startup means later specs can write immediately without setup steps.

**Rejected**: Official `minio` Python SDK (synchronous); `boto3` (synchronous). Both blocked on the
async requirement.

---

### D6 — pgvector image, async SQLAlchemy + asyncpg, Alembic

**Decision**: PostgreSQL 16 via `pgvector/pgvector:pg16`; SQLAlchemy 2.0 async + asyncpg; Alembic
with async `env.py`; a baseline migration so the pipeline is exercised from day one.

**Rationale**: Using the pgvector image now means no image change when SPEC-memory (#6) lands. Async
SQLAlchemy + asyncpg matches the engineering standard. Alembic gives reproducible-up + reversible
(SC-006).

**Rejected**: Plain `postgres:16` (image swap later — avoidable churn); raw SQL scripts (no
autogenerate/version graph); `psycopg3` (fine, but asyncpg is faster for our access pattern).

---

### D7 — Separate `/health` (liveness) and `/ready` (readiness) endpoints

**Decision**: `GET /health` = liveness (no dependency I/O, always cheap). `GET /ready` = readiness
(probes Vault, Postgres, MinIO; returns 200 only when all healthy, 503 with per-dep status otherwise).
Compose `depends_on` uses `condition: service_healthy`.

**Rationale**: Separating liveness from readiness makes FR-008 and the edge cases ("slow dependency →
unhealthy, not failed") testable and avoids a false-healthy during bring-up.

**Rejected**: Single `/health` checking everything (can't distinguish starting from broken; makes
liveness expensive); no readiness probe (fails the bring-up edge cases).

---

### D8 — `testcontainers` for integration, real `compose.yaml` for e2e/smoke

**Decision**: `testcontainers-python` spins up ephemeral Vault/Postgres/MinIO for the integration
tier (hermetic, no pre-running stack needed). The e2e/smoke tier exercises the real committed
`compose.yaml` in CI.

**Rationale**: Gives the three tiers honest, independent meaning: unit (no services), integration
(real services, hermetic, fault-injectable), e2e/smoke (the actual artifact users run, SC-004).

**Rejected**: Reusing `compose.yaml` for integration (slower, harder to inject faults like "stop
Vault"); mocks only (doesn't prove real connectivity).

---

### D9 — `import-linter` for layer-contract enforcement

**Decision**: Enforce inward-only layer dependency (`routers → services → agents → repositories →
infra`; `domain` isolated) in CI via `import-linter` contracts.

**Rationale**: FR-018 requires a *documented and enforced* dependency direction. A CI contract makes
"enforced" real, not aspirational, and catches violations as later specs add files.

**Rejected**: `ruff` tidy-imports rules (coarser, hard to express a layer graph); convention-only
(not enforced → fails FR-018's "enforced").

---

### D10 — Eval gate seeded day 1 (`eval_thresholds.yaml`)

**Decision**: Commit `config/eval_thresholds.yaml` with a single provider-agnostic `smoke` gate
(stack-comes-up-clean) wired into CI as a required check. Full eval harness is SPEC-eval (#13).

**Rationale**: Constitution II mandates the threshold file be seeded on day 1 "so CI gates from the
start." The smoke gate is the only one meaningful for the foundation and is naturally
provider-independent, satisfying "passes on both providers" trivially.

**Rejected**: Defer the file to SPEC-eval (violates the day-1 seeding rule); seed all gates now
(their components don't exist yet).

---

### D11 — compose.yaml: no YAML anchors; `.env` only for vault-seed

**Decision**: Backend containers (`api`, `migrate`) receive all config via explicit `environment:`
keys in `compose.yaml`. `.env` (optional) is read only by `vault-seed` to seed user API keys into
Vault. No YAML anchors/merge keys (`&`, `<<:`).

**Rationale**: Anchors are standard YAML but non-obvious to read. Explicit `environment:` blocks are
self-documenting. Keeping `.env` off the API container prevents silent fallback to file-based
secrets; all runtime secrets come from Vault. The API's bootstrap config (Vault addr/token, DSN) are
well-known dev defaults appropriate for compose env vars.

**Rejected**: Single `env_file: .env` on all containers (couples app config to a file that should
only carry user secrets); YAML anchors (saves ~10 lines but at readability cost).

---

*This file is append-only within each component; later specs add their own section at the bottom.*
