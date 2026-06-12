import { useEffect, useRef, useState } from 'react'
import { queryClient } from './queryClient'

type ConnectionState = 'connected' | 'reconnecting' | 'disconnected'

let globalConnectionState: ConnectionState = 'disconnected'
const listeners = new Set<(s: ConnectionState) => void>()

function setConnectionState(s: ConnectionState) {
  globalConnectionState = s
  listeners.forEach((fn) => fn(s))
}

export function useConnectionState(): ConnectionState {
  const [state, setState] = useState<ConnectionState>(globalConnectionState)
  useEffect(() => {
    listeners.add(setState)
    return () => { listeners.delete(setState) }
  }, [])
  return state
}

export function useSSEStream(token: string | null) {
  const esRef = useRef<EventSource | null>(null)

  useEffect(() => {
    if (!token) return

    function connect() {
      const url = `/incidents/stream?token=${encodeURIComponent(token!)}`
      const es = new EventSource(url)
      esRef.current = es

      es.addEventListener('snapshot', (e: MessageEvent) => {
        setConnectionState('connected')
        try {
          const data = JSON.parse(e.data)
          queryClient.setQueryData(['incidents', 'queue'], (old: unknown) => {
            if (!old || typeof old !== 'object') return old
            return { ...(old as object), items: data.queue }
          })
          // Invalidate KPI view so it refetches fresh data
          queryClient.invalidateQueries({ queryKey: ['kpis'] })
        } catch { /* ignore parse errors */ }
      })

      es.addEventListener('delta', (e: MessageEvent) => {
        setConnectionState('connected')
        try {
          const data = JSON.parse(e.data)
          queryClient.setQueryData(['incidents', 'queue'], (old: unknown) => {
            if (!old || typeof old !== 'object') return old
            const prev = old as { items: unknown[] }
            const deltaMap = new Map(
              (data.queue as Array<{ id: string }>).map((i) => [i.id, i])
            )
            const merged = prev.items.map((item) => {
              const i = item as { id: string }
              return deltaMap.has(i.id) ? deltaMap.get(i.id) : item
            })
            return { ...prev, items: merged }
          })
          queryClient.invalidateQueries({ queryKey: ['kpis'] })
        } catch { /* ignore parse errors */ }
      })

      es.addEventListener('heartbeat', () => {
        setConnectionState('connected')
      })

      es.onerror = () => {
        setConnectionState('reconnecting')
        // EventSource auto-reconnects; trigger on-demand refetch as fallback while disconnected
        queryClient.invalidateQueries({ queryKey: ['incidents', 'queue'] })
        queryClient.invalidateQueries({ queryKey: ['kpis'] })
      }

      es.onopen = () => {
        setConnectionState('connected')
      }
    }

    connect()

    return () => {
      esRef.current?.close()
      setConnectionState('disconnected')
    }
  }, [token])
}
