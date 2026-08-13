// usePolling — refetch a data source on an interval with immediate first fetch.

import { useCallback, useEffect, useRef, useState } from 'react'

export interface PollingState<T> {
  data: T | null
  error: string | null
  loading: boolean
  refresh: () => Promise<void>
}

export function usePolling<T>(
  fetchFn: () => Promise<T>,
  intervalMs: number,
  enabled = true,
): PollingState<T> {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const fnRef = useRef(fetchFn)

  useEffect(() => {
    fnRef.current = fetchFn
  }, [fetchFn])

  const refresh = useCallback(async () => {
    try {
      setError(null)
      const value = await fnRef.current()
      setData(value)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Request failed')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (!enabled) return
    let active = true
    let timer: ReturnType<typeof setInterval> | undefined

    const run = () => {
      fnRef.current()
        .then((value) => {
          if (active) {
            setData(value)
            setError(null)
          }
        })
        .catch((e) => {
          if (active) setError(e instanceof Error ? e.message : 'Request failed')
        })
        .finally(() => {
          if (active) setLoading(false)
        })
    }

    run()
    timer = setInterval(run, intervalMs)
    return () => {
      active = false
      if (timer) clearInterval(timer)
    }
  }, [intervalMs, enabled])

  return { data, error, loading, refresh }
}