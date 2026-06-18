import { useParams, Link } from 'react-router-dom'
import { ArrowLeft, GitBranch } from 'lucide-react'
import { useIncidentDetail } from '@/api/incidents'
import { StatusBadge } from '@/components/StatusBadge'
import { SeverityBadge } from '@/components/SeverityBadge'
import { ErrorState } from '@/components/ErrorState'
import { Skeleton } from '@/components/ui/skeleton'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { ApprovalPanel } from '@/features/approvals/ApprovalPanel'
import { EvidencePanel } from '@/features/map/EvidencePanel'
import type { AuditView } from '@/api/incidents'

function AuditTrail({ audit }: { audit: AuditView[] }) {
  return (
    <Card className="bg-[#0F172A] border-slate-800">
      <CardHeader>
        <CardTitle className="text-slate-300 text-sm">
          Audit Trail ({audit.length})
        </CardTitle>
      </CardHeader>
      <CardContent>
        {audit.length === 0 ? (
          <p className="text-slate-600 text-sm italic">No audit entries yet.</p>
        ) : (
          <ol className="relative border-l border-slate-800 ml-2 space-y-4">
            {audit.map((row, i) => (
              <li key={i} className="ml-4">
                <div className="absolute -left-[5px] mt-1 w-2.5 h-2.5 rounded-full bg-slate-700 border border-slate-600" />
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="font-mono text-xs text-cyan-400">{row.action}</span>
                  <span className="text-xs text-slate-500">by</span>
                  <span className="font-mono text-xs text-slate-300">{row.actor}</span>
                  {row.target && (
                    <>
                      <span className="text-xs text-slate-500">→</span>
                      <span className="font-mono text-xs text-slate-400 truncate max-w-[200px]">{row.target}</span>
                    </>
                  )}
                  <span
                    className={`ml-auto text-[10px] font-semibold uppercase px-1.5 py-0.5 rounded ${
                      row.outcome === 'applied'
                        ? 'bg-cyan-400/10 text-cyan-400'
                        : row.outcome === 'skipped'
                        ? 'bg-slate-700 text-slate-400'
                        : 'bg-red-500/10 text-red-400'
                    }`}
                  >
                    {row.outcome}
                  </span>
                </div>
                <time className="text-[11px] text-slate-600">
                  {new Date(row.created_at).toLocaleString()}
                </time>
              </li>
            ))}
          </ol>
        )}
      </CardContent>
    </Card>
  )
}


export function IncidentDetail() {
  const { id } = useParams<{ id: string }>()
  const { data, isLoading, isError, error } = useIncidentDetail(id)

  if (isLoading) {
    return (
      <div className="space-y-4 p-6">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-48 w-full" />
        <Skeleton className="h-32 w-full" />
      </div>
    )
  }

  if (isError) {
    return (
      <div className="p-6">
        <ErrorState
          message={`Failed to load incident: ${(error as Error)?.message ?? 'unknown error'}`}
        />
      </div>
    )
  }

  if (!data) return null

  return (
    <div className="space-y-5 max-w-4xl p-6">
      {/* Back link */}
      <Link
        to="/queue"
        className="inline-flex items-center gap-1.5 text-sm text-slate-400 hover:text-slate-200 transition-colors cursor-pointer"
      >
        <ArrowLeft className="w-4 h-4" aria-hidden="true" />
        Back to Queue
      </Link>

      {/* Header */}
      <div className="space-y-2">
        <div className="flex items-center gap-3 flex-wrap">
          <SeverityBadge severity={data.severity} />
          <StatusBadge status={data.status} />
          {data.disposition && (
            <span className="font-mono text-xs text-slate-400 bg-slate-800 px-2 py-1 rounded">
              {data.disposition}
            </span>
          )}
        </div>
        <h1 className="text-lg font-semibold text-slate-100 font-mono break-all">
          {data.id}
        </h1>
        <div className="flex items-center gap-4 text-xs text-slate-500">
          <span>Source: {data.source}</span>
          {data.correlation_id && (
            <span className="flex items-center gap-1">
              <GitBranch className="w-3 h-3" aria-hidden="true" />
              {data.correlation_id}
            </span>
          )}
          <span>Updated: {new Date(data.updated_at).toLocaleString()}</span>
        </div>
      </div>

      {/* Pending approval panel */}
      {data.pending_approval && <ApprovalPanel approval={data.pending_approval} />}

      {/* Evidence */}
      <EvidencePanel evidence={data.evidence} />

      {/* Trace link */}
      {data.correlation_id && (
        <Link
          to={`/incidents/${data.id}/trace`}
          className="inline-flex items-center gap-1.5 text-sm text-cyan-400 hover:text-cyan-300 transition-colors cursor-pointer"
        >
          <GitBranch className="w-4 h-4" aria-hidden="true" />
          View pipeline trace
        </Link>
      )}

      {/* Audit trail */}
      <AuditTrail audit={data.audit} />
    </div>
  )
}
