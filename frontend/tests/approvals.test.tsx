import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { ApprovalPanel } from '@/features/approvals/ApprovalPanel'
import { DeadlineCountdown } from '@/features/approvals/DeadlineCountdown'
import * as approvalsApi from '@/api/approvals'
import type { ApprovalView } from '@/api/incidents'

vi.mock('@/api/approvals', () => ({
  useApprovalDecision: vi.fn(),
  usePendingApprovals: vi.fn(),
}))

const mockUseApprovalDecision = vi.mocked(approvalsApi.useApprovalDecision)

function makeApproval(overrides: Partial<ApprovalView> = {}): ApprovalView {
  return {
    id: 1,
    incident_id: 'aaaa-0001',
    plan_id: 'plan-001',
    pending_actions: [
      { action_id: 'isolate_host', target: 'srv-01' },
      { action_id: 'block_ip', target: '10.0.0.1' },
    ],
    rationale: 'Host exhibits signs of compromise.',
    status: 'pending',
    deadline_at: new Date(Date.now() + 3600_000).toISOString(),
    created_at: new Date().toISOString(),
    is_actionable: true,
    ...overrides,
  }
}

function makeMutate(opts: { isSuccess?: boolean; error?: Error | null } = {}) {
  return vi.fn().mockImplementation((_vars: unknown, callbacks?: { onSuccess?: Function; onError?: Function }) => {
    if (opts.error && callbacks?.onError) {
      callbacks.onError(opts.error)
    } else if (callbacks?.onSuccess) {
      callbacks.onSuccess({ incident_id: 'aaaa-0001', decision: 'approve', status: 'resolved', disposition: 'remediated' })
    }
  })
}

function wrapper({ children }: { children: React.ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return (
    <QueryClientProvider client={qc}>
      <MemoryRouter>{children}</MemoryRouter>
    </QueryClientProvider>
  )
}

describe('DeadlineCountdown', () => {
  it('renders remaining time for a future deadline', () => {
    const future = new Date(Date.now() + 3600_000).toISOString()
    render(<DeadlineCountdown deadlineAt={future} />, { wrapper })
    expect(screen.getByTestId('deadline-countdown')).toBeInTheDocument()
    const text = screen.getByTestId('deadline-countdown').textContent
    expect(text).not.toBe('Expired')
    expect(text).toBeTruthy()
  })

  it('shows "Expired" for a past deadline', () => {
    const past = new Date(Date.now() - 3600_000).toISOString()
    render(<DeadlineCountdown deadlineAt={past} />, { wrapper })
    expect(screen.getByTestId('deadline-countdown').textContent).toBe('Expired')
  })

  it('renders nothing when deadlineAt is null', () => {
    render(<DeadlineCountdown deadlineAt={null} />, { wrapper })
    expect(screen.queryByTestId('deadline-countdown')).toBeNull()
  })
})

describe('ApprovalPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  function mockDecision(isPending = false) {
    mockUseApprovalDecision.mockReturnValue({
      mutate: makeMutate(),
      isPending,
      isError: false,
      error: null,
    } as ReturnType<typeof approvalsApi.useApprovalDecision>)
  }

  it('renders rationale and pending actions', () => {
    mockDecision()
    render(<ApprovalPanel approval={makeApproval()} />, { wrapper })
    expect(screen.getByText('Host exhibits signs of compromise.')).toBeInTheDocument()
    expect(screen.getByText(/isolate_host/)).toBeInTheDocument()
    expect(screen.getByText(/block_ip/)).toBeInTheDocument()
  })

  it('renders Approve and Reject buttons when is_actionable', () => {
    mockDecision()
    render(<ApprovalPanel approval={makeApproval()} />, { wrapper })
    expect(screen.getByRole('button', { name: /approve remediation/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /reject remediation/i })).toBeInTheDocument()
  })

  it('hides buttons when is_actionable is false', () => {
    mockDecision()
    render(<ApprovalPanel approval={makeApproval({ is_actionable: false })} />, { wrapper })
    expect(screen.queryByRole('button', { name: /approve remediation/i })).toBeNull()
    expect(screen.queryByRole('button', { name: /reject remediation/i })).toBeNull()
  })

  it('opens confirmation dialog on Approve click', () => {
    mockDecision()
    render(<ApprovalPanel approval={makeApproval()} />, { wrapper })
    fireEvent.click(screen.getByRole('button', { name: /approve remediation/i }))
    // Dialog opens — confirm button appears inside it
    expect(screen.getByRole('button', { name: /confirm approve/i })).toBeInTheDocument()
  })

  it('opens confirmation dialog on Reject click', () => {
    mockDecision()
    render(<ApprovalPanel approval={makeApproval()} />, { wrapper })
    fireEvent.click(screen.getByRole('button', { name: /reject remediation/i }))
    // Dialog opens — confirm button appears inside it
    expect(screen.getByRole('button', { name: /confirm reject/i })).toBeInTheDocument()
  })

  it('disables buttons while isPending', () => {
    mockDecision(true)
    render(<ApprovalPanel approval={makeApproval()} />, { wrapper })
    expect(screen.getByRole('button', { name: /approve remediation/i })).toBeDisabled()
    expect(screen.getByRole('button', { name: /reject remediation/i })).toBeDisabled()
  })

  it('shows already-decided banner after a 409 error', async () => {
    const mutate409 = vi.fn().mockImplementation((_vars: unknown, callbacks?: { onError?: Function }) => {
      callbacks?.onError?.(new Error('409: already decided'))
    })
    mockUseApprovalDecision.mockReturnValue({
      mutate: mutate409,
      isPending: false,
      isError: false,
      error: null,
    } as ReturnType<typeof approvalsApi.useApprovalDecision>)

    render(<ApprovalPanel approval={makeApproval()} />, { wrapper })
    fireEvent.click(screen.getByRole('button', { name: /approve remediation/i }))
    // Confirm in dialog
    fireEvent.click(screen.getByRole('button', { name: /confirm approve/i }))

    await waitFor(() => {
      expect(screen.getByTestId('already-decided-banner')).toBeInTheDocument()
    })
  })

  it('renders deadline countdown', () => {
    mockDecision()
    render(<ApprovalPanel approval={makeApproval()} />, { wrapper })
    expect(screen.getByTestId('deadline-countdown')).toBeInTheDocument()
  })

  it('reject path calls mutate with reject decision', () => {
    const mutate = makeMutate()
    mockUseApprovalDecision.mockReturnValue({
      mutate,
      isPending: false,
      isError: false,
      error: null,
    } as ReturnType<typeof approvalsApi.useApprovalDecision>)

    render(<ApprovalPanel approval={makeApproval()} />, { wrapper })
    fireEvent.click(screen.getByRole('button', { name: /reject remediation/i }))
    fireEvent.click(screen.getByRole('button', { name: /confirm reject/i }))

    expect(mutate).toHaveBeenCalledWith(
      { decision: 'reject' },
      expect.objectContaining({ onSuccess: expect.any(Function) })
    )
  })
})
