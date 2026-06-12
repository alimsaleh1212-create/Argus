---
description: "Task list — React Operations Dashboard (#12)"
---

# Tasks: React Operations Dashboard

**Input**: Design documents from `specs/012-dashboard/`
**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md),
[data-model.md](./data-model.md), [contracts/](./contracts/)

**Tests**: REQUIRED (Constitution II — Test-First, Three-Tier, Eval-Gated). Every story carries unit +
integration tests; backend e2e + one Playwright e2e cover the full surface; the redaction eval gate is
extended (no new LLM eval gate — the dashboard is a deterministic read surface).

**Organization**: Tasks are grouped by user story (US1–US4) so each story is independently
implementable and testable. Backend = Python `backend/` + `tests/`; Frontend = React `frontend/`.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no incomplete dependency)
- **[Story]**: US1–US4; Setup/Foundational/Polish carry no story label
- Exact file paths are in each task

## Milestone → PR mapping (Constitution I — commit per milestone)

- **PR-A (P1)**: Setup + Foundational + US1 — auth, app shell, queue/detail.
- **PR-B (P2)**: US2 + US3 — approve/reject + trace inspector.
- **PR-C (P3)**: US4 + Polish — KPIs, SSE live stream, redaction-gate, e2e, a11y/docs.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Backend deps + frontend toolchain + packaging slot.

- [X] T001 Add backend dependency with uv: `uv add pyjwt` (signed session token; password hashing uses stdlib PBKDF2 — no native dep). Verify `uv.lock` updated.
- [X] T002 [P] Scaffold the Vite + React 18 + TypeScript app in `frontend/` (replace the reserved `frontend/README.md` placeholder): `package.json`, `tsconfig.json`, `vite.config.ts`, `index.html`, `src/main.tsx`, `src/App.tsx`. Add React Router, TanStack Query, TanStack Table, Recharts, Lucide.
- [X] T003 [P] Configure Tailwind + design tokens per [contracts/frontend-ux.md](./contracts/frontend-ux.md): `frontend/tailwind.config.ts`, `frontend/postcss.config.js`, `frontend/src/styles/globals.css` (Dark-Mode OLED slate palette, green accent, semantic severity/status colors, Fira Sans + Fira Code imports).
- [X] T004 [P] Initialize shadcn/ui in `frontend/` (`components.json`) and vendor base primitives into `frontend/src/components/ui/` (button, card, table, dialog, badge, input, tabs, skeleton).
- [X] T005 [P] Add `deploy/frontend/Dockerfile` (multi-stage: `node:20` build → `nginx` static + reverse-proxy `/auth` `/incidents` `/approvals` to `api`) and uncomment/define the `frontend` service in `compose.yaml` (`depends_on: api`).
- [X] T006 [P] Configure frontend test tooling: Vitest + React Testing Library (`frontend/vitest.config.ts`) and Playwright (`frontend/playwright.config.ts`, `frontend/tests/e2e/`).

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Admin auth + shared read DTOs + router registration + frontend app shell. **Every
dashboard endpoint is auth-gated (FR-001), so auth blocks all stories.**

**⚠️ CRITICAL**: No user-story work begins until this phase is complete.

### Backend — auth, DTOs, wiring

- [X] T007 Add `DashboardSettings` (admin_username, vault_path_admin, token_ttl_minutes, algorithm, stream_poll_seconds; `extra="forbid"`) to `backend/infra/config.py` and register as `dashboard:` on the `Settings` aggregate. Add `secret/dashboard` to the api service `ARGUS__VAULT__REQUIRED_PATHS` in `compose.yaml`.
- [X] T008 [P] Create pure read DTOs in `backend/domain/dashboard.py` per [data-model.md](./data-model.md): `IncidentSummary`, `QueuePage`, `IncidentDetailView`, `ApprovalView`, `AuditView`, `SpanView`, `TraceTreeView`, `TelemetryView`, `KpiSnapshot`, `LoginRequest`, `TokenResponse`, `OperatorSession`, stream-event model. No outward imports (domain isolation).
- [X] T009 Implement `backend/services/auth.py`: PBKDF2-HMAC-SHA256 password verify (`hmac.compare_digest`) + HS256 JWT issue/verify (PyJWT), reading hash/salt/iterations/signing-secret from Vault. Pure, testable. (depends T007)
- [X] T010 Add `get_auth_service`, `get_current_operator` (validates bearer header **or** `token` query param → `OperatorSession`; 401 on fail), and `get_trace_repo` providers to `backend/dependencies.py`. (depends T009)
- [X] T011 Implement `backend/routers/auth.py` — `POST /auth/login` (`LoginRequest` → verify → `TokenResponse`; generic 401, constant-time). (depends T009, T008)
- [X] T012 Register `auth`, `incidents`, `approvals` routers in `backend/routers/__init__.py` (currently commented out); apply `Depends(get_current_operator)` as a router-level dependency on `/incidents` and `/approvals` (NOT on `/auth/login`). (depends T011)
- [X] T013 Seed the admin credential into Vault: add `vault kv put secret/dashboard password_hash=… salt=… iterations=… jwt_secret=…` to the `vault-seed` command in `compose.yaml`; document the dev default in `.env.example`. (depends T007)

