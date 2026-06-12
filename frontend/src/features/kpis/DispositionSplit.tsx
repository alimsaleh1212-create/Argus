import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Cell,
  ResponsiveContainer,
} from 'recharts'

interface DispositionSplitProps {
  split: Record<string, number>
}

const COLORS: Record<string, string> = {
  auto_remediated: '#22C55E',
  remediated: '#22C55E',
  rejected_by_human: '#EF4444',
  approval_expired: '#FB923C',
  escalated_response: '#FB923C',
  escalated: '#FB923C',
  _none: '#475569',
}

function colorFor(key: string): string {
  return COLORS[key] ?? '#64748B'
}

export function DispositionSplit({ split }: DispositionSplitProps) {
  const data = Object.entries(split)
    .filter(([key]) => key !== '_none')
    .map(([key, value]) => ({ name: key.replace(/_/g, ' '), key, value }))
    .sort((a, b) => b.value - a.value)

  return (
    <div
      className="rounded-lg bg-slate-900 border border-slate-700 p-4"
      data-testid="disposition-split"
      aria-label="Disposition distribution"
    >
      <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-400 mb-4">
        Disposition Split
      </h3>
      {data.length === 0 ? (
        <p className="text-sm text-slate-500 text-center py-8">No dispositions yet</p>
      ) : (
        <ResponsiveContainer width="100%" height={180}>
          <BarChart data={data} layout="vertical" margin={{ top: 4, right: 24, left: 4, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#1E293B" horizontal={false} />
            <XAxis type="number" tick={{ fill: '#94A3B8', fontSize: 10 }} />
            <YAxis
              type="category"
              dataKey="name"
              tick={{ fill: '#94A3B8', fontSize: 10 }}
              width={110}
            />
            <Tooltip
              contentStyle={{ background: '#0F172A', border: '1px solid #1E293B', color: '#F8FAFC' }}
            />
            <Bar dataKey="value" radius={[0, 4, 4, 0]}>
              {data.map((entry) => (
                <Cell key={entry.key} fill={colorFor(entry.key)} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      )}
    </div>
  )
}
