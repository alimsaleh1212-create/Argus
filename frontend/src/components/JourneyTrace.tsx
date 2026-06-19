import type { JourneyStep } from '@/api/incidents'

const OUTCOME_CLASS: Record<string, string> = {
  advance: 'bg-cyan-500/10 text-cyan-300 border-cyan-500/30',
  resolved: 'bg-emerald-500/10 text-emerald-300 border-emerald-500/30',
  escalated: 'bg-amber-500/10 text-amber-300 border-amber-500/30',
  errored: 'bg-red-500/10 text-red-300 border-red-500/30',
}

export function JourneyTrace({ steps }: { steps: JourneyStep[] }) {
  if (!steps || steps.length === 0) return null
  return (
    <div className="flex items-center gap-1 flex-wrap" data-testid="journey-trace">
      {steps.map((step, i) => (
        <div key={`${step.stage}-${i}`} className="flex items-center gap-1">
          <span
            data-testid={`journey-step-${step.stage}`}
            className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[10px] font-medium ${
              OUTCOME_CLASS[step.outcome] ?? 'bg-slate-700/40 text-slate-300 border-slate-600/40'
            }`}
            title={step.detail ?? undefined}
          >
            {step.label}
            {step.detail && step.stage !== 'terminal' && (
              <span className="opacity-70">· {step.detail}</span>
            )}
            {typeof step.score === 'number' && (
              <span className="font-mono opacity-80">{step.score.toFixed(2)}</span>
            )}
          </span>
          {i < steps.length - 1 && <span className="text-slate-600 text-[10px]">→</span>}
        </div>
      ))}
    </div>
  )
}
