import { useEffect, useState } from 'react'
import { Clock } from 'lucide-react'

interface DeadlineCountdownProps {
  deadlineAt: string | null
}

function formatDuration(ms: number): string {
  if (ms <= 0) return 'Expired'
  const totalSecs = Math.floor(ms / 1000)
  const h = Math.floor(totalSecs / 3600)
  const m = Math.floor((totalSecs % 3600) / 60)
  const s = totalSecs % 60
  if (h > 0) return `${h}h ${m}m ${s}s`
  if (m > 0) return `${m}m ${s}s`
  return `${s}s`
}

export function DeadlineCountdown({ deadlineAt }: DeadlineCountdownProps) {
  const [remaining, setRemaining] = useState<number>(() => {
    if (!deadlineAt) return 0
    return new Date(deadlineAt).getTime() - Date.now()
  })

  useEffect(() => {
    if (!deadlineAt) return
    const update = () => {
      setRemaining(new Date(deadlineAt).getTime() - Date.now())
    }
    update()
    const id = setInterval(update, 1000)
    return () => clearInterval(id)
  }, [deadlineAt])

  if (!deadlineAt) return null

  const expired = remaining <= 0
  const urgent = remaining > 0 && remaining < 5 * 60 * 1000 // < 5 minutes

  return (
    <div
      className={`flex items-center gap-1.5 text-sm font-mono ${
        expired ? 'text-red-400' : urgent ? 'text-amber-400' : 'text-slate-300'
      }`}
      aria-live="polite"
      aria-label={`Deadline: ${expired ? 'expired' : formatDuration(remaining)}`}
    >
      <Clock className="w-4 h-4 flex-shrink-0" aria-hidden="true" />
      <span data-testid="deadline-countdown">
        {expired ? 'Expired' : formatDuration(remaining)}
      </span>
    </div>
  )
}