### Backend — foundational tests

- [X] T014 [P] Unit tests: JWT issue→verify round-trip, expired token → 401, tampered signature → 401, PBKDF2 verify true/false, constant-time (no early return) in `tests/unit/test_auth_token.py`.
- [X] T015 [P] Integration tests: `POST /auth/login` happy + bad-creds 401; a protected endpoint 401 without token, 200 with valid token, 401 with expired token in `tests/integration/test_auth_api.py`.
- [X] T016 [P] Unit tests: dashboard DTO validation (`extra="forbid"`, null-token preserved) in `tests/unit/test_dashboard_dtos.py`.

### Frontend — shell & auth

- [X] T017 [P] API client + TanStack Query provider in `frontend/src/api/client.ts` + `frontend/src/api/queryClient.ts` (base URL, `Authorization: Bearer`, 401 interceptor → sign-out).
- [X] T018 [P] `AuthProvider` + token store (memory + `sessionStorage`) + `requireAuth` route guard in `frontend/src/auth/`.
- [X] T019 [P] `LoginPage` in `frontend/src/auth/LoginPage.tsx` (username/password form, error state, calls `POST /auth/login`).
- [X] T020 [P] `AppShell` (sidebar nav: Queue · KPIs · sign-out + connection indicator; top bar) + `frontend/src/router.tsx` routes (`/login`, `/queue`, `/incidents/:id`, `/incidents/:id/trace`, `/kpis`).
- [X] T021 [P] Shared UI in `frontend/src/components/`: `StatusBadge`, `SeverityBadge`, `EmptyState`, `ErrorState`, `ConnectionIndicator` (color + label/icon, never color alone).
- [X] T022 [P] Frontend component test: login → token stored → guard admits; missing token → redirected to `/login` in `frontend/tests/auth.test.tsx`.

**Checkpoint**: Auth works end to end; protected endpoints reject anonymous access (SC-007); the shell renders behind sign-in. User stories can now proceed.

---

## Phase 3: User Story 1 — Operate the live incident queue (Priority: P1) 🎯 MVP

**Goal**: Sign in → live queue with status/severity/disposition → filter/sort/paginate → open any
incident to detail (summary, evidence, disposition, audit trail).

**Independent Test**: Sign in, load the queue against seeded incidents, confirm
status/severity/disposition render, filter by `awaiting_approval`, open one incident, confirm detail
shows summary/evidence/disposition/audit (spec US1 Independent Test).

### Backend

- [X] T023 [US1] Add read methods `list_for_queue(*, view, statuses, severities, sort, limit, offset)` + `count_for_queue(...)` to `backend/repositories/incidents.py` (read-only; `IncidentRepository` stays the sole table owner; default sort `updated_at DESC`).
- [X] T024 [US1] Implement `GET /incidents` (queue → `QueuePage`), `GET /incidents/{id}` (`IncidentDetailView` incl. embedded `pending_approval` + `audit`), `GET /incidents/{id}/audit` (via `AuditRepository.list_for_incident`) in `backend/routers/incidents.py` per [contracts/incidents-read-api.md](./contracts/incidents-read-api.md). (depends T023, T012, T010)

### Backend tests

- [X] T025 [P] [US1] Unit test queue filter/sort/paginate + view (active/resolved/all) mapping in `tests/unit/test_queue_filter.py`.
- [X] T026 [P] [US1] Integration test `/incidents` queue + detail + audit, 401-without-token, large-backlog paging, and **redaction passthrough** (seeded secret absent from every response) in `tests/integration/test_incidents_api.py`.

