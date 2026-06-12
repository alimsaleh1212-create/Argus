import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { IncidentDetail } from '@/features/incident/IncidentDetail'
import * as incidentsApi from '@/api/incidents'
import type { IncidentDetailView } from '@/api/incidents'

vi.mock('@/api/incidents', () => ({
  useIncidentDetail: vi.fn(),
  useIncidentAudit: vi.fn(),
  useIncidentQueue: vi.fn(),
}))

const mockUseIncidentDetail = vi.mocked(incidentsApi.useIncidentDetail)

const INC_ID = '00000000-0000-0000-0000-000000000001'

function makeDetail(overrides: Partial<IncidentDetailView> = {}): IncidentDetailView {
  return {
    id: INC_ID,
    status: 'resolved',
    severity: 'high',
    disposition: 'remediated',
    source: 'wazuh',
    summary: 'Suspicious login attempt',
    is_awaiting_approval: false,
    created_at: '2026-06-12T09:00:00Z',
    updated_at: '2026-06-12T09:10:00Z',
    evidence: { summary: 'Login from unusual IP', verdict: 'real', flags: ['brute-force'] },
    normalized_event: null,
    correlation_id: 'corr-001',
    pending_approval: null,
    audit: [
      {
        actor: 'system',
        action: 'open_ticket',
        target: 'INC-001',
        outcome: 'applied',
        created_at: '2026-06-12T09:05:00Z',
      },
    ],
    ...overrides,
  }
}

function wrapper({ children }: { children: React.ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return (
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[`/incidents/${INC_ID}`]}>
        <Routes>
          <Route path="/incidents/:id" element={children} />
          <Route path="/queue" element={<div>Queue</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  )
}

describe('IncidentDetail', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders loading skeletons while fetching', () => {
    mockUseIncidentDetail.mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
      error: null,
    } as ReturnType<typeof incidentsApi.useIncidentDetail>)

    render(<IncidentDetail />, { wrapper })
    // Should not render the incident ID
    expect(screen.queryByText(INC_ID)).toBeNull()
  })

  it('renders error state on failure', () => {
    mockUseIncidentDetail.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error('Fetch failed'),
    } as ReturnType<typeof incidentsApi.useIncidentDetail>)

    render(<IncidentDetail />, { wrapper })
    expect(screen.getByText(/failed to load incident/i)).toBeInTheDocument()
  })

  it('renders incident ID, status, and severity badges', () => {
    mockUseIncidentDetail.mockReturnValue({
      data: makeDetail(),
      isLoading: false,
      isError: false,
      error: null,
    } as ReturnType<typeof incidentsApi.useIncidentDetail>)

    render(<IncidentDetail />, { wrapper })
    expect(screen.getByText(INC_ID)).toBeInTheDocument()
    expect(screen.getByText('High')).toBeInTheDocument()
    expect(screen.getByText('Resolved')).toBeInTheDocument()
  })

  it('renders evidence summary', () => {
    mockUseIncidentDetail.mockReturnValue({
      data: makeDetail(),
      isLoading: false,
      isError: false,
      error: null,
    } as ReturnType<typeof incidentsApi.useIncidentDetail>)

    render(<IncidentDetail />, { wrapper })
    expect(screen.getByText('Login from unusual IP')).toBeInTheDocument()
  })

  it('renders evidence flag', () => {
    mockUseIncidentDetail.mockReturnValue({
      data: makeDetail(),
      isLoading: false,
      isError: false,
      error: null,
    } as ReturnType<typeof incidentsApi.useIncidentDetail>)

    render(<IncidentDetail />, { wrapper })
    expect(screen.getByText('brute-force')).toBeInTheDocument()
  })

  it('renders audit trail entry', () => {
    mockUseIncidentDetail.mockReturnValue({
      data: makeDetail(),
      isLoading: false,
      isError: false,
      error: null,
    } as ReturnType<typeof incidentsApi.useIncidentDetail>)

    render(<IncidentDetail />, { wrapper })
    expect(screen.getByText('open_ticket')).toBeInTheDocument()
    expect(screen.getByText('system')).toBeInTheDocument()
    expect(screen.getByText('applied')).toBeInTheDocument()
  })

  it('shows no evidence message when evidence is null', () => {
    mockUseIncidentDetail.mockReturnValue({
      data: makeDetail({ evidence: null }),
      isLoading: false,
      isError: false,
      error: null,
    } as ReturnType<typeof incidentsApi.useIncidentDetail>)

    render(<IncidentDetail />, { wrapper })
    expect(screen.getByText(/no evidence recorded/i)).toBeInTheDocument()
  })

  it('shows pending approval panel when present', () => {
    const detail = makeDetail({
      status: 'awaiting_approval',
      is_awaiting_approval: true,
      pending_approval: {
        id: 1,
        incident_id: INC_ID,
        plan_id: 'plan-001',
        pending_actions: [{ action_id: 'isolate_host', target: 'srv-01' }],
        rationale: 'Host shows signs of compromise.',
        status: 'pending',
        deadline_at: '2026-06-12T10:00:00Z',
        created_at: '2026-06-12T09:00:00Z',
        is_actionable: true,
      },
    })
    mockUseIncidentDetail.mockReturnValue({
      data: detail,
      isLoading: false,
      isError: false,
      error: null,
    } as ReturnType<typeof incidentsApi.useIncidentDetail>)

    render(<IncidentDetail />, { wrapper })
    expect(screen.getByText(/human approval required/i)).toBeInTheDocument()
    expect(screen.getByText('Host shows signs of compromise.')).toBeInTheDocument()
  })

  it('shows trace link when correlation_id is present', () => {
    mockUseIncidentDetail.mockReturnValue({
      data: makeDetail({ correlation_id: 'corr-001' }),
      isLoading: false,
      isError: false,
      error: null,
    } as ReturnType<typeof incidentsApi.useIncidentDetail>)

    render(<IncidentDetail />, { wrapper })
    expect(screen.getByText(/view pipeline trace/i)).toBeInTheDocument()
  })

  it('renders empty audit trail message', () => {
    mockUseIncidentDetail.mockReturnValue({
      data: makeDetail({ audit: [] }),
      isLoading: false,
      isError: false,
      error: null,
    } as ReturnType<typeof incidentsApi.useIncidentDetail>)

    render(<IncidentDetail />, { wrapper })
    expect(screen.getByText(/no audit entries yet/i)).toBeInTheDocument()
  })
})
