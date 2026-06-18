// Pure, framework-free derivations of scannable "data points" for each pipeline
// stage, computed entirely from the snapshot the API already returns (no
// fabricated time series). Kept pure so the card components stay declarative
// and these can be unit-tested in isolation.

import type { StageIncident, StageNode } from '@/api/pipeline'

export const SEVERITY_ORDER = ['critical', 'high', 'medium', 'low'] as const
export type SeverityKey = (typeof SEVERITY_ORDER)[number]

export const SEVERITY_HEX: Record<SeverityKey, string> = {
  critical: '#EF4444', // red-500
  high: '#FB923C', // orange-400
  medium: '#FACC15', // amber-400
  low: '#38BDF8', // sky-400
}

export interface SeveritySlice {
  key: SeverityKey
  count: number
}

/** Count of in-stage incidents per severity, in fixed descending order. */
export function severityMix(incidents: StageIncident[]): SeveritySlice[] {
  const counts = new Map<SeverityKey, number>()
  for (const inc of incidents) {
    const key = inc.severity as SeverityKey
    if (SEVERITY_ORDER.includes(key)) {
      counts.set(key, (counts.get(key) ?? 0) + 1)
    }
  }
  return SEVERITY_ORDER.map((key) => ({ key, count: counts.get(key) ?? 0 })).filter(
    (s) => s.count > 0
  )
}

/** True when any in-stage incident is critical (drives the severe-glow accent). */
export function hasSevere(incidents: StageIncident[]): boolean {
  return incidents.some((i) => i.severity === 'critical')
}

/**
 * Confidence values (0..1) for the stage's own model, as a word-sized series.
 * Triage exposes triage_confidence; enrichment exposes enrichment_confidence.
 * Returned in chronological-ish order (snapshot order) so the sparkline reads
 * left-to-right. Empty for stages without a confidence signal.
 */
export function confidenceSeries(stageKey: string, incidents: StageIncident[]): number[] {
  const pick =
    stageKey === 'triage'
      ? (i: StageIncident) => i.triage_confidence
      : stageKey === 'enrichment'
        ? (i: StageIncident) => i.enrichment_confidence
        : () => null
  return incidents.map(pick).filter((v): v is number => typeof v === 'number')
}

export interface DecisionTally {
  label: string
  count: number
  tone: 'rose' | 'cyan' | 'amber' | 'sky' | 'slate'
}

const VERDICT_TONE: Record<string, DecisionTally['tone']> = {
  real: 'rose',
  confirmed: 'rose',
  noise: 'cyan',
  benign: 'cyan',
  uncertain: 'amber',
  inconclusive: 'amber',
}

function tally<T extends string>(
  values: (T | null)[],
  tone: (v: T) => DecisionTally['tone']
): DecisionTally[] {
  const counts = new Map<T, number>()
  for (const v of values) {
    if (v) counts.set(v, (counts.get(v) ?? 0) + 1)
  }
  return [...counts.entries()]
    .sort((a, b) => b[1] - a[1])
    .map(([label, count]) => ({ label, count, tone: tone(label) }))
}

/**
 * The automated decisions made at this stage, as compact tallies:
 * triage verdicts, enrichment assessments, or fired response playbooks.
 */
export function decisionTallies(stageKey: string, incidents: StageIncident[]): DecisionTally[] {
  if (stageKey === 'triage') {
    return tally(
      incidents.map((i) => i.triage_verdict),
      (v) => VERDICT_TONE[v] ?? 'slate'
    )
  }
  if (stageKey === 'enrichment') {
    return tally(
      incidents.map((i) => i.enrichment_assessment),
      (v) => VERDICT_TONE[v] ?? 'slate'
    )
  }
  if (stageKey === 'response') {
    return tally(
      incidents.map((i) => i.response_plan_id),
      () => 'sky'
    )
  }
  return []
}

export interface StageInsights {
  severity: SeveritySlice[]
  severe: boolean
  confidence: number[]
  /** Mean confidence as a 0..100 integer, or null when no confidence signal. */
  confidencePct: number | null
  decisions: DecisionTally[]
}

export function deriveStageInsights(stage: StageNode): StageInsights {
  const confidence = confidenceSeries(stage.key, stage.incidents)
  const mean =
    confidence.length > 0
      ? confidence.reduce((a, b) => a + b, 0) / confidence.length
      : null
  return {
    severity: severityMix(stage.incidents),
    severe: hasSevere(stage.incidents),
    confidence,
    confidencePct: mean === null ? null : Math.round(mean * 100),
    decisions: decisionTallies(stage.key, stage.incidents),
  }
}
