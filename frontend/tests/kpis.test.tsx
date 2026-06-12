/**
 * Component tests for KPI Dashboard (T056).
 * - KpiDashboard: loading, error, renders charts + stats
 * - VolumeChart: renders with data, handles empty
 * - DispositionSplit: renders with data, handles empty
 * - MttdStat / MemoryHitStat: formats null, ms, rate
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { KpiDashboard } from '@/features/kpis/KpiDashboard'
import { VolumeChart } from '@/features/kpis/VolumeChart'
import { DispositionSplit } from '@/features/kpis/DispositionSplit'
import { MttdStat, MemoryHitStat } from '@/features/kpis/StatCards'
import * as kpisApi from '@/api/kpis'
import type { KpiSnapshot } from '@/api/kpis'

vi.mock('@/api/kpis', () => ({
  useKpis: vi.fn(),
}))

const mockUseKpis = vi.mocked(kpisApi.useKpis)

function makeSnapshot(overrides: Partial<KpiSnapshot> = {}): KpiSnapshot {
  return {
    volume_over_time: [
      { bucket: '2026-06-12T08:00:00Z', count: 12 },
      { bucket: '2026-06-12T09:00:00Z', count: 8 },
    ],
    disposition_split: {
      auto_remediated: 15,
      escalated: 3,
      rejected_by_human: 1,
    },
    mean_time_to_disposition_ms: 90_000,
    memory_hit: { enriched: 20, hits: 8, rate: 0.4 },
    generated_at: '2026-06-12T09:05:00Z',
    ...overrides,
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

// ─── KpiDashboard ────────────────────────────────────────────────────────────

describe('KpiDashboard', () => {
  beforeEach(() => vi.clearAllMocks())

  it('renders loading skeleton', () => {
    mockUseKpis.mockReturnValue({ data: undefined, isLoading: true, error: null } as ReturnType<typeof kpisApi.useKpis>)
    render(<KpiDashboard />, { wrapper })
    expect(screen.getByLabelText('Loading KPIs')).toBeInTheDocument()
  })

  it('renders error state', () => {
    mockUseKpis.mockReturnValue({ data: undefined, isLoading: false, error: new Error('fetch failed') } as ReturnType<typeof kpisApi.useKpis>)
    render(<KpiDashboard />, { wrapper })
    expect(screen.getByText(/failed to load kpis/i)).toBeInTheDocument()
  })

  it('renders all KPI panels when data loaded', () => {
    mockUseKpis.mockReturnValue({ data: makeSnapshot(), isLoading: false, error: null } as ReturnType<typeof kpisApi.useKpis>)
    render(<KpiDashboard />, { wrapper })
    expect(screen.getByTestId('kpi-dashboard')).toBeInTheDocument()
    expect(screen.getByTestId('volume-chart')).toBeInTheDocument()
    expect(screen.getByTestId('disposition-split')).toBeInTheDocument()
    expect(screen.getByTestId('mttd-stat')).toBeInTheDocument()
    expect(screen.getByTestId('memory-hit-stat')).toBeInTheDocument()
  })

  it('shows generated_at timestamp', () => {
    mockUseKpis.mockReturnValue({ data: makeSnapshot(), isLoading: false, error: null } as ReturnType<typeof kpisApi.useKpis>)
    render(<KpiDashboard />, { wrapper })
    expect(screen.getByText(/generated/i)).toBeInTheDocument()
  })
})

// ─── VolumeChart ─────────────────────────────────────────────────────────────

describe('VolumeChart', () => {
  it('renders with bucket data', () => {
    render(
      <VolumeChart buckets={[{ bucket: '2026-06-12T09:00:00Z', count: 5 }]} />,
      { wrapper }
    )
    expect(screen.getByTestId('volume-chart')).toBeInTheDocument()
  })

  it('shows "No data" for empty buckets', () => {
    render(<VolumeChart buckets={[]} />, { wrapper })
    expect(screen.getByText(/no data/i)).toBeInTheDocument()
  })
})

// ─── DispositionSplit ────────────────────────────────────────────────────────

describe('DispositionSplit', () => {
  it('renders with disposition data', () => {
    render(
      <DispositionSplit split={{ auto_remediated: 10, escalated: 2 }} />,
      { wrapper }
    )
    expect(screen.getByTestId('disposition-split')).toBeInTheDocument()
  })

  it('shows "No dispositions yet" for empty split', () => {
    render(<DispositionSplit split={{}} />, { wrapper })
    expect(screen.getByText(/no dispositions yet/i)).toBeInTheDocument()
  })

  it('filters out _none bucket', () => {
    render(
      <DispositionSplit split={{ _none: 99, auto_remediated: 5 }} />,
      { wrapper }
    )
    // _none should not appear (it's filtered out)
    expect(screen.queryByText('none')).toBeNull()
  })
})

// ─── StatCards ───────────────────────────────────────────────────────────────

describe('MttdStat', () => {
  it('renders — when ms is null', () => {
    render(<MttdStat ms={null} />, { wrapper })
    expect(screen.getByTestId('mttd-stat')).toBeInTheDocument()
    expect(screen.getByText('—')).toBeInTheDocument()
  })

  it('formats ms as seconds', () => {
    render(<MttdStat ms={45_000} />, { wrapper })
    expect(screen.getByText('45.0s')).toBeInTheDocument()
  })

  it('formats ms as minutes', () => {
    render(<MttdStat ms={180_000} />, { wrapper })
    expect(screen.getByText('3.0m')).toBeInTheDocument()
  })

  it('formats ms as hours', () => {
    render(<MttdStat ms={3_600_000 * 2} />, { wrapper })
    expect(screen.getByText('2.0h')).toBeInTheDocument()
  })
})

describe('MemoryHitStat', () => {
  it('renders — when rate is null', () => {
    const snapshot = makeSnapshot({ memory_hit: { enriched: 0, hits: 0, rate: null } })
    render(<MemoryHitStat snapshot={snapshot} />, { wrapper })
    expect(screen.getByTestId('memory-hit-stat')).toBeInTheDocument()
    expect(screen.getByText('—')).toBeInTheDocument()
    expect(screen.getByText(/0 hits \/ 0 enriched/)).toBeInTheDocument()
  })

  it('renders rate as percentage', () => {
    const snapshot = makeSnapshot({ memory_hit: { enriched: 20, hits: 8, rate: 0.4 } })
    render(<MemoryHitStat snapshot={snapshot} />, { wrapper })
    expect(screen.getByText('40.0%')).toBeInTheDocument()
    expect(screen.getByText(/8 hits \/ 20 enriched/)).toBeInTheDocument()
  })

  it('shows denominator in the sub label', () => {
    const snapshot = makeSnapshot({ memory_hit: { enriched: 50, hits: 25, rate: 0.5 } })
    render(<MemoryHitStat snapshot={snapshot} />, { wrapper })
    expect(screen.getByText(/25 hits \/ 50 enriched/)).toBeInTheDocument()
  })
})
