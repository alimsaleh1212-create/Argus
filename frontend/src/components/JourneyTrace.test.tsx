import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import { JourneyTrace } from './JourneyTrace'

describe('JourneyTrace', () => {
  it('renders a chip per step with label and score', () => {
    render(
      <JourneyTrace
        steps={[
          { stage: 'intake', label: 'Intake', outcome: 'advance', detail: 'wazuh', score: null },
          { stage: 'triage', label: 'Triage', outcome: 'advance', detail: 'real', score: 0.82 },
          { stage: 'terminal', label: 'remediated', outcome: 'resolved', detail: 'remediated', score: null },
        ]}
      />
    )
    expect(screen.getByText('Intake')).toBeInTheDocument()
    expect(screen.getByText(/0\.82/)).toBeInTheDocument()
    expect(screen.getByTestId('journey-step-terminal')).toHaveTextContent('remediated')
  })

  it('renders nothing for an empty path', () => {
    const { container } = render(<JourneyTrace steps={[]} />)
    expect(container.firstChild).toBeNull()
  })
})
