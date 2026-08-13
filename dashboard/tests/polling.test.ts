import { describe, it, expect, vi } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { usePolling } from '../src/lib/polling'

describe('usePolling', () => {
  it('fetches immediately and refetches on the interval', async () => {
    let calls = 0
    const fetchFn = vi.fn().mockImplementation(async () => {
      calls += 1
      return `v${calls}`
    })
    const { result } = renderHook(() => usePolling(fetchFn, 50))

    await waitFor(() => expect(result.current.data).toBe('v1'))
    expect(result.current.loading).toBe(false)

    await waitFor(() => expect(result.current.data).toBe('v2'), { timeout: 3000 })
    expect(calls).toBeGreaterThanOrEqual(2)
  })

  it('surfaces an error message when the fetch rejects', async () => {
    const fetchFn = vi.fn().mockRejectedValue(new Error('boom'))
    const { result } = renderHook(() => usePolling(fetchFn, 1000))
    await waitFor(() => expect(result.current.error).toBe('boom'))
    expect(result.current.data).toBeNull()
  })

  it('does not fetch when disabled', async () => {
    const fetchFn = vi.fn().mockResolvedValue('x')
    renderHook(() => usePolling(fetchFn, 50, false))
    await new Promise((r) => setTimeout(r, 120))
    expect(fetchFn).not.toHaveBeenCalled()
  })
})
