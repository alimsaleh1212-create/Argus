# SOC Pipeline Map M-c: Human Attention Lane + Drawer + Branch Detail Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship M-c of the SOC Live Pipeline Map (design: `docs/superpowers/specs/2026-06-17-soc-pipeline-map-design.md` §5/§10) — a `HumanAttentionLane` (awaiting-approval + escalated incidents, approve/reject reused), an `IncidentDrawer` (full detail slide-over), a `BranchBreakdown` expand (per-stage outflow + the real in-flight incidents behind each count), and a `JourneyOverlay` (single-incident path highlight) — while also addressing user feedback that the current `/map` page (M-b) is too small and too sparse: it must visually fill more of the page and surface real underlying incident data (especially **which incidents are currently in each stage**), not just bare counts.

**Architecture:** Pure frontend addition under `frontend/src/features/map/`, consuming only existing read endpoints (`useIncidentQueue`, `usePendingApprovals`, `useIncidentDetail`, `useTrace`, `useApprovalDecision`) — zero backend changes. `StageNodeCard` gains expand/dim/journey-timing props; clicking a stage reveals `BranchBreakdown`, which fetches the real incidents currently sitting in that stage (`useIncidentQueue({ view: 'active', status: [...] })`) alongside the existing outflow counts. `HumanAttentionLane` renders below the rail as a wide, content-rich panel (not a thin strip) using `useIncidentQueue({ view: 'all', status: ['escalated'] })` for escalated rows and `usePendingApprovals()` for awaiting rows. Selecting any incident (rail, lane, or `?incident=` deep link) opens `IncidentDrawer` (new `Sheet` primitive) and feeds `JourneyOverlay`, which derives visited stages + per-stage timing from `useTrace()` span names (`supervisor.stage.{key}`) and current stage from `useIncidentDetail()` status. `PipelineMap.tsx` is rewired to a full-height, two-row layout (rail row + attention-lane row) so the page covers the viewport instead of leaving empty space below a small rail.

**Tech Stack:** React 19 + TypeScript, TanStack Query v5, react-router-dom v7 (`useSearchParams`), Tailwind CSS v4 (CSS-first, no `tailwindcss-animate`), `@radix-ui/react-dialog` (new `Sheet` primitive built on the same `Root`/`Portal`/`Content` as `Dialog`), `class-variance-authority`, `lucide-react`, Vitest + `@testing-library/react`, Playwright (gated by `ARGUS_E2E`).

## Global Constraints

- **Isolation guarantee (design §3, hard constraint):** only files under `frontend/src/features/map/`, `frontend/src/components/ui/sheet.tsx`, and test files may be created. `frontend/src/features/incident/IncidentDetail.tsx`, `frontend/src/features/approvals/*`, `frontend/src/api/*`, `frontend/src/router.tsx`, `frontend/src/components/AppShell.tsx`, and every other existing dashboard file are **not modified** in this plan (the nav item + route were already added in M-b). The only existing file this plan modifies is `frontend/src/features/map/PipelineMap.tsx`, `StageNode.tsx`, `FlowEdge.tsx`, and `frontend/tests/pipeline-map.test.tsx` — all of which are M-a/M-b's own new files, not pre-existing dashboard files.
- **No new backend endpoints.** All data comes from the five reused endpoints listed in design §4.5.
- **Backend AND-filter constraint:** `useIncidentQueue`'s `view` and `status` params are AND-combined server-side (`backend/repositories/incidents.py::_build_queue_where`). To fetch escalated incidents you MUST pass `view: 'all'` together with `status: ['escalated']` — `view: 'active'` would incorrectly exclude them. Per-stage in-flight incident lists use `view: 'active'` with the stage's own status list (safe, since every per-stage status is in the active bucket).
- **Backend status→stage / disposition→branch mapping is the source of truth** (`backend/services/pipeline_view.py`):
  ```python
  STAGES = [("intake","Intake"), ("triage","Triage"), ("enrichment","Enrichment"), ("response","Response")]
  _STATUS_TO_STAGE = {"received":"intake","grounding":"intake","grounded":"intake","triaging":"triage","enriching":"enrichment","responding":"response","awaiting_approval":"response"}
  ```
  The frontend mirror (Task 1) must reproduce this exactly.
  - Span names for journey timing follow `f"supervisor.stage.{stage_name.value}"` where `stage_name` ∈ `{"triage","enrichment","response"}` (no "intake" span — intake/grounding precedes the supervisor loop).
- **No `tailwindcss-animate` dependency exists** (confirmed in `package.json`). The new `Sheet` primitive must use real native Tailwind v4 utilities only: `transition-transform duration-300 ease-out` + `data-[state=open]:translate-x-0 data-[state=closed]:translate-x-full`. Do not copy the `animate-in`/`fade-in-0`/`zoom-in-95` classes from `dialog.tsx` — they are currently non-functional in this project.
- **Reduced motion is already handled globally** in `frontend/src/styles/globals.css` (`@media (prefers-reduced-motion: reduce)` forces `transition-duration: 0.01ms`). New CSS-transition-based components need no extra JS branching for it; only timer-driven flash state (already in `useAnimatedPipeline`) needs that, and this plan adds none.
- **Test commands** (run from `frontend/`): `npm test` (vitest run, single process — fine to run repeatedly, no OOM risk like the backend's spaCy/graphiti suite), `npm run lint`, `npm run build` (tsc -b + vite build), `npm run test:e2e` (Playwright, requires `ARGUS_E2E=1` + running stack — skip unless explicitly asked to run the full stack).
- **Accessibility convention:** existing rows use `<tr onClick>` with `cursor-pointer` but no keyboard handling (`IncidentQueue.tsx`). New clickable cards in this plan improve on this slightly: `role="button" tabIndex={0}` + `onKeyDown` for Enter/Space, matching the existing visual style.
- **No placeholders / no TBDs** — every step below contains complete, runnable code.

---

## File Structure

| File | Status | Responsibility |
|------|--------|-----------------|
| `frontend/src/features/map/stageStatuses.ts` | new | Frontend mirror of backend `_STATUS_TO_STAGE` + `STAGES` + helpers (`STAGE_STATUSES`, `stageForStatus`). |
| `frontend/src/features/map/StageNode.tsx` | modify | Add `expanded`, `onToggleExpand`, `dimmed`, `journeyTimingMs` props; render expand affordance. |
| `frontend/src/features/map/FlowEdge.tsx` | modify | Add `highlighted` prop (journey overlay). |
| `frontend/src/features/map/BranchBreakdown.tsx` | new | Expanded per-stage panel: outflow bars + the real incidents currently in that stage. |
| `frontend/src/components/ui/sheet.tsx` | new | Slide-over primitive (Radix Dialog + real Tailwind transitions). |
| `frontend/src/features/map/EvidencePanel.tsx` | new | Standalone copy of evidence rendering (independent of `IncidentDetail.tsx`). |
| `frontend/src/features/map/AuditTrail.tsx` | new | Standalone copy of audit-trail rendering (independent of `IncidentDetail.tsx`). |
| `frontend/src/features/map/IncidentDrawer.tsx` | new | Slide-over: status/severity, evidence, approval panel, audit, "Open full incident ↗" link. |
| `frontend/src/features/map/HumanAttentionLane.tsx` | new | Wide panel: awaiting-approval cards (inline approve/reject) + escalated cards (read-only, opens drawer). |
| `frontend/src/features/map/JourneyOverlay.tsx` | new | `useJourney(incidentId)` hook: visited stages + per-stage timing from trace + detail. |
| `frontend/src/features/map/PipelineMap.tsx` | modify | Full-height two-row layout; wires expand state, selection state (URL-synced), drawer, lane, journey. |
| `frontend/tests/pipeline-map.test.tsx` | modify | Mock new child components; add coverage for expand/select wiring. |
| `frontend/tests/stage-statuses.test.ts` | new | Unit tests for the stage mapping mirror. |
| `frontend/tests/branch-breakdown.test.tsx` | new | Tests for outflow + in-flight incident rendering. |
| `frontend/tests/human-attention-lane.test.tsx` | new | Tests for awaiting/escalated card rendering + actions. |
| `frontend/tests/incident-drawer.test.tsx` | new | Tests for drawer content + embedded approval. |
| `frontend/tests/journey-overlay.test.tsx` | new | Tests for `useJourney` derivation logic. |
| `frontend/tests/e2e/pipeline-map.spec.ts` | new | e2e: map loads, stage expands, lane action, journey overlay. |

---

### Task 1: `stageStatuses.ts` — frontend mirror of the backend stage mapping

**Files:**
- Create: `frontend/src/features/map/stageStatuses.ts`
- Test: `frontend/tests/stage-statuses.test.ts`

**Interfaces:**
- Consumes: nothing (pure data module).
- Produces: `STAGE_KEYS: readonly string[]`, `STAGE_STATUSES: Record<string, string[]>` (stage key → active statuses in that stage), `stageForStatus(status: string): string | null`. Consumed by Task 4 (`BranchBreakdown`), Task 9 (`JourneyOverlay`), Task 10 (`PipelineMap`).

- [ ] **Step 1: Write the failing test**

```typescript
// frontend/tests/stage-statuses.test.ts
import { describe, it, expect } from 'vitest'
import { STAGE_KEYS, STAGE_STATUSES, stageForStatus } from '@/features/map/stageStatuses'

describe('stageStatuses', () => {
  it('declares the four rail stages in order', () => {
    expect(STAGE_KEYS).toEqual(['intake', 'triage', 'enrichment', 'response'])
  })

  it('maps every active status to its backend-defined stage', () => {
    expect(STAGE_STATUSES.intake).toEqual(['received', 'grounding', 'grounded'])
    expect(STAGE_STATUSES.triage).toEqual(['triaging'])
    expect(STAGE_STATUSES.enrichment).toEqual(['enriching'])
    expect(STAGE_STATUSES.response).toEqual(['responding', 'awaiting_approval'])
  })

  it('resolves a status to its stage key', () => {
    expect(stageForStatus('triaging')).toBe('triage')
    expect(stageForStatus('awaiting_approval')).toBe('response')
  })

  it('returns null for terminal/unknown statuses', () => {
    expect(stageForStatus('resolved')).toBeNull()
    expect(stageForStatus('escalated')).toBeNull()
    expect(stageForStatus('nonsense')).toBeNull()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run tests/stage-statuses.test.ts`
Expected: FAIL with "Failed to resolve import @/features/map/stageStatuses"

- [ ] **Step 3: Write minimal implementation**

```typescript
// frontend/src/features/map/stageStatuses.ts
// Mirrors backend/services/pipeline_view.py STAGES + _STATUS_TO_STAGE exactly.
// Keep in sync if the backend mapping changes.

export const STAGE_KEYS = ['intake', 'triage', 'enrichment', 'response'] as const

export const STAGE_STATUSES: Record<string, string[]> = {
  intake: ['received', 'grounding', 'grounded'],
  triage: ['triaging'],
  enrichment: ['enriching'],
  response: ['responding', 'awaiting_approval'],
}

const STATUS_TO_STAGE: Record<string, string> = Object.fromEntries(
  Object.entries(STAGE_STATUSES).flatMap(([stage, statuses]) =>
    statuses.map((status) => [status, stage])
  )
)

export function stageForStatus(status: string): string | null {
  return STATUS_TO_STAGE[status] ?? null
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run tests/stage-statuses.test.ts`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/map/stageStatuses.ts frontend/tests/stage-statuses.test.ts
git commit -m "feat(map): add frontend stage-status mapping mirror"
```

---

### Task 2: `StageNode.tsx` — expand affordance, dim, and journey timing

**Files:**
- Modify: `frontend/src/features/map/StageNode.tsx`
- Test: `frontend/tests/pipeline-map.test.tsx` (extend the existing `StageNodeCard` describe block)

**Interfaces:**
- Consumes: `StageNode` type from `@/api/pipeline` (unchanged).
- Produces: `StageNodeCard({ stage, justChanged, expanded, onToggleExpand, dimmed, journeyTimingMs })` — new props are all optional so Task 10's callers can opt in; `onToggleExpand` is called with no arguments (the parent already knows which stage via closure). Consumed by Task 10 (`PipelineMap`).

- [ ] **Step 1: Write the failing test**

Add to the existing `describe('StageNodeCard', ...)` block in `frontend/tests/pipeline-map.test.tsx` (after the existing three `it` blocks, before the closing `})`):

```typescript
  it('shows an expand toggle and calls onToggleExpand when clicked', async () => {
    const onToggleExpand = vi.fn()
    const { default: userEvent } = await import('@testing-library/user-event')
    render(
      <StageNodeCard stage={stage} justChanged={false} expanded={false} onToggleExpand={onToggleExpand} />
    )
    await userEvent.click(screen.getByRole('button', { name: /expand triage/i }))
    expect(onToggleExpand).toHaveBeenCalledOnce()
  })

  it('renders a collapse label when expanded is true', () => {
    render(
      <StageNodeCard stage={stage} justChanged={false} expanded={true} onToggleExpand={vi.fn()} />
    )
    expect(screen.getByRole('button', { name: /collapse triage/i })).toBeInTheDocument()
  })

  it('applies dimmed styling when dimmed is true', () => {
    render(<StageNodeCard stage={stage} justChanged={false} dimmed={true} />)
    expect(screen.getByTestId('stage-node-triage')).toHaveClass('opacity-40')
  })

  it('shows journey timing when journeyTimingMs is provided', () => {
    render(<StageNodeCard stage={stage} justChanged={false} journeyTimingMs={1500} />)
    expect(screen.getByText('1.5s')).toBeInTheDocument()
  })
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run tests/pipeline-map.test.tsx -t StageNodeCard`
Expected: FAIL — `expand triage` button not found (component does not yet support `onToggleExpand`/`expanded`/`dimmed`/`journeyTimingMs`)

- [ ] **Step 3: Write minimal implementation**

```typescript
// frontend/src/features/map/StageNode.tsx
import { ChevronDown, ChevronUp } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { StageNode } from '@/api/pipeline'

