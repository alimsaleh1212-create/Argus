import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ArrowLeft, GitBranch } from 'lucide-react'
import { useTrace, type SpanView } from '@/api/trace'
import { Telemetry } from './Telemetry'
import { SpanTree } from './SpanTree'
import { SpanDetail } from './SpanDetail'
import { Skeleton } from '@/components/ui/skeleton'
import { ErrorState } from '@/components/ErrorState'

function EmptyTrace({ correlationId }: { correlationId: string }) {
  return (
    <div
      className="text-center py-16 text-slate-400"
      data-testid="empty-trace"
      aria-label="No trace data"
    >
      <GitBranch className="w-10 h-10 mx-auto mb-3 opacity-40" aria-hidden />
      <p className="text-sm">No trace spans recorded yet.</p>
      <p className="text-xs mt-1 font-mono text-slate-500">{correlationId}</p>
    </div>
  )
}

export function TraceInspector() {
  const { id } = useParams<{ id: string }>()
  const { data, isLoading, error } = useTrace(id)
  const [selectedSpan, setSelectedSpan] = useState<SpanView | null>(null)

  if (isLoading) {
    return (
      <div className="min-h-screen bg-slate-950 p-4 sm:p-6" aria-label="Loading trace">
        <div className="max-w-6xl mx-auto space-y-4">
          <Skeleton className="h-8 w-64" />
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-64 w-full" />
        </div>
      </div>
    )
  }

  if (error) {
    return (
      <ErrorState
        message={`Failed to load trace: ${(error as Error)?.message ?? 'unknown error'}`}
      />
    )
  }

  if (!data) return null

  const hasSpans = data.root !== null

  return (
    <div className="min-h-screen bg-slate-950 p-4 sm:p-6">
      <div className="max-w-6xl mx-auto space-y-6">
        {/* Header */}
        <div className="flex items-center gap-3">
          <Link
            to={`/incidents/${id}`}
            className="text-slate-400 hover:text-slate-100 transition-colors cursor-pointer"
            aria-label="Back to incident"
          >
            <ArrowLeft className="w-5 h-5" />
          </Link>
          <div>
            <h1 className="text-lg font-semibold text-slate-100">Pipeline Trace</h1>
            <p className="text-xs font-mono text-slate-400">{data.correlation_id}</p>
          </div>
        </div>

        {/* Telemetry rollup */}
        <Telemetry telemetry={data.telemetry} />

        {/* Tree + detail */}
        {hasSpans ? (
          <div className="grid grid-cols-1 lg:grid-cols-[1fr_22rem] gap-4">
            <SpanTree
              root={data.root!}
              children={data.children}
              onSelect={setSelectedSpan}
              selectedId={selectedSpan?.span_id ?? null}
            />
            {selectedSpan && (
              <SpanDetail span={selectedSpan} onClose={() => setSelectedSpan(null)} />
            )}
          </div>
        ) : (
          <EmptyTrace correlationId={data.correlation_id} />
        )}
      </div>
    </div>
  )
}
