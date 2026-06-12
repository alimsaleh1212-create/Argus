# Argus Operations Dashboard — Frontend

React SPA for the Argus SOAR operations console. Authenticated single-page application backed by the Argus FastAPI backend.

## Tech Stack

- **Vite + React 18 + TypeScript**
- **Tailwind CSS** with OLED dark theme (`#020617` bg, `#22C55E` accent)
- **shadcn/ui** component library
- **TanStack Query** (server state) + **TanStack Table** (incident queue)
- **Recharts** (KPI charts)
- **React Router v6** (client-side routing)
- **Native EventSource** (SSE live updates)

## Running in Development

Requires the backend API running at `http://localhost:8000` (see `compose.yaml`).

```bash
# From the frontend/ directory:
npm install
npm run dev
# → http://localhost:5173
```

Login with the admin credentials configured in Vault (default seed: `admin` / `argus-admin-2026`).

## Running with Docker Compose (full stack)

```bash
# From project root:
docker compose up --build
# Dashboard → http://localhost:5173
# API       → http://localhost:8000
```

## Available Views

| Route | Description |
|---|---|
| `/` | Incident queue (live, filter/sort/paginate) |
| `/incidents/:id` | Incident detail + evidence + audit trail |
| `/incidents/:id/trace` | Pipeline trace inspector (span tree) |
| `/incidents/:id/approve` | Human approval panel for parked remediations |
| `/kpis` | KPI dashboard (volume, disposition, MTTD, memory-hit) |

## Development Commands

```bash
npm run dev          # Start dev server with HMR
npm run build        # Production build → dist/
npm run preview      # Preview production build locally
npm test             # Run Vitest unit tests
npm run lint         # ESLint
```

## Tests

```bash
npm test             # All tests (82 assertions across 7 test files)
npm test -- --ui     # Vitest UI
```

Test coverage:
- `tests/queue.test.tsx` — incident queue table, filters, pagination
- `tests/incident.test.tsx` — detail view, approval panel, evidence panel, audit trail
- `tests/approvals.test.tsx` — approve/reject dialog, countdown, already-decided state
- `tests/trace.test.tsx` — span tree, span detail, telemetry panel, TraceInspector
- `tests/kpis.test.tsx` — KPI dashboard, VolumeChart, DispositionSplit, StatCards
- `tests/stream.test.tsx` — SSE connection, snapshot/delta cache patching, reconnect state
- `tests/auth.test.tsx` — login form, token storage, protected routes

## Architecture Notes

- **Auth**: JWT stored in `sessionStorage` under `argus_token`. All API calls attach `Authorization: Bearer <token>`. SSE uses `?token=` query param (EventSource cannot set headers).
- **SSE**: `useSSEStream(token)` in `AppShell` opens one global connection. Snapshot events patch the TanStack Query queue cache; delta events merge changed rows; heartbeats keep the connection alive.
- **Read-only**: The dashboard has no direct state mutations except `POST /approvals/:id/decision`. All other interactions are reads.
