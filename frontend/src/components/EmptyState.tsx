import { Inbox } from 'lucide-react'

interface EmptyStateProps {
  title?: string
  description?: string
}

export function EmptyState({
  title = 'No incidents',
  description = 'Nothing to show here.',
}: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center py-16 text-slate-500 gap-3">
      <Inbox className="w-10 h-10 opacity-40" aria-hidden="true" />
      <p className="text-base font-medium">{title}</p>
      {description && <p className="text-sm">{description}</p>}
    </div>
  )
}
