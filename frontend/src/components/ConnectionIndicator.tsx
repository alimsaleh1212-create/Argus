import { Wifi, WifiOff } from 'lucide-react'
import { cn } from '@/lib/utils'

type ConnectionState = 'connected' | 'reconnecting' | 'disconnected'

interface ConnectionIndicatorProps {
  state: ConnectionState
  className?: string
}

export function ConnectionIndicator({ state, className }: ConnectionIndicatorProps) {
  const config = {
    connected: {
      icon: Wifi,
      label: 'Live',
      color: 'text-cyan-400',
      dot: 'bg-cyan-400',
    },
    reconnecting: {
      icon: WifiOff,
      label: 'Reconnecting…',
      color: 'text-amber-400',
      dot: 'bg-amber-400',
    },
    disconnected: {
      icon: WifiOff,
      label: 'Offline',
      color: 'text-slate-500',
      dot: 'bg-slate-500',
    },
  }[state]

  const Icon = config.icon

  return (
    <div
      className={cn('flex items-center gap-1.5 text-xs', config.color, className)}
      aria-label={`Connection status: ${config.label}`}
    >
      <span className={cn('w-1.5 h-1.5 rounded-full', config.dot)} />
      <Icon className="w-3.5 h-3.5" aria-hidden="true" />
      <span>{config.label}</span>
    </div>
  )
}
