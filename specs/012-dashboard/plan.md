# Implementation Plan: React Operations Dashboard

**Branch**: `012-dashboard` | **Date**: 2026-06-12 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/012-dashboard/spec.md`

**Component**: #12 `SPEC-dashboard` (T1, ~1 day budget, target day 8). Depends on **#10** (approvals
endpoints, dispositions, audit log), **#2** (trace spans, redaction boundary), **#3** (LLM token
telemetry), **#5/#7** (incident statuses, resume-on-decision), **#1** (secret store, reserved
`frontend/` image slot). All done. It is the final T1 component and the project's showcase surface.

## Summary

Build the operations dashboard — the human surface of Argus. A **separate-image React SPA**
(`frontend/`, Node toolchain, built by `deploy/frontend/Dockerfile`) backed by **read-side backend
endpoints** the dashboard consumes: it lands a SOC operator on a live incident queue, opens any
incident to its grounded detail + audit trail, renders the triage→enrichment→response **trace tree**
with per-step token/latency/model/status telemetry, **approves/rejects** parked destructive
remediations (reusing #10's already-shipped `/approvals` decision flow — the dashboard never mutates
incident state itself), and shows four operational **KPIs**. It is **read-only except the
approve/reject decision**; the supervisor stays the single writer. Everything displayed is redacted at
write time (#2) — the dashboard adds no de-redaction path.

Net-new backend work is small and additive: fill the reserved `/incidents` read router (queue / detail
/ audit / trace / KPIs / SSE stream), add **admin auth** (none exists today — username+password in
Vault → short-lived signed JWT with a role attribute, enforced by a `get_current_operator`
dependency), and register the `/incidents` + `/approvals` routers behind that dependency. **No
migration** — reads hit existing tables (`incidents`, `approval_requests`, `audit_log`, `trace_spans`)
and auth is stateless. The frontend visual layer is built with the **`/ui-ux-pro-max`** skill: a
**Dark-Mode (OLED) slate** design system with a green action accent, Fira Sans / Fira Code typography,
shadcn/ui components on Vite + React + TS + Tailwind, TanStack Query/Table, and Recharts KPIs.

## Technical Context

**Language/Version**: Backend Python 3.12 (existing). Frontend TypeScript 5.x on Node 20 (new, isolated
toolchain — the second runtime image already anticipated by #1).

**Primary Dependencies**:
- *Backend (add via `uv`)*: `PyJWT` (pure-Python signed token; no native build) for the session token;
  stdlib `hashlib.pbkdf2_hmac` + `hmac.compare_digest` for the admin password check (no `bcrypt`
  native dep). SSE via FastAPI `StreamingResponse` (no new lib). Existing: FastAPI, Pydantic v2,
  async SQLAlchemy.
- *Frontend (npm/pnpm in `frontend/`)*: Vite, React 18, TypeScript, Tailwind CSS, shadcn/ui (Radix
  primitives, vendored — no runtime lock-in), TanStack Query (server state + reconnect reconcile),
  TanStack Table (dense queue), Recharts (KPI charts), React Router, Lucide icons, native
  `EventSource` for SSE. Vitest + React Testing Library for component tests; Playwright for one
  happy-path e2e.

**Storage**: No new tables, **no migration**. Reads existing Postgres tables: `incidents`,
`approval_requests`, `audit_log`, `trace_spans`. Admin credential + JWT signing secret live in **Vault**
(KV v2), seeded by the existing `vault-seed` one-shot. No session table (stateless JWT).

**Testing**: Backend `pytest` three-tier (unit / integration / e2e) ≥80% on new code. Frontend Vitest
+ RTL (component/unit) and one Playwright e2e (sign-in → queue → approve). No new **eval** gate — the
dashboard is a deterministic read surface; it extends the existing **redaction** gate with a
dashboard-view assertion (a seeded secret never appears in any `/incidents` response or rendered view).

**Target Platform**: Linux containers via `docker compose`. Frontend served as static assets by nginx
(multi-stage build), same-origin reverse-proxy to the `api` service for `/auth`, `/incidents`,
`/approvals` (avoids CORS and lets SSE stream cleanly). Browser target: modern evergreen.

**Project Type**: Web application — existing Python `backend/` + new React `frontend/` (the reserved
monorepo slot from #1).

**Performance Goals**: Queue loads < 2s for ≥200 incidents (SC-009); a new/changed incident appears in
the queue within 5s with no manual refresh (SC-010); post-decision disposition reflected within 3s
(SC-003). Observability stays off the synchronous path (Constitution VII).

**Constraints**: Read-only except approve/reject (FR-020); single `admin` role for v1, auth structured
so roles extend without reworking API or UI (FR-002); only redacted values displayed (FR-017); graceful
degradation on backend-down / no-trace / stream-drop (FR-021, FR-023).

**Scale/Scope**: Single organization, single admin operator, hundreds of incidents (demo replay). Four
dashboard surfaces (queue+detail, approval, trace, KPIs) mapped to user stories P1–P3.

## Constitution Check

*GATE: re-checked after Phase 1 design — still passing.*

- [x] **I. Spec-Driven Delivery**: this plan + spec + Phase-1 artifacts precede code. "Done" = 3-tier
      green + pushed. The frontend exceeds ~400 lines in total, so it ships as **milestone PRs**
      (Constitution I explicitly allows big specs to commit per internal milestone): (a) auth + app
      shell + queue/detail [P1]; (b) approval panel + trace inspector [P2]; (c) KPIs + SSE live stream
      + polish [P3]. Each PR is focused and independently green.
- [x] **II. Test-First, Three-Tier, Eval-Gated**: backend unit/integration/e2e + frontend Vitest/RTL +
      one Playwright e2e, planned before code, ≥80% on new code. No new LLM eval gate (deterministic
      UI); **extends the redaction gate** with a dashboard-view check. Backend tests run unchanged on
      both LLM providers (no LLM call added).
- [x] **III. Structural Security Boundaries**: the dashboard holds **no action tools** — approve/reject
      routes through #10's `/approvals/{id}/decision`, which executes only inside the re-run response
      stage; the dashboard never mutates incident state. **Redaction relied upon, not re-done**: read
      endpoints return values redacted at write time; an integration + e2e test asserts no seeded
      secret/PII appears in any response or view. Every dashboard endpoint sits behind
      `get_current_operator` (DI-enforced auth). Triage's no-action boundary is untouched.
- [x] **IV. Determinism First**: dashboard adds no LLM call and no orchestration; the supervisor remains
      the deterministic single writer. N/A-but-compliant.
- [x] **V. Human-in-the-Loop**: the dashboard **is** the human surface for the interrupt — it surfaces
      every `awaiting_approval` incident with proposed destructive actions, rationale, and deadline;
      reuses #10's config-backed policy, approval timeout/terminal state, and audit row. A
      second/expired decision is guarded (409) → surfaced as "already decided / expired"; no double
      execution (FR-012, SC-008).
- [x] **VI. Temporal Memory & Graceful Degradation**: read-only over memory outputs (no memory writes);
      the memory-hit KPI reads the enrichment signal already stored in `incidents.evidence`. The UI
      degrades gracefully — empty / error+retry / reconnecting states (FR-021, FR-023).
- [x] **VII. Production Engineering Standards**: async endpoints, DI providers (`get_current_operator`,
      `get_trace_repo`, `get_auth_service`), **Pydantic DTOs at every boundary** (`domain/dashboard.py`),
      structured logging with trace IDs, observability off-path, typed `DashboardSettings`
      (`extra="forbid"`, secrets from Vault fail at startup). `uv` governs the **Python** deps; the
      **frontend Node toolchain is the pre-blessed separate-image exception** established in #1 (D2:
      "separate images are reserved for genuinely different runtimes — the React `frontend/`"). Not a
      violation; recorded below for transparency.
- [x] **Scope & Tiers**: single admin, one authenticated console, no multi-tenancy / embeddable widget
      / live capture / LLM supervisor / 4th agent. T1 component; respects the layering contract.

**No Complexity Tracking entries required** — the only deviation (Node toolchain outside `uv`) is
pre-authorized by #1's one-image-per-runtime rule; the new auth surface is the minimum FR-002 demands.

## Project Structure

### Documentation (this feature)

```text
specs/012-dashboard/
├── plan.md              # This file
├── research.md          # Phase 0 — decisions (stack, SSE vs WS, auth, event source, design system)
├── data-model.md        # Phase 1 — read DTOs, entities, no-migration rationale, repo read methods
├── quickstart.md        # Phase 1 — run/verify the dashboard end to end
├── contracts/           # Phase 1 — API + UX contracts
│   ├── auth-api.md            # POST /auth/login → signed session token; get_current_operator
│   ├── incidents-read-api.md  # GET /incidents (+ detail/audit/trace/kpis) read endpoints
│   ├── stream-sse.md          # GET /incidents/stream — server-push (SSE) contract
│   └── frontend-ux.md         # design system (ui-ux-pro-max) + component/route contract
└── checklists/          # (existing) author checklist
```

### Source Code (repository root)

```text
backend/
├── routers/
│   ├── incidents.py        # FILL (reserved stub) — queue/detail/audit/trace/kpis + SSE stream
│   ├── auth.py             # NEW — POST /auth/login (username+password → signed JWT)
│   ├── approvals.py        # EXISTS (#10) — reused for approve/reject; now registered + auth-guarded
│   └── __init__.py         # REGISTER incidents + approvals + auth (currently commented out)
├── services/
│   ├── auth.py             # NEW — token issue/verify, PBKDF2 password check (pure logic, testable)
│   ├── kpis.py             # NEW — KPI aggregation over incidents/disposition/evidence (read-only)
│   └── dashboard_stream.py # NEW — SSE event producer (server-side queue/KPI snapshot poller)
├── repositories/
│   └── incidents.py        # ADD read methods: list_for_queue / count_for_queue / kpi aggregates
│                           #   (IncidentRepository stays the only module touching the table)
├── domain/
│   └── dashboard.py        # NEW — pure DTOs: IncidentSummary, QueuePage, IncidentDetailView,
│                           #   TraceTreeView/SpanView, AuditView, KpiSnapshot, LoginRequest,
│                           #   TokenResponse, OperatorSession
├── infra/
│   └── config.py           # ADD DashboardSettings (admin user, vault paths, token TTL, algorithm)
└── dependencies.py         # ADD get_current_operator, get_trace_repo, get_auth_service

