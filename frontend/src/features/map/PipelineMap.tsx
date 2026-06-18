import { Fragment, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { Play, Pause, Activity } from 'lucide-react'
import { useAnimatedPipeline } from './useAnimatedPipeline'
import { StageNodeCard } from './StageNode'
import { FlowEdge } from './FlowEdge'
import { TerminalColumn } from './TerminalColumn'
import { BranchBreakdown } from './BranchBreakdown'
import { IncidentDrawer } from './IncidentDrawer'
import { JourneyOverlay, useJourney } from './JourneyOverlay'
import type { TerminalCounts } from '@/api/pipeline'
import { Skeleton } from '@/components/ui/skeleton'
import { ErrorState } from '@/components/ErrorState'
import { EmptyState } from '@/components/EmptyState'
import { Button } from '@/components/ui/button'

const TERMINAL_ROUTES: Record<keyof TerminalCounts, string> = {
  resolved: '/queue?view=resolved',
  escalated: '/attention',
  awaiting: '/approvals',
}

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

  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const selectedIncidentId = searchParams.get('incident')
  // Every stage starts expanded; this Set holds the stages the operator has
  // manually collapsed to reclaim space.
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())
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
    setCollapsed((prev) => {
      const next = new Set(prev)
      if (next.has(stageKey)) next.delete(stageKey)
      else next.add(stageKey)
      return next
    })
  }

  if (isLoading) {
    return (
      <div className="space-y-4 p-6" aria-label="Loading pipeline map">
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

  const totalInFlight = snapshot.stages.reduce((a, s) => a + s.in_flight, 0)
  const isEmpty =
    totalInFlight === 0 &&
    snapshot.terminals.resolved === 0 &&
    snapshot.terminals.escalated === 0 &&
    snapshot.terminals.awaiting === 0

  return (
    <div
      className="argus-grid flex flex-col min-h-[calc(100vh-3.5rem)] gap-6 px-6 py-5"
      data-testid="pipeline-map"
    >
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-xl font-bold tracking-tight text-slate-50">Pipeline Map</h1>
          <p className="text-xs text-slate-500 mt-1">
            Window: last {snapshot.window_hours}h · {totalInFlight} in flight · Updated{' '}
            {new Date(snapshot.generated_at).toLocaleTimeString()}
          </p>
        </div>
        <div className="flex items-center gap-3">
          <span
            className={`flex items-center gap-1.5 text-[11px] font-mono uppercase tracking-wider ${
              paused ? 'text-slate-500' : 'text-cyan-400'
            }`}
          >
            <Activity className={`w-3.5 h-3.5 ${paused ? '' : 'animate-pulse'}`} aria-hidden="true" />
            {paused ? 'Frozen' : 'Streaming'}
          </span>
          <Button
            variant="outline"
            size="sm"
            onClick={togglePaused}
            aria-label={
              paused ? 'Paused, click to resume live updates' : 'Live, click to pause updates'
            }
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
      </div>

      {isEmpty ? (
        <EmptyState
          title="No incidents in flight"
          description="The pipeline is quiet right now."
        />
      ) : (
        <div className="flex flex-col gap-6 flex-1">
          {selectedIncidentId && (
            <JourneyOverlay incidentId={selectedIncidentId} onClear={clearSelection} />
          )}

          <div className="flex items-start gap-2 overflow-x-auto pb-4 xl:gap-3">
            {snapshot.stages.map((stage, i) => {
              const expanded = !collapsed.has(stage.key)
              const dimmed = !!journey && !journey.visitedStages.has(stage.key)
              return (
                <Fragment key={stage.key}>
                  <div className="flex min-w-[260px] flex-1 flex-col gap-3">
                    <StageNodeCard
                      stage={stage}
                      justChanged={changedStageKeys.has(stage.key)}
                      expanded={expanded}
                      onToggleExpand={() => toggleExpand(stage.key)}
                      dimmed={dimmed}
                      journeyTimingMs={journey?.timingByStage[stage.key]}
                    />
                    {expanded && (
                      <BranchBreakdown stage={stage} onSelectIncident={selectIncident} dimmed={dimmed} />
                    )}
                  </div>
                  {i < snapshot.stages.length - 1 && (
                    <div className="self-start pt-[3.25rem]">
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
                    </div>
                  )}
                </Fragment>
              )
            })}

            <div className="self-start pt-[3.25rem]">
              <FlowEdge active={changedTerminalKeys.size > 0} />
            </div>
            <div className="w-[300px] min-w-[260px] flex-shrink-0 self-start">
              <TerminalColumn
                terminals={snapshot.terminals}
                changedKeys={changedTerminalKeys}
                onSelect={(key) => navigate(TERMINAL_ROUTES[key])}
              />
            </div>
          </div>
        </div>
      )}

      <IncidentDrawer incidentId={selectedIncidentId} onClose={clearSelection} />
    </div>
  )
}
