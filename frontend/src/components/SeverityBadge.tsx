import { Badge } from '@/components/ui/badge'

const SEV_CONFIG: Record<string, { label: string; variant: 'sky' | 'warning' | 'orange' | 'danger' }> = {
  low: { label: 'Low', variant: 'sky' },
  medium: { label: 'Medium', variant: 'warning' },
  high: { label: 'High', variant: 'orange' },
  critical: { label: 'Critical', variant: 'danger' },
}

interface SeverityBadgeProps {
  severity: string
}

export function SeverityBadge({ severity }: SeverityBadgeProps) {
  const config = SEV_CONFIG[severity] ?? { label: severity, variant: 'muted' as const }
  return <Badge variant={config.variant as 'sky' | 'warning' | 'orange' | 'danger'}>{config.label}</Badge>
}
