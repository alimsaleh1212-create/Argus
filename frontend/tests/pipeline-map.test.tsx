import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { StageNodeCard } from '@/features/map/StageNode'

describe('StageNodeCard', () => {
  const stage = { key: 'triage', label: 'Triage', in_flight: 4, branches: [] }

  it('renders the stage label and in-flight count', () => {
    render(<StageNodeCard stage={stage} justChanged={false} />)
    expect(screen.getByTestId('stage-node-triage')).toBeInTheDocument()
    expect(screen.getByText('Triage')).toBeInTheDocument()
    expect(screen.getByText('4')).toBeInTheDocument()
  })

  it('applies the flash styling when justChanged is true', () => {
    render(<StageNodeCard stage={stage} justChanged={true} />)
    expect(screen.getByTestId('stage-node-triage')).toHaveClass('border-green-500')
  })

  it('does not apply the flash styling when justChanged is false', () => {
    render(<StageNodeCard stage={stage} justChanged={false} />)
    expect(screen.getByTestId('stage-node-triage')).not.toHaveClass('border-green-500')
  })
})
