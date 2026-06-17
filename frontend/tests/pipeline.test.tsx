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
