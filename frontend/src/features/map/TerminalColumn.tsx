import { CheckCircle2, AlertTriangle, Clock, ArrowRight } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { TerminalCounts } from '@/api/pipeline'

interface TerminalColumnProps {
  terminals: TerminalCounts
  changedKeys: Set<keyof TerminalCounts>
  /** When provided, each block becomes a button routing to its destination. */
  onSelect?: (key: keyof TerminalCounts) => void
}

interface Tile {
  key: keyof TerminalCounts
  label: string
  hint: string
  icon: typeof CheckCircle2
  iconColor: string
  countColor: string
  restBorder: string
  flashBorder: string
  /** Pulse class applied while the block has a non-zero count (needs attention). */
  pulse?: string
}

const TILES: Tile[] = [
  {
    key: 'resolved',
    label: 'Resolved',
    hint: 'View resolved queue',
    icon: CheckCircle2,
    iconColor: 'text-cyan-400',
    countColor: 'text-cyan-300',
    restBorder: 'border-cyan-500/25',
    flashBorder: 'border-cyan-400',
  },
  {
    key: 'escalated',
    label: 'Escalated',
    hint: 'Open human attention',
    icon: AlertTriangle,
    iconColor: 'text-orange-400',
    countColor: 'text-orange-300',
    restBorder: 'border-orange-500/30',
    flashBorder: 'border-orange-500',
    pulse: 'argus-pulse-orange',
  },
  {
    key: 'awaiting',
    label: 'Awaiting',
    hint: 'Approve / reject responses',
    icon: Clock,
    iconColor: 'text-amber-400',
    countColor: 'text-amber-300',
    restBorder: 'border-amber-500/30',
    flashBorder: 'border-amber-500',
    pulse: 'argus-pulse-amber',
  },
]

export function TerminalColumn({ terminals, changedKeys, onSelect }: TerminalColumnProps) {
  return (
    <div className="flex flex-col gap-3 w-full" data-testid="terminal-column">
      <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-500 px-1">
        Outcomes
      </p>
      {TILES.map((tile) => {
        const { key, label, hint, icon: Icon, iconColor, countColor, restBorder, flashBorder, pulse } = tile
        const count = terminals[key]
        const interactive = !!onSelect
        const needsAttention = count > 0 && !!pulse

        return (
          <button
            key={key}
            type="button"
            disabled={!interactive}
            onClick={interactive ? () => onSelect(key) : undefined}
            aria-label={interactive ? `${label}: ${count}. ${hint}` : undefined}
            className={cn(
              'group relative w-full overflow-hidden rounded-xl border px-5 py-4 text-left',
              'bg-gradient-to-br from-slate-900 to-slate-950',
              'transition-[border-color,box-shadow,transform] duration-300 ease-out',
              restBorder,
              needsAttention && pulse,
              changedKeys.has(key) && flashBorder,
              interactive &&
                'cursor-pointer hover:-translate-y-0.5 hover:border-opacity-100 focus-visible:-translate-y-0.5'
            )}
            data-testid={`terminal-${key}`}
          >
            <div className="flex items-center gap-3">
              <span
                className={cn(
                  'flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-lg bg-slate-800/70',
                  iconColor
                )}
              >
                <Icon className="h-5 w-5" aria-hidden="true" />
              </span>
              <div className="flex flex-col">
                <span className="text-sm font-semibold text-slate-100">{label}</span>
                {interactive && (
                  <span className="flex items-center gap-1 text-[11px] text-slate-500 group-hover:text-slate-300 transition-colors">
                    {hint}
                    <ArrowRight className="h-3 w-3" aria-hidden="true" />
                  </span>
                )}
              </div>
              <span className={cn('ml-auto text-4xl font-mono font-bold tabular-nums', countColor)}>
                {count}
              </span>
            </div>
          </button>
        )
      })}
    </div>
  )
}
