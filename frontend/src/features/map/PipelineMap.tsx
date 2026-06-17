import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Play, Pause } from 'lucide-react'
import { useAnimatedPipeline } from './useAnimatedPipeline'
import { StageNodeCard } from './StageNode'
import { FlowEdge } from './FlowEdge'
import { TerminalColumn } from './TerminalColumn'
import { BranchBreakdown } from './BranchBreakdown'
import { HumanAttentionLane } from './HumanAttentionLane'
import { IncidentDrawer } from './IncidentDrawer'
import { JourneyOverlay, useJourney } from './JourneyOverlay'
import { Skeleton } from '@/components/ui/skeleton'
import { ErrorState } from '@/components/ErrorState'
import { EmptyState } from '@/components/EmptyState'
import { Button } from '@/components/ui/button'

export function PipelineMap() {
  const {
    snapshot,
    isLoading,
    error,
    changedStageKeys,
    changedTerminalKeys,
    paused,
    togglePaused,
  } = useAnimatedPipeline()

  const [searchParams, setSearchParams] = useSearchParams()
  const selectedIncidentId = searchParams.get('incident')
  const [expandedStage, setExpandedStage] = useState<string | null>(null)
  const journey = useJourney(selectedIncidentId)

  function selectIncident(id: string) {
    const next = new URLSearchParams(searchParams)
    next.set('incident', id)
    setSearchParams(next)
  }

  function clearSelection() {
    const next = new URLSearchParams(searchParams)
    next.delete('incident')
    setSearchParams(next)
  }

  function toggleExpand(stageKey: string) {
    setExpandedStage((current) => (current === stageKey ? null : stageKey))
  }

  if (isLoading) {
    return (
      <div className="space-y-4" aria-label="Loading pipeline map">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-32 w-full" />
      </div>
    )
  }

  if (error) {
    return (
      <ErrorState
        message={`Failed to load pipeline map: ${(error as Error)?.message ?? 'unknown error'}`}
      />
    )
  }

  if (!snapshot) return null

  const isEmpty =
    snapshot.stages.every((s) => s.in_flight === 0) &&
    snapshot.terminals.resolved === 0 &&
    snapshot.terminals.escalated === 0 &&
    snapshot.terminals.awaiting === 0

  return (
    <div className="flex flex-col min-h-[calc(100vh-4rem)] gap-6" data-testid="pipeline-map">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold text-slate-100">Pipeline Map</h1>
          <p className="text-xs text-slate-500 mt-0.5">
            Window: last {snapshot.window_hours}h · Updated{' '}
            {new Date(snapshot.generated_at).toLocaleTimeString()}
          </p>
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={togglePaused}
          aria-label={paused ? 'Paused, click to resume live updates' : 'Live, click to pause updates'}
        >
          {paused ? (
            <>
              <Play className="w-3.5 h-3.5" aria-hidden="true" />
              Paused
            </>
          ) : (
            <>
              <Pause className="w-3.5 h-3.5" aria-hidden="true" />
              Live
            </>
          )}
        </Button>
      </div>

      {isEmpty ? (
        <EmptyState
          title="No incidents in flight"
          description="The pipeline is quiet right now."
        />
      ) : (
        <div className="flex flex-col gap-6 flex-1">
          {selectedIncidentId && <JourneyOverlay incidentId={selectedIncidentId} onClear={clearSelection} />}

          <div className="flex flex-col xl:flex-row gap-6 items-start">
            <div className="flex items-start flex-wrap gap-y-4 flex-1">
              {snapshot.stages.map((stage, i) => (
                <div key={stage.key} className="flex items-start">
                  <div className="flex flex-col">
                    <StageNodeCard
                      stage={stage}
                      justChanged={changedStageKeys.has(stage.key)}
                      expanded={expandedStage === stage.key}
                      onToggleExpand={() => toggleExpand(stage.key)}
                      dimmed={!!journey && !journey.visitedStages.has(stage.key)}
                      journeyTimingMs={journey?.timingByStage[stage.key]}
                    />
                    {expandedStage === stage.key && (
                      <BranchBreakdown stage={stage} onSelectIncident={selectIncident} />
                    )}
                  </div>
                  {i < snapshot.stages.length - 1 && (
                    <FlowEdge
                      active={
                        changedStageKeys.has(stage.key) ||
                        changedStageKeys.has(snapshot.stages[i + 1].key)
                      }
                      highlighted={
                        !!journey &&
                        journey.visitedStages.has(stage.key) &&
                        journey.visitedStages.has(snapshot.stages[i + 1].key)
                      }
                    />
                  )}
                </div>
              ))}
            </div>
            <TerminalColumn terminals={snapshot.terminals} changedKeys={changedTerminalKeys} />
          </div>

          <HumanAttentionLane onSelectIncident={selectIncident} />
        </div>
      )}

      <IncidentDrawer incidentId={selectedIncidentId} onClose={clearSelection} />
    </div>
  )
}
