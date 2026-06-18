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
