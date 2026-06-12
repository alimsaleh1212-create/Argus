import { useQuery } from '@tanstack/react-query'
import { apiFetch } from './client'

export interface IncidentSummary {
  id: string
  status: string
  severity: string
  disposition: string | null
  source: string
  summary: string | null
  is_awaiting_approval: boolean
  created_at: string
  updated_at: string
}

export interface ApprovalView {
  id: number
  incident_id: string
  plan_id: string
  pending_actions: Record<string, unknown>[]
  rationale: string
  status: string
  deadline_at: string | null
  created_at: string
  is_actionable: boolean
}

export interface AuditView {
  actor: string
  action: string
  target: string | null
  outcome: string
  created_at: string
}

export interface QueuePage {
  items: IncidentSummary[]
  total: number
  limit: number
  offset: number
  view: 'active' | 'resolved' | 'all'
  applied_filters: { status: string[]; severity: string[]; sort: string }
}

export interface IncidentDetailView {
  id: string
  status: string
  severity: string
  disposition: string | null
  source: string
  summary: string | null
  is_awaiting_approval: boolean
  created_at: string
  updated_at: string
  evidence: Record<string, unknown> | null
  normalized_event: Record<string, unknown> | null
  correlation_id: string | null
  pending_approval: ApprovalView | null
  audit: AuditView[]
}

export interface QueueFilters {
  view?: 'active' | 'resolved' | 'all'
  status?: string[]
  severity?: string[]
  sort?: string
  limit?: number
  offset?: number
}

export function useIncidentQueue(filters: QueueFilters = {}) {
  const {
    view = 'active',
    status = [],
    severity = [],
    sort = '-updated_at',
    limit = 50,
    offset = 0,
  } = filters

  const params = new URLSearchParams()
  params.set('view', view)
  params.set('sort', sort)
  params.set('limit', String(limit))
  params.set('offset', String(offset))
  status.forEach((s) => params.append('status', s))
  severity.forEach((s) => params.append('severity', s))

  return useQuery<QueuePage>({
    queryKey: ['incidents', 'queue', { view, status, severity, sort, limit, offset }],
    queryFn: () => apiFetch<QueuePage>(`/incidents?${params.toString()}`),
    staleTime: 10_000,
  })
}

export function useIncidentDetail(incidentId: string | undefined) {
  return useQuery<IncidentDetailView>({
    queryKey: ['incidents', 'detail', incidentId],
    queryFn: () => apiFetch<IncidentDetailView>(`/incidents/${incidentId}`),
    enabled: !!incidentId,
    staleTime: 10_000,
  })
}

export function useIncidentAudit(incidentId: string | undefined) {
  return useQuery<{ audit: AuditView[] }>({
    queryKey: ['incidents', 'audit', incidentId],
    queryFn: () => apiFetch<{ audit: AuditView[] }>(`/incidents/${incidentId}/audit`),
    enabled: !!incidentId,
    staleTime: 30_000,
  })
}