frontend/                   # NEW — Vite + React + TS SPA (replaces the reserved README placeholder)
├── index.html
├── package.json · tsconfig.json · vite.config.ts · tailwind.config.ts · postcss.config.js
├── src/
│   ├── main.tsx · App.tsx · router.tsx
│   ├── api/                # typed client, TanStack Query hooks, EventSource SSE client
│   ├── auth/               # AuthProvider, token store, LoginPage, requireAuth guard
│   ├── components/         # ui/ (shadcn), AppShell (sidebar+topbar), StatusBadge, SeverityBadge,
│   │                       #   ConnectionIndicator, EmptyState, ErrorState
│   ├── features/
│   │   ├── queue/          # IncidentQueue (TanStack Table), columns, filters, pagination
│   │   ├── incident/       # IncidentDetail, EvidencePanel, AuditTrail
│   │   ├── approvals/      # ApprovalPanel, DecisionDialog, DeadlineCountdown
│   │   ├── trace/          # TraceInspector, SpanTree, SpanDetail, Telemetry (tokens/latency/model)
│   │   └── kpis/           # KpiDashboard, VolumeChart, DispositionSplit, MttdStat, MemoryHitStat
│   ├── lib/                # utils, formatters (render null token usage as "unknown")
│   └── styles/globals.css  # Tailwind + design tokens (slate/green, Fira fonts)
└── tests/                  # Vitest + RTL component tests; e2e/ Playwright happy path

deploy/frontend/Dockerfile  # NEW — multi-stage: node build → nginx static + reverse-proxy to api
compose.yaml                # UNCOMMENT/add the `frontend` service (slot already reserved)
```

**Structure Decision**: Web application using the existing monorepo split — Python `backend/` (fill the
reserved `incidents` router + small auth/kpis/stream additions, no restructure) and the reserved React
`frontend/` as a separate image/toolchain (the #1 second-runtime exception). The dashboard's only write
path is #10's `/approvals` decision endpoint; all other endpoints are read-only and behind admin auth.

## Complexity Tracking

> No Constitution violations requiring justification. The single deviation — the frontend Node toolchain
> living outside `uv` — is pre-authorized by Component #1 (D2: separate images for genuinely different
> runtimes; the React `frontend/` is named explicitly). Recorded in the Constitution Check above, not a
> tracked exception.
