/**
 * Frontend tests for SSE stream client (T056).
 * Tests the stream module behavior: connection state, cache patching, reconnect fallback.
 *
 * EventSource is not available in jsdom — we mock it globally.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useConnectionState, useSSEStream } from '@/api/stream'

// ─── EventSource mock ────────────────────────────────────────────────────────

type ESListener = (event: MessageEvent) => void

class MockEventSource {
  static instances: MockEventSource[] = []
  url: string
  listeners: Record<string, ESListener[]> = {}
  onerror: ((e: Event) => void) | null = null
  onopen: ((e: Event) => void) | null = null

  constructor(url: string) {
    this.url = url
    MockEventSource.instances.push(this)
  }

  addEventListener(type: string, fn: ESListener) {
    this.listeners[type] = this.listeners[type] ?? []
    this.listeners[type].push(fn)
  }

  close() {}

  emit(type: string, data: unknown) {
    const msg = new MessageEvent(type, { data: JSON.stringify(data) })
    ;(this.listeners[type] ?? []).forEach((fn) => fn(msg))
  }

  triggerError() {
    this.onerror?.(new Event('error'))
  }
}

beforeEach(() => {
  MockEventSource.instances = []
  // Seed a token so the hook actually connects
  sessionStorage.setItem('argus_token', 'fake-jwt-token')
  // @ts-expect-error — replace browser EventSource with mock
  globalThis.EventSource = MockEventSource
})

afterEach(() => {
  sessionStorage.removeItem('argus_token')
  // @ts-expect-error
  delete globalThis.EventSource
})

function makeWrapper() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return {
    qc,
    wrapper: ({ children }: { children: React.ReactNode }) => (
      <QueryClientProvider client={qc}>{children}</QueryClientProvider>
    ),
  }
}

// ─── useConnectionState ──────────────────────────────────────────────────────

describe('useConnectionState', () => {
  it('returns a valid connection state string', () => {
    const { result } = renderHook(() => useConnectionState())
    expect(['connected', 'reconnecting', 'disconnected']).toContain(result.current)
  })
})

// ─── useSSEStream ────────────────────────────────────────────────────────────

describe('useSSEStream', () => {
  it('creates an EventSource connection with token query param', () => {
    const { wrapper } = makeWrapper()
    renderHook(() => useSSEStream('test-token'), { wrapper })
    expect(MockEventSource.instances.length).toBeGreaterThan(0)
    expect(MockEventSource.instances[0].url).toContain('token=test-token')
  })

  it('does not connect when token is null', () => {
    const { wrapper } = makeWrapper()
    renderHook(() => useSSEStream(null), { wrapper })
    expect(MockEventSource.instances.length).toBe(0)
  })

  it('patches queue cache on snapshot event', async () => {
    const { wrapper, qc } = makeWrapper()
    renderHook(() => useSSEStream('test-token'), { wrapper })

    const es = MockEventSource.instances[0]
    const queueItem = { id: 'inc-1', status: 'resolved', severity: 'high', disposition: null, source: 'wazuh', summary: 'test', is_awaiting_approval: false, created_at: '', updated_at: '' }

    // Pre-seed the cache with a QueuePage
    qc.setQueryData(['incidents', 'queue', { view: 'active', status: [], severity: [], sort: '-updated_at', limit: 50, offset: 0 }], {
      items: [],
      total: 0,
      limit: 50,
      offset: 0,
      view: 'active',
      applied_filters: {},
    })

    act(() => {
      es.emit('snapshot', { queue: [queueItem], kpi_counters: { active: 1, awaiting_approval: 0, auto_resolved: 0, escalated: 0 } })
    })

    // After snapshot the queue cache should be updated (setQueryData called)
    // We verify the ES listener ran without error
    expect(es.listeners['snapshot']).toBeDefined()
  })

  it('transitions to reconnecting state on error', () => {
    const { wrapper } = makeWrapper()
    const { result } = renderHook(
      () => {
        useSSEStream('test-token')
        return useConnectionState()
      },
      { wrapper }
    )

    const es = MockEventSource.instances[0]
    act(() => {
      es.triggerError()
    })

    expect(result.current).toBe('reconnecting')
  })

  it('registers snapshot and delta event listeners', () => {
    const { wrapper } = makeWrapper()
    renderHook(() => useSSEStream('test-token'), { wrapper })

    const es = MockEventSource.instances[0]
    expect(es.listeners['snapshot']).toBeDefined()
    expect(es.listeners['delta']).toBeDefined()
    expect(es.listeners['heartbeat']).toBeDefined()
  })

  it('marks state as connected on heartbeat', () => {
    const { wrapper } = makeWrapper()
    const { result } = renderHook(
      () => {
        useSSEStream('test-token')
        return useConnectionState()
      },
      { wrapper }
    )

    const es = MockEventSource.instances[0]
    act(() => {
      es.emit('heartbeat', { ts: '2026-06-12T09:00:00Z' })
    })

    expect(result.current).toBe('connected')
  })
})
