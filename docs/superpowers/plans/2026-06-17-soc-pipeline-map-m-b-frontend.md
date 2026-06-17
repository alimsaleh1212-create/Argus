# SOC Pipeline Map — M-b (Live Rail Frontend) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the live pipeline rail — a new `/map` page showing the 4-stage rail (Intake/Triage/Enrichment/Response) with live counts, a connector between adjacent stages that flashes on flow, and a terminal column (Resolved/Escalated/Awaiting), all polling the M-a `GET /incidents/pipeline` endpoint every 2s with minimal, reduced-motion-aware animation.

**Architecture:** A thin `api/pipeline.ts` mirrors the existing `api/kpis.ts` data-fetching pattern (react-query) plus one pure `diffSnapshots` function. A separate `features/map/useAnimatedPipeline.ts` hook composes polling + diffing + a transient (300ms) "just changed" flag + a pause toggle + reduced-motion detection, so presentational components stay simple. Three small presentational components (`StageNode.tsx`, `FlowEdge.tsx`, `TerminalColumn.tsx`) render the rail; `PipelineMap.tsx` is the page that wires them together, following `KpiDashboard.tsx`'s loading/error/empty pattern exactly.

**Tech Stack:** React 19, TypeScript, `@tanstack/react-query` v5, Vitest + `@testing-library/react` (incl. `renderHook`), Tailwind v4 (CSS-based `@theme`), `lucide-react` icons, existing `cn()` helper.

## Global Constraints

- **Additive only (D6):** new files only, except **exactly two edits** to existing files: one nav item in `frontend/src/components/AppShell.tsx`, one route in `frontend/src/router.tsx`. No other existing file changes.
- **Polling interval:** `refetchInterval: 2000` (2s), matching design §7.
- **Motion rules (design §6):** animate only the 1-2 elements that changed per tick; ease-out for entering / ease-in for exiting; `prefers-reduced-motion` → no pulse/flash/roll (instant value swap); a Live/Pause toggle freezes polling.
- **Color is never the only signal** (design §6): every status tile pairs an icon with its color.
- **Palette:** reuse existing literal Tailwind classes already used throughout the app (`bg-slate-950`, `bg-slate-900`, `border-slate-700`/`border-slate-800`, `text-slate-100`, `text-green-400`, `text-amber-400`, `text-orange-400`) — do not introduce the unused `--color-*` custom theme tokens; follow the established convention in `KpiDashboard.tsx`/`IncidentDetail.tsx`/`StatCards.tsx`.
- **No new dependencies.** No animation library exists in `package.json` (no framer-motion) — use CSS transitions/`@keyframes` + Tailwind utility classes only.
- **`prefers-reduced-motion` is already enforced globally** in `frontend/src/styles/globals.css:69-75` (forces all `animation-duration`/`transition-duration` to `0.01ms`). This plan additionally gates the *JS-level* "just changed" flag itself (via `usePrefersReducedMotion`) so reduced-motion users never even receive a transient flash class — defense in depth, and the only way to unit-test the reduced-motion branch under jsdom.
- **Naming collision avoidance:** the backend DTO is named `StageNode` (already defined in `frontend/src/api/pipeline.ts` per this plan). The React component file is `features/map/StageNode.tsx` per the design's file table, but its exported component is named **`StageNodeCard`** (not `StageNode`) to avoid shadowing the type when both are imported in the same file (e.g. `PipelineMap.tsx`).
- **Tests:** run only the new file — `npx vitest run tests/pipeline.test.tsx` — never the full `npm test` while iterating (fast enough to run in full once at the end, per Task 7).

---

## File Structure

- **Create** `frontend/src/api/pipeline.ts` — types (`BranchOutflow`, `StageNode`, `TerminalCounts`, `PipelineSnapshot`, `PipelineDelta`) + `usePipeline()` (react-query) + pure `diffSnapshots()`.
- **Create** `frontend/src/features/map/useAnimatedPipeline.ts` — `usePrefersReducedMotion()` + `useAnimatedPipeline()` (composes polling, diffing, transient flash timing, pause toggle).
- **Create** `frontend/src/features/map/StageNode.tsx` — `StageNodeCard` component (one rail tile).
- **Create** `frontend/src/features/map/FlowEdge.tsx` — `FlowEdge` component (connector between two adjacent rail tiles).
- **Create** `frontend/src/features/map/TerminalColumn.tsx` — `TerminalColumn` component (Resolved/Escalated/Awaiting tiles).
- **Create** `frontend/src/features/map/PipelineMap.tsx` — `PipelineMap` page component (loading/error/empty states, Live/Pause button, composes the rail).
- **Modify** `frontend/src/components/AppShell.tsx` — add one nav item.
- **Modify** `frontend/src/router.tsx` — add one lazy route.
- **Create** `frontend/tests/pipeline.test.tsx` — covers the data layer (Tasks 1-2: `diffSnapshots`, `usePipeline`, `useAnimatedPipeline`, `usePrefersReducedMotion`).
- **Create** `frontend/tests/pipeline-map.test.tsx` — covers the UI layer (Tasks 3-6: `StageNodeCard`, `FlowEdge`, `TerminalColumn`, `PipelineMap`). Kept separate from `pipeline.test.tsx` so each task's import block is self-contained rather than growing across 6 tasks in one file.

---

### Task 1: `api/pipeline.ts` — types, `diffSnapshots`, `usePipeline`

