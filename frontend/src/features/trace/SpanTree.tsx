import { useState } from 'react'
import { ChevronRight, ChevronDown, AlertCircle, CheckCircle, Clock } from 'lucide-react'
import type { SpanView } from '@/api/trace'

interface SpanTreeProps {
  root: SpanView
  children: Record<string, SpanView[]>
  onSelect: (span: SpanView) => void
  selectedId: string | null
}

interface SpanNodeProps {
  span: SpanView
  children: Record<string, SpanView[]>
  depth: number
  onSelect: (span: SpanView) => void
  selectedId: string | null
}

function StatusIcon({ status }: { status: string }) {
  if (status === 'error') {
    return <AlertCircle className="w-3.5 h-3.5 text-red-400 shrink-0" aria-label="Error" />
  }
  if (status === 'ok') {
    return <CheckCircle className="w-3.5 h-3.5 text-green-500 shrink-0" aria-label="OK" />
  }
  return <Clock className="w-3.5 h-3.5 text-slate-400 shrink-0" aria-label="Pending" />
}

function formatLatency(ms: number | null): string {
  if (ms === null) return ''
  if (ms >= 1000) return `${(ms / 1000).toFixed(2)}s`
  return `${ms}ms`
}

function formatTokens(n: number | null): string {
  return n === null ? 'unknown' : String(n)
}

function SpanNode({ span, children, depth, onSelect, selectedId }: SpanNodeProps) {
  const [expanded, setExpanded] = useState(true)
  const nodeChildren = children[span.span_id] ?? []
  const hasChildren = nodeChildren.length > 0
  const isSelected = selectedId === span.span_id
  const isError = span.status === 'error'

  return (
    <li>
      <div
        className={[
          'flex items-center gap-2 py-1.5 px-2 rounded cursor-pointer transition-colors',
          'hover:bg-slate-800',
          isSelected ? 'bg-slate-800 ring-1 ring-green-500/50' : '',
          isError ? 'border-l-2 border-red-500 pl-1.5' : '',
        ].join(' ')}
        style={{ paddingLeft: `${depth * 1.25 + 0.5}rem` }}
        onClick={() => onSelect(span)}
        role="button"
        aria-selected={isSelected}
        aria-label={`Span: ${span.name}`}
        data-testid={`span-node-${span.span_id}`}
      >
        {hasChildren ? (
          <button
            onClick={(e) => {
              e.stopPropagation()
              setExpanded((v) => !v)
            }}
            className="text-slate-400 hover:text-slate-100 transition-colors cursor-pointer"
            aria-label={expanded ? 'Collapse' : 'Expand'}
          >
            {expanded ? (
              <ChevronDown className="w-3.5 h-3.5" />
            ) : (
              <ChevronRight className="w-3.5 h-3.5" />
            )}
          </button>
        ) : (
          <span className="w-3.5 h-3.5 shrink-0" />
        )}
        <StatusIcon status={span.status} />
        <span className="font-mono text-xs text-slate-200 flex-1 truncate">{span.name}</span>
        <span className="text-xs text-slate-400 shrink-0">{span.kind}</span>
        {span.latency_ms !== null && (
          <span className="text-xs text-slate-400 font-mono shrink-0">
            {formatLatency(span.latency_ms)}
          </span>
        )}
        {span.tokens_in !== undefined && (
          <span className="text-xs text-slate-500 font-mono shrink-0">
            {formatTokens(span.tokens_in)}/{formatTokens(span.tokens_out)}
          </span>
        )}
      </div>
      {hasChildren && expanded && (
        <ul role="group">
          {nodeChildren.map((child) => (
            <SpanNode
              key={child.span_id}
              span={child}
              children={children}
              depth={depth + 1}
              onSelect={onSelect}
              selectedId={selectedId}
            />
          ))}
        </ul>
      )}
    </li>
  )
}

export function SpanTree({ root, children, onSelect, selectedId }: SpanTreeProps) {
  return (
    <div
      className="rounded-lg bg-slate-900 border border-slate-700 p-3"
      aria-label="Span tree"
      data-testid="span-tree"
    >
      <ul role="tree" aria-label="Pipeline span tree">
        <SpanNode
          span={root}
          children={children}
          depth={0}
          onSelect={onSelect}
          selectedId={selectedId}
        />
      </ul>
    </div>
  )
}
