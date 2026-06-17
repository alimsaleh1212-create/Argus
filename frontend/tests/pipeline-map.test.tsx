import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { StageNodeCard } from '@/features/map/StageNode'
import { FlowEdge } from '@/features/map/FlowEdge'
import { TerminalColumn } from '@/features/map/TerminalColumn'
import { PipelineMap } from '@/features/map/PipelineMap'
import * as animatedApi from '@/features/map/useAnimatedPipeline'

vi.mock('@/features/map/useAnimatedPipeline', () => ({
  useAnimatedPipeline: vi.fn(),
}))

const mockUseAnimatedPipeline = vi.mocked(animatedApi.useAnimatedPipeline)

function makeSnapshot() {
  return {
    stages: [
      { key: 'intake', label: 'Intake', in_flight: 2, branches: [] },
      { key: 'triage', label: 'Triage', in_flight: 4, branches: [] },
      { key: 'enrichment', label: 'Enrichment', in_flight: 1, branches: [] },
      { key: 'response', label: 'Response', in_flight: 3, branches: [] },
    ],
    terminals: { resolved: 10, escalated: 2, awaiting: 1 },
    window_hours: 24,
    generated_at: '2026-06-17T12:00:00Z',
  }
}

function baseAnimatedPipeline() {
  return {
    snapshot: makeSnapshot(),
    isLoading: false,
    error: null,
    changedStageKeys: new Set<string>(),
    changedTerminalKeys: new Set<string>(),
    paused: false,
    togglePaused: vi.fn(),
    prefersReducedMotion: false,
  }
}

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
    expect(screen.getByTestId('stage-node-triage')).toHaveClass('border-cyan-400')
  })

  it('does not apply the flash styling when justChanged is false', () => {
    render(<StageNodeCard stage={stage} justChanged={false} />)
    expect(screen.getByTestId('stage-node-triage')).not.toHaveClass('border-cyan-400')
  })

  it('shows an expand toggle and calls onToggleExpand when clicked', async () => {
    const onToggleExpand = vi.fn()
    const { default: userEvent } = await import('@testing-library/user-event')
    render(
      <StageNodeCard stage={stage} justChanged={false} expanded={false} onToggleExpand={onToggleExpand} />
    )
    await userEvent.click(screen.getByRole('button', { name: /expand triage/i }))
    expect(onToggleExpand).toHaveBeenCalledOnce()
  })

  it('renders a collapse label when expanded is true', () => {
    render(
      <StageNodeCard stage={stage} justChanged={false} expanded={true} onToggleExpand={vi.fn()} />
    )
    expect(screen.getByRole('button', { name: /collapse triage/i })).toBeInTheDocument()
  })

  it('applies dimmed styling when dimmed is true', () => {
    render(<StageNodeCard stage={stage} justChanged={false} dimmed={true} />)
    expect(screen.getByTestId('stage-node-triage')).toHaveClass('opacity-40')
  })

  it('shows journey timing when journeyTimingMs is provided', () => {
    render(<StageNodeCard stage={stage} justChanged={false} journeyTimingMs={1500} />)
    expect(screen.getByText('1.5s')).toBeInTheDocument()
  })
})

