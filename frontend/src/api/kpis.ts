import { useQuery } from '@tanstack/react-query'
import { apiFetch } from './client'

export interface VolumeBucket {
  bucket: string
  count: number
}

export interface MemoryHit {
  enriched: number
  hits: number
  rate: number | null
}

export interface KpiSnapshot {
  volume_over_time: VolumeBucket[]
  disposition_split: Record<string, number>
  mean_time_to_disposition_ms: number | null
  memory_hit: MemoryHit
  generated_at: string
}

export function useKpis() {
  return useQuery<KpiSnapshot>({
    queryKey: ['kpis'],
    queryFn: () => apiFetch<KpiSnapshot>('/incidents/kpis'),
    staleTime: 30_000,
    refetchInterval: 60_000,
  })
}
