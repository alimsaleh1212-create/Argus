# Phase 1 Data Model — Platform & Infrastructure Foundation

**Feature**: `001-platform-infra` | **Date**: 2026-06-06

This component holds **no incident/business data**. Its "model" is the configuration object, the
runtime singleton container, the health/readiness types, and the (near-empty) baseline relational
schema that proves the migration pipeline. Entities below map 1:1 to the spec's Key Entities.

---

## 1. `Settings` (configuration object) — `app/infra/config.py`

A single typed `pydantic-settings` object, `model_config = SettingsConfigDict(extra="forbid",
env_nested_delimiter="__")`. Loaded once at startup; unknown keys → `ValidationError` → non-zero exit
(FR-001, FR-002). Required secrets are `SecretStr`; their `repr`/`str` is masked so they cannot leak
in error output (FR-005, SC-009).

| Field (nested) | Type | Required | Notes |
|----------------|------|----------|-------|
| `app.env` | `Literal["local","ci","prod"]` | yes | selects defaults; demo = `local` |
| `app.log_level` | `Literal["DEBUG","INFO","WARNING","ERROR"]` | no (default `INFO`) | logging seam |
| `vault.addr` | `HttpUrl` | yes | unreachable → refuse boot (FR-003) |
| `vault.token` | `SecretStr` | yes | dev-mode root token in `local` |
| `vault.kv_mount` | `str` | no (default `secret`) | KV v2 mount |
| `vault.required_paths` | `list[str]` | yes | secret paths probed at startup |
| `postgres.dsn` | `PostgresDsn` | yes | async DSN (`postgresql+asyncpg://…`) |
| `postgres.pool_size` | `int` (1–50) | no (default `5`) | bounded |
| `minio.endpoint_url` | `HttpUrl` | yes | S3 endpoint |
| `minio.access_key` | `SecretStr` | yes | resolved value |
| `minio.secret_key` | `SecretStr` | yes | resolved value |
| `minio.buckets` | `list[str]` | no (default `["eval-reports","incident-snapshots"]`) | created if absent |
| `startup.dependency_timeout_s` | `float` (>0) | no (default `5.0`) | fail-fast budget (SC perf) |
| `startup.connect_retries` | `int` (0–10) | no (default `5`) | `tenacity` on transient connect only |

**Validation rules**
- Unknown/extra env var under a known prefix → reject at startup (FR-002).
- Every field in `vault.required_paths` must resolve; first failure aborts boot with the path name
  (never the value) in the message (FR-004/FR-005).
- `SecretStr` everywhere a value is sensitive; `Settings.__repr__` never renders secret values.

**State**: immutable after load (frozen). Rebuilt only on process restart.

---

## 2. `Secret` (resolved at startup, not persisted)

A logical entity, not a stored row. Resolved from Vault KV v2 at startup into in-memory `SecretStr`
holders inside `Settings`/clients.

| Attribute | Type | Notes |
|-----------|------|-------|
| `path` | `str` | Vault KV path (e.g. `secret/minio`) |
| `required` | `bool` | required → missing is fatal; optional → tolerated |
| `value` | `SecretStr` | masked; never logged, never in error output (FR-005) |

**Lifecycle**: resolved once at boot → held in memory for process lifetime → discarded on shutdown.
Never written to disk, logs, traces, or the object store.

---

## 3. `Provider` & `AppContainer` (singleton lifecycle) — `app/infra/container.py`

The runtime registry of long-lived shared resources (FR-011–FR-014).

**`Provider`** (protocol): `name: str` + an async context manager `build(settings) -> AsyncIterator[T]`
that yields one constructed resource and disposes it on exit.

**Provider registry**: an ordered list of `Provider`s. The foundation registers, in order:
`db_engine` → `vault_client` → `blob_client`. Later specs **append** (`redis`, `neo4j`, `llm`,
`guardrails`) without editing existing code (FR-014).

**`AppContainer`**: the built result — a typed object with one attribute per provider
(`container.db_engine`, `container.vault_client`, `container.blob_client`, …), held on `app.state` and
surfaced via `Depends()` providers (FR-012). Substitutable with fakes in tests (FR-013).

| Attribute | Built by | Disposed |
|-----------|----------|----------|
| `db_engine` | async SQLAlchemy `create_async_engine` | `engine.dispose()` |
| `session_factory` | `async_sessionmaker(db_engine)` | (with engine) |
| `vault_client` | `VaultClient(httpx.AsyncClient)` | `aclose()` |
| `blob_client` | `aioboto3` S3 client session | client `__aexit__` |

**State transitions** (process lifecycle):
```
created → building (enter providers in order)
        → ready    (all built; /ready can pass)
        → disposing (exit providers in reverse order)
        → disposed (zero leaked connections — SC-005)
A build failure short-circuits → disposing(partial) → process exits non-zero (no half-up — SC-003).
```

---

## 4. `HealthStatus` / `ReadinessReport` (domain types) — `app/domain/health.py`

Pure types, no outward dependencies.

| Type | Fields | Notes |
|------|--------|-------|
| `DependencyStatus` | `name: str`, `healthy: bool`, `detail: str \| None`, `latency_ms: float \| None` | per-dependency probe result |
| `ReadinessReport` | `ready: bool`, `dependencies: list[DependencyStatus]`, `checked_at: datetime` | `ready = all(d.healthy)` |
| `Liveness` | `status: Literal["ok"]` | cheap, no dependency probing |

`detail` is redaction-safe — only names and reachability, never secret values or raw connection
strings (FR-005).

---

## 5. Baseline relational schema (migration pipeline proof) — `migrations/versions/`

A deliberately minimal schema so FR-015/FR-016 and SC-006 are exercised without inventing
business tables (those belong to later specs).

**Table `schema_marker`** (single-row infra/version marker)

| Column | Type | Notes |
|--------|------|-------|
| `id` | `int` PK | always `1` |
| `app_version` | `text` | foundation version string |
| `initialized_at` | `timestamptz` (default `now()`) | first-boot marker |

- Created by the **baseline** Alembic revision; that revision's `downgrade()` drops it cleanly so
  `upgrade → downgrade → empty` round-trips (SC-006).
- The `pgvector` extension is **available in the image** but not enabled here — enabling it and adding
  vector tables is `SPEC-memory`'s migration, layered on top of this baseline.

---

## Entity → Requirement traceability

| Entity | Satisfies |
|--------|-----------|
| `Settings` | FR-001, FR-002, FR-004, FR-005, FR-006 |
| `Secret` | FR-003, FR-004, FR-005 |
| `Provider` / `AppContainer` | FR-011, FR-012, FR-013, FR-014, FR-017 (blob client) |
| `HealthStatus` / `ReadinessReport` | FR-008, FR-009 |
| Baseline schema | FR-015, FR-016 |
