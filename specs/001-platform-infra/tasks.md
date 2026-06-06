---
description: "Task list for SPEC-platform-infra (Component #1) implementation"
---

# Tasks: Platform & Infrastructure Foundation

**Input**: Design documents from `specs/001-platform-infra/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md),
[data-model.md](./data-model.md), [contracts/](./contracts/)

**Tests**: REQUIRED (constitution Principle II — Test-First, Three-Tier, Eval-Gated). Every story
carries unit/integration/e2e tasks written **before** implementation and green in CI before the spec
is "done". ≥80% coverage on new code.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: US1–US5 (story phases only; Setup/Foundational/Polish carry no story label)
- All paths are repo-root-relative.

## ⚠️ Story sequencing note (read first)

Unlike a typical feature, this is the **root infrastructure** component: the five user stories are
not independent slices — a healthy one-command bring-up (**US1**) *integrates* fail-fast config
(**US2**), the lifecycle/DI seam (**US3**), and the storage substrate (**US4**). Therefore the phases
below are ordered by **build dependency**, not strictly by priority:

`Setup → Foundational → US2 (P1, config) → US4 (P2, storage) → US3 (P2, DI validation) → US1 (P1, bring-up = integration MVP) → US5 (P3, hygiene) → Polish`

Each story remains independently **testable** via its Independent Test (spec.md). The **MVP** for this
component is everything through **US1** (Phase 6): a fresh-clone `docker compose up` that reaches
healthy. US5 (hygiene) and Polish are additive.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project scaffolding and tooling — no runtime behavior yet.

- [ ] T001 Initialize `uv` project: `pyproject.toml` with `requires-python = ">=3.12,<3.13"`, base deps (fastapi, uvicorn, pydantic, pydantic-settings, sqlalchemy[asyncio], asyncpg, alembic, httpx, aioboto3, tenacity, structlog), and `.gitignore` (`.env`, `.venv`, `__pycache__`); generate committed `uv.lock`
- [ ] T002 [P] Create layered `app/` package skeleton with `__init__.py` in `app/`, `app/api/`, `app/services/`, `app/agents/`, `app/repositories/`, `app/domain/`, `app/infra/`
- [ ] T003 [P] Create `tests/` skeleton (`tests/unit/`, `tests/integration/`, `tests/e2e/` with `__init__.py` and `conftest.py`) and configure pytest in `pyproject.toml` (`asyncio_mode=auto`, test paths, `--cov=app`)
- [ ] T004 [P] Configure `ruff` (lint + format) and add `import-linter` dev dep with layer-contract stub in `pyproject.toml`
- [ ] T005 [P] Add `Dockerfile` for the `api` service (uv-based, Python 3.12-slim, runs uvicorn)
- [ ] T006 [P] Seed `eval_thresholds.yaml` at repo root with a provider-agnostic `smoke` gate placeholder (per research.md D10)
- [ ] T007 [P] Write `.env.example` at repo root with every config section and no secret values, per [contracts/settings-schema.md](./contracts/settings-schema.md)

**Checkpoint**: project installs (`uv sync`), `ruff` runs, empty test tiers collect.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The shared spine every story plugs into — the lifecycle/DI mechanism, config machinery,
app factory, health types, logging seam. **No user story can begin until this phase is complete.**

- [ ] T008 [P] Define domain health types `DependencyStatus`, `ReadinessReport`, `Liveness` in `app/domain/health.py` (per [data-model.md](./data-model.md) §4; no outward deps)
- [ ] T009 Define `Provider` protocol + ordered provider **registry** (`register_provider`, iteration, duplicate-name guard) in `app/infra/container.py` (per [contracts/provider-seam.md](./contracts/provider-seam.md))
- [ ] T010 Implement `AppContainer` + lifespan that builds providers **once in order** and disposes them in **reverse order**, with build-failure → reverse-teardown → non-zero exit, in `app/infra/lifespan.py` (depends T009)
- [ ] T011 [P] Implement `Settings` base machinery in `app/infra/config.py`: `pydantic-settings`, `extra="forbid"`, `env_nested_delimiter="__"`, `SecretStr` fields, frozen-after-load, `__repr__` that masks secrets (per [contracts/settings-schema.md](./contracts/settings-schema.md))
- [ ] T012 Implement FastAPI app factory in `app/main.py` wiring the lifespan and exposing `AppContainer` on `app.state` (depends T010)
- [ ] T013 [P] Add minimal `structlog` logging seam (JSON renderer, trace-id placeholder; full redaction is SPEC-observability) in `app/infra/logging.py`

**Checkpoint**: app boots with an empty provider registry; `Settings` validates; lifespan no-ops cleanly.

---

## Phase 3: User Story 2 — Fail-fast configuration & secret resolution (Priority: P1)

**Goal**: The app refuses to start on missing/invalid config, an unknown key, or an unreachable
secret store, with a clear, secret-free error. (Built first — US1 bring-up depends on it.)

**Independent Test**: Start with (a) valid config, (b) missing required secret, (c) unknown key,
(d) Vault unreachable; confirm (a) boots and (b)–(d) refuse boot with a specific, secret-free error.

### Tests for User Story 2 (write first, must FAIL)

- [ ] T014 [P] [US2] Unit: valid `Settings` boots & is frozen; unknown extra key → `ValidationError`/non-zero; `.env.example` declares every required field — in `tests/unit/test_config.py`
- [ ] T015 [P] [US2] Unit: no `SecretStr` value appears in error output across all failure cases — in `tests/unit/test_secret_redaction.py`
- [ ] T016 [P] [US2] Integration: refuse-boot when Vault unreachable and when a required secret is missing (testcontainers Vault, fault-injected) — in `tests/integration/test_startup_failfast.py`

### Implementation for User Story 2

- [ ] T017 [US2] Add `app`, `vault`, and `startup` config sections (incl. `vault.required_paths`, `startup.dependency_timeout_s/connect_retries`) to `app/infra/config.py`
- [ ] T018 [P] [US2] Implement async `VaultClient` (httpx, KV v2 GET, bounded `tenacity` retry on transient connect) in `app/infra/vault.py`
- [ ] T019 [US2] Implement `VaultClientProvider` that resolves all `required_paths` at startup and raises on unreachable/missing (fail-fast); `register_provider` it — in `app/infra/vault.py` (depends T009, T018)
- [ ] T020 [US2] Ensure lifespan converts a provider build failure into a secret-free, path-naming error and non-zero exit in `app/infra/lifespan.py` (depends T010, T019)

**Checkpoint**: every fail-fast case in the Independent Test passes; no secret ever leaks.

---

## Phase 4: User Story 4 — Versioned relational store & object storage (Priority: P2)

**Goal**: Schema is created/evolved via reversible migrations; an object store is available for
reports and snapshots. (Providers plug into the seam from Phase 2.)

**Independent Test**: `alembic upgrade head` on an empty DB reaches current schema; `downgrade base`
returns to empty; an object written to MinIO reads back identical.

### Tests for User Story 4 (write first, must FAIL)

- [ ] T021 [P] [US4] Integration: `alembic upgrade head` → `downgrade base` round-trips with no drift (testcontainers Postgres) — in `tests/integration/test_migrations.py`
- [ ] T022 [P] [US4] Integration: bucket bootstrap creates `eval-reports`/`incident-snapshots`; put/get returns identical bytes (testcontainers MinIO) — in `tests/integration/test_blob.py`

### Implementation for User Story 4

- [ ] T023 [P] [US4] Add `postgres` config section; implement `DbEngineProvider` (async `create_async_engine` + `async_sessionmaker`) and `register_provider` it — in `app/infra/db.py` (depends T009)
- [ ] T024 [P] [US4] Add `minio` config section; implement `BlobClientProvider` (aioboto3 S3) with create-if-absent bucket bootstrap and `register_provider` it — in `app/infra/blob.py` (depends T009)
- [ ] T025 [US4] Add Alembic async `env.py` + `alembic.ini` reading the async DSN from `Settings` — in `migrations/` (depends T023)
- [ ] T026 [US4] Create baseline migration: `schema_marker` table with reversible up/down — in `migrations/versions/` (depends T025)

**Checkpoint**: migrations round-trip; MinIO put/get works; both providers build via the seam.

---

## Phase 5: User Story 3 — Shared-resource lifecycle & injectable dependencies (Priority: P2)

**Goal**: Singletons build exactly once and dispose with zero leaks; every component obtains them via
`Depends()`, substitutable with test doubles. (Validates the seam with the real providers from US2/US4.)

**Independent Test**: Start/stop repeatedly → each singleton built once and released (zero leaks); in a
test, a fake is injected through the DI mechanism without changing consumer code.

### Tests for User Story 3 (write first, must FAIL)

- [ ] T027 [P] [US3] Integration: each registered provider builds exactly once and is reachable as `container.<name>` — in `tests/integration/test_container.py`
- [ ] T028 [P] [US3] Integration: reverse-order teardown leaves zero open connections across repeated start/stop — in `tests/integration/test_lifecycle_teardown.py`
- [ ] T029 [P] [US3] Unit: `app.dependency_overrides` substitutes a provider in a handler; duplicate-name registration fails fast — in `tests/unit/test_di.py`

### Implementation for User Story 3

- [ ] T030 [P] [US3] Implement `Depends()` consumption providers `get_db_session`, `get_blob_client`, `get_vault_client` reading `app.state.container` — in `app/api/deps.py` (depends T019, T023, T024)
- [ ] T031 [US3] Add teardown leak-assertion instrumentation (open-resource probe) to the lifespan disposal path — in `app/infra/lifespan.py` (depends T010)

**Checkpoint**: build-once + zero-leak + override-in-tests all green.

---

## Phase 6: User Story 1 — One-command fresh-clone bring-up (Priority: P1) 🎯 MVP

**Goal**: The headline deliverable — one documented step + one command brings the full stack to
healthy from a fresh clone. This is the **integration MVP** of the foundation.

**Independent Test**: From a clean checkout, `cp .env.example .env` then `docker compose up`; all
services report healthy and the smoke check confirms `/ready` returns 200.

### Tests for User Story 1 (write first, must FAIL)

- [ ] T032 [P] [US1] Unit: `GET /health` returns `200 {"status":"ok"}` and performs **zero** dependency I/O (spy) — in `tests/unit/test_health.py`
- [ ] T033 [P] [US1] Integration: `GET /ready` returns 200 all-healthy; returns 503 + offending dependency `healthy=false` when one service is stopped — in `tests/integration/test_ready.py`
- [ ] T034 [US1] E2E smoke: `docker compose up -d` from a clean checkout → all services healthy and `/ready` 200 within the grace window; `docker compose down` leaves no orphans — in `tests/e2e/test_smoke.py`

### Implementation for User Story 1

- [ ] T035 [US1] Implement readiness probes for vault/postgres/minio (reachability + latency, redaction-safe `detail`, per-dep timeout) — in `app/infra/health.py` (depends T019, T023, T024)
- [ ] T036 [US1] Implement `/health` (Liveness) and `/ready` (ReadinessReport, 200/503) router mounted in the app factory — in `app/api/health.py` (depends T008, T030, T035)
- [ ] T037 [US1] Author `compose.yaml`: `api`, `postgres` (`pgvector/pgvector:pg16`), `vault` (dev), `minio`, each with a `healthcheck`; `api` `depends_on` the three with `condition: service_healthy` (per [contracts/compose-contract.md](./contracts/compose-contract.md))
- [ ] T038 [US1] Add dev-only Vault secret seed (one-shot writing `secret/minio`) and wire `api` env to `.env` — in `compose.yaml` (depends T037)

**Checkpoint**: 🎯 fresh-clone `docker compose up` reaches healthy; smoke green. **MVP complete.**

---

## Phase 7: User Story 5 — Code & secret hygiene gates (Priority: P3)

**Goal**: Pre-commit blocks lint/format/secret violations; dependencies are pinned and reproducible.

**Independent Test**: A planted fake secret and a lint violation are each blocked at commit; a fresh
`uv sync` reproduces the locked versions exactly.

### Tests for User Story 5 (write first, must FAIL)

- [ ] T039 [P] [US5] Test that pre-commit blocks a planted gitleaks-detectable secret and a ruff violation (invoking `pre-commit run`) — in `tests/integration/test_precommit_gates.py`

### Implementation for User Story 5

- [ ] T040 [P] [US5] Author `.pre-commit-config.yaml`: `ruff` (lint+format), `gitleaks`, `import-linter`, end-of-file/trailing-whitespace hooks
- [ ] T041 [P] [US5] Define inward-only layer contracts (`api → services → repositories → infra`; `domain` isolated) for `import-linter` in `pyproject.toml`/`.importlinter`
- [ ] T042 [US5] Document `uv.lock` pinning + `uv sync` reproducibility and the hygiene gates in `README.md`

**Checkpoint**: hygiene gates block bad commits; reproducible install verified.

---

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: CI wiring, decisions of record, docs, coverage, and seam reminders for later specs.

- [ ] T043 [P] CI workflow `.github/workflows/ci.yml`: `uv sync` → `ruff` → `pytest tests/unit tests/integration` (testcontainers) → `gitleaks` → compose **smoke** job; mark the smoke gate a required check
- [ ] T044 [P] Record decisions D1–D10 from [research.md](./research.md) in `DECISIONS.md`
- [ ] T045 [P] Write top-level `README.md` and validate [quickstart.md](./quickstart.md) end-to-end (bring-up, fail-fast demo, teardown)
- [ ] T046 Enforce ≥80% coverage on new code: add `--cov-fail-under=80` to pytest config in `pyproject.toml` and the CI step in `.github/workflows/ci.yml`
- [ ] T047 [P] Add a "extending the stack" note (compose service + provider seam) to `README.md` for later specs (#2,#3,#4,#6,#11)

---

## Dependencies & Execution Order

### Phase dependencies

- **Setup (P1)**: no dependencies.
- **Foundational (P2)**: depends on Setup — **blocks every story**.
- **US2 (Phase 3)**: depends on Foundational. Prerequisite for US1.
- **US4 (Phase 4)**: depends on Foundational. (Independent of US2; could run parallel to US2 if staffed.)
- **US3 (Phase 5)**: depends on Foundational + the concrete providers from **US2** (vault) and **US4** (db/blob) to validate build-once/teardown/overrides.
- **US1 (Phase 6)**: depends on US2 + US4 (+ US3's `deps.py`) — it integrates them into the healthy bring-up.
- **US5 (Phase 7)**: depends only on Setup/Foundational; can run any time after Setup.
- **Polish (Phase 8)**: depends on all desired stories (CI smoke needs US1).

### Within each story

- Tests are written **first** and must FAIL before implementation (TDD; constitution II).
- Config sections before providers; providers before the consumers/endpoints that use them.

### Parallel opportunities

- Setup: T002–T007 are all `[P]`.
- Foundational: T008, T011, T013 are `[P]` (T009→T010, T012 are sequential).
- US2 tests T014/T015/T016 `[P]`; US4 tests T021/T022 `[P]`; US3 tests T027/T028/T029 `[P]`.
- **US2 and US4 are independent** — a second pair of hands could build them concurrently after Foundational.
- US4 providers T023/T024 `[P]` (different files).

---

## Parallel Example: User Story 4

```bash
# Tests first (different files):
Task: "Integration migrations round-trip in tests/integration/test_migrations.py"   # T021
Task: "Integration MinIO put/get in tests/integration/test_blob.py"                  # T022