**Files:**
- Create: `frontend/src/api/pipeline.ts`
- Test: `frontend/tests/pipeline.test.tsx`

**Interfaces:**
- Consumes: `apiFetch<T>(path, options?)` from `frontend/src/api/client.ts` (existing).
- Produces (used by Task 2 and all later tasks):
  - `interface BranchOutflow { to: string; count: number }`
  - `interface StageNode { key: string; label: string; in_flight: number; branches: BranchOutflow[] }`
  - `interface TerminalCounts { resolved: number; escalated: number; awaiting: number }`
  - `interface PipelineSnapshot { stages: StageNode[]; terminals: TerminalCounts; window_hours: number; generated_at: string }`
  - `interface PipelineDelta { changedStageKeys: Set<string>; changedTerminalKeys: Set<keyof TerminalCounts> }`
  - `function diffSnapshots(previous: PipelineSnapshot | undefined, current: PipelineSnapshot): PipelineDelta`
  - `function usePipeline(options?: { paused?: boolean }): UseQueryResult<PipelineSnapshot>` (react-query result: `{ data, isLoading, error, ... }`)

- [ ] **Step 1: Write the failing tests**

Create `frontend/tests/pipeline.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { diffSnapshots, usePipeline, type PipelineSnapshot } from '@/api/pipeline'

function makeSnapshot(overrides: Partial<PipelineSnapshot> = {}): PipelineSnapshot {
  return {
    stages: [
      { key: 'intake', label: 'Intake', in_flight: 2, branches: [] },
      { key: 'triage', label: 'Triage', in_flight: 4, branches: [] },
      { key: 'enrichment', label: 'Enrichment', in_flight: 1, branches: [] },
      { key: 'response', label: 'Response', in_flight: 3, branches: [] },
    ],
    terminals: { resolved: 10, escalated: 2, awaiting: 1 },
    window_hours: 24,
    generated_at: '2026-06-17T12:00:00Z',
    ...overrides,
  }
}

describe('diffSnapshots', () => {
  it('returns empty diff when there is no previous snapshot', () => {
    const delta = diffSnapshots(undefined, makeSnapshot())
    expect(delta.changedStageKeys.size).toBe(0)
    expect(delta.changedTerminalKeys.size).toBe(0)
  })

  it('flags a stage whose in_flight count changed', () => {
    const prev = makeSnapshot()
    const next = makeSnapshot({
      stages: [
        { key: 'intake', label: 'Intake', in_flight: 2, branches: [] },
        { key: 'triage', label: 'Triage', in_flight: 7, branches: [] },
        { key: 'enrichment', label: 'Enrichment', in_flight: 1, branches: [] },
        { key: 'response', label: 'Response', in_flight: 3, branches: [] },
      ],
    })
    const delta = diffSnapshots(prev, next)
    expect(delta.changedStageKeys).toEqual(new Set(['triage']))
  })

  it('does not flag a stage whose in_flight count is unchanged', () => {
    const delta = diffSnapshots(makeSnapshot(), makeSnapshot())
    expect(delta.changedStageKeys.size).toBe(0)
  })

  it('flags a terminal whose count changed', () => {
    const prev = makeSnapshot()
    const next = makeSnapshot({ terminals: { resolved: 11, escalated: 2, awaiting: 1 } })
    const delta = diffSnapshots(prev, next)
    expect(delta.changedTerminalKeys).toEqual(new Set(['resolved']))
  })

  it('flags multiple changed terminals independently', () => {
    const prev = makeSnapshot()
    const next = makeSnapshot({ terminals: { resolved: 10, escalated: 3, awaiting: 0 } })
    const delta = diffSnapshots(prev, next)
    expect(delta.changedTerminalKeys).toEqual(new Set(['escalated', 'awaiting']))
  })
})

describe('usePipeline', () => {
  function wrapper({ children }: { children: React.ReactNode }) {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    return <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  }

  beforeEach(() => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => makeSnapshot(),
      })
    )
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('fetches /incidents/pipeline when not paused', async () => {
    const { result } = renderHook(() => usePipeline(), { wrapper })
    await waitFor(() => expect(result.current.data).toBeDefined())
    expect(fetch).toHaveBeenCalledWith('/incidents/pipeline', expect.anything())
  })

  it('does not fetch when paused', async () => {
    renderHook(() => usePipeline({ paused: true }), { wrapper })
    await new Promise((r) => setTimeout(r, 10))
    expect(fetch).not.toHaveBeenCalled()
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run tests/pipeline.test.tsx`
Expected: FAIL — `Failed to resolve import "@/api/pipeline"`.

- [ ] **Step 3: Implement `api/pipeline.ts`**

Create `frontend/src/api/pipeline.ts`:

