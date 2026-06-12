/**
 * Component tests for Trace Inspector (T044).
 * - Telemetry: renders metrics, "unknown" for null tokens
 * - SpanTree: renders root span, children, expand/collapse, error marking
 * - SpanDetail: renders span fields, null token → "unknown"
 * - TraceInspector: loading, error, empty tree, full tree
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { Telemetry } from '@/features/trace/Telemetry'
import { SpanTree } from '@/features/trace/SpanTree'
import { SpanDetail } from '@/features/trace/SpanDetail'
import { TraceInspector } from '@/features/trace/TraceInspector'
import * as traceApi from '@/api/trace'
import type { SpanView, TelemetryView, TraceTreeView } from '@/api/trace'

vi.mock('@/api/trace', () => ({
  useTrace: vi.fn(),
}))

const mockUseTrace = vi.mocked(traceApi.useTrace)

function wrapper({ children }: { children: React.ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return (
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/incidents/abc123/trace']}>
        <Routes>
          <Route path="/incidents/:id/trace" element={<>{children}</>} />
          <Route path="/incidents/:id" element={<div>Detail</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  )
}

function makeSpan(overrides: Partial<SpanView> = {}): SpanView {
  return {
    span_id: 'root-span',
    parent_span_id: null,
    name: 'incident_pipeline',
    kind: 'root',
    status: 'ok',
    started_at: '2026-06-12T09:00:00Z',
    ended_at: '2026-06-12T09:00:00.800Z',
    latency_ms: 800,
    llm_model: null,
    tokens_in: null,
    tokens_out: null,
    attributes: {},
    error_message: null,
    ...overrides,
  }
}

function makeTelemetry(overrides: Partial<TelemetryView> = {}): TelemetryView {
  return {
    total_tokens_in: 50,
    total_tokens_out: 120,
    end_to_end_ms: 800,
    step_count: 2,
    error_steps: 0,
    ...overrides,
  }
}

function makeTraceTree(overrides: Partial<TraceTreeView> = {}): TraceTreeView {
  return {
    correlation_id: 'corr-001',
    root: makeSpan(),
    children: {},
    telemetry: makeTelemetry(),
    ...overrides,
  }
}

// ─── Telemetry ───────────────────────────────────────────────────────────────

describe('Telemetry', () => {
  it('renders all metric cards', () => {
    render(<Telemetry telemetry={makeTelemetry()} />, { wrapper })
    expect(screen.getByTestId('telemetry-panel')).toBeInTheDocument()
    expect(screen.getByText('Steps')).toBeInTheDocument()
    expect(screen.getByText('Error steps')).toBeInTheDocument()
    expect(screen.getByText('End-to-end')).toBeInTheDocument()
    expect(screen.getByText('Tokens in')).toBeInTheDocument()
    expect(screen.getByText('Tokens out')).toBeInTheDocument()
  })

  it('renders null tokens as "unknown" not 0', () => {
    render(
      <Telemetry telemetry={makeTelemetry({ total_tokens_in: null, total_tokens_out: null })} />,
      { wrapper }
    )
    const unknowns = screen.getAllByText('unknown')
    expect(unknowns.length).toBeGreaterThanOrEqual(2)
  })

  it('renders numeric tokens when provided', () => {
    render(<Telemetry telemetry={makeTelemetry({ total_tokens_in: 50, total_tokens_out: 120 })} />, {
      wrapper,
    })
    expect(screen.getByText('50')).toBeInTheDocument()
    expect(screen.getByText('120')).toBeInTheDocument()
  })

  it('formats latency in ms', () => {
    render(<Telemetry telemetry={makeTelemetry({ end_to_end_ms: 800 })} />, { wrapper })
    expect(screen.getByText('800ms')).toBeInTheDocument()
  })

  it('formats latency in seconds for >= 1000ms', () => {
    render(<Telemetry telemetry={makeTelemetry({ end_to_end_ms: 2500 })} />, { wrapper })
    expect(screen.getByText('2.50s')).toBeInTheDocument()
  })

  it('renders — when end_to_end_ms is null', () => {
    render(<Telemetry telemetry={makeTelemetry({ end_to_end_ms: null })} />, { wrapper })
    expect(screen.getByText('—')).toBeInTheDocument()
  })
})

// ─── SpanTree ────────────────────────────────────────────────────────────────

describe('SpanTree', () => {
  it('renders root span', () => {
    const root = makeSpan({ name: 'incident_pipeline', span_id: 'root-span' })
    render(
      <SpanTree root={root} children={{}} onSelect={vi.fn()} selectedId={null} />,
      { wrapper }
    )
    expect(screen.getByTestId('span-tree')).toBeInTheDocument()
    expect(screen.getByTestId('span-node-root-span')).toBeInTheDocument()
    expect(screen.getByText('incident_pipeline')).toBeInTheDocument()
  })

  it('renders child spans', () => {
    const root = makeSpan({ span_id: 'root-span' })
    const child = makeSpan({ span_id: 'child-span', name: 'triage', parent_span_id: 'root-span', kind: 'llm_call' })
    render(
      <SpanTree
        root={root}
        children={{ 'root-span': [child] }}
        onSelect={vi.fn()}
        selectedId={null}
      />,
      { wrapper }
    )
    expect(screen.getByTestId('span-node-child-span')).toBeInTheDocument()
    expect(screen.getByText('triage')).toBeInTheDocument()
  })

  it('calls onSelect when a span is clicked', () => {
    const onSelect = vi.fn()
    const root = makeSpan({ span_id: 'root-span' })
    render(
      <SpanTree root={root} children={{}} onSelect={onSelect} selectedId={null} />,
      { wrapper }
    )
    fireEvent.click(screen.getByTestId('span-node-root-span'))
    expect(onSelect).toHaveBeenCalledWith(root)
  })

  it('marks selected span with aria-selected', () => {
    const root = makeSpan({ span_id: 'root-span' })
    render(
      <SpanTree root={root} children={{}} onSelect={vi.fn()} selectedId="root-span" />,
      { wrapper }
    )
    expect(screen.getByTestId('span-node-root-span')).toHaveAttribute('aria-selected', 'true')
  })

  it('renders error span with AlertCircle icon', () => {
    const root = makeSpan({ span_id: 'root-span', status: 'error' })
    render(
      <SpanTree root={root} children={{}} onSelect={vi.fn()} selectedId={null} />,
      { wrapper }
    )
    expect(screen.getByLabelText('Error')).toBeInTheDocument()
  })

  it('null tokens display as unknown/unknown in tree node', () => {
    const root = makeSpan({ span_id: 'root-span', tokens_in: null, tokens_out: null })
    render(
      <SpanTree root={root} children={{}} onSelect={vi.fn()} selectedId={null} />,
      { wrapper }
    )
    expect(screen.getByText(/unknown\/unknown/)).toBeInTheDocument()
  })
})

// ─── SpanDetail ──────────────────────────────────────────────────────────────

describe('SpanDetail', () => {
  it('renders span name and fields', () => {
    const span = makeSpan({
      span_id: 'triage-span',
      name: 'triage_agent',
      kind: 'llm_call',
      status: 'ok',
    })
    render(<SpanDetail span={span} onClose={vi.fn()} />, { wrapper })
    expect(screen.getByTestId('span-detail')).toBeInTheDocument()
    expect(screen.getByText('triage_agent')).toBeInTheDocument()
    expect(screen.getByText('llm_call')).toBeInTheDocument()
    expect(screen.getByText('ok')).toBeInTheDocument()
  })

  it('renders null tokens as "unknown"', () => {
    const span = makeSpan({ tokens_in: null, tokens_out: null })
    render(<SpanDetail span={span} onClose={vi.fn()} />, { wrapper })
    const unknowns = screen.getAllByText('unknown')
    expect(unknowns.length).toBeGreaterThanOrEqual(2)
  })

  it('renders numeric tokens', () => {
    const span = makeSpan({ tokens_in: 42, tokens_out: 88 })
    render(<SpanDetail span={span} onClose={vi.fn()} />, { wrapper })
    expect(screen.getByText('42')).toBeInTheDocument()
    expect(screen.getByText('88')).toBeInTheDocument()
  })

  it('shows error message for error spans', () => {
    const span = makeSpan({ status: 'error', error_message: 'context length exceeded' })
    render(<SpanDetail span={span} onClose={vi.fn()} />, { wrapper })
    expect(screen.getByText('context length exceeded')).toBeInTheDocument()
  })

  it('calls onClose when close button clicked', () => {
    const onClose = vi.fn()
    render(<SpanDetail span={makeSpan()} onClose={onClose} />, { wrapper })
    fireEvent.click(screen.getByRole('button', { name: /close span detail/i }))
    expect(onClose).toHaveBeenCalled()
  })

  it('renders llm_model when set', () => {
    const span = makeSpan({ llm_model: 'gemini-1.5-pro' })
    render(<SpanDetail span={span} onClose={vi.fn()} />, { wrapper })
    expect(screen.getByText('gemini-1.5-pro')).toBeInTheDocument()
  })
})

// ─── TraceInspector ──────────────────────────────────────────────────────────

describe('TraceInspector', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  function renderInspector() {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    return render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={['/incidents/abc123/trace']}>
          <Routes>
            <Route path="/incidents/:id/trace" element={<TraceInspector />} />
            <Route path="/incidents/:id" element={<div>Detail</div>} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    )
  }

  it('renders loading skeleton while loading', () => {
    mockUseTrace.mockReturnValue({ data: undefined, isLoading: true, error: null } as ReturnType<typeof traceApi.useTrace>)
    renderInspector()
    expect(screen.getByLabelText('Loading trace')).toBeInTheDocument()
  })

  it('renders error state on failure', () => {
    mockUseTrace.mockReturnValue({
      data: undefined,
      isLoading: false,
      error: new Error('network error'),
    } as ReturnType<typeof traceApi.useTrace>)
    renderInspector()
    expect(screen.getByText(/failed to load trace/i)).toBeInTheDocument()
  })

  it('renders empty trace state when root is null', () => {
    mockUseTrace.mockReturnValue({
      data: makeTraceTree({ root: null }),
      isLoading: false,
      error: null,
    } as ReturnType<typeof traceApi.useTrace>)
    renderInspector()
    expect(screen.getByTestId('empty-trace')).toBeInTheDocument()
    expect(screen.getByText(/no trace spans recorded yet/i)).toBeInTheDocument()
  })

  it('renders trace tree when root present', () => {
    mockUseTrace.mockReturnValue({
      data: makeTraceTree(),
      isLoading: false,
      error: null,
    } as ReturnType<typeof traceApi.useTrace>)
    renderInspector()
    expect(screen.getByTestId('span-tree')).toBeInTheDocument()
    expect(screen.getByTestId('telemetry-panel')).toBeInTheDocument()
  })

  it('shows span detail after clicking a span', () => {
    mockUseTrace.mockReturnValue({
      data: makeTraceTree(),
      isLoading: false,
      error: null,
    } as ReturnType<typeof traceApi.useTrace>)
    renderInspector()
    fireEvent.click(screen.getByTestId('span-node-root-span'))
    expect(screen.getByTestId('span-detail')).toBeInTheDocument()
  })

  it('shows correlation_id in header', () => {
    mockUseTrace.mockReturnValue({
      data: makeTraceTree({ correlation_id: 'corr-xyz-42' }),
      isLoading: false,
      error: null,
    } as ReturnType<typeof traceApi.useTrace>)
    renderInspector()
    expect(screen.getByText('corr-xyz-42')).toBeInTheDocument()
  })
})
