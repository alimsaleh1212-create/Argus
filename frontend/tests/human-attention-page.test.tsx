import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { HumanAttentionPage } from '@/features/attention/HumanAttentionPage'

vi.mock('@/features/map/HumanAttentionLane', () => ({
  HumanAttentionLane: ({ onSelectIncident }: { onSelectIncident: (id: string) => void }) => (
    <button
      data-testid="human-attention-lane-mock"
      onClick={() => onSelectIncident('00000000-0000-0000-0000-000000000009')}
    >
      lane
    </button>
  ),
}))

describe('HumanAttentionPage', () => {
  it('renders a heading and the Human Attention lane', () => {
    render(<HumanAttentionPage />, {
      wrapper: ({ children }) => <MemoryRouter>{children}</MemoryRouter>,
    })
    expect(screen.getByRole('heading', { name: /human attention/i })).toBeInTheDocument()
    expect(screen.getByTestId('human-attention-lane-mock')).toBeInTheDocument()
  })

  it('navigates to the incident detail when the lane selects an incident', async () => {
    const { default: userEvent } = await import('@testing-library/user-event')
    render(
      <MemoryRouter initialEntries={['/attention']}>
        <Routes>
          <Route path="/attention" element={<HumanAttentionPage />} />
          <Route
            path="/incidents/:id"
            element={<div data-testid="incident-detail-mock" />}
          />
        </Routes>
      </MemoryRouter>
    )
    await userEvent.click(screen.getByTestId('human-attention-lane-mock'))
    expect(screen.getByTestId('incident-detail-mock')).toBeInTheDocument()
  })
})
