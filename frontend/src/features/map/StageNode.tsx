import { ChevronDown, ChevronUp } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { StageNode } from '@/api/pipeline'

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

export function StageNodeCard({
  stage,
  justChanged,
  expanded = false,
  onToggleExpand,
  dimmed = false,
  journeyTimingMs,
}: StageNodeCardProps) {
  return (
    <div
      className={cn(
        'rounded-lg bg-slate-900 border border-slate-700 p-4 flex flex-col gap-1 min-w-[140px]',
        'transition-colors duration-300 ease-out',
        justChanged && 'border-cyan-400 bg-cyan-400/10',
        dimmed && 'opacity-40'
      )}
      data-testid={`stage-node-${stage.key}`}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-semibold uppercase tracking-wider text-slate-400">
          {stage.label}
        </span>
        {onToggleExpand && (
          <button
            type="button"
            onClick={onToggleExpand}
            aria-label={expanded ? `Collapse ${stage.label}` : `Expand ${stage.label}`}
            aria-expanded={expanded}
            className="text-slate-500 hover:text-slate-200 transition-colors cursor-pointer"
          >
            {expanded ? (
              <ChevronUp className="w-3.5 h-3.5" aria-hidden="true" />
            ) : (
              <ChevronDown className="w-3.5 h-3.5" aria-hidden="true" />
            )}
          </button>
        )}
      </div>
      <span className="text-2xl font-mono font-bold text-slate-100">{stage.in_flight}</span>
      <span className="text-xs text-slate-500">in flight</span>
      {journeyTimingMs !== undefined && (
        <span className="text-[11px] font-mono text-cyan-400 mt-1">
          {formatTiming(journeyTimingMs)}
        </span>
      )}
    </div>
  )
}