interface StageNodeCardProps {
  stage: StageNode
  justChanged: boolean
  expanded?: boolean
  onToggleExpand?: () => void
  dimmed?: boolean
  journeyTimingMs?: number
}

function formatTiming(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

export function StageNodeCard({
  stage,
  justChanged,
  expanded = false,
  onToggleExpand,
  dimmed = false,
  journeyTimingMs,
}: StageNodeCardProps) {
  return (
    <div
      className={cn(
        'rounded-lg bg-slate-900 border border-slate-700 p-4 flex flex-col gap-1 min-w-[140px]',
        'transition-colors duration-300 ease-out',
        justChanged && 'border-cyan-400 bg-cyan-400/10',
        dimmed && 'opacity-40'
      )}
      data-testid={`stage-node-${stage.key}`}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-semibold uppercase tracking-wider text-slate-400">
          {stage.label}
        </span>
        {onToggleExpand && (
          <button
            type="button"
            onClick={onToggleExpand}
            aria-label={expanded ? `Collapse ${stage.label}` : `Expand ${stage.label}`}
            aria-expanded={expanded}
            className="text-slate-500 hover:text-slate-200 transition-colors cursor-pointer"
          >
            {expanded ? (
              <ChevronUp className="w-3.5 h-3.5" aria-hidden="true" />
            ) : (
              <ChevronDown className="w-3.5 h-3.5" aria-hidden="true" />
            )}
          </button>
        )}
      </div>
      <span className="text-2xl font-mono font-bold text-slate-100">{stage.in_flight}</span>
      <span className="text-xs text-slate-500">in flight</span>
      {journeyTimingMs !== undefined && (
        <span className="text-[11px] font-mono text-cyan-400 mt-1">
          {formatTiming(journeyTimingMs)}
        </span>
      )}
    </div>
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run tests/pipeline-map.test.tsx -t StageNodeCard`
Expected: PASS (all `StageNodeCard` tests, old and new)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/map/StageNode.tsx frontend/tests/pipeline-map.test.tsx
git commit -m "feat(map): add expand, dim, and journey-timing affordances to StageNodeCard"
```

---

### Task 3: `FlowEdge.tsx` — journey highlight

**Files:**
- Modify: `frontend/src/features/map/FlowEdge.tsx`
- Test: `frontend/tests/pipeline-map.test.tsx` (extend the existing `FlowEdge` describe block)

**Interfaces:**
- Consumes: nothing new.
- Produces: `FlowEdge({ active, highlighted })` — `highlighted` is optional, defaults to `false`. Consumed by Task 10 (`PipelineMap`, journey overlay coloring).

- [ ] **Step 1: Write the failing test**

Add to the existing `describe('FlowEdge', ...)` block:

```typescript
  it('applies the journey highlight styling when highlighted is true', () => {
    const { container } = render(<FlowEdge active={false} highlighted={true} />)
    expect(container.querySelector('[data-testid="flow-edge"] > div')).toHaveClass(
      'bg-sky-300'
    )
  })
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run tests/pipeline-map.test.tsx -t FlowEdge`
Expected: FAIL — element does not have class `bg-sky-300` (prop doesn't exist yet)

- [ ] **Step 3: Write minimal implementation**

```typescript
// frontend/src/features/map/FlowEdge.tsx
import { cn } from '@/lib/utils'

interface FlowEdgeProps {
  active: boolean
  highlighted?: boolean
}

export function FlowEdge({ active, highlighted = false }: FlowEdgeProps) {
  return (
    <div
      className="flex items-center px-1.5"
      data-testid="flow-edge"
      aria-hidden="true"
    >
      <div
        className={cn(
          'h-0.5 w-6 sm:w-10 rounded-full bg-slate-700',
          'transition-colors duration-300 ease-in',
          active && 'bg-cyan-400',
          highlighted && 'bg-sky-300'
        )}
      />
    </div>
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run tests/pipeline-map.test.tsx -t FlowEdge`
Expected: PASS (all `FlowEdge` tests, old and new)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/map/FlowEdge.tsx frontend/tests/pipeline-map.test.tsx
git commit -m "feat(map): add journey-highlight styling to FlowEdge"
```

---

### Task 4: `BranchBreakdown.tsx` — outflow bars + real in-flight incidents

**Files:**
- Create: `frontend/src/features/map/BranchBreakdown.tsx`
- Test: `frontend/tests/branch-breakdown.test.tsx`

**Interfaces:**
- Consumes: `StageNode`/`BranchOutflow` from `@/api/pipeline`; `STAGE_STATUSES` from `@/features/map/stageStatuses` (Task 1); `useIncidentQueue` from `@/api/incidents` (existing, unmodified); `SeverityBadge` from `@/components/SeverityBadge`, `StatusBadge` from `@/components/StatusBadge` (existing, unmodified).
- Produces: `BranchBreakdown({ stage, onSelectIncident })` where `stage: StageNode` and `onSelectIncident: (incidentId: string) => void`. Consumed by Task 10 (`PipelineMap`, rendered under an expanded `StageNodeCard`).

This is the component that directly answers the user's "richer... showing under the hood main info especially ongoing incidents" feedback: expanding a stage shows not just outflow counts but the actual incidents sitting in that stage right now (id, severity, status, summary, age).

- [ ] **Step 1: Write the failing test**

```typescript
// frontend/tests/branch-breakdown.test.tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { BranchBreakdown } from '@/features/map/BranchBreakdown'
import * as incidentsApi from '@/api/incidents'
import type { IncidentSummary } from '@/api/incidents'

vi.mock('@/api/incidents', () => ({
  useIncidentQueue: vi.fn(),
}))

const mockUseIncidentQueue = vi.mocked(incidentsApi.useIncidentQueue)

const stage = {
  key: 'triage',
  label: 'Triage',
  in_flight: 2,
  branches: [
    { to: 'resolved', count: 5 },
    { to: 'escalated', count: 1 },
  ],
}

function makeIncident(overrides: Partial<IncidentSummary> = {}): IncidentSummary {
  return {
    id: '00000000-0000-0000-0000-000000000001',
    status: 'triaging',
    severity: 'high',
    disposition: null,
    source: 'wazuh',
    summary: 'Suspicious login attempt',
    is_awaiting_approval: false,
    created_at: '2026-06-18T09:00:00Z',
    updated_at: '2026-06-18T09:05:00Z',
    ...overrides,
  }
}

function wrapper({ children }: { children: React.ReactNode }) {
  return <MemoryRouter>{children}</MemoryRouter>
}

describe('BranchBreakdown', () => {
  beforeEach(() => vi.clearAllMocks())

  it('renders outflow bars with destination and count', () => {
    mockUseIncidentQueue.mockReturnValue({
      data: { items: [], total: 0, limit: 10, offset: 0, view: 'active', applied_filters: { status: [], severity: [], sort: '' } },
      isLoading: false,
      isError: false,
      error: null,
    } as ReturnType<typeof incidentsApi.useIncidentQueue>)

    render(<BranchBreakdown stage={stage} onSelectIncident={vi.fn()} />, { wrapper })
    expect(screen.getByText(/resolved/i)).toBeInTheDocument()
    expect(screen.getByText('5')).toBeInTheDocument()
    expect(screen.getByText(/escalated/i)).toBeInTheDocument()
    expect(screen.getByText('1')).toBeInTheDocument()
  })

  it('renders the real incidents currently in this stage', () => {
    mockUseIncidentQueue.mockReturnValue({
      data: {
        items: [makeIncident()],
        total: 1,
        limit: 10,
        offset: 0,
        view: 'active',
        applied_filters: { status: [], severity: [], sort: '' },
      },
      isLoading: false,
      isError: false,
      error: null,
    } as ReturnType<typeof incidentsApi.useIncidentQueue>)

    render(<BranchBreakdown stage={stage} onSelectIncident={vi.fn()} />, { wrapper })
    expect(screen.getByText('Suspicious login attempt')).toBeInTheDocument()
    expect(screen.getByText('High')).toBeInTheDocument()
  })

  it('queries with view=active and the stage statuses', () => {
    mockUseIncidentQueue.mockReturnValue({
      data: { items: [], total: 0, limit: 10, offset: 0, view: 'active', applied_filters: { status: [], severity: [], sort: '' } },
      isLoading: false,
      isError: false,
      error: null,
    } as ReturnType<typeof incidentsApi.useIncidentQueue>)

    render(<BranchBreakdown stage={stage} onSelectIncident={vi.fn()} />, { wrapper })
    expect(mockUseIncidentQueue).toHaveBeenCalledWith(
      expect.objectContaining({ view: 'active', status: ['triaging'] })
    )
  })

  it('calls onSelectIncident when an incident row is clicked', async () => {
    const onSelectIncident = vi.fn()
    mockUseIncidentQueue.mockReturnValue({
      data: {
        items: [makeIncident()],
        total: 1,
        limit: 10,
        offset: 0,
        view: 'active',
        applied_filters: { status: [], severity: [], sort: '' },
      },
      isLoading: false,
      isError: false,
      error: null,
    } as ReturnType<typeof incidentsApi.useIncidentQueue>)
    const { default: userEvent } = await import('@testing-library/user-event')

    render(<BranchBreakdown stage={stage} onSelectIncident={onSelectIncident} />, { wrapper })
    await userEvent.click(screen.getByRole('button', { name: /open incident 00000000-0000-0000-0000-000000000001/i }))
    expect(onSelectIncident).toHaveBeenCalledWith('00000000-0000-0000-0000-000000000001')
  })

  it('shows an empty message when no incidents are currently in this stage', () => {
    mockUseIncidentQueue.mockReturnValue({
      data: { items: [], total: 0, limit: 10, offset: 0, view: 'active', applied_filters: { status: [], severity: [], sort: '' } },
      isLoading: false,
      isError: false,
      error: null,
    } as ReturnType<typeof incidentsApi.useIncidentQueue>)

    render(<BranchBreakdown stage={stage} onSelectIncident={vi.fn()} />, { wrapper })
    expect(screen.getByText(/no incidents currently in this stage/i)).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run tests/branch-breakdown.test.tsx`
Expected: FAIL with "Failed to resolve import @/features/map/BranchBreakdown"

- [ ] **Step 3: Write minimal implementation**

```typescript
// frontend/src/features/map/BranchBreakdown.tsx
import { useIncidentQueue } from '@/api/incidents'
import { SeverityBadge } from '@/components/SeverityBadge'
import { StatusBadge } from '@/components/StatusBadge'
import { Skeleton } from '@/components/ui/skeleton'
import { STAGE_STATUSES } from './stageStatuses'
import type { StageNode } from '@/api/pipeline'

interface BranchBreakdownProps {
  stage: StageNode
  onSelectIncident: (incidentId: string) => void
}

const BRANCH_COLOR: Record<string, string> = {
  resolved: 'bg-cyan-400',
  escalated: 'bg-orange-400',
}

function timeAgo(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime()
  const mins = Math.floor(ms / 60_000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours}h ago`
  return `${Math.floor(hours / 24)}d ago`
}

export function BranchBreakdown({ stage, onSelectIncident }: BranchBreakdownProps) {
  const statuses = STAGE_STATUSES[stage.key] ?? []
  const { data, isLoading } = useIncidentQueue({
    view: 'active',
    status: statuses,
    sort: '-updated_at',
    limit: 10,
  })

  const maxCount = Math.max(1, ...stage.branches.map((b) => b.count))

  return (
    <div
      className="rounded-lg bg-slate-900/60 border border-slate-800 p-4 mt-2 w-full sm:w-[340px] space-y-4"
      data-testid={`branch-breakdown-${stage.key}`}
    >
      <div>
        <p className="text-xs font-semibold uppercase tracking-wider text-slate-500 mb-2">
          Outflow (window)
        </p>
        {stage.branches.length === 0 ? (
          <p className="text-xs text-slate-600 italic">No outflow recorded in this window.</p>
        ) : (
          <ul className="space-y-1.5">
            {stage.branches.map((branch) => (
              <li key={branch.to} className="flex items-center gap-2">
                <span className="text-xs text-slate-400 capitalize w-20 flex-shrink-0">
                  {branch.to}
                </span>
                <div className="flex-1 h-1.5 rounded-full bg-slate-800 overflow-hidden">
                  <div
                    className={`h-full rounded-full ${BRANCH_COLOR[branch.to] ?? 'bg-slate-500'}`}
                    style={{ width: `${(branch.count / maxCount) * 100}%` }}
                  />
                </div>
                <span className="text-xs font-mono text-slate-300 w-6 text-right">
                  {branch.count}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div>
        <p className="text-xs font-semibold uppercase tracking-wider text-slate-500 mb-2">
          Currently in stage ({stage.in_flight})
        </p>
        {isLoading ? (
          <div className="space-y-1.5">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        ) : !data || data.items.length === 0 ? (
          <p className="text-xs text-slate-600 italic">No incidents currently in this stage.</p>
        ) : (
          <ul className="space-y-1.5">
            {data.items.map((incident) => (
              <li key={incident.id}>
                <button
                  type="button"
                  onClick={() => onSelectIncident(incident.id)}
                  aria-label={`Open incident ${incident.id}`}
                  className="w-full text-left bg-slate-800/60 hover:bg-slate-800 border border-slate-700 rounded px-2.5 py-1.5 transition-colors cursor-pointer"
                >
                  <div className="flex items-center gap-2 flex-wrap">
                    <SeverityBadge severity={incident.severity} />
                    <StatusBadge status={incident.status} />
                    <span className="text-[11px] text-slate-500 ml-auto">
                      {timeAgo(incident.updated_at)}
                    </span>
                  </div>
                  {incident.summary && (
                    <p className="text-xs text-slate-300 mt-1 truncate">{incident.summary}</p>
                  )}
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run tests/branch-breakdown.test.tsx`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/map/BranchBreakdown.tsx frontend/tests/branch-breakdown.test.tsx
git commit -m "feat(map): add BranchBreakdown with outflow bars and live in-stage incidents"
```

---

### Task 5: `sheet.tsx` — slide-over primitive

**Files:**
- Create: `frontend/src/components/ui/sheet.tsx`

**Interfaces:**
- Consumes: `@radix-ui/react-dialog` (existing dependency, already used by `dialog.tsx`); `cn` from `@/lib/utils`.
- Produces: `Sheet` (= `DialogPrimitive.Root`), `SheetTrigger`, `SheetPortal`, `SheetClose`, `SheetContent`, `SheetHeader`, `SheetTitle`, `SheetDescription` — same export shape as `dialog.tsx` so callers (Task 7) use it identically to `Dialog`. Consumed by Task 7 (`IncidentDrawer`).

**Note on testing:** there is no existing precedent in this repo for testing `Dialog` standalone — it is only exercised indirectly through components that use it (`ApprovalPanel`/`DecisionDialog` tests). `Sheet` follows the same convention: it is verified indirectly through `IncidentDrawer`'s test file (Task 7), not with a standalone test here. This step is a direct creation + typecheck, not red/green TDD.

- [ ] **Step 1: Write the implementation**

```typescript
// frontend/src/components/ui/sheet.tsx
import * as React from 'react'
import * as DialogPrimitive from '@radix-ui/react-dialog'
import { X } from 'lucide-react'
import { cn } from '@/lib/utils'

const Sheet = DialogPrimitive.Root
const SheetTrigger = DialogPrimitive.Trigger
const SheetPortal = DialogPrimitive.Portal
const SheetClose = DialogPrimitive.Close

const SheetOverlay = React.forwardRef<
  React.ComponentRef<typeof DialogPrimitive.Overlay>,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Overlay>
>(({ className, ...props }, ref) => (
  <DialogPrimitive.Overlay
    ref={ref}
    className={cn(
      'fixed inset-0 z-50 bg-black/60 backdrop-blur-sm transition-opacity duration-300',
      'data-[state=open]:opacity-100 data-[state=closed]:opacity-0',
      className
    )}
    {...props}
  />
))
SheetOverlay.displayName = DialogPrimitive.Overlay.displayName

const SheetContent = React.forwardRef<
  React.ComponentRef<typeof DialogPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Content>
>(({ className, children, ...props }, ref) => (
  <SheetPortal>
    <SheetOverlay />
    <DialogPrimitive.Content
      ref={ref}
      className={cn(
        'fixed right-0 top-0 z-50 h-full w-full max-w-md border-l border-slate-700 bg-slate-900 p-6 shadow-2xl overflow-y-auto',
        'transition-transform duration-300 ease-out',
        'data-[state=open]:translate-x-0 data-[state=closed]:translate-x-full',
        className
      )}
      {...props}
    >
      {children}
      <DialogPrimitive.Close className="absolute right-4 top-4 rounded-sm opacity-70 ring-offset-slate-950 transition-opacity hover:opacity-100 focus:outline-none focus:ring-2 focus:ring-sky-400 focus:ring-offset-2 disabled:pointer-events-none cursor-pointer">
        <X className="h-4 w-4" />
        <span className="sr-only">Close</span>
      </DialogPrimitive.Close>
    </DialogPrimitive.Content>
  </SheetPortal>
))
SheetContent.displayName = DialogPrimitive.Content.displayName

const SheetHeader = ({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) => (
  <div className={cn('flex flex-col space-y-1.5 mb-4', className)} {...props} />
)
SheetHeader.displayName = 'SheetHeader'

const SheetTitle = React.forwardRef<
  React.ComponentRef<typeof DialogPrimitive.Title>,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Title>
>(({ className, ...props }, ref) => (
  <DialogPrimitive.Title
    ref={ref}
    className={cn('text-lg font-semibold leading-none tracking-tight text-slate-50', className)}
    {...props}
  />
))
SheetTitle.displayName = DialogPrimitive.Title.displayName

const SheetDescription = React.forwardRef<
  React.ComponentRef<typeof DialogPrimitive.Description>,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Description>
>(({ className, ...props }, ref) => (
  <DialogPrimitive.Description
    ref={ref}
    className={cn('text-sm text-slate-400', className)}
    {...props}
  />
))
SheetDescription.displayName = DialogPrimitive.Description.displayName

export {
  Sheet,
  SheetPortal,
  SheetOverlay,
  SheetClose,
  SheetTrigger,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
}
```

- [ ] **Step 2: Typecheck**

Run: `cd frontend && npx tsc -b --noEmit`
Expected: no new errors attributable to `sheet.tsx`

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/ui/sheet.tsx
git commit -m "feat(ui): add Sheet slide-over primitive with real Tailwind v4 transitions"
```

---

### Task 6: `EvidencePanel.tsx` + `AuditTrail.tsx` — standalone evidence/audit rendering

**Files:**
- Create: `frontend/src/features/map/EvidencePanel.tsx`
- Create: `frontend/src/features/map/AuditTrail.tsx`
- Test: `frontend/tests/incident-drawer.test.tsx` (covers these indirectly via Task 7; no separate test file — same convention as `EvidencePanel`/`AuditTrail` in `IncidentDetail.tsx`, which are private, untested-in-isolation functions)

**Interfaces:**
- Consumes: `Record<string, unknown> | null` (evidence) / `AuditView[]` (audit) from `@/api/incidents` (existing, unmodified). `Card`/`CardHeader`/`CardTitle`/`CardContent` from `@/components/ui/card` (existing, unmodified).
- Produces: `EvidencePanel({ evidence }: { evidence: Record<string, unknown> | null })`, `AuditTrail({ audit }: { audit: AuditView[] })`. Consumed by Task 7 (`IncidentDrawer`).

**Isolation note:** this duplicates (does not import) the rendering logic in `frontend/src/features/incident/IncidentDetail.tsx`'s private `EvidencePanel`/`AuditTrail` functions. `IncidentDetail.tsx` is a pre-existing dashboard file and is not touched by this plan (design §3 hard isolation constraint) — these are independent copies, not shared imports.

- [ ] **Step 1: Write the implementation**

```typescript
// frontend/src/features/map/EvidencePanel.tsx
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'

interface EvidencePanelProps {
  evidence: Record<string, unknown> | null
}

export function EvidencePanel({ evidence }: EvidencePanelProps) {
  if (!evidence) {
    return (
      <Card className="bg-[#0F172A] border-slate-800">
        <CardHeader>
          <CardTitle className="text-slate-300 text-sm">Evidence</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-slate-600 text-sm italic">No evidence recorded.</p>
        </CardContent>
      </Card>
    )
  }

  return (
    <Card className="bg-[#0F172A] border-slate-800">
      <CardHeader>
        <CardTitle className="text-slate-300 text-sm">Evidence</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {Boolean(evidence.summary) && (
          <div>
            <p className="text-xs text-slate-500 uppercase tracking-wider mb-1">Summary</p>
            <p className="text-slate-200 text-sm">{String(evidence.summary)}</p>
          </div>
        )}
        {Boolean(evidence.verdict) && (
          <div>
            <p className="text-xs text-slate-500 uppercase tracking-wider mb-1">Verdict</p>
            <span className="font-mono text-xs text-cyan-400">{String(evidence.verdict)}</span>
          </div>
        )}
        {Array.isArray(evidence.flags) && evidence.flags.length > 0 && (
          <div>
            <p className="text-xs text-slate-500 uppercase tracking-wider mb-1">Flags</p>
            <div className="flex flex-wrap gap-1.5">
              {evidence.flags.map((flag, i) => (
                <span key={i} className="bg-amber-500/10 text-amber-400 text-xs px-2 py-0.5 rounded font-mono">
                  {String(flag)}
                </span>
              ))}
            </div>
          </div>
        )}
        {Array.isArray(evidence.retrieved_context) && evidence.retrieved_context.length > 0 && (
          <div>
            <p className="text-xs text-slate-500 uppercase tracking-wider mb-1">
              Retrieved Context ({evidence.retrieved_context.length})
            </p>
            <ul className="space-y-1">
              {evidence.retrieved_context.map((ctx, i) => (
                <li key={i} className="text-xs text-slate-400 font-mono truncate">
                  {JSON.stringify(ctx)}
                </li>
              ))}
            </ul>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
```

```typescript
// frontend/src/features/map/AuditTrail.tsx
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import type { AuditView } from '@/api/incidents'

interface AuditTrailProps {
  audit: AuditView[]
}

export function AuditTrail({ audit }: AuditTrailProps) {
  return (
    <Card className="bg-[#0F172A] border-slate-800">
      <CardHeader>
        <CardTitle className="text-slate-300 text-sm">
          Audit Trail ({audit.length})
        </CardTitle>
      </CardHeader>
      <CardContent>
        {audit.length === 0 ? (
          <p className="text-slate-600 text-sm italic">No audit entries yet.</p>
        ) : (
          <ol className="relative border-l border-slate-800 ml-2 space-y-4">
            {audit.map((row, i) => (
              <li key={i} className="ml-4">
                <div className="absolute -left-[5px] mt-1 w-2.5 h-2.5 rounded-full bg-slate-700 border border-slate-600" />
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="font-mono text-xs text-cyan-400">{row.action}</span>
                  <span className="text-xs text-slate-500">by</span>
                  <span className="font-mono text-xs text-slate-300">{row.actor}</span>
                  {row.target && (
                    <>
                      <span className="text-xs text-slate-500">→</span>
                      <span className="font-mono text-xs text-slate-400 truncate max-w-[200px]">{row.target}</span>
                    </>
                  )}
                  <span
                    className={`ml-auto text-[10px] font-semibold uppercase px-1.5 py-0.5 rounded ${
                      row.outcome === 'applied'
                        ? 'bg-cyan-400/10 text-cyan-400'
                        : row.outcome === 'skipped'
                        ? 'bg-slate-700 text-slate-400'
                        : 'bg-red-500/10 text-red-400'
                    }`}
                  >
                    {row.outcome}
                  </span>
                </div>
                <time className="text-[11px] text-slate-600">
                  {new Date(row.created_at).toLocaleString()}
                </time>
              </li>
            ))}
          </ol>
        )}
      </CardContent>
    </Card>
  )
}
```

- [ ] **Step 2: Typecheck**

Run: `cd frontend && npx tsc -b --noEmit`
Expected: no new errors attributable to `EvidencePanel.tsx` or `AuditTrail.tsx`

- [ ] **Step 3: Commit**

```bash
git add frontend/src/features/map/EvidencePanel.tsx frontend/src/features/map/AuditTrail.tsx
git commit -m "feat(map): add standalone EvidencePanel and AuditTrail for the drawer"
```

---

### Task 7: `IncidentDrawer.tsx` — full-detail slide-over

**Files:**
- Create: `frontend/src/features/map/IncidentDrawer.tsx`
- Test: `frontend/tests/incident-drawer.test.tsx`

**Interfaces:**
- Consumes: `Sheet`/`SheetContent`/`SheetHeader`/`SheetTitle`/`SheetDescription` from `@/components/ui/sheet` (Task 5); `EvidencePanel` (Task 6); `AuditTrail` (Task 6); `useIncidentDetail` from `@/api/incidents` (existing, unmodified); `ApprovalPanel` from `@/features/approvals/ApprovalPanel` (existing, unmodified); `SeverityBadge`, `StatusBadge`, `ErrorState`, `Skeleton` (existing, unmodified).
- Produces: `IncidentDrawer({ incidentId, onClose }: { incidentId: string | null; onClose: () => void })`. Consumed by Task 10 (`PipelineMap`).

- [ ] **Step 1: Write the failing test**

```typescript
// frontend/tests/incident-drawer.test.tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { IncidentDrawer } from '@/features/map/IncidentDrawer'
import * as incidentsApi from '@/api/incidents'
import type { IncidentDetailView } from '@/api/incidents'

vi.mock('@/api/incidents', () => ({
  useIncidentDetail: vi.fn(),
}))

vi.mock('@/api/approvals', () => ({
  useApprovalDecision: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
}))

const mockUseIncidentDetail = vi.mocked(incidentsApi.useIncidentDetail)

const INC_ID = '00000000-0000-0000-0000-000000000001'

function makeDetail(overrides: Partial<IncidentDetailView> = {}): IncidentDetailView {
  return {
    id: INC_ID,
    status: 'escalated',
    severity: 'critical',
    disposition: 'escalated_triage',
    source: 'wazuh',
    summary: 'Suspicious login attempt',
    is_awaiting_approval: false,
    created_at: '2026-06-18T09:00:00Z',
    updated_at: '2026-06-18T09:10:00Z',
    evidence: { summary: 'Login from unusual IP' },
    normalized_event: null,
    correlation_id: 'corr-001',
    pending_approval: null,
    audit: [],
    ...overrides,
  }
}

function wrapper({ children }: { children: React.ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return (
    <QueryClientProvider client={qc}>
      <MemoryRouter>{children}</MemoryRouter>
    </QueryClientProvider>
  )
}

describe('IncidentDrawer', () => {
  beforeEach(() => vi.clearAllMocks())

  it('renders nothing when incidentId is null', () => {
    const { container } = render(<IncidentDrawer incidentId={null} onClose={vi.fn()} />, { wrapper })
    expect(container.querySelector('[role="dialog"]')).toBeNull()
  })

  it('renders loading state while fetching', () => {
    mockUseIncidentDetail.mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
      error: null,
    } as ReturnType<typeof incidentsApi.useIncidentDetail>)
    render(<IncidentDrawer incidentId={INC_ID} onClose={vi.fn()} />, { wrapper })
    expect(screen.queryByText(INC_ID)).toBeNull()
  })

  it('renders incident id, severity, status, evidence, and audit', () => {
    mockUseIncidentDetail.mockReturnValue({
      data: makeDetail(),
      isLoading: false,
      isError: false,
      error: null,
    } as ReturnType<typeof incidentsApi.useIncidentDetail>)
    render(<IncidentDrawer incidentId={INC_ID} onClose={vi.fn()} />, { wrapper })
    expect(screen.getByText(INC_ID)).toBeInTheDocument()
    expect(screen.getByText('Critical')).toBeInTheDocument()
    expect(screen.getByText('Login from unusual IP')).toBeInTheDocument()
  })

  it('shows the open-full-incident link pointing at /incidents/:id', () => {
    mockUseIncidentDetail.mockReturnValue({
      data: makeDetail(),
      isLoading: false,
      isError: false,
      error: null,
    } as ReturnType<typeof incidentsApi.useIncidentDetail>)
    render(<IncidentDrawer incidentId={INC_ID} onClose={vi.fn()} />, { wrapper })
    const link = screen.getByRole('link', { name: /open full incident/i })
    expect(link).toHaveAttribute('href', `/incidents/${INC_ID}`)
  })

  it('embeds the ApprovalPanel when a pending approval exists', () => {
    mockUseIncidentDetail.mockReturnValue({
      data: makeDetail({
        status: 'awaiting_approval',
        is_awaiting_approval: true,
        pending_approval: {
          id: 1,
          incident_id: INC_ID,
          plan_id: 'plan-001',
          pending_actions: [{ action_id: 'isolate_host', target: 'srv-01' }],
          rationale: 'Host shows signs of compromise.',
          status: 'pending',
          deadline_at: '2026-06-18T10:00:00Z',
          created_at: '2026-06-18T09:00:00Z',
          is_actionable: true,
        },
      }),
      isLoading: false,
      isError: false,
      error: null,
    } as ReturnType<typeof incidentsApi.useIncidentDetail>)
    render(<IncidentDrawer incidentId={INC_ID} onClose={vi.fn()} />, { wrapper })
    expect(screen.getByText(/human approval required/i)).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run tests/incident-drawer.test.tsx`
Expected: FAIL with "Failed to resolve import @/features/map/IncidentDrawer"

- [ ] **Step 3: Write minimal implementation**

```typescript
// frontend/src/features/map/IncidentDrawer.tsx
import { Link } from 'react-router-dom'
import { ExternalLink, GitBranch } from 'lucide-react'
import { useIncidentDetail } from '@/api/incidents'
import { SeverityBadge } from '@/components/SeverityBadge'
import { StatusBadge } from '@/components/StatusBadge'
import { ErrorState } from '@/components/ErrorState'
import { Skeleton } from '@/components/ui/skeleton'
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetDescription } from '@/components/ui/sheet'
import { ApprovalPanel } from '@/features/approvals/ApprovalPanel'
import { EvidencePanel } from './EvidencePanel'
import { AuditTrail } from './AuditTrail'

interface IncidentDrawerProps {
  incidentId: string | null
  onClose: () => void
}

export function IncidentDrawer({ incidentId, onClose }: IncidentDrawerProps) {
  const { data, isLoading, isError, error } = useIncidentDetail(incidentId ?? undefined)

  return (
    <Sheet open={!!incidentId} onOpenChange={(open) => { if (!open) onClose() }}>
      <SheetContent>
        {isLoading && (
          <div className="space-y-4">
            <Skeleton className="h-8 w-48" />
            <Skeleton className="h-32 w-full" />
          </div>
        )}

        {isError && (
          <ErrorState
            message={`Failed to load incident: ${(error as Error)?.message ?? 'unknown error'}`}
          />
        )}

        {data && (
          <div className="space-y-5">
            <SheetHeader>
              <div className="flex items-center gap-2 flex-wrap">
                <SeverityBadge severity={data.severity} />
                <StatusBadge status={data.status} />
              </div>
              <SheetTitle className="font-mono text-sm break-all">{data.id}</SheetTitle>
              <SheetDescription>
                Source: {data.source} · Updated: {new Date(data.updated_at).toLocaleString()}
              </SheetDescription>
            </SheetHeader>

            {data.pending_approval && <ApprovalPanel approval={data.pending_approval} />}

            <EvidencePanel evidence={data.evidence} />

            {data.correlation_id && (
              <Link
                to={`/incidents/${data.id}/trace`}
                className="inline-flex items-center gap-1.5 text-sm text-cyan-400 hover:text-cyan-300 transition-colors cursor-pointer"
              >
                <GitBranch className="w-4 h-4" aria-hidden="true" />
                View pipeline trace
              </Link>
            )}

            <AuditTrail audit={data.audit} />

            <Link
              to={`/incidents/${data.id}`}
              className="inline-flex items-center gap-1.5 text-sm text-slate-400 hover:text-slate-200 transition-colors cursor-pointer"
            >
              <ExternalLink className="w-4 h-4" aria-hidden="true" />
              Open full incident ↗
            </Link>
          </div>
        )}
      </SheetContent>
    </Sheet>
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run tests/incident-drawer.test.tsx`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/map/IncidentDrawer.tsx frontend/tests/incident-drawer.test.tsx
git commit -m "feat(map): add IncidentDrawer slide-over with evidence, approval, and audit"
```

---

### Task 8: `HumanAttentionLane.tsx` — awaiting (actionable) + escalated (read-only) cards

**Files:**
- Create: `frontend/src/features/map/HumanAttentionLane.tsx`
- Test: `frontend/tests/human-attention-lane.test.tsx`

**Interfaces:**
- Consumes: `usePendingApprovals`, `useApprovalDecision` from `@/api/approvals` (existing, unmodified); `useIncidentQueue` from `@/api/incidents` (existing, unmodified) called with `{ view: 'all', status: ['escalated'], sort: '-updated_at', limit: 20 }` (per the AND-filter constraint); `DecisionDialog` from `@/features/approvals/DecisionDialog` (existing, unmodified); `DeadlineCountdown` from `@/features/approvals/DeadlineCountdown` (existing, unmodified); `SeverityBadge`, `StatusBadge`.
- Produces: `HumanAttentionLane({ onSelectIncident }: { onSelectIncident: (incidentId: string) => void })`. Consumed by Task 10 (`PipelineMap`).

This is the wide, content-rich panel addressing the user's feedback that the page is "small and poor" with no visibility into escalated incidents beyond a bare count — it renders the actual awaiting/escalated incidents with rationale, deadline, severity, and summary.

- [ ] **Step 1: Write the failing test**

```typescript
// frontend/tests/human-attention-lane.test.tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { HumanAttentionLane } from '@/features/map/HumanAttentionLane'
import * as approvalsApi from '@/api/approvals'
import * as incidentsApi from '@/api/incidents'
import type { ApprovalSummary } from '@/api/approvals'
import type { IncidentSummary } from '@/api/incidents'

vi.mock('@/api/approvals', () => ({
  usePendingApprovals: vi.fn(),
  useApprovalDecision: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
}))

vi.mock('@/api/incidents', () => ({
  useIncidentQueue: vi.fn(),
}))

const mockUsePendingApprovals = vi.mocked(approvalsApi.usePendingApprovals)
const mockUseIncidentQueue = vi.mocked(incidentsApi.useIncidentQueue)

function makeApproval(overrides: Partial<ApprovalSummary> = {}): ApprovalSummary {
  return {
    id: 1,
    incident_id: '00000000-0000-0000-0000-000000000001',
    plan_id: 'plan-001',
    pending_actions: [{ action_id: 'isolate_host', target: 'srv-01' }],
    rationale: 'Host shows signs of compromise.',
    status: 'pending',
    deadline_at: '2026-06-18T10:00:00Z',
    created_at: '2026-06-18T09:00:00Z',
    ...overrides,
  }
}

function makeEscalated(overrides: Partial<IncidentSummary> = {}): IncidentSummary {
  return {
    id: '00000000-0000-0000-0000-000000000002',
    status: 'escalated',
    severity: 'critical',
    disposition: 'escalated_triage',
    source: 'wazuh',
    summary: 'Repeated privilege escalation attempts',
    is_awaiting_approval: false,
    created_at: '2026-06-18T08:00:00Z',
    updated_at: '2026-06-18T08:30:00Z',
    ...overrides,
  }
}

function emptyQueuePage() {
  return { items: [], total: 0, limit: 20, offset: 0, view: 'all' as const, applied_filters: { status: [], severity: [], sort: '' } }
}

function wrapper({ children }: { children: React.ReactNode }) {
  return <MemoryRouter>{children}</MemoryRouter>
}

describe('HumanAttentionLane', () => {
  beforeEach(() => vi.clearAllMocks())

  it('queries escalated incidents with view=all and status=escalated', () => {
    mockUsePendingApprovals.mockReturnValue({ data: { approvals: [] }, isLoading: false } as ReturnType<typeof approvalsApi.usePendingApprovals>)
    mockUseIncidentQueue.mockReturnValue({ data: emptyQueuePage(), isLoading: false } as ReturnType<typeof incidentsApi.useIncidentQueue>)
    render(<HumanAttentionLane onSelectIncident={vi.fn()} />, { wrapper })
    expect(mockUseIncidentQueue).toHaveBeenCalledWith(
      expect.objectContaining({ view: 'all', status: ['escalated'] })
    )
  })

  it('renders an awaiting-approval card with rationale and deadline', () => {
    mockUsePendingApprovals.mockReturnValue({ data: { approvals: [makeApproval()] }, isLoading: false } as ReturnType<typeof approvalsApi.usePendingApprovals>)
    mockUseIncidentQueue.mockReturnValue({ data: emptyQueuePage(), isLoading: false } as ReturnType<typeof incidentsApi.useIncidentQueue>)
    render(<HumanAttentionLane onSelectIncident={vi.fn()} />, { wrapper })
    expect(screen.getByText('Host shows signs of compromise.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /approve remediation/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /reject remediation/i })).toBeInTheDocument()
  })

  it('renders an escalated card as read-only with no approve/reject buttons', () => {
    mockUsePendingApprovals.mockReturnValue({ data: { approvals: [] }, isLoading: false } as ReturnType<typeof approvalsApi.usePendingApprovals>)
    mockUseIncidentQueue.mockReturnValue({ data: { ...emptyQueuePage(), items: [makeEscalated()] }, isLoading: false } as ReturnType<typeof incidentsApi.useIncidentQueue>)
    render(<HumanAttentionLane onSelectIncident={vi.fn()} />, { wrapper })
    expect(screen.getByText('Repeated privilege escalation attempts')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /approve remediation/i })).toBeNull()
  })

  it('calls onSelectIncident when an escalated card is clicked', async () => {
    const onSelectIncident = vi.fn()
    mockUsePendingApprovals.mockReturnValue({ data: { approvals: [] }, isLoading: false } as ReturnType<typeof approvalsApi.usePendingApprovals>)
    mockUseIncidentQueue.mockReturnValue({ data: { ...emptyQueuePage(), items: [makeEscalated()] }, isLoading: false } as ReturnType<typeof incidentsApi.useIncidentQueue>)
    const { default: userEvent } = await import('@testing-library/user-event')
    render(<HumanAttentionLane onSelectIncident={onSelectIncident} />, { wrapper })
    await userEvent.click(screen.getByRole('button', { name: /view detail/i }))
    expect(onSelectIncident).toHaveBeenCalledWith('00000000-0000-0000-0000-000000000002')
  })

  it('shows an empty message when nothing needs human attention', () => {
    mockUsePendingApprovals.mockReturnValue({ data: { approvals: [] }, isLoading: false } as ReturnType<typeof approvalsApi.usePendingApprovals>)
    mockUseIncidentQueue.mockReturnValue({ data: emptyQueuePage(), isLoading: false } as ReturnType<typeof incidentsApi.useIncidentQueue>)
    render(<HumanAttentionLane onSelectIncident={vi.fn()} />, { wrapper })
    expect(screen.getByText(/nothing needs your attention/i)).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run tests/human-attention-lane.test.tsx`
Expected: FAIL with "Failed to resolve import @/features/map/HumanAttentionLane"

- [ ] **Step 3: Write minimal implementation**

```typescript
// frontend/src/features/map/HumanAttentionLane.tsx
import { useState } from 'react'
import { ShieldAlert, AlertTriangle } from 'lucide-react'
import { usePendingApprovals, useApprovalDecision } from '@/api/approvals'
import { useIncidentQueue } from '@/api/incidents'
import type { ApprovalSummary } from '@/api/approvals'
import type { IncidentSummary } from '@/api/incidents'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { SeverityBadge } from '@/components/SeverityBadge'
import { DeadlineCountdown } from '@/features/approvals/DeadlineCountdown'
import { DecisionDialog } from '@/features/approvals/DecisionDialog'

interface HumanAttentionLaneProps {
  onSelectIncident: (incidentId: string) => void
}

function AwaitingCard({ approval, onOpen }: { approval: ApprovalSummary; onOpen: () => void }) {
  const [pendingDecision, setPendingDecision] = useState<'approve' | 'reject' | null>(null)
  const [decided, setDecided] = useState(false)
  const { mutate, isPending } = useApprovalDecision(approval.id)

  function handleConfirm() {
    if (!pendingDecision) return
    mutate(
      { decision: pendingDecision },
      { onSuccess: () => { setPendingDecision(null); setDecided(true) }, onError: () => setPendingDecision(null) }
    )
  }

  return (
    <>
      <Card className="border-amber-500/30 bg-amber-500/5" data-testid={`awaiting-card-${approval.incident_id}`}>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-amber-400 text-sm">
            <ShieldAlert className="w-4 h-4" aria-hidden="true" />
            Awaiting Approval
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <button
            type="button"
            onClick={onOpen}
            aria-label={`Open incident ${approval.incident_id}`}
            className="font-mono text-xs text-slate-400 hover:text-slate-200 transition-colors cursor-pointer truncate block w-full text-left"
          >
            {approval.incident_id}
          </button>
          <p className="text-slate-200 text-sm">{approval.rationale}</p>
          {approval.deadline_at && <DeadlineCountdown deadlineAt={approval.deadline_at} />}
          {decided ? (
            <p className="text-xs text-slate-500 italic">Decision recorded.</p>
          ) : (
            <div className="flex gap-3">
              <Button
                variant="default"
                size="sm"
                onClick={() => setPendingDecision('approve')}
                disabled={isPending}
                aria-label="Approve remediation"
                className="bg-cyan-500 hover:bg-cyan-400 text-slate-950"
              >
                Approve
              </Button>
              <Button
                variant="destructive"
                size="sm"
                onClick={() => setPendingDecision('reject')}
                disabled={isPending}
                aria-label="Reject remediation"
              >
                Reject
              </Button>
            </div>
          )}
        </CardContent>
      </Card>
      {pendingDecision && (
        <DecisionDialog
          open={!!pendingDecision}
          onOpenChange={(open) => { if (!open) setPendingDecision(null) }}
          decision={pendingDecision}
          onConfirm={handleConfirm}
          isLoading={isPending}
        />
      )}
    </>
  )
}

function EscalatedCard({ incident, onOpen }: { incident: IncidentSummary; onOpen: () => void }) {
  return (
    <Card className="border-orange-500/30 bg-orange-500/5" data-testid={`escalated-card-${incident.id}`}>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-orange-400 text-sm">
          <AlertTriangle className="w-4 h-4" aria-hidden="true" />
          Escalated
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-2">
        <div className="flex items-center gap-2 flex-wrap">
          <SeverityBadge severity={incident.severity} />
          <span className="font-mono text-[11px] text-slate-500 truncate">{incident.id}</span>
        </div>
        {incident.summary && <p className="text-slate-200 text-sm">{incident.summary}</p>}
        <Button variant="outline" size="sm" onClick={onOpen} aria-label="View detail">
          View detail
        </Button>
      </CardContent>
    </Card>
  )
}

export function HumanAttentionLane({ onSelectIncident }: HumanAttentionLaneProps) {
  const { data: pending } = usePendingApprovals()
  const { data: escalatedPage } = useIncidentQueue({
    view: 'all',
    status: ['escalated'],
    sort: '-updated_at',
    limit: 20,
  })

  const awaiting = pending?.approvals ?? []
  const escalated = escalatedPage?.items ?? []
  const isEmpty = awaiting.length === 0 && escalated.length === 0

  return (
    <div className="space-y-3" data-testid="human-attention-lane">
      <h2 className="text-sm font-semibold uppercase tracking-wider text-slate-400">
        Human Attention
      </h2>
      {isEmpty ? (
        <p className="text-sm text-slate-600 italic py-6">Nothing needs your attention right now.</p>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
          {awaiting.map((approval) => (
            <AwaitingCard
              key={approval.id}
              approval={approval}
              onOpen={() => onSelectIncident(approval.incident_id)}
            />
          ))}
          {escalated.map((incident) => (
            <EscalatedCard
              key={incident.id}
              incident={incident}
              onOpen={() => onSelectIncident(incident.id)}
            />
          ))}
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run tests/human-attention-lane.test.tsx`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/map/HumanAttentionLane.tsx frontend/tests/human-attention-lane.test.tsx
git commit -m "feat(map): add HumanAttentionLane with actionable awaiting and read-only escalated cards"
```

---

### Task 9: `JourneyOverlay.tsx` — `useJourney` hook + journey summary strip

**Files:**
- Create: `frontend/src/features/map/JourneyOverlay.tsx`
- Test: `frontend/tests/journey-overlay.test.tsx`

**Interfaces:**
- Consumes: `useIncidentDetail` from `@/api/incidents`, `useTrace` from `@/api/trace` (both existing, unmodified); `stageForStatus` from `@/features/map/stageStatuses` (Task 1).
- Produces: `useJourney(incidentId: string | null): Journey | null` where `Journey = { currentStage: string | null; visitedStages: Set<string>; timingByStage: Record<string, number> }`; `JourneyOverlay({ incidentId, onClear }: { incidentId: string | null; onClear: () => void })`. Consumed by Task 10 (`PipelineMap`, which reads `useJourney`'s return to compute each `StageNodeCard`'s `dimmed`/`journeyTimingMs` props and each `FlowEdge`'s `highlighted` prop, and renders `<JourneyOverlay>` as the summary strip).

No backend endpoint exists for journey data (design §7.4/§8) — this derives it entirely from the two existing detail/trace reads: `currentStage` from the incident's live status (via `stageForStatus`, Task 1), `visitedStages`/`timingByStage` from parsing `supervisor.stage.{triage|enrichment|response}` span names out of the trace tree (`backend/services/supervisor.py` span-naming convention) plus an implicit `intake` (every incident passes through intake before the supervisor loop, which has no span of its own).

- [ ] **Step 1: Write the failing test**

```typescript
// frontend/tests/journey-overlay.test.tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { renderHook } from '@testing-library/react'
import { useJourney, JourneyOverlay } from '@/features/map/JourneyOverlay'
import * as incidentsApi from '@/api/incidents'
import * as traceApi from '@/api/trace'
import type { IncidentDetailView } from '@/api/incidents'
import type { TraceTreeView } from '@/api/trace'

vi.mock('@/api/incidents', () => ({
  useIncidentDetail: vi.fn(),
}))

vi.mock('@/api/trace', () => ({
  useTrace: vi.fn(),
}))

const mockUseIncidentDetail = vi.mocked(incidentsApi.useIncidentDetail)
const mockUseTrace = vi.mocked(traceApi.useTrace)

const INC_ID = '00000000-0000-0000-0000-000000000001'

function makeDetail(overrides: Partial<IncidentDetailView> = {}): IncidentDetailView {
  return {
    id: INC_ID,
    status: 'responding',
    severity: 'high',
    disposition: null,
    source: 'wazuh',
    summary: null,
    is_awaiting_approval: false,
    created_at: '2026-06-18T09:00:00Z',
    updated_at: '2026-06-18T09:10:00Z',
    evidence: null,
    normalized_event: null,
    correlation_id: 'corr-001',
    pending_approval: null,
    audit: [],
    ...overrides,
  }
}

function makeTrace(): TraceTreeView {
  return {
    correlation_id: 'corr-001',
    root: {
      span_id: 'root', parent_span_id: null, name: 'pipeline', kind: 'internal', status: 'ok',
      started_at: null, ended_at: null, latency_ms: null, llm_model: null, tokens_in: null, tokens_out: null,
      attributes: {}, error_message: null,
    },
    children: {
      root: [
        { span_id: 't1', parent_span_id: 'root', name: 'supervisor.stage.triage', kind: 'internal', status: 'ok', started_at: null, ended_at: null, latency_ms: 800, llm_model: null, tokens_in: null, tokens_out: null, attributes: {}, error_message: null },
        { span_id: 'e1', parent_span_id: 'root', name: 'supervisor.stage.enrichment', kind: 'internal', status: 'ok', started_at: null, ended_at: null, latency_ms: 1200, llm_model: null, tokens_in: null, tokens_out: null, attributes: {}, error_message: null },
      ],
    },
    telemetry: { total_tokens_in: null, total_tokens_out: null, end_to_end_ms: 2000, step_count: 2, error_steps: 0 },
  }
}

describe('useJourney', () => {
  beforeEach(() => vi.clearAllMocks())

  it('returns null when incidentId is null', () => {
    mockUseIncidentDetail.mockReturnValue({ data: undefined } as ReturnType<typeof incidentsApi.useIncidentDetail>)
    mockUseTrace.mockReturnValue({ data: undefined } as ReturnType<typeof traceApi.useTrace>)
    const { result } = renderHook(() => useJourney(null))
    expect(result.current).toBeNull()
  })

  it('returns null while the incident detail has not loaded', () => {
    mockUseIncidentDetail.mockReturnValue({ data: undefined } as ReturnType<typeof incidentsApi.useIncidentDetail>)
    mockUseTrace.mockReturnValue({ data: undefined } as ReturnType<typeof traceApi.useTrace>)
    const { result } = renderHook(() => useJourney(INC_ID))
    expect(result.current).toBeNull()
  })

  it('derives currentStage from the live status', () => {
    mockUseIncidentDetail.mockReturnValue({ data: makeDetail({ status: 'responding' }) } as ReturnType<typeof incidentsApi.useIncidentDetail>)
    mockUseTrace.mockReturnValue({ data: undefined } as ReturnType<typeof traceApi.useTrace>)
    const { result } = renderHook(() => useJourney(INC_ID))
    expect(result.current?.currentStage).toBe('response')
  })

  it('always includes intake in visitedStages once detail is loaded', () => {
    mockUseIncidentDetail.mockReturnValue({ data: makeDetail() } as ReturnType<typeof incidentsApi.useIncidentDetail>)
    mockUseTrace.mockReturnValue({ data: undefined } as ReturnType<typeof traceApi.useTrace>)
    const { result } = renderHook(() => useJourney(INC_ID))
    expect(result.current?.visitedStages.has('intake')).toBe(true)
  })

  it('derives visitedStages and timingByStage from trace span names', () => {
    mockUseIncidentDetail.mockReturnValue({ data: makeDetail() } as ReturnType<typeof incidentsApi.useIncidentDetail>)
    mockUseTrace.mockReturnValue({ data: makeTrace() } as ReturnType<typeof traceApi.useTrace>)
    const { result } = renderHook(() => useJourney(INC_ID))
    expect(result.current?.visitedStages.has('triage')).toBe(true)
    expect(result.current?.visitedStages.has('enrichment')).toBe(true)
    expect(result.current?.visitedStages.has('response')).toBe(false)
    expect(result.current?.timingByStage.triage).toBe(800)
    expect(result.current?.timingByStage.enrichment).toBe(1200)
  })

  it('returns null currentStage for a terminal status', () => {
    mockUseIncidentDetail.mockReturnValue({ data: makeDetail({ status: 'resolved' }) } as ReturnType<typeof incidentsApi.useIncidentDetail>)
    mockUseTrace.mockReturnValue({ data: undefined } as ReturnType<typeof traceApi.useTrace>)
    const { result } = renderHook(() => useJourney(INC_ID))
    expect(result.current?.currentStage).toBeNull()
  })
})

describe('JourneyOverlay', () => {
  beforeEach(() => vi.clearAllMocks())

  it('renders nothing when incidentId is null', () => {
    mockUseIncidentDetail.mockReturnValue({ data: undefined } as ReturnType<typeof incidentsApi.useIncidentDetail>)
    mockUseTrace.mockReturnValue({ data: undefined } as ReturnType<typeof traceApi.useTrace>)
    const { container } = render(<JourneyOverlay incidentId={null} onClear={vi.fn()} />)
    expect(container.firstChild).toBeNull()
  })

  it('renders the visited stage path and a clear control once loaded', () => {
    mockUseIncidentDetail.mockReturnValue({ data: makeDetail() } as ReturnType<typeof incidentsApi.useIncidentDetail>)
    mockUseTrace.mockReturnValue({ data: makeTrace() } as ReturnType<typeof traceApi.useTrace>)
    const onClear = vi.fn()
    render(<JourneyOverlay incidentId={INC_ID} onClear={onClear} />)
    expect(screen.getByText(/intake/i)).toBeInTheDocument()
    expect(screen.getByText(/triage/i)).toBeInTheDocument()
    expect(screen.getByText(/enrichment/i)).toBeInTheDocument()
    screen.getByRole('button', { name: /clear journey/i }).click()
    expect(onClear).toHaveBeenCalledOnce()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run tests/journey-overlay.test.tsx`
Expected: FAIL with "Failed to resolve import @/features/map/JourneyOverlay"

- [ ] **Step 3: Write minimal implementation**

```typescript
// frontend/src/features/map/JourneyOverlay.tsx
import { X } from 'lucide-react'
import { useIncidentDetail } from '@/api/incidents'
import { useTrace } from '@/api/trace'
import { stageForStatus, STAGE_KEYS } from './stageStatuses'

export interface Journey {
  currentStage: string | null
  visitedStages: Set<string>
  timingByStage: Record<string, number>
}

const STAGE_SPAN_RE = /^supervisor\.stage\.(triage|enrichment|response)$/

export function useJourney(incidentId: string | null): Journey | null {
  const { data: detail } = useIncidentDetail(incidentId ?? undefined)
  const { data: trace } = useTrace(incidentId ?? undefined)

  if (!incidentId || !detail) return null

  const visitedStages = new Set<string>(['intake'])
  const timingByStage: Record<string, number> = {}

  if (trace?.root) {
    const allSpans = [trace.root, ...Object.values(trace.children).flat()]
    for (const span of allSpans) {
      const match = STAGE_SPAN_RE.exec(span.name)
      if (!match) continue
      const stage = match[1]
      visitedStages.add(stage)
      if (span.latency_ms != null) {
        timingByStage[stage] = (timingByStage[stage] ?? 0) + span.latency_ms
      }
    }
  }

  return {
    currentStage: stageForStatus(detail.status),
    visitedStages,
    timingByStage,
  }
}

function formatTiming(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

interface JourneyOverlayProps {
  incidentId: string | null
  onClear: () => void
}

export function JourneyOverlay({ incidentId, onClear }: JourneyOverlayProps) {
  const journey = useJourney(incidentId)
  if (!journey) return null

  const path = STAGE_KEYS.filter((key) => journey.visitedStages.has(key))

  return (
    <div
      className="flex items-center gap-2 flex-wrap rounded-lg bg-sky-400/10 border border-sky-400/30 px-3 py-2 text-xs"
      data-testid="journey-overlay"
    >
      <span className="text-sky-300 font-semibold uppercase tracking-wider">Journey:</span>
      {path.map((stage, i) => (
        <span key={stage} className="flex items-center gap-1.5 text-slate-300 capitalize">
          {stage}
          {journey.timingByStage[stage] !== undefined && (
            <span className="font-mono text-cyan-400">{formatTiming(journey.timingByStage[stage])}</span>
          )}
          {i < path.length - 1 && <span className="text-slate-600">&rarr;</span>}
        </span>
      ))}
      <button
        type="button"
        onClick={onClear}
        aria-label="Clear journey"
        className="ml-auto text-slate-500 hover:text-slate-200 transition-colors cursor-pointer"
      >
        <X className="w-3.5 h-3.5" aria-hidden="true" />
      </button>
    </div>
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run tests/journey-overlay.test.tsx`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/map/JourneyOverlay.tsx frontend/tests/journey-overlay.test.tsx
git commit -m "feat(map): add useJourney derivation and JourneyOverlay summary strip"
```

---

### Task 10: Rewire `PipelineMap.tsx` — bigger layout, full integration, e2e, verification

**Files:**
- Modify: `frontend/src/features/map/PipelineMap.tsx`
- Modify: `frontend/tests/pipeline-map.test.tsx`
- Create: `frontend/tests/e2e/pipeline-map.spec.ts`

**Interfaces:**
- Consumes: everything produced by Tasks 1–9 (`StageNodeCard`, `FlowEdge`, `TerminalColumn`, `BranchBreakdown`, `HumanAttentionLane`, `IncidentDrawer`, `JourneyOverlay` + `useJourney`), plus existing `useAnimatedPipeline` (unmodified) and `useSearchParams` from `react-router-dom`.
- Produces: the page itself — no other task depends on `PipelineMap.tsx`'s internals.

This is the task that directly answers "it may be bigger covering the page": the container becomes a full-height flex column (`min-h-[calc(100vh-4rem)]`) with the rail+terminals row and the new full-width `HumanAttentionLane` row stacked to fill the viewport, instead of a small rail floating above empty space.

- [ ] **Step 1: Write the failing test additions**

In `frontend/tests/pipeline-map.test.tsx`, add four new `vi.mock` calls immediately after the existing `vi.mock('@/features/map/useAnimatedPipeline', ...)` block (around line 12):

```typescript
vi.mock('@/features/map/HumanAttentionLane', () => ({
  HumanAttentionLane: () => <div data-testid="human-attention-lane-mock" />,
}))

vi.mock('@/features/map/IncidentDrawer', () => ({
  IncidentDrawer: ({ incidentId }: { incidentId: string | null }) => (
    <div data-testid="incident-drawer-mock">{incidentId ?? 'none'}</div>
  ),
}))

vi.mock('@/features/map/BranchBreakdown', () => ({
  BranchBreakdown: ({ stage }: { stage: { key: string } }) => (
    <div data-testid={`branch-breakdown-mock-${stage.key}`} />
  ),
}))

vi.mock('@/features/map/JourneyOverlay', () => ({
  JourneyOverlay: () => null,
  useJourney: vi.fn(() => null),
}))
```

Then add these `it` blocks inside the existing `describe('PipelineMap', ...)` block, just before its closing `})`:

```typescript
  it('renders the Human Attention lane below the rail', () => {
    mockUseAnimatedPipeline.mockReturnValue(
      baseAnimatedPipeline() as ReturnType<typeof animatedApi.useAnimatedPipeline>
    )
    render(<PipelineMap />, { wrapper: ({ children }) => <MemoryRouter>{children}</MemoryRouter> })
    expect(screen.getByTestId('human-attention-lane-mock')).toBeInTheDocument()
  })

  it('expands a stage to reveal its branch breakdown when the expand toggle is clicked', async () => {
    mockUseAnimatedPipeline.mockReturnValue(
      baseAnimatedPipeline() as ReturnType<typeof animatedApi.useAnimatedPipeline>
    )
    const { default: userEvent } = await import('@testing-library/user-event')
    render(<PipelineMap />, { wrapper: ({ children }) => <MemoryRouter>{children}</MemoryRouter> })
    expect(screen.queryByTestId('branch-breakdown-mock-triage')).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: /expand triage/i }))
    expect(screen.getByTestId('branch-breakdown-mock-triage')).toBeInTheDocument()
  })

  it('collapses an expanded stage when its toggle is clicked again', async () => {
    mockUseAnimatedPipeline.mockReturnValue(
      baseAnimatedPipeline() as ReturnType<typeof animatedApi.useAnimatedPipeline>
    )
    const { default: userEvent } = await import('@testing-library/user-event')
    render(<PipelineMap />, { wrapper: ({ children }) => <MemoryRouter>{children}</MemoryRouter> })
    await userEvent.click(screen.getByRole('button', { name: /expand triage/i }))
    await userEvent.click(screen.getByRole('button', { name: /collapse triage/i }))
    expect(screen.queryByTestId('branch-breakdown-mock-triage')).toBeNull()
  })

  it('passes the incident id from the URL query param to the drawer', () => {
    mockUseAnimatedPipeline.mockReturnValue(
      baseAnimatedPipeline() as ReturnType<typeof animatedApi.useAnimatedPipeline>
    )
    render(<PipelineMap />, {
      wrapper: ({ children }) => (
        <MemoryRouter initialEntries={['/map?incident=00000000-0000-0000-0000-000000000099']}>
          {children}
        </MemoryRouter>
      ),
    })
    expect(screen.getByTestId('incident-drawer-mock')).toHaveTextContent(
      '00000000-0000-0000-0000-000000000099'
    )
  })

  it('passes no incident id to the drawer when none is selected', () => {
    mockUseAnimatedPipeline.mockReturnValue(
      baseAnimatedPipeline() as ReturnType<typeof animatedApi.useAnimatedPipeline>
    )
    render(<PipelineMap />, { wrapper: ({ children }) => <MemoryRouter>{children}</MemoryRouter> })
    expect(screen.getByTestId('incident-drawer-mock')).toHaveTextContent('none')
  })
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run tests/pipeline-map.test.tsx`
Expected: FAIL — `human-attention-lane-mock` not found, no `button` named `/expand triage/i` reachable from a clean render (current `PipelineMap.tsx` renders neither `HumanAttentionLane` nor an expand-capable rail, and ignores the `incident` query param)

- [ ] **Step 3: Write the implementation**

```typescript
// frontend/src/features/map/PipelineMap.tsx
import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Play, Pause } from 'lucide-react'
import { useAnimatedPipeline } from './useAnimatedPipeline'
import { StageNodeCard } from './StageNode'
import { FlowEdge } from './FlowEdge'
import { TerminalColumn } from './TerminalColumn'
import { BranchBreakdown } from './BranchBreakdown'
import { HumanAttentionLane } from './HumanAttentionLane'
import { IncidentDrawer } from './IncidentDrawer'
import { JourneyOverlay, useJourney } from './JourneyOverlay'
import { Skeleton } from '@/components/ui/skeleton'
import { ErrorState } from '@/components/ErrorState'
import { EmptyState } from '@/components/EmptyState'
import { Button } from '@/components/ui/button'

export function PipelineMap() {
  const {
    snapshot,
    isLoading,
    error,
    changedStageKeys,
    changedTerminalKeys,
    paused,
    togglePaused,
  } = useAnimatedPipeline()

  const [searchParams, setSearchParams] = useSearchParams()
  const selectedIncidentId = searchParams.get('incident')
  const [expandedStage, setExpandedStage] = useState<string | null>(null)
  const journey = useJourney(selectedIncidentId)

  function selectIncident(id: string) {
    const next = new URLSearchParams(searchParams)
    next.set('incident', id)
    setSearchParams(next)
  }

  function clearSelection() {
    const next = new URLSearchParams(searchParams)
    next.delete('incident')
    setSearchParams(next)
  }

  function toggleExpand(stageKey: string) {
    setExpandedStage((current) => (current === stageKey ? null : stageKey))
  }

  if (isLoading) {
    return (
      <div className="space-y-4" aria-label="Loading pipeline map">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-32 w-full" />
      </div>
    )
  }

  if (error) {
    return (
      <ErrorState
        message={`Failed to load pipeline map: ${(error as Error)?.message ?? 'unknown error'}`}
      />
    )
  }

  if (!snapshot) return null

  const isEmpty =
    snapshot.stages.every((s) => s.in_flight === 0) &&
    snapshot.terminals.resolved === 0 &&
    snapshot.terminals.escalated === 0 &&
    snapshot.terminals.awaiting === 0

  return (
    <div className="flex flex-col min-h-[calc(100vh-4rem)] gap-6" data-testid="pipeline-map">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold text-slate-100">Pipeline Map</h1>
          <p className="text-xs text-slate-500 mt-0.5">
            Window: last {snapshot.window_hours}h · Updated{' '}
            {new Date(snapshot.generated_at).toLocaleTimeString()}
          </p>
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={togglePaused}
          aria-label={paused ? 'Paused, click to resume live updates' : 'Live, click to pause updates'}
        >
          {paused ? (
            <>
              <Play className="w-3.5 h-3.5" aria-hidden="true" />
              Paused
            </>
          ) : (
            <>
              <Pause className="w-3.5 h-3.5" aria-hidden="true" />
              Live
            </>
          )}
        </Button>
      </div>

      {isEmpty ? (
        <EmptyState
          title="No incidents in flight"
          description="The pipeline is quiet right now."
        />
      ) : (
        <div className="flex flex-col gap-6 flex-1">
          {selectedIncidentId && <JourneyOverlay incidentId={selectedIncidentId} onClear={clearSelection} />}

          <div className="flex flex-col xl:flex-row gap-6 items-start">
            <div className="flex items-start flex-wrap gap-y-4 flex-1">
              {snapshot.stages.map((stage, i) => (
                <div key={stage.key} className="flex items-start">
                  <div className="flex flex-col">
                    <StageNodeCard
                      stage={stage}
                      justChanged={changedStageKeys.has(stage.key)}
                      expanded={expandedStage === stage.key}
                      onToggleExpand={() => toggleExpand(stage.key)}
                      dimmed={!!journey && !journey.visitedStages.has(stage.key)}
                      journeyTimingMs={journey?.timingByStage[stage.key]}
                    />
                    {expandedStage === stage.key && (
                      <BranchBreakdown stage={stage} onSelectIncident={selectIncident} />
                    )}
                  </div>
                  {i < snapshot.stages.length - 1 && (
                    <FlowEdge
                      active={
                        changedStageKeys.has(stage.key) ||
                        changedStageKeys.has(snapshot.stages[i + 1].key)
                      }
                      highlighted={
                        !!journey &&
                        journey.visitedStages.has(stage.key) &&
                        journey.visitedStages.has(snapshot.stages[i + 1].key)
                      }
                    />
                  )}
                </div>
              ))}
            </div>
            <TerminalColumn terminals={snapshot.terminals} changedKeys={changedTerminalKeys} />
          </div>

          <HumanAttentionLane onSelectIncident={selectIncident} />
        </div>
      )}

      <IncidentDrawer incidentId={selectedIncidentId} onClose={clearSelection} />
    </div>
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run tests/pipeline-map.test.tsx`
Expected: PASS (all `StageNodeCard`/`FlowEdge`/`TerminalColumn`/`PipelineMap` tests, old and new)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/map/PipelineMap.tsx frontend/tests/pipeline-map.test.tsx
git commit -m "feat(map): rewire PipelineMap with full-height layout, attention lane, drawer, and journey overlay"
```

- [ ] **Step 6: Add the e2e spec**

```typescript
// frontend/tests/e2e/pipeline-map.spec.ts
/**
 * Playwright e2e: pipeline map loads, a stage expands, the human-attention
 * lane shows real incidents, and selecting one opens the drawer.
 *
 * Requires the full stack (frontend + backend) running. Run with:
 *   docker compose up --build, then ARGUS_E2E=1 npx playwright test pipeline-map
 *
 * Skipped when ARGUS_E2E is not set, keeping CI fast.
 */
import { test, expect } from '@playwright/test'

const RUN_E2E = !!process.env.ARGUS_E2E

test.describe('Pipeline map e2e', () => {
  test.skip(!RUN_E2E, 'Set ARGUS_E2E=1 to run full-stack e2e tests')

  async function login(page: import('@playwright/test').Page) {
    await page.goto('/login')
    await page.getByLabel(/username/i).fill('admin')
    await page.getByLabel(/password/i).fill(process.env.ARGUS_ADMIN_PASS ?? 'admin123')
    await page.getByRole('button', { name: /sign in/i }).click()
    await page.waitForURL('/queue')
  }

  test('map loads and shows the rail and human attention lane', async ({ page }) => {
    await login(page)
    await page.goto('/map')
    await expect(page.getByTestId('pipeline-map')).toBeVisible({ timeout: 5000 })
    await expect(page.getByText(/human attention/i)).toBeVisible()
  })

  test('expanding a stage reveals its branch breakdown', async ({ page }) => {
    await login(page)
    await page.goto('/map')
    await expect(page.getByTestId('pipeline-map')).toBeVisible({ timeout: 5000 })
    await page.getByRole('button', { name: /expand triage/i }).click()
    await expect(page.getByTestId('branch-breakdown-triage')).toBeVisible({ timeout: 5000 })
  })

  test('selecting an escalated incident opens the drawer with its detail', async ({ page }) => {
    await login(page)
    await page.goto('/map')
    await expect(page.getByTestId('pipeline-map')).toBeVisible({ timeout: 5000 })
    const card = page.getByText(/escalated/i).first()
    if (await card.isVisible().catch(() => false)) {
      await card.click()
      await expect(page.getByRole('dialog')).toBeVisible({ timeout: 5000 })
    }
  })
})
```

- [ ] **Step 7: Commit the e2e spec**

```bash
git add frontend/tests/e2e/pipeline-map.spec.ts
git commit -m "test(e2e): add pipeline map e2e spec for stage expand and drawer"
```

- [ ] **Step 8: Final verification**

Run each of these from `frontend/` and confirm the stated result before considering M-c done:

```bash
npm test
```
Expected: all suites pass, including `stage-statuses.test.ts`, `branch-breakdown.test.tsx`, `incident-drawer.test.tsx`, `human-attention-lane.test.tsx`, `journey-overlay.test.tsx`, and the extended `pipeline-map.test.tsx`.

```bash
npm run lint
```
Expected: no errors.

```bash
npm run build
```
Expected: `tsc -b` and `vite build` both succeed with no type errors.

Then do a manual smoke check per repo UI-testing guidance: `npm run dev`, sign in, navigate to `/map`, and confirm: the page now fills the viewport (rail + Human Attention lane stacked, no large empty area below), expanding a stage shows real in-flight incidents (not just a count), the Human Attention lane lists real awaiting/escalated incidents with rationale/summary, clicking one opens the drawer with evidence/audit, and selecting an incident dims non-visited stages on the rail. `npm run test:e2e` (with `ARGUS_E2E=1` and the full stack via `docker compose up --build`) is optional here since it requires the running stack — note explicitly if it was not run.

---

## Self-Review

**1. Spec coverage** (design doc §5/§10 M-c scope): `HumanAttentionLane` → Task 8. `IncidentDrawer` → Task 7 (+ primitive in Task 5, + standalone evidence/audit in Task 6). `BranchBreakdown` expand → Task 4 (+ the expand affordance on `StageNodeCard` in Task 2). `JourneyOverlay` → Task 9 (+ the dim/highlight wiring on `StageNodeCard`/`FlowEdge` in Tasks 2/3). e2e → Task 10 Step 6. User's richness/sizing feedback → Task 10's full-height layout (Step 3) + `BranchBreakdown`'s live in-stage incident list (Task 4) + `HumanAttentionLane`'s real incident cards (Task 8). All design-doc isolation constraints (§3) respected — no pre-existing dashboard file is modified.

**2. Placeholder scan:** no "TBD"/"TODO"/"add error handling" phrases; every step has complete, runnable code; no step says "similar to Task N" without repeating the code.

**3. Type consistency:** `StageNode`/`BranchOutflow`/`TerminalCounts` (from `@/api/pipeline`, unmodified) used identically across Tasks 2–4 and 10. `IncidentSummary`/`IncidentDetailView`/`QueueFilters`/`AuditView` (from `@/api/incidents`, unmodified) used identically across Tasks 4, 7, 8. `ApprovalSummary`/`DecisionRequest` (from `@/api/approvals`, unmodified) used identically in Task 8. `Journey` type defined once in Task 9 (`JourneyOverlay.tsx`) and consumed by name only in Task 10 (no redefinition). `STAGE_KEYS`/`STAGE_STATUSES`/`stageForStatus` defined once in Task 1 and imported by name (not redeclared) in Tasks 4, 9, 10. `onSelectIncident`/`onToggleExpand`/`onClear`/`onClose` callback names are consistent at each call site between the producing and consuming tasks.

