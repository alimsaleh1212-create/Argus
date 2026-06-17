// Mirrors backend/services/pipeline_view.py STAGES + _STATUS_TO_STAGE exactly.
// Keep in sync if the backend mapping changes.

export const STAGE_KEYS = ['intake', 'triage', 'enrichment', 'response'] as const

export const STAGE_STATUSES: Record<string, string[]> = {
  intake: ['received', 'grounding', 'grounded'],
  triage: ['triaging'],
  enrichment: ['enriching'],
  response: ['responding', 'awaiting_approval'],
}

const STATUS_TO_STAGE: Record<string, string> = Object.fromEntries(
  Object.entries(STAGE_STATUSES).flatMap(([stage, statuses]) =>
    statuses.map((status) => [status, stage])
  )
)

export function stageForStatus(status: string): string | null {
  return STATUS_TO_STAGE[status] ?? null
}
