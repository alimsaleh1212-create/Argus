# Phase 0 — Research & Decisions: React Operations Dashboard (#12)

All unknowns are resolved here; none required user clarification beyond the four spec clarifications
(auth, queue default, memory-hit definition, live-update mechanism), which are already encoded in the
spec. The rest are standard engineering choices recorded with rationale + rejected alternatives.

---

## RD1 — Frontend stack: Vite + React + TS + Tailwind + shadcn/ui + TanStack + Recharts

**Decision**: Vite + React 18 + TypeScript + Tailwind CSS, **shadcn/ui** components (Radix primitives,
vendored into the repo), **TanStack Query** for server state, **TanStack Table** for the dense incident
queue, **Recharts** for KPI charts, **React Router** for routing, **Lucide** for SVG icons, native
`EventSource` for the live stream.

**Rationale**:
- Vite gives fast dev + a clean static build for the nginx image — no SSR needed for a single-page
  internal console (the spec rules out an embeddable widget / public surface).
- shadcn/ui is **owned code, not a runtime dependency** — components are copied in and themable with
  Tailwind tokens, so there's no version lock-in and the design system is fully under our control. The
  ui-ux-pro-max skill integrates with the shadcn MCP for component examples.
- TanStack Query is the right fit for FR-023: it caches the queue, refetches in the background, and
  gives the **reconcile-on-reconnect** + **on-demand-refresh fallback** behavior for free — SSE events
  invalidate/patch the query cache; if the stream drops, polling refetch covers the gap.
- TanStack Table covers FR-005 (filter/sort/paginate a dense table) without hand-rolling.
- Recharts is React-native and composable, matching the Tailwind dark theme (see RD9).

**Alternatives rejected**: Next.js (SSR/routing/server components are overkill for one authed SPA and
complicate the static-image + reverse-proxy story); Material UI / Ant Design (heavier, opinionated
aesthetics that fight a custom security-tooling look); raw `fetch` + `useEffect` (re-implements
caching/reconnect that TanStack Query already solves); a charting heavyweight like Plotly (bundle cost
for four simple chart families).

## RD2 — Authentication: username+password (Vault) → short-lived signed JWT with a role attribute

