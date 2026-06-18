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
