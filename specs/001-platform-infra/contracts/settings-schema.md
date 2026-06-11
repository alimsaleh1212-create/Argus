# Contract — Configuration & Secrets Schema

**Feature**: `001-platform-infra` | Consumed by: every component (each extends `Settings` with its own
nested section through the same `extra="forbid"` object).

This contract fixes how configuration and secrets behave so the system **fails fast and loud** rather
than booting broken (FR-001–FR-006, Constitution VII).

---

## Loading rules

- One typed `pydantic-settings` object (`Settings`), built **once** at startup.
- `model_config = SettingsConfigDict(extra="forbid", env_nested_delimiter="__", env_file=".env")`.
- Env var naming: `ARGUS__<SECTION>__<FIELD>` (e.g. `ARGUS__VAULT__ADDR`).
- **Unknown/extra key under any known section ⇒ `ValidationError` ⇒ process exits non-zero** (FR-002).
- Sensitive fields are `SecretStr`; `Settings.__repr__`/log rendering never emits their values (FR-005).

## Secret resolution (startup)

1. Validate non-secret config from env/`.env`.
2. Connect to Vault (`vault.addr`, `vault.token`); **unreachable ⇒ refuse boot** (FR-003).
3. For each path in `vault.required_paths`, fetch from KV v2; **missing/malformed required ⇒ refuse
   boot**, error names the **path** only (FR-004/FR-005).
4. Optional secrets may be absent without aborting (FR-006).

## Error-output guarantee

Any startup failure prints an actionable message identifying the offending **key or Vault path** and
MUST NOT contain any secret value (FR-005, SC-009). Verified by a unit test that asserts no `SecretStr`
value appears in the captured stderr/exception text across all failure cases.

## `.env.example` (committed; no real values)

```dotenv
# --- app ---
ARGUS__APP__ENV=local
ARGUS__APP__LOG_LEVEL=INFO

# --- vault (dev mode in compose) ---
ARGUS__VAULT__ADDR=http://vault:8200
ARGUS__VAULT__TOKEN=dev-root-token          # dev only; real deploys inject a real token
ARGUS__VAULT__KV_MOUNT=secret
ARGUS__VAULT__REQUIRED_PATHS=["secret/minio"]

# --- postgres ---
ARGUS__POSTGRES__DSN=postgresql+asyncpg://sentinel:sentinel@postgres:5432/sentinel

# --- minio (access/secret are RESOLVED FROM VAULT, not set here) ---
ARGUS__MINIO__ENDPOINT_URL=http://minio:9000
ARGUS__MINIO__BUCKETS=["eval-reports","incident-snapshots"]

# --- startup budgets ---
ARGUS__STARTUP__DEPENDENCY_TIMEOUT_S=5.0
ARGUS__STARTUP__CONNECT_RETRIES=5
```

- `.env.example` is committed and exhaustive (every required key documented); `.env` is git-ignored.
- A missing `.env` on a fresh clone yields a fail-fast error naming the missing required keys and
  pointing at `.env.example` (spec edge case).

### Contract tests (must exist)
- Valid full config ⇒ boots; `Settings` is frozen/immutable after load (unit).
- Unknown extra env key ⇒ `ValidationError`, non-zero exit (unit).
- Missing required secret / unreachable Vault ⇒ refuse boot, error names the path, **no value leaked**
  (integration + unit).
- `.env.example` contains every required field that `Settings` declares (unit — introspection check).
