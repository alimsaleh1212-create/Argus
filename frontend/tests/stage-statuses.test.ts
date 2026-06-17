import { describe, it, expect } from 'vitest'
import { STAGE_KEYS, STAGE_STATUSES, stageForStatus } from '@/features/map/stageStatuses'

describe('stageStatuses', () => {
  it('declares the four rail stages in order', () => {
    expect(STAGE_KEYS).toEqual(['intake', 'triage', 'enrichment', 'response'])
  })

  it('maps every active status to its backend-defined stage', () => {
    expect(STAGE_STATUSES.intake).toEqual(['received', 'grounding', 'grounded'])
    expect(STAGE_STATUSES.triage).toEqual(['triaging'])
    expect(STAGE_STATUSES.enrichment).toEqual(['enriching'])
    expect(STAGE_STATUSES.response).toEqual(['responding', 'awaiting_approval'])
  })

  it('resolves a status to its stage key', () => {
    expect(stageForStatus('triaging')).toBe('triage')
    expect(stageForStatus('awaiting_approval')).toBe('response')
  })

  it('returns null for terminal/unknown statuses', () => {
    expect(stageForStatus('resolved')).toBeNull()
    expect(stageForStatus('escalated')).toBeNull()
    expect(stageForStatus('nonsense')).toBeNull()
  })
})
