# Argus — AI-driven SOAR Platform

**Component #1: Platform & Infrastructure Foundation**

One-command bring-up of the full backing stack (Vault, Postgres pgvector, MinIO) with fail-fast
config, async dependency injection via the provider seam, versioned Alembic migrations, and
enforced code hygiene gates.

---

## Quick start (fresh clone)

```bash
git clone <repo-url> argus && cd argus
cp .env.example .env            # only manual step; defaults work for local dev
docker compose up -d
```

Once healthy (usually < 1 min):

```bash
curl http://localhost:8000/health   # → {"status":"ok"}
curl http://localhost:8000/ready    # → 200, all deps healthy
```

## Prerequisites

- Docker Engine + Docker Compose v2
- `uv` (Python dependency manager — `curl -LsSf https://astral.sh/uv/install.sh | sh`)
- `git`

## Running tests

```bash
uv sync                              # install pinned deps
uv run pytest tests/unit             # unit tests (fast, no services)
uv run pytest tests/integration      # integration tests (testcontainers)
uv run pytest tests/e2e              # e2e smoke (requires Docker)
```

On `docker compose up`, a one-shot `migrate` service applies migrations and a
one-shot `vault-seed` service seeds dev secrets *before* the API starts — no
manual step.

## Migrations

```bash
make migrate                         # alembic upgrade head  (config/alembic.ini)
make downgrade                       # alembic downgrade base
# or directly:
uv run alembic -c config/alembic.ini upgrade head
```

## Hygiene gates

```bash
make lint                            # ruff check + import-linter
uv run pre-commit run --all-files    # ruff (lint+format), gitleaks, import-linter
```

All gates run automatically on every commit via pre-commit hooks.

## Fail-fast demonstration

```bash
docker compose stop vault
docker compose up -d --force-recreate api   # api exits non-zero; logs name vault
docker compose start vault
docker compose up -d --force-recreate api   # api recovers
```

## Tear down

```bash
docker compose down       # clean stop
docker compose down -v    # also wipe postgres/minio volumes
```

## Extending the stack (for later specs)

Later components attach new services and providers **without modifying this foundation**:

1. **Append a service** to `compose.yaml` (see the reserved block) with a `healthcheck`.
2. **Implement a `Provider`** (see `backend/infra/container.py`) and call `register_provider()` at import time.
3. The lifespan builds it in registration order and disposes it in reverse — zero changes to `lifespan.py`.

Reserved seams already stubbed: `infra/cache.py` (Redis #4), `infra/queue.py` (#4/#5),
`infra/memory.py` (Neo4j #6), `infra/llm.py` (#3), `infra/redaction.py` (#2), `infra/guardrails.py` (#11),
plus reserved compose slots for redis/neo4j/guardrails/frontend.

## Architecture

```
argus/
├── backend/                    # the Python package (one image; api + worker + migrate)
│   ├── main.py                 # thin app factory
│   ├── worker.py               # reserved: queue consumer (#4/#5)
│   ├── dependencies.py         # shared Depends() providers
│   ├── routers/                # thin HTTP layer (health live; ingest/incidents/approvals reserved)
│   ├── services/               # use-case orchestration
│   ├── agents/                 # triage / enrichment / response (reserved)
│   ├── repositories/           # data access
│   ├── domain/                 # pure types/enums (no outward deps)
│   ├── infra/                  # config, container, lifespan, vault, db, blob, health, logging + seams
│   └── db/migrations/          # Alembic (async env + baseline)
├── frontend/                   # reserved: React dashboard (#12)
├── deploy/<svc>/Dockerfile     # one Dockerfile per built image (api; frontend/guardrails reserved)
├── config/                     # alembic.ini, eval_thresholds.yaml
├── compose.yaml  pyproject.toml  uv.lock  Makefile
└── tests/  specs/  docs/
```

Import direction is **inward-only** (`routers → services → agents → repositories → infra`;
`domain` isolated), enforced in CI via `import-linter`. One backend image runs as several
containers (api / worker / migrate) — different commands, same venv.

See `specs/001-platform-infra/` for the full specification and decision log.
