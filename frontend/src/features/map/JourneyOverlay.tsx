import { X } from 'lucide-react'
import { useIncidentDetail } from '@/api/incidents'
import { useTrace } from '@/api/trace'
import { stageForStatus, STAGE_KEYS } from './stageStatuses'

export interface Journey {
  currentStage: string | null
  visitedStages: Set<string>
  timingByStage: Record<string, number>
}

const STAGE_SPAN_RE = /^supervisor\.stage\.(triage|enrichment|response)$/

export function useJourney(incidentId: string | null): Journey | null {
  const { data: detail } = useIncidentDetail(incidentId ?? undefined)
  const { data: trace } = useTrace(incidentId ?? undefined)

  if (!incidentId || !detail) return null

  const visitedStages = new Set<string>(['intake'])
  const timingByStage: Record<string, number> = {}

  if (trace?.root) {
    const allSpans = [trace.root, ...Object.values(trace.children).flat()]
    for (const span of allSpans) {
      const match = STAGE_SPAN_RE.exec(span.name)
      if (!match) continue
      const stage = match[1]
      visitedStages.add(stage)
      if (span.latency_ms != null) {
        timingByStage[stage] = (timingByStage[stage] ?? 0) + span.latency_ms
      }
    }
  }

  return {
    currentStage: stageForStatus(detail.status),
    visitedStages,
    timingByStage,
  }
}

function formatTiming(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

interface JourneyOverlayProps {
  incidentId: string | null
  onClear: () => void
}

export function JourneyOverlay({ incidentId, onClear }: JourneyOverlayProps) {
  const journey = useJourney(incidentId)
  if (!journey) return null

  const path = STAGE_KEYS.filter((key) => journey.visitedStages.has(key))

  return (
    <div
      className="flex items-center gap-2 flex-wrap rounded-lg bg-sky-400/10 border border-sky-400/30 px-3 py-2 text-xs"
      data-testid="journey-overlay"
    >
      <span className="text-sky-300 font-semibold uppercase tracking-wider">Journey:</span>
      {path.map((stage, i) => (
        <span key={stage} className="flex items-center gap-1.5 text-slate-300 capitalize">
          {stage}
          {journey.timingByStage[stage] !== undefined && (
            <span className="font-mono text-cyan-400">{formatTiming(journey.timingByStage[stage])}</span>
          )}
          {i < path.length - 1 && <span className="text-slate-600">&rarr;</span>}
        </span>
      ))}
      <button
        type="button"
        onClick={onClear}
        aria-label="Clear journey"
        className="ml-auto text-slate-500 hover:text-slate-200 transition-colors cursor-pointer"
      >
        <X className="w-3.5 h-3.5" aria-hidden="true" />
      </button>
    </div>
  )
}
