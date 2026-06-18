import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { BranchBreakdown } from '@/features/map/BranchBreakdown'
import type { StageNode } from '@/api/pipeline'

function makeStage(overrides: Partial<StageNode> = {}): StageNode {
  return {
    key: 'triage',
    label: 'Triage',
    in_flight: 2,
    branches: [
      { to: 'resolved', count: 5 },
      { to: 'escalated', count: 1 },
    ],
    incidents: [],
    ...overrides,
  }
}

function makeIncident(overrides: Partial<NonNullable<StageNode['incidents'][number]>> = {}) {
  return {
    id: '00000000-0000-0000-0000-000000000001',
    status: 'triaging',
    severity: 'high',
    source: 'wazuh',
    summary: 'Suspicious login attempt',
    updated_at: '2026-06-18T09:05:00Z',
    triage_verdict: 'real',
    triage_confidence: 0.82,
    enrichment_assessment: null,
    enrichment_confidence: null,
    response_plan_id: null,
    response_selected_by: null,
    response_verdict: null,
    ...overrides,
  }
}

function wrapper({ children }: { children: React.ReactNode }) {
  return <MemoryRouter>{children}</MemoryRouter>
}

describe('BranchBreakdown', () => {
  it('renders outflow bars with destination and count', () => {
    render(<BranchBreakdown stage={makeStage()} onSelectIncident={vi.fn()} />, { wrapper })
    expect(screen.getByText('5')).toBeInTheDocument()
    expect(screen.getByText('1')).toBeInTheDocument()
  })

  it('renders in-flight incidents from the snapshot with severity and summary', () => {
    render(
      <BranchBreakdown
        stage={makeStage({ incidents: [makeIncident()] })}
        onSelectIncident={vi.fn()}
      />,
      { wrapper }
    )
    expect(screen.getByText('Suspicious login attempt')).toBeInTheDocument()
    expect(screen.getByText('High')).toBeInTheDocument()
  })

  it('renders triage and enrichment score chips when present', () => {
    render(
      <BranchBreakdown
        stage={makeStage({
          incidents: [
            makeIncident({
              triage_verdict: 'real',
              triage_confidence: 0.82,
              enrichment_assessment: 'confirmed',
              enrichment_confidence: 0.71,
              response_plan_id: 'isolate_host',
              response_verdict: 'verified',
            }),
          ],
        })}
        onSelectIncident={vi.fn()}
      />,
      { wrapper }
    )
    expect(screen.getByText(/real · 82%/i)).toBeInTheDocument()
    expect(screen.getByText(/confirmed · 71%/i)).toBeInTheDocument()
    expect(screen.getByText(/isolate_host · verified/i)).toBeInTheDocument()
  })

  it('calls onSelectIncident when an incident row is clicked', async () => {
    const onSelectIncident = vi.fn()
    const { default: userEvent } = await import('@testing-library/user-event')
    render(
      <BranchBreakdown
        stage={makeStage({ incidents: [makeIncident()] })}
        onSelectIncident={onSelectIncident}
      />,
      { wrapper }
    )
    await userEvent.click(
      screen.getByRole('button', { name: /open incident 00000000-0000-0000-0000-000000000001/i })
    )
    expect(onSelectIncident).toHaveBeenCalledWith('00000000-0000-0000-0000-000000000001')
  })

  it('shows an empty message when no incidents are currently in this stage', () => {
    render(<BranchBreakdown stage={makeStage()} onSelectIncident={vi.fn()} />, { wrapper })
    expect(screen.getByText(/no incidents currently in this stage/i)).toBeInTheDocument()
  })
})
