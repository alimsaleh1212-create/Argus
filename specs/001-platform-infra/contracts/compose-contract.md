# Contract — Docker Compose Stack & Extension Seam

**Feature**: `001-platform-infra` | Consumed by: every later spec that adds a backing service, plus
the CI smoke job and `quickstart.md`.

The foundation owns `compose.yaml` and the **services that are always present**. Later specs extend
the **same file** with their service + a matching provider (see `provider-seam.md`). This is the
"orchestration scaffold" ownership seam from the spec's Assumptions.

---

## Services owned by this spec

| Service | Image | Purpose | Exposed (local) |
|---------|-------|---------|-----------------|
| `api` | built from repo `Dockerfile` | the FastAPI app | `8000` |
| `postgres` | `pgvector/pgvector:pg16` | relational store (vector ext reserved for memory) | `5432` |
| `vault` | `hashicorp/vault` (dev mode) | secret store | `8200` |
| `minio` | `minio/minio` | object store (`eval-reports`, `incident-snapshots`) | `9000`/`9001` |

## Guarantees (the contract)

1. **One command, fresh clone ⇒ healthy** (FR-007, SC-001): after copying `.env.example`→`.env`,
   `docker compose up` brings all services to healthy with no other manual step, in < 10 min.
2. **Ordered, health-gated startup**: `api` `depends_on` postgres/vault/minio with
   `condition: service_healthy`; `api` reports healthy only when its own `/ready` passes (FR-008).
3. **Clean shutdown** (FR-010, SC-005): `docker compose down` stops everything; no orphaned
   containers/volumes left running.
4. **Deterministic, named volumes** for postgres/minio data; reset = `docker compose down -v`.
5. **Secrets seeded for dev only**: a one-shot init writes `secret/minio` into dev-mode Vault so the
   demo is one-command; production injects real secrets via real Vault (`.env`-driven, same client).

## Extension seam (how later specs add a service)

A later spec appends its service block and **does not modify** existing services. Example
(`SPEC-ingestion` adds Redis):

```yaml
# appended by SPEC-ingestion — foundation services untouched
redis:
  image: redis:7
  healthcheck:
    test: ["CMD", "redis-cli", "ping"]
    interval: 5s
    timeout: 3s
    retries: 10
# and api gains: depends_on: { redis: { condition: service_healthy } }
```

Reserved-but-not-present in v1 foundation (added by their owning specs): `redis` (#4), `neo4j` (#6),
`guardrails` sidecar (#11), and the React `dashboard` build (#12).

### Contract tests (must exist)
- **Smoke (e2e)**: `docker compose up -d` from a clean checkout ⇒ all services healthy and `/ready`
  returns 200 within the grace window; CI job is a required check (SC-004).
- **Teardown**: after `docker compose down`, no project containers remain (SC-005).
- **Fresh-clone reproducibility**: smoke runs in CI from the committed `compose.yaml` + `.env.example`
  with `uv.lock` pinned (SC-008).
