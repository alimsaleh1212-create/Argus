import { useKpis } from '@/api/kpis'
import { Skeleton } from '@/components/ui/skeleton'
import { ErrorState } from '@/components/ErrorState'
import { VolumeChart } from './VolumeChart'
import { DispositionSplit } from './DispositionSplit'
import { MttdStat, MemoryHitStat } from './StatCards'

export function KpiDashboard() {
  const { data, isLoading, error } = useKpis()

  if (isLoading) {
    return (
      <div className="min-h-screen bg-slate-950 p-4 sm:p-6" aria-label="Loading KPIs">
        <div className="max-w-6xl mx-auto space-y-4">
          <Skeleton className="h-8 w-48" />
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <Skeleton className="h-52" />
            <Skeleton className="h-52" />
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <Skeleton className="h-24" />
            <Skeleton className="h-24" />
          </div>
        </div>
      </div>
    )
  }

  if (error) {
    return (
      <ErrorState
        message={`Failed to load KPIs: ${(error as Error)?.message ?? 'unknown error'}`}
      />
    )
  }

  if (!data) return null

  return (
    <div
      className="min-h-screen bg-slate-950 p-4 sm:p-6"
      data-testid="kpi-dashboard"
    >
      <div className="max-w-6xl mx-auto space-y-6">
        <div>
          <h1 className="text-lg font-semibold text-slate-100">Operational KPIs</h1>
          <p className="text-xs text-slate-500 mt-0.5">
            Generated {new Date(data.generated_at).toLocaleString()}
          </p>
        </div>

        {/* Charts row */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <VolumeChart buckets={data.volume_over_time} />
          <DispositionSplit split={data.disposition_split} />
        </div>

        {/* Stat cards row */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <MttdStat ms={data.mean_time_to_disposition_ms} />
          <MemoryHitStat snapshot={data} />
        </div>
      </div>
    </div>
  )
}
