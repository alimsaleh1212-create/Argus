import { useState } from 'react'
import { ShieldAlert } from 'lucide-react'
import { useApprovalDecision } from '@/api/approvals'
import type { ApprovalView } from '@/api/incidents'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { DeadlineCountdown } from './DeadlineCountdown'
import { DecisionDialog } from './DecisionDialog'

interface ApprovalPanelProps {
  approval: ApprovalView
  onDecided?: () => void
}

export function ApprovalPanel({ approval, onDecided }: ApprovalPanelProps) {
  const [pendingDecision, setPendingDecision] = useState<'approve' | 'reject' | null>(null)
  const [alreadyDecided, setAlreadyDecided] = useState(false)

  const { mutate, isPending } = useApprovalDecision(approval.id)

  const isActionable = approval.is_actionable && !alreadyDecided

  function handleDecisionConfirm() {
    if (!pendingDecision) return

    mutate(
      { decision: pendingDecision },
      {
        onSuccess: () => {
          setPendingDecision(null)
          setAlreadyDecided(true)
          onDecided?.()
        },
        onError: (err) => {
          setPendingDecision(null)
          if (err.message.includes('409') || err.message.includes('already decided')) {
            setAlreadyDecided(true)
          }
        },
      }
    )
  }

  return (
    <>
      <Card
        className="border-amber-500/30 bg-amber-500/5"
        data-testid="approval-panel"
      >
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-amber-400 text-sm">
            <ShieldAlert className="w-4 h-4" aria-hidden="true" />
            Human Approval Required
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* Rationale */}
          <div>
            <p className="text-xs text-slate-500 uppercase tracking-wider mb-1">Rationale</p>
            <p className="text-slate-200 text-sm">{approval.rationale}</p>
          </div>

          {/* Pending actions */}
          <div>
            <p className="text-xs text-slate-500 uppercase tracking-wider mb-1.5">
              Pending Actions ({approval.pending_actions.length})
            </p>
            <ul className="space-y-2">
              {approval.pending_actions.map((action, i) => (
                <li
                  key={i}
                  className="bg-slate-900 border border-slate-700 rounded-md px-3.5 py-3"
                >
                  <dl className="grid grid-cols-[minmax(0,7rem)_1fr] gap-x-3 gap-y-1.5 font-mono text-xs">
                    {Object.entries(action).map(([key, value]) => (
                      <div key={key} className="contents">
                        <dt className="text-slate-500 break-all">{key}</dt>
                        <dd className="text-slate-200 break-all whitespace-pre-wrap">
                          {typeof value === 'object' && value !== null
                            ? JSON.stringify(value)
                            : String(value)}
                        </dd>
                      </div>
                    ))}
                  </dl>
                </li>
              ))}
            </ul>
          </div>

          {/* Deadline countdown */}
          {approval.deadline_at && (
            <div className="flex items-center gap-2">
              <span className="text-xs text-slate-500 uppercase tracking-wider">Deadline:</span>
              <DeadlineCountdown deadlineAt={approval.deadline_at} />
            </div>
          )}

          {/* Already decided banner */}
          {alreadyDecided && (
            <div
              className="bg-slate-800 border border-slate-700 rounded px-3 py-2 text-sm text-slate-400"
              role="alert"
              data-testid="already-decided-banner"
            >
              This approval has already been decided or has expired.
            </div>
          )}

          {/* Action buttons */}
          {isActionable && (
            <div className="flex gap-3 pt-1">
              <Button
                variant="default"
                onClick={() => setPendingDecision('approve')}
                disabled={isPending}
                aria-label="Approve remediation"
                className="bg-cyan-500 hover:bg-cyan-400 text-slate-950 min-h-[44px]"
              >
                Approve
              </Button>
              <Button
                variant="destructive"
                onClick={() => setPendingDecision('reject')}
                disabled={isPending}
                aria-label="Reject remediation"
                className="min-h-[44px]"
              >
                Reject
              </Button>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Confirmation dialog */}
      {pendingDecision && (
        <DecisionDialog
          open={!!pendingDecision}
          onOpenChange={(open) => { if (!open) setPendingDecision(null) }}
          decision={pendingDecision}
          onConfirm={handleDecisionConfirm}
          isLoading={isPending}
        />
      )}
    </>
  )
}