**Decision** (realizes the spec's auth clarification + FR-002/003): A single `admin` credential lives in
**Vault** as a PBKDF2-HMAC-SHA256 hash (+ salt) plus a separate JWT **signing secret**, seeded by the
existing `vault-seed` one-shot. `POST /auth/login` verifies the password with
`hmac.compare_digest(pbkdf2(...), stored_hash)` and, on success, issues an **HS256 JWT** carrying
`sub=admin`, `role=admin`, `iat`, and a bounded `exp` (default 60 min, typed config). Every dashboard
endpoint depends on `get_current_operator`, which validates the signature + expiry and returns an
`OperatorSession`; unauthenticated/expired requests get `401` → the SPA routes to sign-in (no silent
failure). The token's explicit `role` attribute is what lets more roles be added later **without
reworking the API or UI** (FR-002) — endpoints can later assert `role in {...}` with no signature
change.

**Rationale**: stateless JWT means **no session table and no migration** — the supervisor's
single-writer guarantee and the "incidents table unchanged" invariant are preserved. PBKDF2 + PyJWT are
both pure-Python (PyJWT) or stdlib (PBKDF2) — **no native build** (avoids a `bcrypt` wheel on the image)
while remaining a defensible password KDF for a single admin credential.

**Token transport**: `Authorization: Bearer <jwt>` on REST calls (matches existing API conventions);
the SPA holds the token in memory + `sessionStorage` (cleared on tab close, short TTL limits exposure).
For the SSE stream, native `EventSource` can't set headers, so the token is passed as a **query param**
on the stream URL and validated by the same dependency (documented in `stream-sse.md`). The actor
identity from `get_current_operator` replaces the hardcoded `"admin"` literal currently in
`approvals.py` as `decided_by`/audit actor.

**Alternatives rejected**: httpOnly cookie sessions (cleaner CSRF story but adds CSRF tokens + cookie
plumbing for a single-admin demo — more surface, not less); server-side session store (needs a table +
migration, contradicts "incidents table unchanged" simplicity and adds a writer); OAuth/OIDC/an identity
provider (out of scope — one org, one admin); `bcrypt`/`passlib` (native dep for no benefit here).

## RD3 — Live updates: Server-Sent Events (SSE), not WebSocket

**Decision** (realizes the spec's "server push over a persistent stream" clarification, FR-023): use
**SSE** via FastAPI `StreamingResponse` at `GET /incidents/stream`. The browser's native `EventSource`
provides automatic reconnect; the client shows a "reconnecting" indicator on drop and reconciles via a
full snapshot on (re)connect, falling back to TanStack Query on-demand refresh while disconnected.

**Rationale**: the push is **one-directional** (server → client) for queue + KPI updates. The only
client → server action is approve/reject, which already has its own REST endpoint (#10's
`/approvals/{id}/decision`) — so a bidirectional WebSocket buys nothing and costs a protocol upgrade,
heartbeat/ping management, and a heavier client. SSE is plain HTTP (works through the nginx reverse
proxy and any corporate proxy), auto-reconnects natively, and needs **no new dependency**.

**Alternatives rejected**: WebSocket (bidirectional overkill; manual reconnect/heartbeat); long-polling
(more requests, worse latency, no native reconnect); client-side interval polling only (fails the
"persistent stream" clarification and is wasteful at the 5s freshness target).

## RD4 — SSE event source: API-side periodic snapshot poll (no worker/supervisor change)

**Decision**: the **API process** produces stream events by polling the `incidents` table on a short
server-side interval (default 2s, typed config) and emitting the current queue snapshot + lightweight
KPI counters. The dashboard stays **purely read-side** — neither the worker nor the supervisor is
touched, preserving the single-writer boundary.

**Rationale**: at v1 scale (single org, hundreds of incidents, demo replay) a 2s server-side snapshot
comfortably meets SC-010 (change visible ≤5s) and SC-009 (queue < 2s for 200 rows) at negligible cost,
and it requires **zero changes to the acting path**. Cross-process eventing (the worker writes,
the API serves — separate processes) would otherwise need a message bus.

**Alternatives rejected** (documented as the clean scale-up path, **not built in v1** — "don't
overengineer"): **Redis pub/sub** where the supervisor/worker publish `incident_changed` and the API
fans out to SSE — the correct choice at higher scale, but it adds a publish call inside the single-writer
path and more wiring for no v1 benefit; **Postgres LISTEN/NOTIFY** — same cross-process win but ties the
stream to a DB-specific feature and a dedicated listener connection. Both are noted in DECISIONS.md as
the documented upgrade; v1 takes the simplest sufficient path.

## RD5 — Read-side `/incidents` endpoints fill the reserved router (no new write path)

**Decision**: implement the reserved `backend/routers/incidents.py` with read-only endpoints, all behind
`get_current_operator`:
- `GET /incidents` — queue page: `status[]`, `severity[]`, `view=active|resolved|all` (default
  `active`), `sort`, `limit`, `offset`. Backed by a new `IncidentRepository.list_for_queue(...)` +
  `count_for_queue(...)`.
- `GET /incidents/{id}` — detail: status, severity, disposition, source, timestamps, grounded
  summary/evidence.
- `GET /incidents/{id}/audit` — audit trail via `AuditRepository.list_for_incident`.
- `GET /incidents/{id}/trace` — trace tree via `TraceRepository.get_trace_tree(correlation_id)`,
  serialized to `TraceTreeView` with per-step tokens/model/latency/status (null tokens → `"unknown"`).
- `GET /incidents/kpis` — `KpiSnapshot` (RD6).
- `GET /incidents/stream` — SSE (RD3/RD4).
The approval panel reuses #10's existing `GET /approvals` + `POST /approvals/{id}/decision` — now
**registered** in `routers/__init__.py` (currently commented out) and auth-guarded.

**Rationale**: the seam was reserved exactly for this (#1 D2; the router docstring says "consumed by the
dashboard (#12)"). `IncidentRepository` stays the **only** module touching the `incidents` table — we
add **read** methods to it, never a second writer. Reusing `/approvals` honors the #10 contract that
"#12 builds only the React UI that calls these endpoints."

**Alternatives rejected**: a separate read-model/CQRS store (massive overkill for a demo console);
duplicating approval logic in the dashboard (violates the #10 ownership seam and Constitution III).

## RD6 — KPI computation reads existing records; memory-hit uses the enrichment evidence signal

**Decision**: a pure `services/kpis.py` computes the four KPI families from existing data, reconciling
exactly with the records (FR-019, SC-006):
- **Alert volume over time** — count of incidents bucketed by `created_at` (line/area).
- **Disposition split** — counts of auto-resolved vs escalated vs awaiting-approval from `status` /
  `disposition`.
- **Mean time to disposition** — mean(`updated_at − created_at`) over terminal incidents.
- **Memory-hit rate** — per the spec clarification: **numerator** = incidents that reached enrichment
  for which enrichment surfaced ≥1 relevant prior incident/temporal fact; **denominator** = incidents
  that reached enrichment. The enrichment stage already writes its report into `incidents.evidence`
  (JSONB, via the supervisor's evidence_patch); the KPI reads that flag/count. Incidents resolved before
  enrichment are excluded from the denominator.

**Rationale**: all inputs already exist on read; no new persistence, no memory writes. Aggregation lives
in a `services/` module (it spans incident + evidence data) calling repository read methods.

**Open implementation note**: confirm the exact evidence key the enrichment stage uses for the
memory-hit signal when wiring `services/kpis.py` (read `domain/enrichment.py` / the #9 evidence_patch
shape). If absent for a given incident, that incident counts toward the denominator but not the
numerator (a miss) — never an error.

## RD7 — No migration; reads existing tables; auth credential in Vault

**Decision**: #12 adds **no Alembic migration**. It reads `incidents`, `approval_requests`, `audit_log`,
`trace_spans` (all already created by #2/#3/#4/#10). Auth is stateless (JWT) with the credential +
signing secret in Vault. This preserves "incidents table unchanged / supervisor is the single writer"
(carried from #10) and keeps the fresh-clone `docker compose up` migration chain unchanged.

## RD8 — Redaction is upstream; the dashboard asserts, never re-redacts (Constitution III)

**Decision**: read endpoints return stored values that were redacted at write time by #2 (logs / LLM
prompts / stored snapshots / spans). The dashboard introduces **no de-redaction path** and never
requests raw data. Verification: an integration test seeds a fake secret/PII into an incident's evidence
+ spans and asserts it never appears in any `/incidents` response; the e2e renders the trace and asserts
the same in the view — folded into the existing **redaction** eval gate (no new gate).

**Rationale**: trust the structural boundary; prove it with a test rather than adding a redundant redact
step that could mask a missing upstream redaction.

## RD9 — Visual design system (via `/ui-ux-pro-max`): Dark-Mode OLED slate + green accent

**Decision** (from the ui-ux-pro-max design-system query for "security operations / incident response /
dark mode"; full detail in `contracts/frontend-ux.md`):
- **Style**: Dark Mode (OLED) — professional, trustworthy security-tooling aesthetic; WCAG-AAA-capable
  contrast; excellent perf.
- **Palette**: background `#020617` (slate-950), surfaces `#0F172A` / `#1E293B` (slate-900/800), text
  `#F8FAFC`, primary action / positive accent `#22C55E` (green-500). **Semantic** colors layered on top
  for SOC meaning: severity (low→critical) and status/disposition badges with **both color and
  label/icon** (never color alone — accessibility).
- **Typography**: **Fira Sans** (body/UI) + **Fira Code** (mono — incident IDs, IPs, hashes, tokens,
  trace span names). Data/analytics/technical mood, well-suited to a console.
- **Charts**: line/area for volume-over-time, horizontal bar (or donut) for disposition split, big-stat
  cards for MTTD and memory-hit %, a custom collapsible span tree for the trace inspector.
- **Layout**: an **application shell** (left sidebar nav + top bar + main content), **not** the
  "Horizontal Scroll Journey" landing pattern the matcher initially surfaced — that is a
  marketing-page pattern; an information-dense operations console rejects it in favor of a stable
  shell with dense tables and drill-down panels.

**Rationale**: dark mode is the expected idiom for 24/7 SOC tooling (eye strain, glanceability); the
slate+green system reads as calm/operational with a single confident action color; mono type makes the
dense identifiers scannable. Accessibility is non-negotiable (color never the only signal; visible focus
rings; 44px touch targets; `prefers-reduced-motion` honored).

## RD10 — Packaging: separate image, nginx static + reverse-proxy, compose `frontend` service

**Decision**: `deploy/frontend/Dockerfile` is multi-stage — `node:20` builds the Vite bundle, then an
`nginx` stage serves the static assets and **reverse-proxies** `/auth`, `/incidents`, `/approvals` (and
the SSE stream) to the `api` service. The `frontend` service (slot already reserved + commented in
`compose.yaml`) is uncommented and wired with `depends_on: api`.

**Rationale**: same-origin reverse-proxy avoids CORS config and makes SSE work without preflight; the
Node toolchain stays entirely inside this image (the #1 second-runtime exception) — it never touches the
Python `uv` venv or backend image. A clean `docker compose up` brings up the console with no manual step
(honors the day-9 reproducibility gate).

**Alternatives rejected**: serving the SPA from FastAPI `StaticFiles` (couples the frontend build into
the Python image/venv — violates the separate-toolchain decision); direct cross-origin API calls + CORS
(more config, complicates SSE/auth).

## RD11 — Testing & milestone strategy

**Decision**: three-tier backend tests + frontend component tests + one e2e, delivered as milestone PRs
(Constitution I): **(a)** auth + app shell + queue/detail [P1]; **(b)** approval panel + trace inspector
[P2]; **(c)** KPIs + SSE + polish [P3]. Backend: unit (token issue/verify + expiry, PBKDF2 check, queue
filter/sort, KPI math, trace-tree serialization, SSE event shape), integration (each endpoint vs real
Postgres incl. auth-required 401s and the redaction assertion), e2e (sign-in → queue → detail → trace;
approve via dashboard → `remediated`; reject → `rejected_by_human`; already-decided → refused). Frontend:
Vitest + RTL (queue table, approval guard, trace render, "unknown" telemetry, empty/error/reconnect
states) + Playwright happy path. No new eval gate; extend the redaction gate.

---

## Resolved unknowns summary

| Question | Resolution |
|----------|-----------|
| Frontend stack | Vite + React + TS + Tailwind + shadcn/ui + TanStack Query/Table + Recharts (RD1) |
| Auth mechanism | Username+password in Vault → HS256 JWT w/ role; `get_current_operator` (RD2) |
| Live updates | SSE via `EventSource`; reconnect + reconcile + refetch fallback (RD3) |
| SSE event source | API-side 2s snapshot poll; Redis pub/sub deferred as scale-up (RD4) |
| Read endpoints | Fill reserved `/incidents` router; reuse + register `/approvals` (RD5) |
| KPI / memory-hit | Aggregate existing records; memory-hit from enrichment evidence signal (RD6) |
| Migration | None — reads existing tables, stateless auth (RD7) |
| Redaction | Upstream; dashboard asserts via tests, no de-redaction path (RD8) |
| Design system | Dark-Mode OLED slate + green, Fira Sans/Code, app-shell (RD9) |
| Packaging | Separate image, nginx static + reverse-proxy, compose `frontend` (RD10) |
| Tests/milestones | 3-tier + frontend + e2e; three milestone PRs P1→P2→P3 (RD11) |
