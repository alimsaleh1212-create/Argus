# Quickstart — React Operations Dashboard (#12)

How to build, run, and verify the dashboard end to end. The dashboard is **read-only except
approve/reject**; it reads existing tables and adds **no migration**.

## Prerequisites

- The full stack from #1–#10 up via `docker compose` (Postgres, Redis, Vault, MinIO, Neo4j, `api`,
  `worker`, one-shot `vault-seed` + `migrate`).
- Backend deps added with **uv**: `uv add pyjwt` (signed token). Password hashing uses stdlib
  (`hashlib.pbkdf2_hmac` + `hmac.compare_digest`) — no extra dep. SSE uses FastAPI's `StreamingResponse`
  — no extra dep.
- Frontend toolchain (isolated, **not** in the Python venv): Node 20 + npm/pnpm inside `frontend/`.
- The admin credential is seeded into **Vault** by the existing `vault-seed` one-shot (PBKDF2 hash +
  salt + JWT signing secret at `DashboardSettings.vault_path_admin`).

## Run the whole thing (turnkey)

```bash
docker compose up --build        # brings up backend + the new `frontend` service (nginx)
# Dashboard:  http://localhost:5173  (or the mapped frontend port)
# API direct: http://localhost:8000
```

The `frontend` service serves the built SPA and reverse-proxies `/auth`, `/incidents`, `/approvals`
(incl. the SSE stream) to `api` — same-origin, no CORS.

## Frontend dev loop (hot reload)

```bash
cd frontend
npm install
npm run dev          # Vite dev server; proxies API calls to http://localhost:8000
npm run test         # Vitest + React Testing Library
npm run test:e2e     # Playwright happy-path (sign-in → queue → approve)
npm run build        # production bundle (what the Docker image ships)
```

## Backend tests (three-tier, ≥80% on new code)

```bash
uv run pytest tests/unit/test_auth_token.py tests/unit/test_kpi_aggregation.py \
              tests/unit/test_queue_filter.py tests/unit/test_trace_serialization.py
uv run pytest tests/integration/test_auth_api.py tests/integration/test_incidents_api.py \
              tests/integration/test_kpis_api.py tests/integration/test_stream_sse.py \
              tests/integration/test_dashboard_redaction.py
uv run pytest tests/e2e/test_dashboard_e2e.py
```

## Verify each user story

**US1 — queue & detail (P1)**
1. `POST /auth/login` with the seeded admin creds → get a token.
2. Open the dashboard, sign in. The queue lists seeded incidents with status/severity/disposition,
   most-recent first.
3. Filter status = `awaiting_approval` → only parked incidents remain.
4. Open an incident → detail shows grounded summary, evidence, status/disposition, and the audit trail.
5. Hit any `/incidents/*` endpoint without a token → `401` (SC-007).

**US2 — approve / reject (P2)**
1. Ensure an incident is parked in `awaiting_approval` (run a destructive-plan incident through #10).
2. Open it → the approval panel shows the specific destructive actions, the rationale, and a live
   countdown to the deadline (SC-002).
3. **Approve** → pipeline resumes, actions recorded as applied, disposition → `remediated` (≤3s, SC-003).
4. Repeat with **Reject** → `rejected_by_human`, no action applied.
5. Submit a decision on an already-decided/expired approval → refused, shown as "already decided /
   expired"; no second remediation (SC-008).

**US3 — trace inspector (P2)**
1. Open a completed incident's trace → triage/enrichment/response render as a tree.
2. Each step shows tokens in/out, model, latency, status; a provider that omitted tokens shows
   **"unknown"**, not `0` (SC-005).
3. An incident with a handled error path → the errored step is marked and its recovery is visible.
4. Confirm a seeded fake secret/PII never appears unredacted anywhere (SC-004).

**US4 — KPIs (P3)**
1. Open `/kpis` → volume-over-time, disposition split, mean-time-to-disposition, and memory-hit rate all
   render as visualizations.
2. Cross-check counts against the incident records — they reconcile exactly (SC-006). Memory-hit =
   `hits / enriched` (denominator = incidents that reached enrichment).

**Live updates (FR-023)**
1. With the queue open, create/advance an incident → it appears within ~5s, no manual refresh (SC-010).
2. Kill the stream (stop/restart `api`) → "reconnecting" indicator; it reconnects and reconciles the
   queue; on-demand refetch covers the gap meanwhile.

## Milestone PRs (Constitution I — big spec commits per milestone)

1. **P1**: auth + app shell + queue/detail (+ `/auth`, `/incidents`, `/incidents/{id}`,
   `/incidents/{id}/audit`, register routers).
2. **P2**: approval panel + trace inspector (+ `/incidents/{id}/trace`, reuse `/approvals`).
3. **P3**: KPIs + SSE live stream + polish (+ `/incidents/kpis`, `/incidents/stream`).

Each PR ships with its tests green (3-tier backend + frontend) before merge.