### Frontend

- [X] T027 [P] [US1] Query hooks `useIncidentQueue` / `useIncidentDetail` / `useIncidentAudit` in `frontend/src/api/incidents.ts`.
- [X] T028 [US1] `IncidentQueue` (TanStack Table) + columns + status/severity filters + active/resolved/all toggle + pagination in `frontend/src/features/queue/`; empty state when no incidents. (depends T027)
- [X] T029 [US1] `IncidentDetail` + `EvidencePanel` + `AuditTrail` in `frontend/src/features/incident/`. (depends T027)
- [X] T030 [P] [US1] Component tests: queue render/filter/paginate/empty-state; detail render + audit in `frontend/tests/queue.test.tsx`, `frontend/tests/incident.test.tsx`.

### E2E

- [X] T031 [US1] Backend e2e: sign-in → list queue (seeded) → open detail → audit present in `tests/e2e/test_dashboard_e2e.py`.

**Checkpoint**: US1 fully functional and independently testable — the MVP console (SC-001, SC-009).

---

## Phase 4: User Story 2 — Approve / reject a parked remediation (Priority: P2)

**Goal**: Open an `awaiting_approval` incident → see proposed destructive actions + rationale +
deadline countdown → approve (resumes → `remediated`) or reject (→ `rejected_by_human`); a
second/expired decision is safely refused.

**Independent Test**: Park an incident with a known destructive plan, approve it → pipeline resumes to
`remediated` with executed actions in the audit trail; repeat with reject → `rejected_by_human` (spec
US2 Independent Test). Reuses #10's `/approvals` endpoints — **no new write path**.

### Backend

- [X] T032 [US2] Replace the hardcoded `actor = "admin"` in `backend/routers/approvals.py` with `get_current_operator().subject` for `decided_by`/audit actor; confirm the router-level auth dependency from T012 is applied.

### Backend tests

- [X] T033 [P] [US2] Integration tests (dashboard-auth path): approve → `remediated`, reject → `rejected_by_human`, 409 on already-decided/expired (no double execution), 401 without token in `tests/integration/test_approvals_dashboard.py`.

### Frontend

- [X] T034 [P] [US2] Query hooks `usePendingApprovals` + `useApprovalDecision` (`POST /approvals/{id}/decision`) in `frontend/src/api/approvals.ts`.
- [X] T035 [US2] `ApprovalPanel` + `DecisionDialog` + `DeadlineCountdown` in `frontend/src/features/approvals/`: render specific actions + rationale + live countdown **before** any decision (SC-002); disable buttons during the async call; on 409 show "already decided / expired" and disable controls (FR-012, SC-008). (depends T034)
- [X] T036 [US2] Embed `ApprovalPanel` in `IncidentDetail`; ensure the queue `awaiting_approval` filter surfaces parked incidents; reflect post-decision disposition ≤3s (SC-003). (depends T035, T029)
- [X] T037 [P] [US2] Component tests: countdown renders; buttons disable during submit; 409 → "already decided / expired"; reject path in `frontend/tests/approvals.test.tsx`.

### E2E

- [X] T038 [US2] Playwright e2e: sign-in → queue → open parked incident → approve → see `remediated`, in `frontend/tests/e2e/approve.spec.ts`. Backend e2e: extend `tests/e2e/test_dashboard_e2e.py` with reject → `rejected_by_human` and an already-decided refusal.

**Checkpoint**: US1 + US2 both work independently — the headline human-in-the-loop demo moment.

---

## Phase 5: User Story 3 — Inspect the pipeline trace & telemetry (Priority: P2)

**Goal**: Open an incident's trace → triage→enrichment→response as a navigable tree; each step shows
evidence + rationale + tokens/model/latency/status; error paths marked with recovery; all redacted.

**Independent Test**: Run one incident e2e (mocked externals), open the trace, confirm all three stages
render as a tree with per-step telemetry; a seeded sensitive value never appears unredacted (spec US3).

### Backend

- [X] T039 [US3] Implement `GET /incidents/{id}/trace` in `backend/routers/incidents.py`: resolve `correlation_id`, load `TraceRepository.get_trace_tree(...)`, serialize to `TraceTreeView`/`SpanView` + `TelemetryView` rollup (`TelemetryRecord.from_trace_tree`); preserve null tokens as `null`; empty/partial tree (no spans yet) → 200, not error. (depends T012, T010 `get_trace_repo`)

