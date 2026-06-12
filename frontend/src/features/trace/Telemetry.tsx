import type { TelemetryView } from '@/api/trace'

interface TelemetryProps {
  telemetry: TelemetryView
}

function MetricCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg bg-slate-800 px-4 py-3 flex flex-col gap-1">
      <span className="text-xs text-slate-400 uppercase tracking-wide">{label}</span>
      <span className="font-mono text-sm text-slate-100">{value}</span>
    </div>
  )
}

function formatTokens(n: number | null): string {
  return n === null ? 'unknown' : String(n)
}

function formatMs(n: number | null): string {
  if (n === null) return '—'
  if (n >= 1000) return `${(n / 1000).toFixed(2)}s`
  return `${n}ms`
}

export function Telemetry({ telemetry }: TelemetryProps) {
  return (
    <section aria-label="Pipeline telemetry" data-testid="telemetry-panel">
      <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-400 mb-3">
        Pipeline Telemetry
      </h3>
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
        <MetricCard label="Steps" value={String(telemetry.step_count)} />
        <MetricCard label="Error steps" value={String(telemetry.error_steps)} />
        <MetricCard label="End-to-end" value={formatMs(telemetry.end_to_end_ms)} />
        <MetricCard label="Tokens in" value={formatTokens(telemetry.total_tokens_in)} />
        <MetricCard label="Tokens out" value={formatTokens(telemetry.total_tokens_out)} />
      </div>
    </section>
  )
}
