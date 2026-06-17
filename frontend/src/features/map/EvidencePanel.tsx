import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'

interface EvidencePanelProps {
  evidence: Record<string, unknown> | null
}

export function EvidencePanel({ evidence }: EvidencePanelProps) {
  if (!evidence) {
    return (
      <Card className="bg-[#0F172A] border-slate-800">
        <CardHeader>
          <CardTitle className="text-slate-300 text-sm">Evidence</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-slate-600 text-sm italic">No evidence recorded.</p>
        </CardContent>
      </Card>
    )
  }

  return (
    <Card className="bg-[#0F172A] border-slate-800">
      <CardHeader>
        <CardTitle className="text-slate-300 text-sm">Evidence</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {Boolean(evidence.summary) && (
          <div>
            <p className="text-xs text-slate-500 uppercase tracking-wider mb-1">Summary</p>
            <p className="text-slate-200 text-sm">{String(evidence.summary)}</p>
          </div>
        )}
        {Boolean(evidence.verdict) && (
          <div>
            <p className="text-xs text-slate-500 uppercase tracking-wider mb-1">Verdict</p>
            <span className="font-mono text-xs text-cyan-400">{String(evidence.verdict)}</span>
          </div>
        )}
        {Array.isArray(evidence.flags) && evidence.flags.length > 0 && (
          <div>
            <p className="text-xs text-slate-500 uppercase tracking-wider mb-1">Flags</p>
            <div className="flex flex-wrap gap-1.5">
              {evidence.flags.map((flag, i) => (
                <span key={i} className="bg-amber-500/10 text-amber-400 text-xs px-2 py-0.5 rounded font-mono">
                  {String(flag)}
                </span>
              ))}
            </div>
          </div>
        )}
        {Array.isArray(evidence.retrieved_context) && evidence.retrieved_context.length > 0 && (
          <div>
            <p className="text-xs text-slate-500 uppercase tracking-wider mb-1">
              Retrieved Context ({evidence.retrieved_context.length})
            </p>
            <ul className="space-y-1">
              {evidence.retrieved_context.map((ctx, i) => (
                <li key={i} className="text-xs text-slate-400 font-mono truncate">
                  {JSON.stringify(ctx)}
                </li>
              ))}
            </ul>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
