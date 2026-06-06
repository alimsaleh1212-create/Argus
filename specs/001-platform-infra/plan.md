# Implementation Plan: Platform & Infrastructure Foundation

**Branch**: `001-platform-infra` | **Date**: 2026-06-06 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/001-platform-infra/spec.md`

## Summary

Stand up the runnable, fail-fast backend foundation that every later Sentinel component plugs into:
a Docker Compose stack, a single typed configuration object, Vault-resolved secrets that refuse to
boot when missing, a MinIO object store, a versioned Postgres (pgvector) relational store via async
Alembic, a layered `app/` skeleton, and a **lifespan-managed singleton container with a registration
seam** so later specs attach their own singletons without editing the foundation. The component is
"done" when unit + integration + e2e/smoke tests are green in CI and a fresh-clone `docker compose up`
reaches a healthy state. It contains **no incident/business logic** — it is the spine that keeps the
system in a continuously valid, demonstrable state from day one and the place where the project's
production engineering standards (async, DI, Pydantic, `extra="forbid"`, fail-fast secrets, `uv`,
`ruff`/`gitleaks`/pre-commit) are first established.

## Technical Context

**Language/Version**: Python 3.12 (pinned `requires-python = ">=3.12,<3.13"`); managed with `uv`.

**Primary Dependencies**: FastAPI + uvicorn (async web + native `Depends()` DI + lifespan);
`pydantic` v2 + `pydantic-settings` (typed config, `extra="forbid"`, `SecretStr`); SQLAlchemy 2.0
async + `asyncpg`; Alembic (async migrations); `httpx` (async HTTP, also used for the Vault KV API);
`aioboto3` (async S3 client for MinIO); `tenacity` (bounded retry on transient startup connectivity).
Dev/CI: `ruff` (lint+format), `gitleaks`, `pre-commit`, `pytest` + `pytest-asyncio`,
`testcontainers` (hermetic integration tests). `structlog` is introduced here only as the logging
seam; full tracing/redaction is owned by `SPEC-observability` (#2).

**Storage**: PostgreSQL 16 via the `pgvector/pgvector:pg16` image (vector extension reserved for
`SPEC-memory`); MinIO (S3-compatible object store; buckets `eval-reports`, `incident-snapshots`);
HashiCorp Vault (dev mode in compose) as the secret store. Redis / Neo4j / guardrails sidecar are
**added to the same compose file by their owning specs** via the documented seam — not here.

**Testing**: `pytest` + `pytest-asyncio` (`asyncio_mode=auto`). Unit = Settings validation,
container wiring with fakes, secret-never-in-error checks (no live services). Integration = app boots
against real Vault/Postgres/MinIO spun up by `testcontainers`; `/ready` healthy; Alembic
upgrade→downgrade; MinIO put/get; fail-fast when Vault is unreachable. e2e/smoke = `docker compose up`
from a clean checkout reaches healthy, asserted by a CI smoke job.

**Target Platform**: Linux containers orchestrated by Docker Compose v2 on a single host
(developer machine / CI runner). No cloud, cluster, or multi-host orchestration in v1.

**Project Type**: Backend web service + infrastructure scaffold (the React dashboard is a separate
later component, `SPEC-dashboard` #12; this plan only reserves its place in the compose stack).

**Performance Goals**: Fresh-clone bring-up to healthy in **< 10 min** on a typical dev machine
(SC-001); fail-fast startup decision in **< 5 s** once dependencies are reachable/declared missing;
foundation adds no measurable latency to the (future) synchronous incident path.

**Constraints**: Single organization / single tenant; local/demo deployment; async all the way down;
all shared state obtained via DI (no module globals); required secrets fail at startup; no secret
value ever appears in startup/runtime error output.

**Scale/Scope**: Demo-scale single-SOC workload; the foundation only needs to reliably host the
later pipeline and survive repeated start/stop cycles with zero leaked connections.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design — still passing.*

Derived from `.specify/memory/constitution.md` (v1.0.0).

- [x] **I. Spec-Driven Delivery**: `spec.md` precedes code; "done" = unit + integration + e2e green
      in CI and pushed. This foundation carries **internal milestones** so PRs stay ≤ ~400 lines:
      (a) compose + config + fail-fast boot; (b) lifespan/DI container + health/ready + Alembic
      baseline; (c) pre-commit + CI + `eval_thresholds.yaml` placeholder + smoke job. Each is a
      focused PR.
- [x] **II. Test-First, Three-Tier, Eval-Gated**: three tiers planned (unit/integration/e2e+smoke),
      green daily, ≥80% on new code. `eval_thresholds.yaml` is **seeded on day 1** with a provider-
      agnostic `smoke` gate so CI gates from the start; the full eval harness is `SPEC-eval` (#13).
      *Both-providers* requirement is N/A here (no LLM call) and is satisfied structurally by keeping
      the smoke gate provider-independent.
- [x] **III. Structural Security Boundaries**: the DI/lifespan container built here is precisely the
      mechanism that later enforces "triage holds no action tools." This spec contributes the
      security-relevant guarantees it owns: `SecretStr` config, **no secret value in any error output**
      (FR-005, unit-tested), `.env` git-ignored + `.env.example` committed, `gitleaks` in pre-commit.
      Full redaction-before-write is `SPEC-observability` (#2), which attaches via this seam.
- [x] **IV. Determinism First**: N/A — no supervisor/agents/LLM in this component. The deterministic
      substrate (config, health, migrations) is what determinism is later built on.
- [x] **V. Human-in-the-Loop**: N/A — no remediation actions in this component.
- [x] **VI. Temporal Memory & Graceful Degradation**: this spec provisions the **fallback substrate**
      itself — Postgres (pgvector image) + the relational store + Alembic — so the documented
      Graphiti→`valid_from`/`valid_to` degradation has a home. Neo4j/Graphiti attach later via the seam.
- [x] **VII. Production Engineering Standards**: this is the component that **establishes** them —
      async I/O, FastAPI `Depends()` DI, lifespan singletons, Pydantic at every boundary, typed
      `pydantic-settings` (`extra="forbid"`, secrets fail at startup, Vault refuses-to-boot), `uv`,
      pinned deps, `ruff`+`gitleaks`+pre-commit, Conventional Commits, `feature/` branches.
- [x] **Scope & Tiers**: strictly v1, T1; builds none of the out-of-scope items (no ML detector,
      multi-tenancy, embeddable widget, live capture, LLM supervisor, 4th agent); is the root of the
      layering contract (everything depends on it).

**Result: PASS — no violations.** Complexity Tracking table intentionally empty. Non-obvious choices
(async Vault-over-`httpx` vs `hvac`; `aioboto3` vs the sync `minio` SDK; `testcontainers` for
integration; Python 3.12 pin) are recorded in [research.md](./research.md) and will be carried into
`DECISIONS.md` at implementation.

## Project Structure

### Documentation (this feature)

```text
specs/001-platform-infra/
├── plan.md              # This file (/speckit-plan output)
├── research.md          # Phase 0 — decisions & rationale
├── data-model.md        # Phase 1 — config/entity model & state
├── quickstart.md        # Phase 1 — fresh-clone bring-up & verification
├── contracts/           # Phase 1 — health endpoints, provider seam, settings, compose
│   ├── health-api.md
│   ├── provider-seam.md
│   ├── settings-schema.md
│   └── compose-contract.md
├── checklists/
│   └── requirements.md  # (already created by /speckit-specify)
└── tasks.md             # Phase 2 — created by /speckit-tasks (NOT here)
```

### Source Code (repository root)

```text
compose.yaml                  # Docker Compose v2 — api, postgres(pgvector), vault(dev), minio
                              #   (Redis/Neo4j/guardrails appended later via the seam)
