import { cn } from '@/lib/utils'
import type { StageNode } from '@/api/pipeline'

interface StageNodeCardProps {
  stage: StageNode
  justChanged: boolean
}

export function StageNodeCard({ stage, justChanged }: StageNodeCardProps) {
  return (
    <div
      className={cn(
        'rounded-lg bg-slate-900 border border-slate-700 p-4 flex flex-col gap-1 min-w-[120px]',
        'transition-colors duration-300 ease-out',
        justChanged && 'border-green-500 bg-green-500/10'
      )}
      data-testid={`stage-node-${stage.key}`}
    >
      <span className="text-xs font-semibold uppercase tracking-wider text-slate-400">
        {stage.label}
      </span>
      <span className="text-2xl font-mono font-bold text-slate-100">{stage.in_flight}</span>
      <span className="text-xs text-slate-500">in flight</span>
    </div>
  )
}
