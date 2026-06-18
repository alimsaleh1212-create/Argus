import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { AlertTriangle, CheckCircle2 } from 'lucide-react'

interface DecisionDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  decision: 'approve' | 'reject'
  onConfirm: () => void
  isLoading: boolean
}

export function DecisionDialog({
  open,
  onOpenChange,
  decision,
  onConfirm,
  isLoading,
}: DecisionDialogProps) {
  const isApprove = decision === 'approve'

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="bg-[#0F172A] border-slate-700 text-slate-100">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            {isApprove ? (
              <CheckCircle2 className="w-5 h-5 text-cyan-400" aria-hidden="true" />
            ) : (
              <AlertTriangle className="w-5 h-5 text-red-400" aria-hidden="true" />
            )}
            {isApprove ? 'Approve Remediation' : 'Reject Remediation'}
          </DialogTitle>
          <DialogDescription className="text-slate-400">
            {isApprove
              ? 'The destructive actions will be executed immediately. This cannot be undone.'
              : 'The incident will be marked as rejected. No destructive actions will run.'}
          </DialogDescription>
        </DialogHeader>

        <div className="flex justify-end gap-3 mt-4">
          <Button
            variant="ghost"
            onClick={() => onOpenChange(false)}
            disabled={isLoading}
          >
            Cancel
          </Button>
          <Button
            variant={isApprove ? 'default' : 'destructive'}
            onClick={onConfirm}
            disabled={isLoading}
            aria-label={isApprove ? 'Confirm approve' : 'Confirm reject'}
          >
            {isLoading
              ? 'Processing…'
              : isApprove
              ? 'Approve'
              : 'Reject'}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
