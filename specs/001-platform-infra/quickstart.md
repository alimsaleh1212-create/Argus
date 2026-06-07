# Quickstart — Platform & Infrastructure Foundation

**Feature**: `001-platform-infra` | This is the fresh-clone bring-up and verification path the
foundation must satisfy (FR-007, SC-001). It is also demo moment "the stack comes up clean."

## Prerequisites (documented baseline only)

- Docker Engine + Docker Compose v2
- `uv` (for local dev / running tests outside containers)
- `git`

## 1. Clone & configure (the one manual step)

```bash
git clone <repo-url> sentinel && cd sentinel
cp .env.example .env            # the only manual setup step; defaults work for local
```

## 2. Bring the stack up (the one command)

```bash
docker compose up -d
```

Expected: `postgres`, `vault`, `minio` become healthy, then `api` becomes healthy once `/ready`
passes — all within the bring-up grace window (target < 10 min, typically < 1 min).

## 3. Verify

```bash
curl -fsS http://localhost:8000/health   # → {"status":"ok"}
curl -fsS http://localhost:8000/ready    # → 200 with every dependency healthy=true
docker compose ps                        # → all services "healthy"
```

Fail-fast demonstration (FR-003): stop Vault and restart the api — it must **refuse to boot** with a
clear error naming Vault, not start half-up:

```bash
docker compose stop vault
docker compose up -d --force-recreate api   # api exits non-zero; logs name vault as unreachable
docker compose start vault                   # api recovers to healthy on next recreate
```

## 4. Tear down

```bash
docker compose down       # clean stop, no orphans (FR-010)
docker compose down -v    # also wipe postgres/minio volumes for a true fresh start
```

## 5. Run the three test tiers locally

```bash
uv sync                              # install pinned deps (uv.lock)
uv run pytest tests/unit             # schemas, container wiring (fakes), secret-not-leaked
uv run pytest tests/integration      # boots vs real Vault/PG/MinIO (testcontainers); ready; migrate; put/get
uv run pytest tests/e2e              # compose smoke (brings the real stack up, asserts healthy)
```

## 6. Migrations (FR-015/FR-016, SC-006)

```bash
uv run alembic upgrade head          # apply baseline to an empty DB → current schema
uv run alembic downgrade base        # reverse cleanly → empty
```

## 7. Hygiene gates (FR-020/FR-021)

```bash
uv run pre-commit run --all-files    # ruff (lint+format), gitleaks, import-linter
# committing a fake secret or a lint violation is blocked before it lands
```

## Definition of done (this component)

- [ ] `docker compose up` from a fresh clone reaches healthy (smoke green in CI).
- [ ] `/health` and `/ready` behave per `contracts/health-api.md`.
- [ ] Missing required secret / unreachable Vault / unknown config key all **refuse boot** with a
      secret-free error.
- [ ] Singletons build once via the provider seam and dispose with zero leaks on shutdown.
- [ ] Alembic `upgrade head` → `downgrade base` round-trips with no drift.
- [ ] `eval_thresholds.yaml` seeded with the `smoke` gate; CI runs ruff + unit + integration +
      gitleaks + smoke and is green.
- [ ] Unit + integration + e2e all green; ≥80% coverage on new code; pushed behind focused PR(s).
