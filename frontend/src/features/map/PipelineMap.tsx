import { Play, Pause } from 'lucide-react'
import { useAnimatedPipeline } from './useAnimatedPipeline'
import { StageNodeCard } from './StageNode'
import { FlowEdge } from './FlowEdge'
import { TerminalColumn } from './TerminalColumn'
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
    <div className="space-y-6" data-testid="pipeline-map">
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
        <div className="flex flex-col sm:flex-row gap-6">
          <div className="flex items-center flex-wrap gap-y-3">
            {snapshot.stages.map((stage, i) => (
              <div key={stage.key} className="flex items-center">
                <StageNodeCard
                  stage={stage}
                  justChanged={changedStageKeys.has(stage.key)}
                />
                {i < snapshot.stages.length - 1 && (
                  <FlowEdge
                    active={
                      changedStageKeys.has(stage.key) ||
                      changedStageKeys.has(snapshot.stages[i + 1].key)
                    }
                  />
                )}
              </div>
            ))}
          </div>
          <TerminalColumn terminals={snapshot.terminals} changedKeys={changedTerminalKeys} />
        </div>
      )}
    </div>
  )
}
