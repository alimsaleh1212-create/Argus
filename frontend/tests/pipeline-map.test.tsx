import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { StageNodeCard } from '@/features/map/StageNode'
import { FlowEdge } from '@/features/map/FlowEdge'
import { TerminalColumn } from '@/features/map/TerminalColumn'

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

describe('FlowEdge', () => {
  it('renders without the active styling by default', () => {
    const { container } = render(<FlowEdge active={false} />)
    expect(container.querySelector('[data-testid="flow-edge"] > div')).not.toHaveClass(
      'bg-green-500'
    )
  })

  it('applies the active styling when active is true', () => {
    const { container } = render(<FlowEdge active={true} />)
    expect(container.querySelector('[data-testid="flow-edge"] > div')).toHaveClass(
      'bg-green-500'
    )
  })
})

describe('TerminalColumn', () => {
  const terminals = { resolved: 12, escalated: 3, awaiting: 1 }

  it('renders all three terminal tiles with an icon and a count each', () => {
    render(<TerminalColumn terminals={terminals} changedKeys={new Set()} />)
    expect(screen.getByTestId('terminal-resolved')).toBeInTheDocument()
    expect(screen.getByTestId('terminal-escalated')).toBeInTheDocument()
    expect(screen.getByTestId('terminal-awaiting')).toBeInTheDocument()
    expect(screen.getByText('12')).toBeInTheDocument()
    expect(screen.getByText('3')).toBeInTheDocument()
    expect(screen.getByText('1')).toBeInTheDocument()
  })

  it('flashes only the tile whose key changed', () => {
    render(
      <TerminalColumn terminals={terminals} changedKeys={new Set(['escalated'])} />
    )
    expect(screen.getByTestId('terminal-escalated')).toHaveClass('border-orange-500')
    expect(screen.getByTestId('terminal-resolved')).not.toHaveClass('border-green-500')
  })

  it('labels each tile so color is never the only signal', () => {
    render(<TerminalColumn terminals={terminals} changedKeys={new Set()} />)
    expect(screen.getByText('Resolved')).toBeInTheDocument()
    expect(screen.getByText('Escalated')).toBeInTheDocument()
    expect(screen.getByText('Awaiting')).toBeInTheDocument()
  })
})
