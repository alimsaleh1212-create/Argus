import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'

interface EvidencePanelProps {
  evidence: Record<string, unknown> | null
}

function Section({
  label,
  children,
}: {
  label: string
  children: React.ReactNode
}) {
  return (
    <div>
      <p className="text-xs text-slate-500 uppercase tracking-wider mb-1">{label}</p>
      {children}
    </div>
  )
}

function pct(n: unknown): string {
  const v = typeof n === 'number' ? n : typeof n === 'string' ? Number(n) : NaN
  if (Number.isNaN(v) || v < 0 || v > 1) return ''
  return `${Math.round(v * 100)}%`
}

function FindingList({ items }: { items: unknown[] }) {
  if (!items || items.length === 0) return null
  return (
    <ul className="space-y-1">
      {items.map((item, i) => (
        <li key={i} className="text-xs text-slate-400 font-mono break-words">
          {typeof item === 'string' ? item : JSON.stringify(item)}
        </li>
      ))}
    </ul>
  )
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

  const triage = (evidence.triage as Record<string, unknown> | undefined) ?? undefined
  const enrichment = (evidence.enrichment as Record<string, unknown> | undefined) ?? undefined
  const response = (evidence.response as Record<string, unknown> | undefined) ?? undefined
  const plan = (response?.plan as Record<string, unknown> | undefined) ?? undefined
  const verification = (response?.verification as Record<string, unknown> | undefined) ?? undefined

  const triageConf = pct(triage?.confidence)
  const enrichConf = pct(enrichment?.confidence)

  return (
    <Card className="bg-[#0F172A] border-slate-800">
      <CardHeader>
        <CardTitle className="text-slate-300 text-sm">Evidence</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {Boolean(evidence.summary) && (
          <Section label="Summary">
            <p className="text-slate-200 text-sm">{String(evidence.summary)}</p>
          </Section>
        )}
        {Boolean(evidence.verdict) && (
          <Section label="Verdict">
            <span className="font-mono text-xs text-cyan-400">{String(evidence.verdict)}</span>
          </Section>
        )}
        {Array.isArray(evidence.flags) && evidence.flags.length > 0 && (
          <Section label="Flags">
            <div className="flex flex-wrap gap-1.5">
              {evidence.flags.map((flag, i) => (
                <span
                  key={i}
                  className="bg-amber-500/10 text-amber-400 text-xs px-2 py-0.5 rounded font-mono"
                >
                  {String(flag)}
                </span>
              ))}
            </div>
          </Section>
        )}

        {triage && (
          <Section label={`Triage${triage.verdict ? ` · ${triage.verdict}` : ''}${triageConf ? ` · ${triageConf}` : ''}`}>
            <div className="space-y-1">
              {Boolean(triage.assessed_severity) && (
                <p className="text-xs text-slate-400">
                  Assessed severity:{' '}
                  <span className="font-mono text-slate-200">{String(triage.assessed_severity)}</span>
                </p>
              )}
              {Boolean(triage.rationale) && (
                <p className="text-xs text-slate-300">{String(triage.rationale)}</p>
              )}
              {Array.isArray(triage.cited_evidence) && triage.cited_evidence.length > 0 && (
                <FindingList items={triage.cited_evidence as unknown[]} />
              )}
            </div>
          </Section>
        )}

        {enrichment && (
          <Section
            label={`Enrichment${enrichment.assessment ? ` · ${enrichment.assessment}` : ''}${enrichConf ? ` · ${enrichConf}` : ''}`}
          >
            <div className="space-y-1">
              {Boolean(enrichment.correlation_summary) && (
                <p className="text-xs text-slate-300">{String(enrichment.correlation_summary)}</p>
              )}
              {Array.isArray(enrichment.external_findings) &&
                (enrichment.external_findings as unknown[]).length > 0 && (
                  <div>
                    <p className="text-[10px] text-slate-500 uppercase tracking-wider mt-1">
                      External findings
                    </p>
                    <FindingList items={enrichment.external_findings as unknown[]} />
                  </div>
                )}
              {Array.isArray(enrichment.internal_findings) &&
                (enrichment.internal_findings as unknown[]).length > 0 && (
                  <div>
                    <p className="text-[10px] text-slate-500 uppercase tracking-wider mt-1">
                      Internal findings
                    </p>
                    <FindingList items={enrichment.internal_findings as unknown[]} />
                  </div>
                )}
              {Array.isArray(enrichment.cited_evidence) &&
                (enrichment.cited_evidence as unknown[]).length > 0 && (
                  <FindingList items={enrichment.cited_evidence as unknown[]} />
                )}
            </div>
          </Section>
        )}

        {response && (
          <Section label="Response">
            <div className="space-y-1">
              {plan && (
                <div className="flex flex-wrap gap-2 text-xs">
                  {Boolean(plan.playbook_id) && (
                    <span className="font-mono text-sky-400">{String(plan.playbook_id)}</span>
                  )}
                  {Boolean(plan.selected_by) && (
                    <span className="text-slate-500">via {String(plan.selected_by)}</span>
                  )}
                </div>
              )}
              {Boolean(plan?.rationale) && (
                <p className="text-xs text-slate-300">{String(plan?.rationale)}</p>
              )}
              {verification && (
                <p className="text-xs">
                  Verification:{' '}
                  <span
                    className={`font-mono ${
                      verification.verdict === 'verified'
                        ? 'text-cyan-400'
                        : verification.verdict === 'regressed'
                        ? 'text-rose-400'
                        : 'text-amber-400'
                    }`}
                  >
                    {String(verification.verdict ?? 'unknown')}
                  </span>
                </p>
              )}
              {Array.isArray(response.results) && (response.results as unknown[]).length > 0 && (
                <FindingList items={response.results as unknown[]} />
              )}
            </div>
          </Section>
        )}

        {Array.isArray(evidence.retrieved_context) &&
          (evidence.retrieved_context as unknown[]).length > 0 && (
            <Section label={`Retrieved Context (${(evidence.retrieved_context as unknown[]).length})`}>
              <FindingList items={evidence.retrieved_context as unknown[]} />
            </Section>
          )}
      </CardContent>
    </Card>
  )
}
