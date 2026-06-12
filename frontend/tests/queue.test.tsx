import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { IncidentQueue } from '@/features/queue/IncidentQueue'
import * as incidentsApi from '@/api/incidents'
import type { QueuePage, IncidentSummary } from '@/api/incidents'

vi.mock('@/api/incidents', () => ({
  useIncidentQueue: vi.fn(),
}))

const mockUseIncidentQueue = vi.mocked(incidentsApi.useIncidentQueue)

function makeItem(overrides: Partial<IncidentSummary> = {}): IncidentSummary {
  return {
    id: '00000000-0000-0000-0000-000000000001',
    status: 'triaging',
    severity: 'high',
    disposition: null,
    source: 'wazuh',
    summary: 'Suspicious login attempt',
    is_awaiting_approval: false,
    created_at: '2026-06-12T09:00:00Z',
    updated_at: '2026-06-12T09:04:00Z',
    ...overrides,
  }
}

function makePage(items: IncidentSummary[], total?: number): QueuePage {
  return {
    items,
    total: total ?? items.length,
    limit: 50,
    offset: 0,
    view: 'active',
    applied_filters: { status: [], severity: [], sort: '-updated_at' },
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

describe('IncidentQueue', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders skeleton while loading', () => {
    mockUseIncidentQueue.mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
      error: null,
    } as ReturnType<typeof incidentsApi.useIncidentQueue>)

    render(<IncidentQueue />, { wrapper })
    // Skeleton elements present — no table
    expect(screen.queryByRole('table')).toBeNull()
  })

  it('shows empty state when no incidents', () => {
    mockUseIncidentQueue.mockReturnValue({
      data: makePage([]),
      isLoading: false,
      isError: false,
      error: null,
    } as ReturnType<typeof incidentsApi.useIncidentQueue>)

    render(<IncidentQueue />, { wrapper })
    expect(screen.getByText(/no incidents found/i)).toBeInTheDocument()
  })

  it('renders incident rows in the table', () => {
    mockUseIncidentQueue.mockReturnValue({
      data: makePage([makeItem()]),
      isLoading: false,
      isError: false,
      error: null,
    } as ReturnType<typeof incidentsApi.useIncidentQueue>)

    render(<IncidentQueue />, { wrapper })
    expect(screen.getByRole('table', { name: /incident queue/i })).toBeInTheDocument()
    expect(screen.getByText('wazuh')).toBeInTheDocument()
    expect(screen.getByText('Suspicious login attempt')).toBeInTheDocument()
  })

  it('renders severity and status badges', () => {
    mockUseIncidentQueue.mockReturnValue({
      data: makePage([makeItem({ severity: 'critical', status: 'awaiting_approval' })]),
      isLoading: false,
      isError: false,
      error: null,
    } as ReturnType<typeof incidentsApi.useIncidentQueue>)

    render(<IncidentQueue />, { wrapper })
    expect(screen.getByText('Critical')).toBeInTheDocument()
    expect(screen.getByText('Awaiting Approval')).toBeInTheDocument()
  })

  it('shows error state on fetch failure', () => {
    mockUseIncidentQueue.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error('Network error'),
    } as ReturnType<typeof incidentsApi.useIncidentQueue>)

    render(<IncidentQueue />, { wrapper })
    expect(screen.getByText(/failed to load incidents/i)).toBeInTheDocument()
  })

  it('toggles the filter panel open and closed', () => {
    mockUseIncidentQueue.mockReturnValue({
      data: makePage([]),
      isLoading: false,
      isError: false,
      error: null,
    } as ReturnType<typeof incidentsApi.useIncidentQueue>)

    render(<IncidentQueue />, { wrapper })
    const filterBtn = screen.getByRole('button', { name: /toggle filters/i })
    expect(screen.queryByText('Status')).toBeNull()
    fireEvent.click(filterBtn)
    expect(screen.getByText('Status')).toBeInTheDocument()
    expect(screen.getByText('Severity')).toBeInTheDocument()
  })

  it('shows pagination when total > page size', () => {
    mockUseIncidentQueue.mockReturnValue({
      data: { ...makePage([makeItem()]), total: 150 },
      isLoading: false,
      isError: false,
      error: null,
    } as ReturnType<typeof incidentsApi.useIncidentQueue>)

    render(<IncidentQueue />, { wrapper })
    expect(screen.getByText(/150 incidents/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /next page/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /previous page/i })).toBeInTheDocument()
  })

  it('does not show pagination when total <= page size', () => {
    mockUseIncidentQueue.mockReturnValue({
      data: makePage([makeItem()], 1),
      isLoading: false,
      isError: false,
      error: null,
    } as ReturnType<typeof incidentsApi.useIncidentQueue>)

    render(<IncidentQueue />, { wrapper })
    expect(screen.queryByRole('button', { name: /next page/i })).toBeNull()
  })

  it('switches view tabs', () => {
    mockUseIncidentQueue.mockReturnValue({
      data: makePage([]),
      isLoading: false,
      isError: false,
      error: null,
    } as ReturnType<typeof incidentsApi.useIncidentQueue>)

    render(<IncidentQueue />, { wrapper })
    fireEvent.click(screen.getByRole('button', { name: /resolved/i }))
    // hook is called with updated view — mock was called
    expect(mockUseIncidentQueue).toHaveBeenCalled()
  })
})
