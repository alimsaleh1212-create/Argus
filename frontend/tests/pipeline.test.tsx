import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { diffSnapshots, usePipeline, type PipelineSnapshot } from '@/api/pipeline'
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

  beforeEach(async () => {
    // Configure the mock to delegate to the real usePipeline implementation
    const actual = await vi.importActual<typeof import('@/api/pipeline')>('@/api/pipeline')
    mockUsePipeline.mockImplementation(actual.usePipeline)

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
    // Reset the mock for useAnimatedPipeline tests
    mockUsePipeline.mockReset()
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
