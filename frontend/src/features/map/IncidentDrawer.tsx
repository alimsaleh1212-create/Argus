import { Link } from 'react-router-dom'
import { ExternalLink, GitBranch } from 'lucide-react'
import { useIncidentDetail } from '@/api/incidents'
import { SeverityBadge } from '@/components/SeverityBadge'
import { StatusBadge } from '@/components/StatusBadge'
import { ErrorState } from '@/components/ErrorState'
import { Skeleton } from '@/components/ui/skeleton'
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetDescription } from '@/components/ui/sheet'
import { ApprovalPanel } from '@/features/approvals/ApprovalPanel'
import { EvidencePanel } from './EvidencePanel'
import { AuditTrail } from './AuditTrail'

interface IncidentDrawerProps {
  incidentId: string | null
  onClose: () => void
}

export function IncidentDrawer({ incidentId, onClose }: IncidentDrawerProps) {
  const { data, isLoading, isError, error } = useIncidentDetail(incidentId ?? undefined) ?? {}

  return (
    <Sheet open={!!incidentId} onOpenChange={(open) => { if (!open) onClose() }}>
      <SheetContent>
        {isLoading && (
          <div className="space-y-4">
            <Skeleton className="h-8 w-48" />
            <Skeleton className="h-32 w-full" />
          </div>
        )}

        {isError && (
          <ErrorState
            message={`Failed to load incident: ${(error as Error)?.message ?? 'unknown error'}`}
          />
        )}

        {data && (
          <div className="space-y-5">
            <SheetHeader>
              <div className="flex items-center gap-2 flex-wrap">
                <SeverityBadge severity={data.severity} />
                <StatusBadge status={data.status} />
              </div>
              <SheetTitle className="font-mono text-sm break-all">{data.id}</SheetTitle>
              <SheetDescription>
                Source: {data.source} · Updated: {new Date(data.updated_at).toLocaleString()}
              </SheetDescription>
            </SheetHeader>

            {data.pending_approval && <ApprovalPanel approval={data.pending_approval} />}

            <EvidencePanel evidence={data.evidence} />

            {data.correlation_id && (
              <Link
                to={`/incidents/${data.id}/trace`}
                className="inline-flex items-center gap-1.5 text-sm text-cyan-400 hover:text-cyan-300 transition-colors cursor-pointer"
              >
                <GitBranch className="w-4 h-4" aria-hidden="true" />
                View pipeline trace
              </Link>
            )}

            <AuditTrail audit={data.audit} />

            <Link
              to={`/incidents/${data.id}`}
              className="inline-flex items-center gap-1.5 text-sm text-slate-400 hover:text-slate-200 transition-colors cursor-pointer"
            >
              <ExternalLink className="w-4 h-4" aria-hidden="true" />
              Open full incident ↗
            </Link>
          </div>
        )}
      </SheetContent>
    </Sheet>
  )
}
