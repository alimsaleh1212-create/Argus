import { useQuery } from '@tanstack/react-query'
import { apiFetch } from './client'

export interface SpanView {
  span_id: string
  parent_span_id: string | null
  name: string
  kind: string
  status: string
  started_at: string | null
  ended_at: string | null
  latency_ms: number | null
  llm_model: string | null
  tokens_in: number | null
  tokens_out: number | null
  attributes: Record<string, unknown>
  error_message: string | null
}

export interface TelemetryView {
  total_tokens_in: number | null
  total_tokens_out: number | null
  end_to_end_ms: number | null
  step_count: number
  error_steps: number
}

export interface TraceTreeView {
  correlation_id: string
  root: SpanView | null
  children: Record<string, SpanView[]>
  telemetry: TelemetryView
}

export function useTrace(incidentId: string | undefined) {
  return useQuery<TraceTreeView>({
    queryKey: ['incidents', 'trace', incidentId],
    queryFn: () => apiFetch<TraceTreeView>(`/incidents/${incidentId}/trace`),
    enabled: !!incidentId,
    staleTime: 30_000,
  })
}
