# Contract — Auth API (`routers/auth.py`) + `get_current_operator`

New in #12 (no auth exists today). Realizes FR-001/002/003 + the spec's auth clarification: a single
`admin` signs in with a username + password held in Vault; the backend verifies it and issues a
**short-lived signed session token** carrying an explicit `role`, sent on every dashboard call.
Stateless — **no session table, no migration** (RD2/RD7).

---

## `POST /auth/login`

Exchange admin credentials for a session token.

**Body** (`LoginRequest`, `extra="forbid"`):
```json
{ "username": "admin", "password": "<secret>" }
```

**Flow**:
1. Load the stored admin record from Vault (KV v2): PBKDF2-HMAC-SHA256 hash + salt + iterations, and a
   separate JWT signing secret. (Seeded by the existing `vault-seed` one-shot.)
2. Verify with `hmac.compare_digest(pbkdf2(password, salt, iters), stored_hash)` (constant-time).
3. On success, mint an **HS256 JWT**: `{ "sub": "admin", "role": "admin", "iat": …, "exp": … }`,
   `exp = now + token_ttl_minutes` (typed config, default 60).
4. Return the token.

**200 →** (`TokenResponse`)
```json
{ "access_token": "<jwt>", "token_type": "bearer", "expires_in": 3600, "role": "admin" }
```

**Errors**: `401` (bad username/password — generic message, no user enumeration); `422` (malformed
body). Verification time is constant regardless of which field is wrong.

---

## `get_current_operator` (FastAPI dependency)

Guards **every** dashboard endpoint (`/incidents/*`, `/approvals*`) and the SSE stream.

- Reads the bearer token from `Authorization: Bearer <jwt>` (REST) or the `token` query param (SSE only
  — `EventSource` can't set headers; see `stream-sse.md`).
- Validates signature (signing secret from Vault) + `exp`. On failure → `401`, no body.
- On success returns `OperatorSession { subject, role, expires_at }`.
- The returned `subject` is the **actor** for approve/reject (`decided_by`) and audit rows — replaces
  the hardcoded `"admin"` literal currently in `approvals.py`.

**Extensibility (FR-002)**: the explicit `role` claim means future roles are added by asserting
`role in {…}` inside specific endpoints — **no signature change, no UI rework, no API restructure**. v1
ships the single `admin` role only.

---

## Config (`DashboardSettings` in `infra/config.py`, `extra="forbid"`)

- `admin_username: str` (default `"admin"`)
- `vault_path_admin: str` — KV v2 path holding `{ password_hash, salt, iterations, jwt_secret }`
- `token_ttl_minutes: int` (default 60, `gt=0`)
- `algorithm: str` (default `"HS256"`)
- `stream_poll_seconds: float` (default 2.0 — used by the SSE producer, RD4)

Required Vault secret resolves at startup; if the path is unreachable the app fails to boot (Vault
refuses to boot if unreachable — Constitution VII). Added to the `Settings` aggregate as
`dashboard: DashboardSettings`.

---

## Dependencies / wiring

- New providers in `dependencies.py`: `get_auth_service` (token issue/verify + password check; pure
  logic in `services/auth.py`), `get_current_operator`, `get_trace_repo`.
- `routers/__init__.py` registers `auth.router`, `incidents.router`, `approvals.router`. The
  `/incidents/*` and `/approvals*` routers apply `Depends(get_current_operator)` (router-level
  dependency); `/auth/login` is unauthenticated by design.

## Tests

- **unit**: token issue→verify round-trip; expired token → `401`; tampered signature → `401`; PBKDF2
  verify true/false; constant-time path (no early return on username mismatch).
- **integration**: `POST /auth/login` happy + bad-creds `401`; a protected endpoint returns `401`
  without a token and `200` with a valid one; expired token rejected.
- **e2e**: sign-in yields a token that unlocks the queue; stale token mid-session → next call `401` →
  SPA routes to sign-in (SC-007, FR-003).
