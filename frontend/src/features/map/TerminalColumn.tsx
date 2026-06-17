import { CheckCircle2, AlertTriangle, Clock } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { TerminalCounts } from '@/api/pipeline'

interface TerminalColumnProps {
  terminals: TerminalCounts
  changedKeys: Set<keyof TerminalCounts>
}

const TILES: {
  key: keyof TerminalCounts
  label: string
  icon: typeof CheckCircle2
  iconColor: string
  flashBorder: string
}[] = [
  { key: 'resolved', label: 'Resolved', icon: CheckCircle2, iconColor: 'text-green-400', flashBorder: 'border-green-500' },
  { key: 'escalated', label: 'Escalated', icon: AlertTriangle, iconColor: 'text-orange-400', flashBorder: 'border-orange-500' },
  { key: 'awaiting', label: 'Awaiting', icon: Clock, iconColor: 'text-amber-400', flashBorder: 'border-amber-500' },
]

export function TerminalColumn({ terminals, changedKeys }: TerminalColumnProps) {
  return (
    <div className="flex flex-col gap-2" data-testid="terminal-column">
      {TILES.map(({ key, label, icon: Icon, iconColor, flashBorder }) => (
        <div
          key={key}
          className={cn(
            'rounded-lg bg-slate-900 border border-slate-700 px-3 py-2 flex items-center gap-2',
            'transition-colors duration-300 ease-out',
            changedKeys.has(key) && flashBorder
          )}
          data-testid={`terminal-${key}`}
        >
          <Icon className={cn('w-4 h-4', iconColor)} aria-hidden="true" />
          <span className="text-xs text-slate-400">{label}</span>
          <span className="ml-auto text-base font-mono font-bold text-slate-100">
            {terminals[key]}
          </span>
        </div>
      ))}
    </div>
  )
}