```ts
import { useQuery } from '@tanstack/react-query'
import { apiFetch } from './client'

export interface BranchOutflow {
  to: string
  count: number
}

export interface StageNode {
  key: string
  label: string
  in_flight: number
  branches: BranchOutflow[]
}

export interface TerminalCounts {
  resolved: number
  escalated: number
  awaiting: number
}

export interface PipelineSnapshot {
  stages: StageNode[]
  terminals: TerminalCounts
  window_hours: number
  generated_at: string
}

export interface PipelineDelta {
  changedStageKeys: Set<string>
  changedTerminalKeys: Set<keyof TerminalCounts>
}

const EMPTY_DELTA: PipelineDelta = {
  changedStageKeys: new Set(),
  changedTerminalKeys: new Set(),
}

export function diffSnapshots(
  previous: PipelineSnapshot | undefined,
  current: PipelineSnapshot
): PipelineDelta {
  if (!previous) return EMPTY_DELTA

  const changedStageKeys = new Set<string>()
  for (const stage of current.stages) {
    const prevStage = previous.stages.find((s) => s.key === stage.key)
    if (!prevStage || prevStage.in_flight !== stage.in_flight) {
      changedStageKeys.add(stage.key)
    }
  }

  const changedTerminalKeys = new Set<keyof TerminalCounts>()
  for (const key of ['resolved', 'escalated', 'awaiting'] as const) {
    if (previous.terminals[key] !== current.terminals[key]) {
      changedTerminalKeys.add(key)
    }
  }

  return { changedStageKeys, changedTerminalKeys }
}

export function usePipeline(options: { paused?: boolean } = {}) {
  const { paused = false } = options
  return useQuery<PipelineSnapshot>({
    queryKey: ['pipeline'],
    queryFn: () => apiFetch<PipelineSnapshot>('/incidents/pipeline'),
    refetchInterval: 2000,
    enabled: !paused,
  })
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npx vitest run tests/pipeline.test.tsx`
Expected: PASS (7 tests: 5 `diffSnapshots` + 2 `usePipeline`).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/pipeline.ts frontend/tests/pipeline.test.tsx
git commit -m "feat(map): Add pipeline API types, diffSnapshots, and usePipeline hook"
```

---

### Task 2: `features/map/useAnimatedPipeline.ts` — reduced motion + animation derivation

**Files:**
- Create: `frontend/src/features/map/useAnimatedPipeline.ts`
- Test: `frontend/tests/pipeline.test.tsx` (append)

**Interfaces:**
- Consumes (Task 1): `usePipeline`, `diffSnapshots`, `PipelineSnapshot`, `TerminalCounts` from `@/api/pipeline`.
- Produces (used by Task 6 `PipelineMap.tsx`):
  - `function usePrefersReducedMotion(): boolean`
  - `function useAnimatedPipeline(): { snapshot: PipelineSnapshot | undefined; isLoading: boolean; error: Error | null; changedStageKeys: Set<string>; changedTerminalKeys: Set<keyof TerminalCounts>; paused: boolean; togglePaused: () => void; prefersReducedMotion: boolean }`

- [ ] **Step 1: Write the failing tests**

Append to `frontend/tests/pipeline.test.tsx`:

```tsx
import { act } from '@testing-library/react'
import * as pipelineApi from '@/api/pipeline'
import { useAnimatedPipeline, usePrefersReducedMotion } from '@/features/map/useAnimatedPipeline'

vi.mock('@/api/pipeline', async () => {
  const actual = await vi.importActual<typeof import('@/api/pipeline')>('@/api/pipeline')
  return { ...actual, usePipeline: vi.fn() }
})

const mockUsePipeline = vi.mocked(pipelineApi.usePipeline)

function mockMatchMedia(matches: boolean) {
  vi.stubGlobal(
    'matchMedia',
    vi.fn().mockImplementation(() => ({
      matches,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }))
  )
}

describe('usePrefersReducedMotion', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('returns false when the media query does not match', () => {
    mockMatchMedia(false)
    const { result } = renderHook(() => usePrefersReducedMotion())
    expect(result.current).toBe(false)
  })

  it('returns true when the media query matches', () => {
    mockMatchMedia(true)
    const { result } = renderHook(() => usePrefersReducedMotion())
    expect(result.current).toBe(true)
  })
})

