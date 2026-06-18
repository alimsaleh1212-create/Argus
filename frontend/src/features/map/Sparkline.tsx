import { useId } from 'react'
import type { SeveritySlice } from './stageInsights'
import { SEVERITY_HEX } from './stageInsights'

interface SparklineProps {
  /** Series values; rendered as a word-sized line, no axes. */
  values: number[]
  /** Domain min/max for the y-scale. Defaults to 0..1 (confidence). */
  min?: number
  max?: number
  color?: string
  width?: number
  height?: number
  className?: string
  ariaLabel?: string
}

/**
 * A tiny, axis-less, word-sized line chart. Intended to sit inline inside a
 * stage card as a "scannable data point", never as a full chart.
 */
export function Sparkline({
  values,
  min = 0,
  max = 1,
  color = '#38BDF8',
  width = 72,
  height = 22,
  className,
  ariaLabel,
}: SparklineProps) {
  const gradId = useId()
  if (values.length === 0) return null

  const span = max - min || 1
  const pad = 2
  const innerH = height - pad * 2
  const stepX = values.length > 1 ? (width - pad * 2) / (values.length - 1) : 0

  const points = values.map((v, i) => {
    const clamped = Math.min(max, Math.max(min, v))
    const x = pad + i * stepX + (values.length === 1 ? (width - pad * 2) / 2 : 0)
    const y = pad + innerH - ((clamped - min) / span) * innerH
    return [x, y] as const
  })

  const line = points.map(([x, y], i) => `${i === 0 ? 'M' : 'L'}${x.toFixed(1)} ${y.toFixed(1)}`).join(' ')
  const area = `${line} L${points[points.length - 1][0].toFixed(1)} ${height - pad} L${points[0][0].toFixed(1)} ${height - pad} Z`
  const [lastX, lastY] = points[points.length - 1]

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      className={className}
      role="img"
      aria-label={ariaLabel ?? 'trend sparkline'}
      preserveAspectRatio="none"
    >
      <defs>
        <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.35" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={area} fill={`url(#${gradId})`} />
      <path
        d={line}
        fill="none"
        stroke={color}
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <circle cx={lastX} cy={lastY} r="1.8" fill={color} />
    </svg>
  )
}

interface SeverityBarProps {
  slices: SeveritySlice[]
  className?: string
}

/** A compact stacked severity distribution bar (critical→low, left to right). */
export function SeverityBar({ slices, className }: SeverityBarProps) {
  const total = slices.reduce((a, s) => a + s.count, 0)
  if (total === 0) return null
  return (
    <div
      className={`flex h-1.5 w-full overflow-hidden rounded-full bg-slate-800 ${className ?? ''}`}
      role="img"
      aria-label={slices.map((s) => `${s.count} ${s.key}`).join(', ')}
    >
      {slices.map((s) => (
        <div
          key={s.key}
          style={{ width: `${(s.count / total) * 100}%`, backgroundColor: SEVERITY_HEX[s.key] }}
        />
      ))}
    </div>
  )
}