### Backend tests

- [X] T040 [P] [US3] Unit test trace-tree serialization: tree shape, null-token preserved (not 0), error span carries `error_message` in `tests/unit/test_trace_serialization.py`.
- [X] T041 [P] [US3] Integration test `/incidents/{id}/trace`: full tree, redaction passthrough, empty/partial tree for in-flight incident in `tests/integration/test_trace_api.py`.

### Frontend

- [X] T042 [P] [US3] Query hook `useTrace` in `frontend/src/api/trace.ts`.
- [X] T043 [US3] `TraceInspector` + `SpanTree` (collapsible) + `SpanDetail` + `Telemetry` in `frontend/src/features/trace/`: tokens/model/latency/status; null → "unknown" (FR-015); error span clearly marked with recovery visible (FR-016). (depends T042)
- [X] T044 [P] [US3] Component tests: tree render, "unknown" telemetry, marked error span, no-secret assertion, partial-tree graceful state in `frontend/tests/trace.test.tsx`.

**Checkpoint**: US1–US3 independently functional — the auditable-intelligence showcase (SC-004, SC-005).

---

## Phase 6: User Story 4 — Operational KPIs (Priority: P3) + Live stream

**Goal**: KPI view (alert volume over time, disposition split, mean time to disposition, memory-hit
rate) as visualizations that reconcile with records; queue + KPIs update live over server push.

**Independent Test**: With seeded incidents across dispositions/times, open the KPI view → all four
families render and counts reconcile exactly (spec US4). A new/changed incident appears in the queue
within 5s with no refresh (SC-010).

### Backend — KPIs

