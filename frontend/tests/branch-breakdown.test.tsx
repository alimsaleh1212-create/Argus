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
