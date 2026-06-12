import { Badge } from '@/components/ui/badge'

const STATUS_CONFIG: Record<string, { label: string; variant: 'warning' | 'success' | 'orange' | 'danger' | 'sky' | 'muted' }> = {
  awaiting_approval: { label: 'Awaiting Approval', variant: 'warning' },
  resolved: { label: 'Resolved', variant: 'success' },
  escalated: { label: 'Escalated', variant: 'orange' },
  rejected_by_human: { label: 'Rejected', variant: 'danger' },
  approval_expired: { label: 'Expired', variant: 'muted' },
  auto_remediated: { label: 'Auto-remediated', variant: 'success' },
  remediated: { label: 'Remediated', variant: 'success' },
  escalated_response: { label: 'Escalated', variant: 'orange' },
  triaging: { label: 'Triaging', variant: 'sky' },
  enriching: { label: 'Enriching', variant: 'sky' },
  responding: { label: 'Responding', variant: 'sky' },
  grounded: { label: 'Grounded', variant: 'sky' },
  received: { label: 'Received', variant: 'muted' },
  grounding: { label: 'Grounding', variant: 'muted' },
  failed: { label: 'Failed', variant: 'danger' },
}

interface StatusBadgeProps {
  status: string
}

export function StatusBadge({ status }: StatusBadgeProps) {
  const config = STATUS_CONFIG[status] ?? { label: status, variant: 'muted' as const }
  return <Badge variant={config.variant}>{config.label}</Badge>
}
