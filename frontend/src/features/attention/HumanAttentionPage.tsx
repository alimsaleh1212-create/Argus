import { useNavigate } from 'react-router-dom'
import { HumanAttentionLane } from '@/features/map/HumanAttentionLane'

export function HumanAttentionPage() {
  const navigate = useNavigate()

  function handleSelect(incidentId: string) {
    navigate(`/incidents/${incidentId}`)
  }

  return (
    <div className="p-6" data-testid="human-attention-page">
      <div className="mb-4">
        <h1 className="text-lg font-semibold text-slate-100">Human Attention</h1>
        <p className="text-xs text-slate-500 mt-0.5">
          Incidents awaiting approval or escalated for human review.
        </p>
      </div>
      <HumanAttentionLane onSelectIncident={handleSelect} />
    </div>
  )
}
