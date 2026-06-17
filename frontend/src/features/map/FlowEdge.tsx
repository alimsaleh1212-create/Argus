import { cn } from '@/lib/utils'

interface FlowEdgeProps {
  active: boolean
  highlighted?: boolean
}

export function FlowEdge({ active, highlighted = false }: FlowEdgeProps) {
  return (
    <div
      className="flex items-center px-1.5"
      data-testid="flow-edge"
      aria-hidden="true"
    >
      <div
        className={cn(
          'h-0.5 w-6 sm:w-10 rounded-full bg-slate-700',
          'transition-colors duration-300 ease-in',
          active && 'bg-cyan-400',
          highlighted && 'bg-sky-300'
        )}
      />
    </div>
  )
}