.env.example                  # committed; documents every required/optional setting (no values)
pyproject.toml                # uv project; requires-python; ruff config; deps
uv.lock                       # pinned, committed
.pre-commit-config.yaml       # ruff (lint+format), gitleaks, end-of-file/trailing-whitespace
eval_thresholds.yaml          # seeded day 1 with the provider-agnostic `smoke` gate placeholder
alembic.ini
.github/workflows/ci.yml      # uv install → ruff → pytest(unit+integration) → gitleaks → smoke

app/
├── main.py                   # FastAPI app factory + lifespan wiring; mounts /health, /ready
├── api/                      # interface layer (health/ready router; later: incidents, approvals)
│   └── health.py
├── services/                 # use-case orchestration (empty placeholder for later specs)
├── agents/                   # placeholder (triage/enrichment/response attach later)
├── repositories/             # data access (empty placeholder)
├── domain/                   # pure types/enums (no outward deps): HealthStatus, etc.
└── infra/                    # the foundation lives here
    ├── config.py             # Settings (pydantic-settings, extra="forbid", SecretStr)
    ├── container.py          # AppContainer + Provider protocol + registry (the seam)
    ├── lifespan.py           # builds/disposes singletons via registered providers
    ├── vault.py              # async VaultClient (httpx) — startup secret resolution
    ├── blob.py               # async MinIO/S3 client (aioboto3) + bucket bootstrap
    ├── db.py                 # async SQLAlchemy engine/session factory provider
    └── health.py             # readiness probes for vault/postgres/minio

migrations/                   # Alembic (async env.py) — baseline migration committed
└── versions/

tests/
├── unit/                     # Settings validation, container wiring (fakes), secret-not-leaked
├── integration/              # boot vs real Vault/PG/MinIO (testcontainers); ready; migrate; put/get
└── e2e/                      # compose smoke: fresh-up reaches healthy
```

**Structure Decision**: A single backend service in a layered `app/` package (api / services /
agents / repositories / domain / infra) per the brief's hygiene standard and FR-018. Import direction
is **inward-only** (`api → services → repositories → infra`; `domain` depends on nothing), enforced in
CI via a `ruff`/import-linter rule (FR-018). The foundation's own code concentrates in `app/infra`;
the other layers ship as thin, documented placeholders so later specs add files without restructuring.
The compose file and `app/infra` together own the orchestration scaffold + Vault + MinIO + relational
store + config + lifecycle; all other backing services and singletons attach through the **provider
seam** (`app/infra/container.py`), satisfying the spec's ownership-seam assumption.

## Complexity Tracking

> No constitution violations — table intentionally empty.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| —         | —          | —                                   |
