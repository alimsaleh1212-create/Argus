import { ChevronDown, ChevronUp } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { StageNode } from '@/api/pipeline'
import { deriveStageInsights, type DecisionTally } from './stageInsights'
import { Sparkline, SeverityBar } from './Sparkline'

interface StageNodeCardProps {
  stage: StageNode
  justChanged: boolean
  expanded?: boolean
  onToggleExpand?: () => void
  dimmed?: boolean
  journeyTimingMs?: number
}

function formatTiming(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

const TALLY_TONE: Record<DecisionTally['tone'], string> = {
  rose: 'text-rose-300 bg-rose-500/10 ring-rose-500/30',
  cyan: 'text-cyan-300 bg-cyan-500/10 ring-cyan-500/30',
  amber: 'text-amber-300 bg-amber-500/10 ring-amber-500/30',
  sky: 'text-sky-300 bg-sky-500/10 ring-sky-500/30',
  slate: 'text-slate-300 bg-slate-700/40 ring-slate-600/40',
}

const SPARK_COLOR: Record<string, string> = {
  triage: '#22D3EE',
  enrichment: '#A78BFA',
}

export function StageNodeCard({
  stage,
  justChanged,
  expanded = false,
  onToggleExpand,
  dimmed = false,
  journeyTimingMs,
}: StageNodeCardProps) {
  const insights = deriveStageInsights(stage)
  const active = stage.in_flight > 0
  const severe = insights.severe && !dimmed

  return (
    <div
      className={cn(
        'group relative rounded-xl border p-4 flex flex-col gap-3',
        'bg-gradient-to-b from-slate-900/90 to-slate-950/90 border-slate-700/80',
        'transition-[border-color,box-shadow,background-color] duration-300 ease-out',
        active && 'border-slate-600',
        justChanged && 'border-cyan-400 bg-cyan-400/10 shadow-[0_0_24px_-6px_rgba(34,211,238,0.6)]',
        severe && 'argus-pulse-red border-red-500/60',
        dimmed && 'opacity-40'
      )}
      data-testid={`stage-node-${stage.key}`}
    >
      {/* top accent rail */}
      <div
        className={cn(
          'absolute inset-x-0 top-0 h-0.5 rounded-t-xl',
          severe ? 'bg-red-500' : active ? 'bg-sky-400/70' : 'bg-slate-700'
        )}
        aria-hidden="true"
      />

      <div className="flex items-center justify-between gap-2">
        <span className="text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-400">
          {stage.label}
        </span>
        {onToggleExpand && (
          <button
            type="button"
            onClick={onToggleExpand}
            aria-label={expanded ? `Collapse ${stage.label}` : `Expand ${stage.label}`}
            aria-expanded={expanded}
            className="rounded-md p-0.5 text-slate-500 hover:text-slate-100 hover:bg-slate-800 transition-colors cursor-pointer"
          >
            {expanded ? (
              <ChevronUp className="w-4 h-4" aria-hidden="true" />
            ) : (
              <ChevronDown className="w-4 h-4" aria-hidden="true" />
            )}
          </button>
        )}
      </div>

      <div className="flex items-end justify-between gap-2">
        <div className="flex flex-col">
          <span
            className={cn(
              'text-4xl font-mono font-bold leading-none tabular-nums',
              active ? 'text-slate-50' : 'text-slate-600'
            )}
          >
            {stage.in_flight}
          </span>
          <span className="text-[11px] text-slate-500 mt-1">in flight</span>
        </div>

        {insights.confidence.length > 0 && (
          <div className="flex flex-col items-end gap-1">
            <Sparkline
              values={insights.confidence}
              color={SPARK_COLOR[stage.key] ?? '#38BDF8'}
              ariaLabel={`${stage.label} confidence trend`}
            />
            {insights.confidencePct !== null && (
              <span className="text-[10px] font-mono text-slate-400">
                <span className="opacity-60">μ conf</span>{' '}
                <span className="text-slate-200">{insights.confidencePct}%</span>
              </span>
            )}
          </div>
        )}
      </div>

      {insights.severity.length > 0 && (
        <SeverityBar slices={insights.severity} />
      )}

      {insights.decisions.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {insights.decisions.slice(0, 4).map((d) => (
            <span
              key={d.label}
              className={cn(
                'inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[10px] font-mono ring-1 ring-inset',
                TALLY_TONE[d.tone]
              )}
            >
              <span className="truncate max-w-[88px]">{d.label}</span>
              <span className="opacity-70 tabular-nums">{d.count}</span>
            </span>
          ))}
        </div>
      )}

      {journeyTimingMs !== undefined && (
        <span className="text-[11px] font-mono text-cyan-400">{formatTiming(journeyTimingMs)}</span>
      )}
    </div>
  )
}
