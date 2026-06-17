import { useEffect, useRef, useState } from 'react'
import {
  diffSnapshots,
  usePipeline,
  type PipelineSnapshot,
  type TerminalCounts,
} from '@/api/pipeline'

const FLASH_DURATION_MS = 300

export function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(
    () =>
      typeof window !== 'undefined' &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches
  )

  useEffect(() => {
    const mql = window.matchMedia('(prefers-reduced-motion: reduce)')
    const onChange = () => setReduced(mql.matches)
    mql.addEventListener('change', onChange)
    return () => mql.removeEventListener('change', onChange)
  }, [])

  return reduced
}

export function useAnimatedPipeline() {
  const [paused, setPaused] = useState(false)
  const prefersReducedMotion = usePrefersReducedMotion()
  const query = usePipeline({ paused })
  const previousRef = useRef<PipelineSnapshot | undefined>(undefined)
  const [changedStageKeys, setChangedStageKeys] = useState<Set<string>>(new Set())
  const [changedTerminalKeys, setChangedTerminalKeys] = useState<Set<keyof TerminalCounts>>(
    new Set()
  )

  useEffect(() => {
    if (!query.data) return
    const delta = diffSnapshots(previousRef.current, query.data)
    previousRef.current = query.data

    if (prefersReducedMotion) return
    if (delta.changedStageKeys.size === 0 && delta.changedTerminalKeys.size === 0) return

    setChangedStageKeys(delta.changedStageKeys)
    setChangedTerminalKeys(delta.changedTerminalKeys)
    const timer = setTimeout(() => {
      setChangedStageKeys(new Set())
      setChangedTerminalKeys(new Set())
    }, FLASH_DURATION_MS)
    return () => clearTimeout(timer)
  }, [query.data, prefersReducedMotion])

  return {
    snapshot: query.data,
    isLoading: query.isLoading,
    error: query.error as Error | null,
    changedStageKeys,
    changedTerminalKeys,
    paused,
    togglePaused: () => setPaused((p) => !p),
    prefersReducedMotion,
  }
}
