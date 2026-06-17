import { useQuery } from '@tanstack/react-query'
import { apiFetch } from './client'

export interface BranchOutflow {
  to: string
  count: number
}

export interface StageNode {
  key: string
  label: string
  in_flight: number
  branches: BranchOutflow[]
}

export interface TerminalCounts {
  resolved: number
  escalated: number
  awaiting: number
}

export interface PipelineSnapshot {
  stages: StageNode[]
  terminals: TerminalCounts
  window_hours: number
  generated_at: string
}

export interface PipelineDelta {
  changedStageKeys: Set<string>
  changedTerminalKeys: Set<keyof TerminalCounts>
}

const EMPTY_DELTA: PipelineDelta = {
  changedStageKeys: new Set(),
  changedTerminalKeys: new Set(),
}

export function diffSnapshots(
  previous: PipelineSnapshot | undefined,
  current: PipelineSnapshot
): PipelineDelta {
  if (!previous) return EMPTY_DELTA

  const changedStageKeys = new Set<string>()
  for (const stage of current.stages) {
    const prevStage = previous.stages.find((s) => s.key === stage.key)
    if (!prevStage || prevStage.in_flight !== stage.in_flight) {
      changedStageKeys.add(stage.key)
    }
  }

  const changedTerminalKeys = new Set<keyof TerminalCounts>()
  for (const key of ['resolved', 'escalated', 'awaiting'] as const) {
    if (previous.terminals[key] !== current.terminals[key]) {
      changedTerminalKeys.add(key)
    }
  }

  return { changedStageKeys, changedTerminalKeys }
}

export function usePipeline(options: { paused?: boolean } = {}) {
  const { paused = false } = options
  return useQuery<PipelineSnapshot>({
    queryKey: ['pipeline'],
    queryFn: () => apiFetch<PipelineSnapshot>('/incidents/pipeline'),
    refetchInterval: 2000,
    enabled: !paused,
  })
}
