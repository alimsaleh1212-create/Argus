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
