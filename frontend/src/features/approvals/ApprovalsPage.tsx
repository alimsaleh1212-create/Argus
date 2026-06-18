import { Link } from 'react-router-dom'
import { ClipboardCheck, ExternalLink } from 'lucide-react'
import { usePendingApprovals, type ApprovalSummary } from '@/api/approvals'
import type { ApprovalView } from '@/api/incidents'
import { ApprovalPanel } from './ApprovalPanel'
import { Skeleton } from '@/components/ui/skeleton'
import { ErrorState } from '@/components/ErrorState'
import { EmptyState } from '@/components/EmptyState'

/** Bridge the list summary to the richer view the panel renders. */
function toApprovalView(summary: ApprovalSummary): ApprovalView {
  const notExpired = !summary.deadline_at || new Date(summary.deadline_at).getTime() > Date.now()
  return { ...summary, is_actionable: summary.status === 'pending' && notExpired }
}

export function ApprovalsPage() {
  const { data, isLoading, error, refetch } = usePendingApprovals()
  const approvals = data?.approvals ?? []

  return (
    <div className="px-6 py-5" data-testid="approvals-page">
      <div className="mb-5 flex items-center gap-3">
        <span className="flex h-10 w-10 items-center justify-center rounded-lg bg-amber-500/10 text-amber-400">
          <ClipboardCheck className="h-5 w-5" aria-hidden="true" />
        </span>
        <div>
          <h1 className="text-xl font-bold tracking-tight text-slate-50">Awaiting Approval</h1>
          <p className="text-xs text-slate-500 mt-0.5">
            Destructive remediations paused for a human decision. Approve to execute, reject to hold.
          </p>
        </div>
        {approvals.length > 0 && (
          <span className="ml-auto rounded-full bg-amber-500/10 px-3 py-1 text-sm font-mono font-semibold text-amber-300 ring-1 ring-inset ring-amber-500/30">
            {approvals.length} pending
          </span>
        )}
      </div>

      {isLoading ? (
        <div className="space-y-3" aria-label="Loading pending approvals">
          <Skeleton className="h-40 w-full max-w-2xl" />
          <Skeleton className="h-40 w-full max-w-2xl" />
        </div>
      ) : error ? (
        <ErrorState
          message={`Failed to load approvals: ${(error as Error)?.message ?? 'unknown error'}`}
        />
      ) : approvals.length === 0 ? (
        <EmptyState
          title="Nothing awaiting approval"
          description="No remediations are currently paused for a human decision."
        />
      ) : (
        <div className="grid gap-4 lg:grid-cols-2 2xl:grid-cols-3">
          {approvals.map((summary) => (
            <div key={summary.id} className="flex flex-col gap-2">
              <ApprovalPanel approval={toApprovalView(summary)} onDecided={() => refetch()} />
              <Link
                to={`/incidents/${summary.incident_id}`}
                className="inline-flex w-fit items-center gap-1 text-xs text-slate-500 hover:text-sky-400 transition-colors px-1"
              >
                Incident {summary.incident_id.slice(0, 8)} · view full context
                <ExternalLink className="h-3 w-3" aria-hidden="true" />
              </Link>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
