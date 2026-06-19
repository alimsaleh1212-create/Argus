import { useState } from 'react'
import { ShieldAlert, AlertTriangle } from 'lucide-react'
import { usePendingApprovals, useApprovalDecision } from '@/api/approvals'
import { useIncidentQueue, useAcknowledgeIncident, useResolveIncident } from '@/api/incidents'
import type { ApprovalSummary } from '@/api/approvals'
import type { IncidentSummary } from '@/api/incidents'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { SeverityBadge } from '@/components/SeverityBadge'
import { JourneyTrace } from '@/components/JourneyTrace'
import { ConfirmDialog } from '@/components/ConfirmDialog'
import { DeadlineCountdown } from '@/features/approvals/DeadlineCountdown'
import { DecisionDialog } from '@/features/approvals/DecisionDialog'

interface HumanAttentionLaneProps {
  onSelectIncident: (incidentId: string) => void
}

function AwaitingCard({ approval, onOpen }: { approval: ApprovalSummary; onOpen: () => void }) {
  const [pendingDecision, setPendingDecision] = useState<'approve' | 'reject' | null>(null)
  const [decided, setDecided] = useState(false)
  const { mutate, isPending } = useApprovalDecision(approval.id)

  function handleConfirm() {
    if (!pendingDecision) return
    mutate(
      { decision: pendingDecision },
      { onSuccess: () => { setPendingDecision(null); setDecided(true) }, onError: () => setPendingDecision(null) }
    )
  }

  return (
    <>
      <Card className="border-amber-500/30 bg-amber-500/5" data-testid={`awaiting-card-${approval.incident_id}`}>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-amber-400 text-sm">
            <ShieldAlert className="w-4 h-4" aria-hidden="true" />
            Awaiting Approval
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <button
            type="button"
            onClick={onOpen}
            aria-label={`Open incident ${approval.incident_id}`}
            className="font-mono text-xs text-slate-400 hover:text-slate-200 transition-colors cursor-pointer truncate block w-full text-left"
          >
            {approval.incident_id}
          </button>
          <p className="text-slate-200 text-sm">{approval.rationale}</p>
          {approval.deadline_at && <DeadlineCountdown deadlineAt={approval.deadline_at} />}
          {decided ? (
            <p className="text-xs text-slate-500 italic">Decision recorded.</p>
          ) : (
            <div className="flex gap-3">
              <Button
                variant="default"
                size="sm"
                onClick={() => setPendingDecision('approve')}
                disabled={isPending}
                aria-label="Approve remediation"
                className="bg-cyan-500 hover:bg-cyan-400 text-slate-950"
              >
                Approve
              </Button>
              <Button
                variant="destructive"
                size="sm"
                onClick={() => setPendingDecision('reject')}
                disabled={isPending}
                aria-label="Reject remediation"
              >
                Reject
              </Button>
            </div>
          )}
        </CardContent>
      </Card>
      {pendingDecision && (
        <DecisionDialog
          open={!!pendingDecision}
          onOpenChange={(open) => { if (!open) setPendingDecision(null) }}
          decision={pendingDecision}
          onConfirm={handleConfirm}
          isLoading={isPending}
        />
      )}
    </>
  )
}

function EscalatedCard({ incident, onOpen }: { incident: IncidentSummary; onOpen: () => void }) {
  const [pendingAction, setPendingAction] = useState<'acknowledge' | 'resolve' | null>(null)
  const { mutate: acknowledge, isPending: isAcknowledging } = useAcknowledgeIncident()
  const { mutate: resolve, isPending: isResolving } = useResolveIncident()
  const isLoading = isAcknowledging || isResolving

  function handleConfirm() {
    if (pendingAction === 'acknowledge') {
      acknowledge(incident.id, { onSuccess: () => setPendingAction(null), onError: () => setPendingAction(null) })
    } else if (pendingAction === 'resolve') {
      resolve(incident.id, { onSuccess: () => setPendingAction(null), onError: () => setPendingAction(null) })
    }
  }

  return (
    <>
      <Card className="border-orange-500/30 bg-orange-500/5" data-testid={`escalated-card-${incident.id}`}>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-orange-400 text-sm">
            <AlertTriangle className="w-4 h-4" aria-hidden="true" />
            Escalated
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          <div className="flex items-center gap-2 flex-wrap">
            <SeverityBadge severity={incident.severity} />
            <span className="font-mono text-[11px] text-slate-500 truncate">{incident.id}</span>
          </div>
          {incident.summary && <p className="text-slate-200 text-sm">{incident.summary}</p>}
          <JourneyTrace steps={incident.journey ?? []} />
          <div className="flex gap-3 flex-wrap">
            <Button variant="outline" size="sm" onClick={onOpen} aria-label="View detail">
              View detail
            </Button>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => setPendingAction('acknowledge')}
              disabled={isLoading}
              aria-label="Acknowledge incident"
            >
              Acknowledge
            </Button>
            <Button
              variant="default"
              size="sm"
              onClick={() => setPendingAction('resolve')}
              disabled={isLoading}
              aria-label="Resolve incident"
              className="bg-cyan-500 hover:bg-cyan-400 text-slate-950"
            >
              Resolve
            </Button>
          </div>
        </CardContent>
      </Card>
      {pendingAction && (
        <ConfirmDialog
          open={!!pendingAction}
          onOpenChange={(open) => { if (!open) setPendingAction(null) }}
          title={pendingAction === 'acknowledge' ? 'Acknowledge Incident' : 'Resolve Incident'}
          description={
            pendingAction === 'acknowledge'
              ? 'This incident will be marked as acknowledged and removed from the human attention lane.'
              : 'This incident will be marked as resolved. This cannot be undone.'
          }
          confirmLabel={pendingAction === 'acknowledge' ? 'Acknowledge' : 'Resolve'}
          onConfirm={handleConfirm}
          isLoading={isLoading}
        />
      )}
    </>
  )
}

export function HumanAttentionLane({ onSelectIncident }: HumanAttentionLaneProps) {
  const { data: pending } = usePendingApprovals()
  const { data: escalatedPage } = useIncidentQueue({
    view: 'all',
    status: ['escalated'],
    sort: '-updated_at',
    limit: 20,
  })

  const awaiting = pending?.approvals ?? []
  const escalated = (escalatedPage?.items ?? []).filter((i) => !i.acknowledged_at)
  const isEmpty = awaiting.length === 0 && escalated.length === 0

  return (
    <div className="space-y-3" data-testid="human-attention-lane">
      <h2 className="text-sm font-semibold uppercase tracking-wider text-slate-400">
        Human Attention
      </h2>
      {isEmpty ? (
        <p className="text-sm text-slate-600 italic py-6">Nothing needs your attention right now.</p>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
          {awaiting.map((approval) => (
            <AwaitingCard
              key={approval.id}
              approval={approval}
              onOpen={() => onSelectIncident(approval.incident_id)}
            />
          ))}
          {escalated.map((incident) => (
            <EscalatedCard
              key={incident.id}
              incident={incident}
              onOpen={() => onSelectIncident(incident.id)}
            />
          ))}
        </div>
      )}
    </div>
  )
}
