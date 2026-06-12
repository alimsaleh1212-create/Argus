import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiFetch } from './client'

export interface ApprovalSummary {
  id: number
  incident_id: string
  plan_id: string
  pending_actions: Record<string, unknown>[]
  rationale: string
  status: string
  deadline_at: string | null
  created_at: string
}

export interface DecisionRequest {
  decision: 'approve' | 'reject'
  note?: string
}

export interface DecisionResponse {
  incident_id: string
  decision: string
  status: string
  disposition: string | null
}

export function usePendingApprovals() {
  return useQuery<{ approvals: ApprovalSummary[] }>({
    queryKey: ['approvals', 'pending'],
    queryFn: () => apiFetch<{ approvals: ApprovalSummary[] }>('/approvals?status=pending'),
    staleTime: 10_000,
  })
}

export function useApprovalDecision(approvalId: number) {
  const qc = useQueryClient()
  return useMutation<DecisionResponse, Error, DecisionRequest>({
    mutationFn: (body) =>
      apiFetch<DecisionResponse>(`/approvals/${approvalId}/decision`, {
        method: 'POST',
        body: JSON.stringify(body),
      }),
    onSuccess: (data) => {
      // Invalidate the queue so the updated incident status reflects
      qc.invalidateQueries({ queryKey: ['incidents', 'queue'] })
      qc.invalidateQueries({ queryKey: ['incidents', 'detail', data.incident_id] })
      qc.invalidateQueries({ queryKey: ['approvals', 'pending'] })
    },
  })
}
