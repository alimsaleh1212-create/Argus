import { useIncidentQueue } from '@/api/incidents'
import { SeverityBadge } from '@/components/SeverityBadge'
import { StatusBadge } from '@/components/StatusBadge'
import { Skeleton } from '@/components/ui/skeleton'
import { STAGE_STATUSES } from './stageStatuses'
import type { StageNode } from '@/api/pipeline'

interface BranchBreakdownProps {
  stage: StageNode
  onSelectIncident: (incidentId: string) => void
}

const BRANCH_COLOR: Record<string, string> = {
  resolved: 'bg-cyan-400',
  escalated: 'bg-orange-400',
}

function timeAgo(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime()
  const mins = Math.floor(ms / 60_000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours}h ago`
  return `${Math.floor(hours / 24)}d ago`
}

export function BranchBreakdown({ stage, onSelectIncident }: BranchBreakdownProps) {
  const statuses = STAGE_STATUSES[stage.key] ?? []
  const { data, isLoading } = useIncidentQueue({
    view: 'active',
    status: statuses,
    sort: '-updated_at',
    limit: 10,
  })

  const maxCount = Math.max(1, ...stage.branches.map((b) => b.count))

  return (
    <div
      className="rounded-lg bg-slate-900/60 border border-slate-800 p-4 mt-2 w-full sm:w-[340px] space-y-4"
      data-testid={`branch-breakdown-${stage.key}`}
    >
      <div>
        <p className="text-xs font-semibold uppercase tracking-wider text-slate-500 mb-2">
          Outflow (window)
        </p>
        {stage.branches.length === 0 ? (
          <p className="text-xs text-slate-600 italic">No outflow recorded in this window.</p>
        ) : (
          <ul className="space-y-1.5">
            {stage.branches.map((branch) => (
              <li key={branch.to} className="flex items-center gap-2">
                <span className="text-xs text-slate-400 capitalize w-20 flex-shrink-0">
                  {branch.to}
                </span>
                <div className="flex-1 h-1.5 rounded-full bg-slate-800 overflow-hidden">
                  <div
                    className={`h-full rounded-full ${BRANCH_COLOR[branch.to] ?? 'bg-slate-500'}`}
                    style={{ width: `${(branch.count / maxCount) * 100}%` }}
                  />
                </div>
                <span className="text-xs font-mono text-slate-300 w-6 text-right">
                  {branch.count}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div>
        <p className="text-xs font-semibold uppercase tracking-wider text-slate-500 mb-2">
          Currently in stage ({stage.in_flight})
        </p>
        {isLoading ? (
          <div className="space-y-1.5">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        ) : !data || data.items.length === 0 ? (
          <p className="text-xs text-slate-600 italic">No incidents currently in this stage.</p>
        ) : (
          <ul className="space-y-1.5">
            {data.items.map((incident) => (
              <li key={incident.id}>
                <button
                  type="button"
                  onClick={() => onSelectIncident(incident.id)}
                  aria-label={`Open incident ${incident.id}`}
                  className="w-full text-left bg-slate-800/60 hover:bg-slate-800 border border-slate-700 rounded px-2.5 py-1.5 transition-colors cursor-pointer"
                >
                  <div className="flex items-center gap-2 flex-wrap">
                    <SeverityBadge severity={incident.severity} />
                    <StatusBadge status={incident.status} />
                    <span className="text-[11px] text-slate-500 ml-auto">
                      {timeAgo(incident.updated_at)}
                    </span>
                  </div>
                  {incident.summary && (
                    <p className="text-xs text-slate-300 mt-1 truncate">{incident.summary}</p>
                  )}
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}
