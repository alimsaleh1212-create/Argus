import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import type { AuditView } from '@/api/incidents'

interface AuditTrailProps {
  audit: AuditView[]
}

export function AuditTrail({ audit }: AuditTrailProps) {
  return (
    <Card className="bg-[#0F172A] border-slate-800">
      <CardHeader>
        <CardTitle className="text-slate-300 text-sm">
          Audit Trail ({audit.length})
        </CardTitle>
      </CardHeader>
      <CardContent>
        {audit.length === 0 ? (
          <p className="text-slate-600 text-sm italic">No audit entries yet.</p>
        ) : (
          <ol className="relative border-l border-slate-800 ml-2 space-y-4">
            {audit.map((row, i) => (
              <li key={i} className="ml-4">
                <div className="absolute -left-[5px] mt-1 w-2.5 h-2.5 rounded-full bg-slate-700 border border-slate-600" />
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="font-mono text-xs text-cyan-400">{row.action}</span>
                  <span className="text-xs text-slate-500">by</span>
                  <span className="font-mono text-xs text-slate-300">{row.actor}</span>
                  {row.target && (
                    <>
                      <span className="text-xs text-slate-500">→</span>
                      <span className="font-mono text-xs text-slate-400 truncate max-w-[200px]">{row.target}</span>
                    </>
                  )}
                  <span
                    className={`ml-auto text-[10px] font-semibold uppercase px-1.5 py-0.5 rounded ${
                      row.outcome === 'applied'
                        ? 'bg-cyan-400/10 text-cyan-400'
                        : row.outcome === 'skipped'
                        ? 'bg-slate-700 text-slate-400'
                        : 'bg-red-500/10 text-red-400'
                    }`}
                  >
                    {row.outcome}
                  </span>
                </div>
                <time className="text-[11px] text-slate-600">
                  {new Date(row.created_at).toLocaleString()}
                </time>
              </li>
            ))}
          </ol>
        )}
      </CardContent>
    </Card>
  )
}