- [X] T045 [US4] Add KPI aggregate reads to `backend/repositories/incidents.py`: volume buckets by `created_at`, disposition/status counts, MTTD over terminal rows, enriched-count + memory-hit-count from the `evidence` JSONB (confirm the enrichment memory-hit key from `domain/enrichment.py` / #9 evidence_patch — RD6).
- [X] T046 [US4] Implement `backend/services/kpis.py` → compose `KpiSnapshot`; memory-hit rate = hits/enriched (`None` when enriched=0; denominator = incidents that reached enrichment). (depends T045)
- [X] T047 [US4] Implement `GET /incidents/kpis` in `backend/routers/incidents.py`. (depends T046)

### Backend — SSE live stream

- [X] T048 [US4] Implement `backend/services/dashboard_stream.py`: API-side snapshot poller (`dashboard.stream_poll_seconds`) emitting `snapshot` / `delta` / `heartbeat` events (read-only; no worker/supervisor change — RD4). (depends T023)
- [X] T049 [US4] Implement `GET /incidents/stream` (FastAPI `StreamingResponse`, `text/event-stream`; auth via `token` query param through `get_current_operator`; snapshot-on-connect) in `backend/routers/incidents.py` per [contracts/stream-sse.md](./contracts/stream-sse.md). (depends T048)

### Backend tests

- [X] T050 [P] [US4] Unit test KPI aggregation math + memory-hit rate edge (enriched=0 → None) in `tests/unit/test_kpi_aggregation.py`.
- [X] T051 [P] [US4] Integration test `/incidents/kpis` reconciliation against seeded records (SC-006) in `tests/integration/test_kpis_api.py`.
- [X] T052 [P] [US4] Integration test SSE: connect with valid token → initial `snapshot`; advance an incident → subsequent tick reflects it; bad/expired token → 401 in `tests/integration/test_stream_sse.py`.

### Frontend

- [X] T053 [P] [US4] `useKpis` hook + EventSource SSE client (auto-reconnect, reconcile-on-snapshot, refetch fallback while disconnected) in `frontend/src/api/kpis.ts` + `frontend/src/api/stream.ts`.
- [X] T054 [US4] `KpiDashboard` + `VolumeChart` (line/area) + `DispositionSplit` (bar/donut) + `MttdStat` + `MemoryHitStat` (Recharts) in `frontend/src/features/kpis/`. (depends T053)
- [X] T055 [US4] Wire SSE into the queue + KPI views: live updates patch the query cache; `ConnectionIndicator` shows "reconnecting" on drop (FR-023). (depends T053, T028)
- [X] T056 [P] [US4] Component tests: KPI render/reconcile; SSE drop → reconnecting + refetch fallback; reconnect → snapshot reconciles queue (no dupes) in `frontend/tests/kpis.test.tsx`, `frontend/tests/stream.test.tsx`.

**Checkpoint**: All four stories independently functional; live updates working (SC-010).

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Eval gate, full e2e, accessibility, coverage, docs, turnkey verification.

- [X] T057 [P] Extend the **redaction eval gate** with a dashboard-view assertion: a seeded fake secret/PII never appears in any `/incidents` response or rendered view, in `tests/integration/test_dashboard_redaction.py` (wired into the existing redaction gate — no new gate, SC-004).
- [X] T058 [P] Backend e2e full flow in `tests/e2e/test_dashboard_e2e.py`: sign-in → queue → detail → trace → approve → `remediated` → reject → `rejected_by_human` → already-decided refused.
- [X] T059 [P] Accessibility + responsive pass across `frontend/src/` per the ui-ux-pro-max checklist: focus rings, ≥44px touch targets, color-never-sole-signal, `prefers-reduced-motion`, Lucide SVG icons (no emoji), breakpoints 375/768/1024/1440.
- [X] T060 [P] Quality gates: ≥80% coverage on new backend code (`uv run pytest --cov`); `ruff` + `import-linter` clean; `cd frontend && npm run build` clean; `npm run test` green.
- [X] T061 [P] Update `README.md` + cross-check [quickstart.md](./quickstart.md) with dashboard run/verify instructions.
- [X] T062 Verify turnkey `docker compose up --build` brings up the `frontend` service and the console end to end; rehearse SC-003 (≤3s post-decision), SC-009 (<2s queue for 200), SC-010 (≤5s live update).

---

## Dependencies & Execution Order

**Phase order**: Setup (1) → Foundational (2) → US1 (3) → US2 (4) → US3 (5) → US4 (6) → Polish (7).

- **Foundational (Phase 2) blocks everything** — auth gates every dashboard endpoint (FR-001); DTOs +
  router registration + app shell are shared by all stories.
- **US1 (P1)** depends only on Foundational → the MVP.
- **US2 (P2)** reuses #10's `/approvals` (already built); depends on Foundational (auth actor) + US1
  detail surface for embedding (T036).
- **US3 (P2)** depends on Foundational + `get_trace_repo`; independent of US2.
- **US4 (P3)** depends on Foundational + US1 queue read (T023 for the stream poller) + US1 queue UI
  (T055 wiring); KPIs independent of US2/US3.
- **Polish (Phase 7)** depends on all stories.

**Story independence**: US2, US3, US4 can each be built/tested without the others once Foundational +
US1 land. US1 is the only hard prerequisite (it provides the queue/detail surface + the queue read
method the stream reuses).

## Parallel Execution Examples

- **Setup**: T002, T003, T004, T005, T006 run in parallel (distinct files); T001 (backend dep) parallel to all.
- **Foundational backend vs frontend**: T008/T014/T016 (DTOs/tests) ∥ T017–T022 (frontend shell) while T007→T009→T010→T011→T012 run as the auth chain.
- **US1**: T025, T027, T030 [P] alongside T023→T024 (backend) and T028/T029 (frontend, after T027).
- **US3**: T040, T041, T042, T044 [P]; T039 (backend) ∥ T043 (frontend, after T042).
- **US4**: KPI chain (T045→T046→T047) ∥ SSE chain (T048→T049); tests T050/T051/T052/T056 [P].
- **Polish**: T057–T061 all [P]; T062 last.

## Implementation Strategy

- **MVP = Phase 1 + Phase 2 + Phase 3 (US1)** → a usable authenticated console (PR-A). Stop here and the
  dashboard is a complete, honest increment.
- **PR-B** adds US2 + US3 (the human-in-the-loop demo moment + the trace showcase).
- **PR-C** adds US4 + Polish (KPIs, live stream, eval gate, e2e, a11y) → the full #12 deliverable.
- Each PR ships with its three-tier tests green before merge (Constitution I/II).

---

## Summary

- **Total tasks**: 62
- **Setup**: 6 (T001–T006) · **Foundational**: 16 (T007–T022) · **US1**: 9 (T023–T031) ·
  **US2**: 7 (T032–T038) · **US3**: 6 (T039–T044) · **US4**: 12 (T045–T056) · **Polish**: 6 (T057–T062)
- **Suggested MVP**: US1 (queue + detail behind auth).
- **Parallel opportunities**: backend ∥ frontend throughout; ~30 tasks marked [P].