describe('FlowEdge', () => {
  it('renders without the active styling by default', () => {
    const { container } = render(<FlowEdge active={false} />)
    expect(container.querySelector('[data-testid="flow-edge"] > div')).not.toHaveClass(
      'bg-cyan-400'
    )
  })

  it('applies the active styling when active is true', () => {
    const { container } = render(<FlowEdge active={true} />)
    expect(container.querySelector('[data-testid="flow-edge"] > div')).toHaveClass(
      'bg-cyan-400'
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
    expect(screen.getByTestId('terminal-resolved')).not.toHaveClass('border-cyan-400')
  })

  it('labels each tile so color is never the only signal', () => {
    render(<TerminalColumn terminals={terminals} changedKeys={new Set()} />)
    expect(screen.getByText('Resolved')).toBeInTheDocument()
    expect(screen.getByText('Escalated')).toBeInTheDocument()
    expect(screen.getByText('Awaiting')).toBeInTheDocument()
  })
})

describe('PipelineMap', () => {
  beforeEach(() => vi.clearAllMocks())

  it('renders a loading skeleton while data is loading', () => {
    mockUseAnimatedPipeline.mockReturnValue({
      ...baseAnimatedPipeline(),
      snapshot: undefined,
      isLoading: true,
    } as ReturnType<typeof animatedApi.useAnimatedPipeline>)
    render(<PipelineMap />, { wrapper: ({ children }) => <MemoryRouter>{children}</MemoryRouter> })
    expect(screen.getByLabelText('Loading pipeline map')).toBeInTheDocument()
  })

  it('renders an error state when the fetch fails', () => {
    mockUseAnimatedPipeline.mockReturnValue({
      ...baseAnimatedPipeline(),
      snapshot: undefined,
      isLoading: false,
      error: new Error('fetch failed'),
    } as ReturnType<typeof animatedApi.useAnimatedPipeline>)
    render(<PipelineMap />, { wrapper: ({ children }) => <MemoryRouter>{children}</MemoryRouter> })
    expect(screen.getByText(/failed to load pipeline map/i)).toBeInTheDocument()
  })

  it('renders an empty state when there are no in-flight incidents and no terminals', () => {
    mockUseAnimatedPipeline.mockReturnValue({
      ...baseAnimatedPipeline(),
      snapshot: {
        stages: [
          { key: 'intake', label: 'Intake', in_flight: 0, branches: [] },
          { key: 'triage', label: 'Triage', in_flight: 0, branches: [] },
          { key: 'enrichment', label: 'Enrichment', in_flight: 0, branches: [] },
          { key: 'response', label: 'Response', in_flight: 0, branches: [] },
        ],
        terminals: { resolved: 0, escalated: 0, awaiting: 0 },
        window_hours: 24,
        generated_at: '2026-06-17T12:00:00Z',
      },
    } as ReturnType<typeof animatedApi.useAnimatedPipeline>)
    render(<PipelineMap />, { wrapper: ({ children }) => <MemoryRouter>{children}</MemoryRouter> })
    expect(screen.getByText(/no incidents in flight/i)).toBeInTheDocument()
  })

  it('renders the rail and terminal column when data is loaded', () => {
    mockUseAnimatedPipeline.mockReturnValue(
      baseAnimatedPipeline() as ReturnType<typeof animatedApi.useAnimatedPipeline>
    )
    render(<PipelineMap />, { wrapper: ({ children }) => <MemoryRouter>{children}</MemoryRouter> })
    expect(screen.getByTestId('stage-node-intake')).toBeInTheDocument()
    expect(screen.getByTestId('stage-node-response')).toBeInTheDocument()
    expect(screen.getByTestId('terminal-column')).toBeInTheDocument()
  })

  it('calls togglePaused when the Live/Pause button is clicked', async () => {
    const togglePaused = vi.fn()
    mockUseAnimatedPipeline.mockReturnValue({
      ...baseAnimatedPipeline(),
      togglePaused,
    } as ReturnType<typeof animatedApi.useAnimatedPipeline>)
    const { default: userEvent } = await import('@testing-library/user-event')
    render(<PipelineMap />, { wrapper: ({ children }) => <MemoryRouter>{children}</MemoryRouter> })
    await userEvent.click(screen.getByRole('button', { name: /live|pause/i }))
    expect(togglePaused).toHaveBeenCalledOnce()
  })

  it('shows "Paused" label when paused is true', () => {
    mockUseAnimatedPipeline.mockReturnValue({
      ...baseAnimatedPipeline(),
      paused: true,
    } as ReturnType<typeof animatedApi.useAnimatedPipeline>)
    render(<PipelineMap />, { wrapper: ({ children }) => <MemoryRouter>{children}</MemoryRouter> })
    expect(screen.getByRole('button', { name: /paused/i })).toBeInTheDocument()
  })
})
