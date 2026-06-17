# SOC Live Pipeline Map — Design

- **Date:** 2026-06-17
- **Status:** Approved (brainstorm) — pending implementation plan
- **Author:** Ali (with Claude)
- **Scope:** A new dashboard view, additive alongside the existing Component #12 dashboard

## 1. Problem & Goal

The existing operations dashboard (Component #12) gives SOC analysts a queue, an
incident-detail page, a span-trace inspector, and a KPI page. What it does **not** give is a
*situational-awareness map*: a single screen that shows, at a glance, **where incidents are right
now** as they move through the detection→response pipeline, makes **human-facing incidents
impossible to miss**, and lets an analyst **act** (approve/reject) or **drill into full detail**
without losing the big picture.

**Goal:** ship an impressive, clear, live **pipeline map** view that:
1. shows the whole workflow as a left→right rail with **live per-stage counts** that animate as
   incidents move;
2. lets an analyst **expand a stage** to see its branch breakdown (full
   [`incident-workflow.mmd`](../../incident-workflow.mmd) fidelity on demand);
3. surfaces **`awaiting_approval`** (actionable: Approve/Reject) and **`escalated`** (read-only,
   full detail on click) incidents in a prominent **Human Attention** lane;
4. opens a **full-detail drawer** for any incident, and overlays a **single-incident journey** path
   on the rail when one is selected.

This is a **new, additive view** — it does not modify the existing dashboard.

## 2. Key decisions (resolved during brainstorm)

| # | Decision | Choice |
|---|----------|--------|
| D1 | View shape | **Both**: live aggregate map is the landing; click an incident → overlay *its* journey on the same map. Live map ships first. |
| D2 | Map fidelity | **Layered**: clean 4-stage rail by default; expand a stage for branch detail (full `.mmd` fidelity on demand). |
| D3 | Motion | **Live transitions**: count-up + node pulse + edge flash, each backed by a real data delta. Not flowing particles. |
| D4 | Data path | **New read-only `GET /incidents/pipeline`** aggregate endpoint, polled every 2s; client diffs consecutive snapshots to drive animation. (Rejected: client-only — inaccurate; transitions table — needs migration, overkill.) |
| D5 | Actions | **Approve/Reject** on `awaiting_approval` only (reuse existing `/approvals/{id}/decision`). `escalated` incidents are **shown read-only** with full detail on click — **no** new write authority, **no** migration. |
| D6 | Isolation | New files only; the **only** edits to existing files are two additive lines (one nav item, one route). |

## 3. Isolation guarantee (hard constraint)

This view **adds, never edits**:
- **Backend:** one new read endpoint, one new service module, additive DTOs. No migration, no FSM
  change, no new writer (Constitution III/IV preserved — read-only aggregation; the only write is
  the pre-existing approve/reject path).
- **Frontend:** a new `frontend/src/features/map/` folder + a new `frontend/src/api/pipeline.ts`
  hook. The **only** touches to existing files are **two additive lines**: one nav item in
  [`AppShell.tsx`](../../../frontend/src/components/AppShell.tsx) and one route in
  [`router.tsx`](../../../frontend/src/router.tsx).
- Existing Queue / Incident / Trace / KPI pages, their components, and the existing SSE stream are
  **untouched**. (Fallback if zero edits are required: make `/map` reachable by URL only and skip
  the nav link.)

## 4. Backend (read-only, zero migration)

### 4.1 New endpoint
`GET /incidents/pipeline` → `PipelineSnapshot`, added to
[`backend/routers/incidents.py`](../../../backend/routers/incidents.py), built by a new
`backend/services/pipeline_view.py` (mirrors the existing `services/kpis.py` pattern). Pure DTOs
added to `backend/domain/dashboard.py`.

### 4.2 `PipelineSnapshot` shape
```
PipelineSnapshot:
  stages:    [ StageNode ]          # the rail
  terminals: TerminalCounts         # resolved / escalated / awaiting_approval (rolling window)
  generated_at: datetime

StageNode:
  key:          "intake" | "triage" | "enrichment" | "response"
  label:        str
  in_flight:    int                 # live count of incidents currently in this stage
  branches:     [ BranchOutflow ]   # for the expandable detail (D2)

BranchOutflow:
  to:    str                        # e.g. "enrichment", "resolved", "escalated"
  count: int
```

### 4.3 Data derivation (no new tables)
- **Stage in-flight counts** — group *active* incidents by status:
  - `intake` ← `received` + `grounding` + `grounded`
  - `triage` ← `triaging`
  - `enrichment` ← `enriching`
  - `response` ← `responding` + `awaiting_approval`
- **Branch outflow (expand)** — derived from the **stage-tagged dispositions already stored**
  (`auto_resolved_triage`, `escalated_triage`, `auto_resolved_enrichment`, `escalated_enrichment`,
  `auto_remediated`, `remediated`, `remediation_unverified`, `rejected_by_human`,
  `approval_expired`, `escalated_response`, `auto_resolved_noise`). Each disposition maps to the
  stage it exited from → the `.mmd` branch detail comes for free.
- **Terminal counts** — incidents that reached a terminal status within a **rolling window**
  (config, default 24h) so numbers stay meaningful and bounded.
- Requires a small **read-only** count-by-status / count-by-disposition-since helper on
  [`repositories/incidents.py`](../../../backend/repositories/incidents.py).

### 4.4 Graceful degradation
Aggregation is best-effort: a failing sub-count returns `0` for that node rather than a 500. The
snapshot exposes only **counts and enums** — no incident text — so there is **no new redaction
surface** and no eval-gate change. (To be confirmed during implementation.)

### 4.5 Reused endpoints (no new backend needed for the action layer)
- `GET /approvals?status=pending` — awaiting-approval list (Human Attention lane)
- `GET /incidents?status=escalated` — escalated list (Human Attention lane)
- `GET /incidents/{id}` — full detail (drawer)
- `GET /incidents/{id}/trace` — span timeline (journey overlay)
- `POST /approvals/{id}/decision` — the **only** write (Approve/Reject)

## 5. Frontend (`features/map/`)

| File | Responsibility |
|------|----------------|
| `PipelineMap.tsx` | Page container; lays out rail + Human Attention lane; owns selected-incident + pause state. |
| `StageNode.tsx` | Stage card: animated count-up, ambient ring when holding incidents, glow burst on increase; expandable to `BranchBreakdown`. |
| `BranchBreakdown.tsx` | Expanded outflow rows (→ enrich 4 · → resolved 2 · → escalated 1) with mini bars. |
| `FlowEdge.tsx` | Connector between nodes; flashes green when flow occurred since last tick. |
| `TerminalColumn.tsx` | Resolved / Escalated / Awaiting tiles — **icon + color**, never color alone. |
| `HumanAttentionLane.tsx` | Prominent list of `awaiting_approval` (Approve/Reject) + `escalated` (View detail) cards. |
| `IncidentDrawer.tsx` | Slide-over (shadcn `Sheet`/`Dialog`); reuses `useIncidentDetail` to show evidence/audit/trace/status; embeds `ApprovalPanel` for awaiting; "Open full incident ↗" link to `/incidents/:id`. |
| `JourneyOverlay.tsx` | On incident select, highlight its path on the rail, ghost the rest, show per-stage timing + branch taken. |
| `api/pipeline.ts` | `usePipeline()` (react-query, `refetchInterval: 2000`, paused on toggle/hidden tab) + snapshot-diff helper that derives which nodes/edges animate. |

**Reused as-is:** `SeverityBadge`, `StatusBadge`, `Card`, `ErrorState`, `EmptyState`, `Skeleton`,
`ApprovalPanel`, `DecisionDialog`, `useIncidentDetail`, `usePendingApprovals`,
`useApprovalDecision`, theme tokens, `cn`.

## 6. Visual & motion design (ui-ux-pro-max informed)

- **Palette** = the existing dashboard's (Dark Mode / OLED): bg `#020617`, surfaces `#0F172A` /
  `#1E293B`, green accent `#22C55E`, text `#F8FAFC`. No new fonts. Minimal glow — not neon.
- **Layout** = horizontal "journey track" rail (the recommended pattern), left→right by stage.
- **Motion rules** (from UX guidance):
  - Animate **only the 1–2 elements that changed** per tick (the increased node + the edge that
    carried flow) — never the whole board.
  - **ease-out** for entering (glow burst ~250ms, count roll), **ease-in** for exiting (edge flash
    fade ~300ms). No `linear`.
  - **`prefers-reduced-motion`** → instant value swap + 150ms fade; no pulse/flash/roll.
  - A **Live / Pause toggle** (top-right) freezes polling + motion for always-on wall displays
    (satisfies the "flashing elements → provide a pause control" rule).
- **Color is never the only signal:** Resolved = green check, Escalated = amber triangle,
  Awaiting = slate pause icon. Focus rings on interactive nodes; keyboard-expandable; aria-labels;
  `cursor-pointer` on all clickable cards; 150–300ms `transition-colors`.

## 7. Data & interaction flow

1. `usePipeline()` polls `GET /incidents/pipeline` every 2s (paused on toggle / hidden tab).
2. Client keeps the previous snapshot; on a new one, diff counts → mark increased nodes + active
   edges for one animation tick → render.
3. **Human Attention lane** renders from `usePendingApprovals()` (awaiting) +
   `/incidents?status=escalated` (escalated).
4. Click any incident (node drill-in, lane card, or `/map?incident=<id>` deep link) → open
   `IncidentDrawer` (full detail) and overlay its journey on the rail via `JourneyOverlay`.
5. Approve/Reject inside the drawer/lane → existing `useApprovalDecision` → invalidates queue +
   detail + approvals; the next 2s poll reflects the new state.

## 8. Error / degradation

- Pipeline endpoint error → reuse `ErrorState`; keep the last-good snapshot dimmed with a "stale"
  chip — never blank the board.
- Empty system → `EmptyState` ("No incidents in flight").
- Approve/Reject conflict (already decided / expired) → handled by the existing `ApprovalPanel`
  409 path.

## 9. Testing

- **Backend:** unit tests for `pipeline_view` (status→stage grouping, disposition→branch mapping,
  rolling-window filter) with seeded incidents; router shape test for `GET /incidents/pipeline`.
  Run via `scripts/run-tests.sh` / `make test-*` (never one big `pytest` — avoids the
  spaCy/graphiti OOM).
- **Frontend (vitest):** snapshot-diff / animation-derivation logic; `PipelineMap` render + stage
  expand; `HumanAttentionLane` (awaiting shows Approve/Reject, escalated shows View detail only);
  `IncidentDrawer` detail render + embedded approval; `JourneyOverlay` path highlight; the
  reduced-motion branch.
- **Playwright e2e:** `/map` loads, a stage expands, a journey overlays, an approval is actioned.

## 10. Milestones (≤ ~400 lines each, matching repo discipline)

- **M-a:** backend `GET /incidents/pipeline` + `pipeline_view` service + DTOs + repo helper + tests
  (the data spine).
- **M-b:** live rail (`StageNode` / `FlowEdge` / `TerminalColumn` + `usePipeline` polling + motion
  + reduced-motion + Live/Pause) + nav item + route + tests.
- **M-c:** `HumanAttentionLane` (approve/reject reuse + escalated cards) + `IncidentDrawer` +
  `BranchBreakdown` expand + `JourneyOverlay` + e2e.

## 11. Out of scope

- Flowing-particle animation (chose pulse/flash; D3).
- New decision actions on `escalated` incidents (acknowledge/claim/close/re-run) — would need new
  write endpoints + a migration + a Constitution III/IV exception. Explicitly **not** in this work.
- Any change to the existing dashboard pages, the SSE stream, the FSM, or the DB schema.
- Throughput sparkline / streaming chart (possible later, additive).