describe('useAnimatedPipeline', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    mockMatchMedia(false)
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('flags a changed stage for 300ms then clears it', () => {
    mockUsePipeline.mockReturnValue({
      data: makeSnapshot(),
      isLoading: false,
      error: null,
    } as ReturnType<typeof pipelineApi.usePipeline>)
    const { result, rerender } = renderHook(() => useAnimatedPipeline())
    expect(result.current.changedStageKeys.size).toBe(0)

    mockUsePipeline.mockReturnValue({
      data: makeSnapshot({
        stages: [
          { key: 'intake', label: 'Intake', in_flight: 2, branches: [] },
          { key: 'triage', label: 'Triage', in_flight: 9, branches: [] },
          { key: 'enrichment', label: 'Enrichment', in_flight: 1, branches: [] },
          { key: 'response', label: 'Response', in_flight: 3, branches: [] },
        ],
      }),
      isLoading: false,
      error: null,
    } as ReturnType<typeof pipelineApi.usePipeline>)
    rerender()

    expect(result.current.changedStageKeys).toEqual(new Set(['triage']))
    act(() => {
      vi.advanceTimersByTime(300)
    })
    expect(result.current.changedStageKeys.size).toBe(0)
  })

  it('never flags changes when prefers-reduced-motion is set', () => {
    mockMatchMedia(true)
    mockUsePipeline.mockReturnValue({
      data: makeSnapshot(),
      isLoading: false,
      error: null,
    } as ReturnType<typeof pipelineApi.usePipeline>)
    const { result, rerender } = renderHook(() => useAnimatedPipeline())

    mockUsePipeline.mockReturnValue({
      data: makeSnapshot({ terminals: { resolved: 99, escalated: 2, awaiting: 1 } }),
      isLoading: false,
      error: null,
    } as ReturnType<typeof pipelineApi.usePipeline>)
    rerender()

    expect(result.current.changedTerminalKeys.size).toBe(0)
    expect(result.current.prefersReducedMotion).toBe(true)
  })

  it('togglePaused flips the paused flag passed to usePipeline', () => {
    mockUsePipeline.mockReturnValue({
      data: makeSnapshot(),
      isLoading: false,
      error: null,
    } as ReturnType<typeof pipelineApi.usePipeline>)
    const { result } = renderHook(() => useAnimatedPipeline())
    expect(result.current.paused).toBe(false)

    act(() => result.current.togglePaused())

    expect(result.current.paused).toBe(true)
    expect(mockUsePipeline).toHaveBeenLastCalledWith({ paused: true })
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run tests/pipeline.test.tsx`
Expected: FAIL — `Failed to resolve import "@/features/map/useAnimatedPipeline"`.

- [ ] **Step 3: Implement `useAnimatedPipeline.ts`**

Create `frontend/src/features/map/useAnimatedPipeline.ts`:

```ts
import { useEffect, useRef, useState } from 'react'
import {
  diffSnapshots,
  usePipeline,
  type PipelineSnapshot,
  type TerminalCounts,
} from '@/api/pipeline'

const FLASH_DURATION_MS = 300

export function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(
    () =>
      typeof window !== 'undefined' &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches
  )

  useEffect(() => {
    const mql = window.matchMedia('(prefers-reduced-motion: reduce)')
    const onChange = () => setReduced(mql.matches)
    mql.addEventListener('change', onChange)
    return () => mql.removeEventListener('change', onChange)
  }, [])

  return reduced
}

export function useAnimatedPipeline() {
  const [paused, setPaused] = useState(false)
  const prefersReducedMotion = usePrefersReducedMotion()
  const query = usePipeline({ paused })
  const previousRef = useRef<PipelineSnapshot | undefined>(undefined)
  const [changedStageKeys, setChangedStageKeys] = useState<Set<string>>(new Set())
  const [changedTerminalKeys, setChangedTerminalKeys] = useState<Set<keyof TerminalCounts>>(
    new Set()
  )

  useEffect(() => {
    if (!query.data) return
    const delta = diffSnapshots(previousRef.current, query.data)
    previousRef.current = query.data

    if (prefersReducedMotion) return
    if (delta.changedStageKeys.size === 0 && delta.changedTerminalKeys.size === 0) return

    setChangedStageKeys(delta.changedStageKeys)
    setChangedTerminalKeys(delta.changedTerminalKeys)
    const timer = setTimeout(() => {
      setChangedStageKeys(new Set())
      setChangedTerminalKeys(new Set())
    }, FLASH_DURATION_MS)
    return () => clearTimeout(timer)
  }, [query.data, prefersReducedMotion])

  return {
    snapshot: query.data,
    isLoading: query.isLoading,
    error: query.error as Error | null,
    changedStageKeys,
    changedTerminalKeys,
    paused,
    togglePaused: () => setPaused((p) => !p),
    prefersReducedMotion,
  }
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npx vitest run tests/pipeline.test.tsx`
Expected: PASS (12 tests: 7 from Task 1 + 5 new).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/map/useAnimatedPipeline.ts frontend/tests/pipeline.test.tsx
git commit -m "feat(map): Add useAnimatedPipeline hook (reduced motion + flash timing)"
```

---

### Task 3: `features/map/StageNode.tsx` — `StageNodeCard` component

**Files:**
- Create: `frontend/src/features/map/StageNode.tsx`
- Test: `frontend/tests/pipeline-map.test.tsx` (new file)

**Interfaces:**
- Consumes (Task 1): `StageNode` type from `@/api/pipeline`.
- Produces (used by Task 6): `StageNodeCard({ stage: StageNode, justChanged: boolean }): JSX.Element`

- [ ] **Step 1: Write the failing test**

Create `frontend/tests/pipeline-map.test.tsx`:

```tsx
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { StageNodeCard } from '@/features/map/StageNode'

describe('StageNodeCard', () => {
  const stage = { key: 'triage', label: 'Triage', in_flight: 4, branches: [] }

  it('renders the stage label and in-flight count', () => {
    render(<StageNodeCard stage={stage} justChanged={false} />)
    expect(screen.getByTestId('stage-node-triage')).toBeInTheDocument()
    expect(screen.getByText('Triage')).toBeInTheDocument()
    expect(screen.getByText('4')).toBeInTheDocument()
  })

  it('applies the flash styling when justChanged is true', () => {
    render(<StageNodeCard stage={stage} justChanged={true} />)
    expect(screen.getByTestId('stage-node-triage')).toHaveClass('border-green-500')
  })

  it('does not apply the flash styling when justChanged is false', () => {
    render(<StageNodeCard stage={stage} justChanged={false} />)
    expect(screen.getByTestId('stage-node-triage')).not.toHaveClass('border-green-500')
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run tests/pipeline-map.test.tsx`
Expected: FAIL — `Failed to resolve import "@/features/map/StageNode"`.

- [ ] **Step 3: Implement `StageNode.tsx`**

Create `frontend/src/features/map/StageNode.tsx`:

```tsx
import { cn } from '@/lib/utils'
import type { StageNode } from '@/api/pipeline'

interface StageNodeCardProps {
  stage: StageNode
  justChanged: boolean
}

export function StageNodeCard({ stage, justChanged }: StageNodeCardProps) {
  return (
    <div
      className={cn(
        'rounded-lg bg-slate-900 border border-slate-700 p-4 flex flex-col gap-1 min-w-[120px]',
        'transition-colors duration-300 ease-out',
        justChanged && 'border-green-500 bg-green-500/10'
      )}
      data-testid={`stage-node-${stage.key}`}
    >
      <span className="text-xs font-semibold uppercase tracking-wider text-slate-400">
        {stage.label}
      </span>
      <span className="text-2xl font-mono font-bold text-slate-100">{stage.in_flight}</span>
      <span className="text-xs text-slate-500">in flight</span>
    </div>
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run tests/pipeline-map.test.tsx`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/map/StageNode.tsx frontend/tests/pipeline-map.test.tsx
git commit -m "feat(map): Add StageNodeCard component"
```

---

### Task 4: `features/map/FlowEdge.tsx` — connector component

**Files:**
- Create: `frontend/src/features/map/FlowEdge.tsx`
- Test: `frontend/tests/pipeline-map.test.tsx` (append)

**Interfaces:**
- Consumes: nothing from earlier tasks (a single `active: boolean` prop).
- Produces (used by Task 6): `FlowEdge({ active: boolean }): JSX.Element`

- [ ] **Step 1: Write the failing test**

Add `import { FlowEdge } from '@/features/map/FlowEdge'` to the top import block of `frontend/tests/pipeline-map.test.tsx` (alongside the existing `StageNodeCard` import), then append:

```tsx
describe('FlowEdge', () => {
  it('renders without the active styling by default', () => {
    const { container } = render(<FlowEdge active={false} />)
    expect(container.querySelector('[data-testid="flow-edge"] > div')).not.toHaveClass(
      'bg-green-500'
    )
  })

  it('applies the active styling when active is true', () => {
    const { container } = render(<FlowEdge active={true} />)
    expect(container.querySelector('[data-testid="flow-edge"] > div')).toHaveClass(
      'bg-green-500'
    )
  })
})
```

(The component below puts `data-testid="flow-edge"` on the outer wrapper and the flash styling on the inner bar, so the test queries the inner `div` directly rather than asserting on the outer wrapper.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run tests/pipeline-map.test.tsx`
Expected: FAIL — `Failed to resolve import "@/features/map/FlowEdge"`.

- [ ] **Step 3: Implement `FlowEdge.tsx`**

Create `frontend/src/features/map/FlowEdge.tsx`:

```tsx
import { cn } from '@/lib/utils'

interface FlowEdgeProps {
  active: boolean
}

export function FlowEdge({ active }: FlowEdgeProps) {
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
          active && 'bg-green-500'
        )}
      />
    </div>
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run tests/pipeline-map.test.tsx`
Expected: PASS (5 tests: 3 from Task 3 + 2 new).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/map/FlowEdge.tsx frontend/tests/pipeline-map.test.tsx
git commit -m "feat(map): Add FlowEdge connector component"
```

---

### Task 5: `features/map/TerminalColumn.tsx` — Resolved/Escalated/Awaiting tiles

**Files:**
- Create: `frontend/src/features/map/TerminalColumn.tsx`
- Test: `frontend/tests/pipeline-map.test.tsx` (append)

**Interfaces:**
- Consumes (Task 1): `TerminalCounts` type from `@/api/pipeline`.
- Produces (used by Task 6): `TerminalColumn({ terminals: TerminalCounts, changedKeys: Set<keyof TerminalCounts> }): JSX.Element`

- [ ] **Step 1: Write the failing test**

Add `import { TerminalColumn } from '@/features/map/TerminalColumn'` to the top import block of `frontend/tests/pipeline-map.test.tsx`, then append:

```tsx
describe('TerminalColumn', () => {
  const terminals = { resolved: 12, escalated: 3, awaiting: 1 }

  it('renders all three terminal tiles with an icon and a count each', () => {
    render(<TerminalColumn terminals={terminals} changedKeys={new Set()} />)
    expect(screen.getByTestId('terminal-resolved')).toBeInTheDocument()
    expect(screen.getByTestId('terminal-escalated')).toBeInTheDocument()
    expect(screen.getByTestId('terminal-awaiting')).toBeInTheDocument()
    expect(screen.getByText('12')).toBeInTheDocument()
    expect(screen.getByText('3')).toBeInTheDocument()
    expect(screen.getByText('1')).toBeInTheDocument()
  })

  it('flashes only the tile whose key changed', () => {
    render(
      <TerminalColumn terminals={terminals} changedKeys={new Set(['escalated'])} />
    )
    expect(screen.getByTestId('terminal-escalated')).toHaveClass('border-orange-500')
    expect(screen.getByTestId('terminal-resolved')).not.toHaveClass('border-green-500')
  })

  it('labels each tile so color is never the only signal', () => {
    render(<TerminalColumn terminals={terminals} changedKeys={new Set()} />)
    expect(screen.getByText('Resolved')).toBeInTheDocument()
    expect(screen.getByText('Escalated')).toBeInTheDocument()
    expect(screen.getByText('Awaiting')).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run tests/pipeline-map.test.tsx`
Expected: FAIL — `Failed to resolve import "@/features/map/TerminalColumn"`.

- [ ] **Step 3: Implement `TerminalColumn.tsx`**

Create `frontend/src/features/map/TerminalColumn.tsx`:

```tsx
import { CheckCircle2, AlertTriangle, Clock } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { TerminalCounts } from '@/api/pipeline'

interface TerminalColumnProps {
  terminals: TerminalCounts
  changedKeys: Set<keyof TerminalCounts>
}

const TILES: {
  key: keyof TerminalCounts
  label: string
  icon: typeof CheckCircle2
  iconColor: string
  flashBorder: string
}[] = [
  { key: 'resolved', label: 'Resolved', icon: CheckCircle2, iconColor: 'text-green-400', flashBorder: 'border-green-500' },
  { key: 'escalated', label: 'Escalated', icon: AlertTriangle, iconColor: 'text-orange-400', flashBorder: 'border-orange-500' },
  { key: 'awaiting', label: 'Awaiting', icon: Clock, iconColor: 'text-amber-400', flashBorder: 'border-amber-500' },
]

export function TerminalColumn({ terminals, changedKeys }: TerminalColumnProps) {
  return (
    <div className="flex flex-col gap-2" data-testid="terminal-column">
      {TILES.map(({ key, label, icon: Icon, iconColor, flashBorder }) => (
        <div
          key={key}
          className={cn(
            'rounded-lg bg-slate-900 border border-slate-700 px-3 py-2 flex items-center gap-2',
            'transition-colors duration-300 ease-out',
            changedKeys.has(key) && flashBorder
          )}
          data-testid={`terminal-${key}`}
        >
          <Icon className={cn('w-4 h-4', iconColor)} aria-hidden="true" />
          <span className="text-xs text-slate-400">{label}</span>
          <span className="ml-auto text-base font-mono font-bold text-slate-100">
            {terminals[key]}
          </span>
        </div>
      ))}
    </div>
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run tests/pipeline-map.test.tsx`
Expected: PASS (8 tests: 5 from Tasks 3-4 + 3 new).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/map/TerminalColumn.tsx frontend/tests/pipeline-map.test.tsx
git commit -m "feat(map): Add TerminalColumn component"
```

---

### Task 6: `features/map/PipelineMap.tsx` + nav item + route

**Files:**
- Create: `frontend/src/features/map/PipelineMap.tsx`
- Modify: `frontend/src/components/AppShell.tsx` (add one nav item)
- Modify: `frontend/src/router.tsx` (add one route)
- Test: `frontend/tests/pipeline-map.test.tsx` (append)

**Interfaces:**
- Consumes (Task 2): `useAnimatedPipeline` from `@/features/map/useAnimatedPipeline`.
- Consumes (Tasks 3-5): `StageNodeCard`, `FlowEdge`, `TerminalColumn`.
- Consumes (existing): `Skeleton` (`@/components/ui/skeleton`), `ErrorState` (`@/components/ErrorState`), `EmptyState` (`@/components/EmptyState`), `Button` (`@/components/ui/button`), `cn` (`@/lib/utils`).
- Produces: `PipelineMap(): JSX.Element` — the page mounted at route `/map`.

- [ ] **Step 1: Write the failing test**

Add to the top import block of `frontend/tests/pipeline-map.test.tsx`:

```tsx
import { vi, beforeEach } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import { PipelineMap } from '@/features/map/PipelineMap'
import * as animatedApi from '@/features/map/useAnimatedPipeline'
```

(`describe`, `it`, `expect`, `render`, `screen` are already imported from the earlier tasks in this file — do not duplicate those import lines.)

Then add this mock declaration near the top of the file, alongside the other imports (mocks must be hoisted to module scope, so place it directly after the imports, before any `describe` block):

```tsx
vi.mock('@/features/map/useAnimatedPipeline', () => ({
  useAnimatedPipeline: vi.fn(),
}))

const mockUseAnimatedPipeline = vi.mocked(animatedApi.useAnimatedPipeline)

function makeSnapshot() {
  return {
    stages: [
      { key: 'intake', label: 'Intake', in_flight: 2, branches: [] },
      { key: 'triage', label: 'Triage', in_flight: 4, branches: [] },
      { key: 'enrichment', label: 'Enrichment', in_flight: 1, branches: [] },
      { key: 'response', label: 'Response', in_flight: 3, branches: [] },
    ],
    terminals: { resolved: 10, escalated: 2, awaiting: 1 },
    window_hours: 24,
    generated_at: '2026-06-17T12:00:00Z',
  }
}

function baseAnimatedPipeline() {
  return {
    snapshot: makeSnapshot(),
    isLoading: false,
    error: null,
    changedStageKeys: new Set<string>(),
    changedTerminalKeys: new Set<string>(),
    paused: false,
    togglePaused: vi.fn(),
    prefersReducedMotion: false,
  }
}
```

Then append the test suite:

```tsx
describe('PipelineMap', () => {
  beforeEach(() => vi.clearAllMocks())

  it('renders a loading skeleton while data is loading', () => {
    mockUseAnimatedPipeline.mockReturnValue({
      ...baseAnimatedPipeline(),
      snapshot: undefined,
      isLoading: true,
    } as ReturnType<typeof animatedApi.useAnimatedPipeline>)
    render(<PipelineMap />, { wrapper: ({ children }) => <MemoryRouter>{children}</MemoryRouter> })
    expect(screen.getByLabelText('Loading pipeline map')).toBeInTheDocument()
  })

  it('renders an error state when the fetch fails', () => {
    mockUseAnimatedPipeline.mockReturnValue({
      ...baseAnimatedPipeline(),
      snapshot: undefined,
      isLoading: false,
      error: new Error('fetch failed'),
    } as ReturnType<typeof animatedApi.useAnimatedPipeline>)
    render(<PipelineMap />, { wrapper: ({ children }) => <MemoryRouter>{children}</MemoryRouter> })
    expect(screen.getByText(/failed to load pipeline map/i)).toBeInTheDocument()
  })

  it('renders an empty state when there are no in-flight incidents and no terminals', () => {
    mockUseAnimatedPipeline.mockReturnValue({
      ...baseAnimatedPipeline(),
      snapshot: {
        stages: [
          { key: 'intake', label: 'Intake', in_flight: 0, branches: [] },
          { key: 'triage', label: 'Triage', in_flight: 0, branches: [] },
          { key: 'enrichment', label: 'Enrichment', in_flight: 0, branches: [] },
          { key: 'response', label: 'Response', in_flight: 0, branches: [] },
        ],
        terminals: { resolved: 0, escalated: 0, awaiting: 0 },
        window_hours: 24,
        generated_at: '2026-06-17T12:00:00Z',
      },
    } as ReturnType<typeof animatedApi.useAnimatedPipeline>)
    render(<PipelineMap />, { wrapper: ({ children }) => <MemoryRouter>{children}</MemoryRouter> })
    expect(screen.getByText(/no incidents in flight/i)).toBeInTheDocument()
  })

  it('renders the rail and terminal column when data is loaded', () => {
    mockUseAnimatedPipeline.mockReturnValue(
      baseAnimatedPipeline() as ReturnType<typeof animatedApi.useAnimatedPipeline>
    )
    render(<PipelineMap />, { wrapper: ({ children }) => <MemoryRouter>{children}</MemoryRouter> })
    expect(screen.getByTestId('stage-node-intake')).toBeInTheDocument()
    expect(screen.getByTestId('stage-node-response')).toBeInTheDocument()
    expect(screen.getByTestId('terminal-column')).toBeInTheDocument()
  })

  it('calls togglePaused when the Live/Pause button is clicked', async () => {
    const togglePaused = vi.fn()
    mockUseAnimatedPipeline.mockReturnValue({
      ...baseAnimatedPipeline(),
      togglePaused,
    } as ReturnType<typeof animatedApi.useAnimatedPipeline>)
    const { default: userEvent } = await import('@testing-library/user-event')
    render(<PipelineMap />, { wrapper: ({ children }) => <MemoryRouter>{children}</MemoryRouter> })
    await userEvent.click(screen.getByRole('button', { name: /live|pause/i }))
    expect(togglePaused).toHaveBeenCalledOnce()
  })

  it('shows "Paused" label when paused is true', () => {
    mockUseAnimatedPipeline.mockReturnValue({
      ...baseAnimatedPipeline(),
      paused: true,
    } as ReturnType<typeof animatedApi.useAnimatedPipeline>)
    render(<PipelineMap />, { wrapper: ({ children }) => <MemoryRouter>{children}</MemoryRouter> })
    expect(screen.getByRole('button', { name: /paused/i })).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run tests/pipeline-map.test.tsx`
Expected: FAIL — `Failed to resolve import "@/features/map/PipelineMap"`.

- [ ] **Step 3a: Implement `PipelineMap.tsx`**

Create `frontend/src/features/map/PipelineMap.tsx`:

```tsx
import { Play, Pause } from 'lucide-react'
import { useAnimatedPipeline } from './useAnimatedPipeline'
import { StageNodeCard } from './StageNode'
import { FlowEdge } from './FlowEdge'
import { TerminalColumn } from './TerminalColumn'
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
    <div className="space-y-6" data-testid="pipeline-map">
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
          aria-label={paused ? 'Resume live updates' : 'Pause live updates'}
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
        <div className="flex flex-col sm:flex-row gap-6">
          <div className="flex items-center flex-wrap gap-y-3">
            {snapshot.stages.map((stage, i) => (
              <div key={stage.key} className="flex items-center">
                <StageNodeCard
                  stage={stage}
                  justChanged={changedStageKeys.has(stage.key)}
                />
                {i < snapshot.stages.length - 1 && (
                  <FlowEdge
                    active={
                      changedStageKeys.has(stage.key) ||
                      changedStageKeys.has(snapshot.stages[i + 1].key)
                    }
                  />
                )}
              </div>
            ))}
          </div>
          <TerminalColumn terminals={snapshot.terminals} changedKeys={changedTerminalKeys} />
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 3b: Add the nav item to `AppShell.tsx`**

In `frontend/src/components/AppShell.tsx`, change the icon import line and the `navItems` array:

```ts
import { Shield, LayoutDashboard, BarChart3, LogOut, GitGraph } from 'lucide-react'
```

```ts
const navItems = [
  { to: '/queue', label: 'Queue', icon: LayoutDashboard },
  { to: '/map', label: 'Pipeline Map', icon: GitGraph },
  { to: '/kpis', label: 'KPIs', icon: BarChart3 },
]
```

- [ ] **Step 3c: Add the route to `router.tsx`**

In `frontend/src/router.tsx`, add the lazy import alongside the existing ones:

```ts
const PipelineMap = lazy(() =>
  import('@/features/map/PipelineMap').then((m) => ({ default: m.PipelineMap }))
)
```

And add the route entry inside the `children` array, after the `'queue'` route and before `'incidents/:id'`:

```ts
      {
        path: 'map',
        element: (
          <Suspense fallback={<Loading />}>
            <PipelineMap />
          </Suspense>
        ),
      },
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run tests/pipeline-map.test.tsx`
Expected: PASS (14 tests: 8 from Tasks 3-5 + 6 new).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/map/PipelineMap.tsx frontend/src/components/AppShell.tsx frontend/src/router.tsx frontend/tests/pipeline-map.test.tsx
git commit -m "feat(map): Add PipelineMap page, nav item, and route"
```

---

### Task 7: Full verification

**Files:** none (verification only).

- [ ] **Step 1: Run both new test files**

Run: `cd frontend && npx vitest run tests/pipeline.test.tsx tests/pipeline-map.test.tsx`
Expected: all 26 tests pass (12 + 14).

- [ ] **Step 2: Run the full frontend suite**

Run: `cd frontend && npm test`
Expected: all existing test files plus the two new ones pass — no regressions in `incident.test.tsx`, `stream.test.tsx`, `approvals.test.tsx`, `trace.test.tsx`, `auth.test.tsx`, `kpis.test.tsx`, `queue.test.tsx`.

- [ ] **Step 3: Typecheck and lint**

Run: `cd frontend && npx tsc -b --noEmit && npm run lint`
Expected: no type errors, no lint errors, in any new or modified file.

- [ ] **Step 4: Manual smoke check**

Run: `cd frontend && npm run dev`, sign in, click "Pipeline Map" in the sidebar nav, confirm the rail renders with live counts from the real backend (`GET /incidents/pipeline`, shipped in M-a) and the Live/Pause button toggles polling.

---

## Self-Review

**1. Spec coverage** (against `docs/superpowers/specs/2026-06-17-soc-pipeline-map-design.md` §5-7, and §10 M-b scope: "live rail (`StageNode`/`FlowEdge`/`TerminalColumn` + `usePipeline` polling + motion + reduced-motion + Live/Pause) + nav item + route + tests"):
- `usePipeline()` 2s polling → Task 1. ✅
- snapshot-diff helper → Task 1 `diffSnapshots`. ✅
- `StageNode.tsx` (stage card, animated count, glow on increase) → Task 3 `StageNodeCard`. ✅ (expand-to-`BranchBreakdown` is explicitly M-c per design §10 — not built here; see Next plans.)
- `FlowEdge.tsx` (flashes on flow) → Task 4. ✅
- `TerminalColumn.tsx` (icon + color, never color alone) → Task 5 — each tile pairs a distinct lucide icon with its color and a text label. ✅
- Motion rules (§6: animate only changed elements, ease-out/ease-in, reduced-motion, Live/Pause) → Task 2 `useAnimatedPipeline` (300ms transient flag, `prefersReducedMotion` gate) + Task 6 (Live/Pause button). ✅
- Isolation (D6: two additive lines only) → Task 6 Steps 3b/3c are the only edits to pre-existing files; everything else is new files. ✅
- Testing (§9 frontend bullet, M-b-relevant parts): "snapshot-diff/animation-derivation logic" → Task 1-2 tests; "`PipelineMap` render... the reduced-motion branch" → Task 2 + Task 6 tests. ✅ (`HumanAttentionLane`, `IncidentDrawer`, `JourneyOverlay`, stage-expand, and the Playwright e2e are explicitly M-c — not in scope here.)
- Out of scope (§11): no flowing particles (uses discrete flash, not particles) ✅; no new escalated-incident actions (none added) ✅; no existing-dashboard changes (only the two additive lines) ✅.

**2. Placeholder scan:** No TBD/TODO; every step shows complete, runnable code; every test has real assertions against rendered DOM or hook return values. ✅

**3. Type consistency:** `StageNode`/`TerminalCounts`/`PipelineSnapshot`/`BranchOutflow`/`PipelineDelta` (Task 1) are imported with identical field names in Tasks 2-6. `diffSnapshots(previous, current)` and `usePipeline(options?)` signatures match between Task 1's implementation and Task 2's consumption. `useAnimatedPipeline()`'s returned shape (`snapshot`, `isLoading`, `error`, `changedStageKeys`, `changedTerminalKeys`, `paused`, `togglePaused`, `prefersReducedMotion`) matches exactly between Task 2's implementation and Task 6's consumption/mocking. `StageNodeCard({ stage, justChanged })`, `FlowEdge({ active })`, `TerminalColumn({ terminals, changedKeys })` prop names are consistent between their defining tasks (3/4/5) and Task 6's usage. The `StageNode` type vs `StageNodeCard` component naming collision is resolved per Global Constraints and used consistently in every task. ✅

---

## Next plans (after M-b merges)

- **M-c:** `HumanAttentionLane` (approve/reject reuse of `ApprovalPanel`/`useApprovalDecision` + escalated cards) + `IncidentDrawer` (full detail slide-over) + `BranchBreakdown` (modifies `StageNode.tsx` to add the click-to-expand interaction this plan deliberately omitted) + `JourneyOverlay` (single-incident path highlight) + the Playwright e2e covering the full interactive flow.
