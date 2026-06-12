import * as React from 'react'
import { cva, type VariantProps } from 'class-variance-authority'
import { cn } from '@/lib/utils'

const badgeVariants = cva(
  'inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-semibold transition-colors',
  {
    variants: {
      variant: {
        default: 'bg-slate-800 text-slate-50',
        success: 'bg-green-500/20 text-green-400 border border-green-500/30',
        warning: 'bg-amber-400/20 text-amber-400 border border-amber-400/30',
        danger: 'bg-red-500/20 text-red-400 border border-red-500/30',
        orange: 'bg-orange-400/20 text-orange-400 border border-orange-400/30',
        sky: 'bg-sky-400/20 text-sky-400 border border-sky-400/30',
        muted: 'bg-slate-800 text-slate-400 border border-slate-700',
      },
    },
    defaultVariants: {
      variant: 'default',
    },
  }
)

interface BadgeProps
  extends React.HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, ...props }: BadgeProps) {
  return <div className={cn(badgeVariants({ variant }), className)} {...props} />
}

export { Badge, badgeVariants }