# Then the two providers (different files):
Task: "DbEngineProvider in app/infra/db.py"     # T023
Task: "BlobClientProvider in app/infra/blob.py"  # T024
```

---

## Implementation Strategy

### MVP first (through US1)

1. Phase 1 Setup → 2. Phase 2 Foundational → 3. US2 (config/secrets) → 4. US4 (storage) →
5. US3 (DI validation) → 6. **US1 bring-up** → **STOP & VALIDATE**: fresh-clone `docker compose up`
is healthy and smoke is green. This is the complete, demonstrable foundation.

### Incremental delivery (commit at each, PRs ≤ ~400 lines per constitution I)

- **PR-a**: Setup + Foundational (skeleton boots, empty registry).
- **PR-b**: US2 + US4 + US3 (config/secrets, storage, DI seam validated).
- **PR-c**: US1 (compose + health/ready + smoke green) → **MVP / internal milestone tag candidate**.
- **PR-d**: US5 + Polish (hygiene gates, CI, README, DECISIONS, coverage gate).

### Notes

- `[P]` = different files, no incomplete-task dependency.
- Each story is independently **testable** via its Independent Test even though build order is
  dependency-driven (see sequencing note).
- Commit after each task or logical group; keep all three test tiers green before calling the spec done.
- Backing services for later specs (Redis #4, Neo4j #6, guardrails #11, dashboard #12) attach via the
  documented compose + provider seam — out of scope here.
