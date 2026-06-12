import { X } from 'lucide-react'
import type { SpanView } from '@/api/trace'

interface SpanDetailProps {
  span: SpanView
  onClose: () => void
}

function Field({ label, value }: { label: string; value: string | null }) {
  if (value === null || value === undefined) return null
  return (
    <div className="grid grid-cols-[6rem_1fr] gap-x-3 py-1.5 border-b border-slate-800 last:border-0">
      <dt className="text-xs text-slate-400 self-center">{label}</dt>
      <dd className="font-mono text-xs text-slate-200 break-all">{value}</dd>
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

export function SpanDetail({ span, onClose }: SpanDetailProps) {
  return (
    <aside
      className="rounded-lg bg-slate-900 border border-slate-700 p-4 space-y-1"
      aria-label={`Span detail: ${span.name}`}
      data-testid="span-detail"
    >
      <div className="flex items-center justify-between mb-3">
        <h4 className="text-sm font-semibold text-slate-100 font-mono">{span.name}</h4>
        <button
          onClick={onClose}
          aria-label="Close span detail"
          className="text-slate-400 hover:text-slate-100 transition-colors cursor-pointer"
        >
          <X className="w-4 h-4" />
        </button>
      </div>
      <dl>
        <Field label="Span ID" value={span.span_id} />
        <Field label="Parent" value={span.parent_span_id} />
        <Field label="Kind" value={span.kind} />
        <Field label="Status" value={span.status} />
        <Field label="Latency" value={formatMs(span.latency_ms)} />
        <Field label="Model" value={span.llm_model} />
        <Field label="Tokens in" value={formatTokens(span.tokens_in)} />
        <Field label="Tokens out" value={formatTokens(span.tokens_out)} />
        {span.error_message && (
          <div className="mt-2 rounded bg-red-950/50 border border-red-800 p-2">
            <p className="text-xs text-red-400 font-mono">{span.error_message}</p>
          </div>
        )}
        {Object.keys(span.attributes).length > 0 && (
          <div className="mt-2">
            <p className="text-xs text-slate-400 mb-1">Attributes</p>
            <pre className="text-xs font-mono text-slate-300 bg-slate-800 rounded p-2 overflow-auto max-h-40">
              {JSON.stringify(span.attributes, null, 2)}
            </pre>
          </div>
        )}
      </dl>
    </aside>
  )
}
