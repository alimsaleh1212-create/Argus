import type { KpiSnapshot } from '@/api/kpis'

function StatCard({
  label,
  value,
  sub,
  testId,
}: {
  label: string
  value: string
  sub?: string
  testId?: string
}) {
  return (
    <div
      className="rounded-lg bg-slate-900 border border-slate-700 p-4 flex flex-col gap-1"
      data-testid={testId}
    >
      <span className="text-xs font-semibold uppercase tracking-wider text-slate-400">{label}</span>
      <span className="text-2xl font-mono font-bold text-slate-100">{value}</span>
      {sub && <span className="text-xs text-slate-500">{sub}</span>}
    </div>
  )
}

function formatMs(ms: number | null): string {
  if (ms === null) return '—'
  if (ms >= 3_600_000) return `${(ms / 3_600_000).toFixed(1)}h`
  if (ms >= 60_000) return `${(ms / 60_000).toFixed(1)}m`
  return `${(ms / 1000).toFixed(1)}s`
}

function formatRate(rate: number | null): string {
  if (rate === null) return '—'
  return `${(rate * 100).toFixed(1)}%`
}

interface MttdStatProps {
  ms: number | null
}

export function MttdStat({ ms }: MttdStatProps) {
  return (
    <StatCard
      label="Mean Time to Disposition"
      value={formatMs(ms)}
      sub={ms === null ? 'No terminal incidents yet' : 'avg across resolved / escalated'}
      testId="mttd-stat"
    />
  )
}

interface MemoryHitStatProps {
  snapshot: KpiSnapshot
}

export function MemoryHitStat({ snapshot }: MemoryHitStatProps) {
  const { enriched, hits, rate } = snapshot.memory_hit
  return (
    <StatCard
      label="Memory Hit Rate"
      value={formatRate(rate)}
      sub={`${hits} hits / ${enriched} enriched`}
      testId="memory-hit-stat"
    />
  )
}
