import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from 'recharts'
import type { VolumeBucket } from '@/api/kpis'

interface VolumeChartProps {
  buckets: VolumeBucket[]
}

function formatBucket(iso: string): string {
  try {
    const d = new Date(iso)
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  } catch {
    return iso
  }
}

export function VolumeChart({ buckets }: VolumeChartProps) {
  const data = [...buckets].reverse().map((b) => ({
    time: formatBucket(b.bucket),
    count: b.count,
  }))

  return (
    <div
      className="rounded-lg bg-slate-900 border border-slate-700 p-4"
      data-testid="volume-chart"
      aria-label="Alert volume over time"
    >
      <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-400 mb-4">
        Alert Volume
      </h3>
      {data.length === 0 ? (
        <p className="text-sm text-slate-500 text-center py-8">No data</p>
      ) : (
        <ResponsiveContainer width="100%" height={180}>
          <AreaChart data={data} margin={{ top: 4, right: 4, left: -20, bottom: 0 }}>
            <defs>
              <linearGradient id="alertGradient" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="#22C55E" stopOpacity={0.25} />
                <stop offset="95%" stopColor="#22C55E" stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="#1E293B" />
            <XAxis dataKey="time" tick={{ fill: '#94A3B8', fontSize: 10 }} />
            <YAxis tick={{ fill: '#94A3B8', fontSize: 10 }} />
            <Tooltip
              contentStyle={{ background: '#0F172A', border: '1px solid #1E293B', color: '#F8FAFC' }}
            />
            <Area
              type="monotone"
              dataKey="count"
              stroke="#22C55E"
              fill="url(#alertGradient)"
              strokeWidth={2}
            />
          </AreaChart>
        </ResponsiveContainer>
      )}
    </div>
  )
}
