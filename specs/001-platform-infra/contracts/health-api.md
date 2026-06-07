# Contract — Health & Readiness API

**Feature**: `001-platform-infra` | Consumed by: Docker Compose healthchecks, the CI smoke job, and
(later) `SPEC-dashboard` for a system-status indicator.

The foundation exposes exactly two HTTP endpoints. No business endpoints are added here.

---

## `GET /health` — liveness

Cheap, dependency-free. Confirms the process is up and serving.

- **200 OK** always while the process is alive.
- Body:
  ```json
  { "status": "ok" }
  ```
- MUST NOT touch Vault/Postgres/MinIO (so it stays fast and cannot flap on a slow dependency).

## `GET /ready` — readiness

Actively probes every required dependency; gates "is the stack usable yet?" (FR-008, FR-009).

- **200 OK** when **all** required dependencies are reachable.
- **503 Service Unavailable** when any required dependency is unreachable (e.g. during bring-up or an
  outage) — the process is alive but not ready.
- Body (both cases):
  ```json
  {
    "ready": true,
    "checked_at": "2026-06-06T12:00:00Z",
    "dependencies": [
      { "name": "postgres", "healthy": true,  "latency_ms": 3.1,  "detail": null },
      { "name": "vault",    "healthy": true,  "latency_ms": 5.4,  "detail": null },
      { "name": "minio",    "healthy": true,  "latency_ms": 4.8,  "detail": null }
    ]
  }
  ```
- `detail` is populated only on failure and MUST be redaction-safe: a reachability reason
  (`"connection refused"`, `"timeout after 5s"`) — **never** a DSN, token, or secret value (FR-005).
- Probe timeout per dependency = `startup.dependency_timeout_s` (default 5 s).

### Contract tests (must exist)
- `/health` returns 200 with `{"status":"ok"}` and performs **zero** dependency I/O (unit, with a spy).
- `/ready` returns 200 with all-healthy when services are up (integration, testcontainers).
- `/ready` returns 503 and `ready=false` with the offending dependency `healthy=false` when that
  service is stopped (integration — fault injection).
- No `/ready` failure body contains any value from `Settings`' `SecretStr` fields (unit + integration).

### Compose usage
```yaml
api:
  healthcheck:
    test: ["CMD", "curl", "-fsS", "http://localhost:8000/ready"]
    interval: 5s
    timeout: 6s
    retries: 12          # ~60s bring-up grace before unhealthy
postgres: { healthcheck: { test: ["CMD-SHELL", "pg_isready -U $$POSTGRES_USER"], ... } }
vault:    { healthcheck: { test: ["CMD", "vault", "status"], ... } }
minio:    { healthcheck: { test: ["CMD", "mc", "ready", "local"], ... } }
```
Service start order uses `depends_on: { <dep>: { condition: service_healthy } }`.
