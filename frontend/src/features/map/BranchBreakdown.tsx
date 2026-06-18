import { SeverityBadge } from '@/components/SeverityBadge'
import { StatusBadge } from '@/components/StatusBadge'
import type { StageIncident, StageNode } from '@/api/pipeline'

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

const VERDICT_TONE: Record<string, string> = {
  real: 'text-rose-400 bg-rose-400/10',
  noise: 'text-cyan-400 bg-cyan-400/10',
  uncertain: 'text-amber-400 bg-amber-400/10',
}
const ASSESSMENT_TONE: Record<string, string> = {
  confirmed: 'text-rose-400 bg-rose-400/10',
  benign: 'text-cyan-400 bg-cyan-400/10',
  inconclusive: 'text-amber-400 bg-amber-400/10',
}

function pct(n: number | null): string {
  if (n === null) return ''
  return `${Math.round(n * 100)}%`
}

function ScoreChips({ incident }: { incident: StageIncident }) {
  const chips: { label: string; value: string; tone: string }[] = []
  if (incident.triage_verdict) {
    chips.push({
      label: 'triage',
      value: `${incident.triage_verdict}${incident.triage_confidence !== null ? ` · ${pct(incident.triage_confidence)}` : ''}`,
      tone: VERDICT_TONE[incident.triage_verdict] ?? 'text-slate-300 bg-slate-700/40',
    })
  }
  if (incident.enrichment_assessment) {
    chips.push({
      label: 'enrich',
      value: `${incident.enrichment_assessment}${incident.enrichment_confidence !== null ? ` · ${pct(incident.enrichment_confidence)}` : ''}`,
      tone: ASSESSMENT_TONE[incident.enrichment_assessment] ?? 'text-slate-300 bg-slate-700/40',
    })
  }
  if (incident.response_plan_id) {
    chips.push({
      label: 'response',
      value: incident.response_verdict
        ? `${incident.response_plan_id} · ${incident.response_verdict}`
        : incident.response_plan_id,
      tone: 'text-sky-400 bg-sky-400/10',
    })
  }
  if (chips.length === 0) return null
  return (
    <div className="flex flex-wrap gap-1 mt-1.5">
      {chips.map((c) => (
        <span
          key={c.label}
          className={`text-[10px] font-mono px-1.5 py-0.5 rounded ${c.tone}`}
        >
          <span className="opacity-60">{c.label}</span> {c.value}
        </span>
      ))}
    </div>
  )
}

export function BranchBreakdown({ stage, onSelectIncident }: BranchBreakdownProps) {
  const maxCount = Math.max(1, ...stage.branches.map((b) => b.count))
  const incidents = stage.incidents

  return (
    <div
      className="rounded-lg bg-slate-900/60 border border-slate-800 p-4 mt-2 w-full sm:w-[360px] space-y-4"
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
          In stage ({stage.in_flight})
        </p>
        {incidents.length === 0 ? (
          <p className="text-xs text-slate-600 italic">No incidents currently in this stage.</p>
        ) : (
          <ul className="space-y-1.5">
            {incidents.map((incident) => (
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
                    <span className="text-[10px] font-mono text-slate-500 truncate">
                      {incident.source}
                    </span>
                    <span className="text-[11px] text-slate-500 ml-auto">
                      {timeAgo(incident.updated_at)}
                    </span>
                  </div>
                  {incident.summary && (
                    <p className="text-xs text-slate-300 mt-1 truncate">{incident.summary}</p>
                  )}
                  <ScoreChips incident={incident} />
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}
