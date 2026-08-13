import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import {
  storeTokens,
  getAccessToken,
  clearTokens,
  decodeUser,
  login,
  getOverview,
} from '../src/lib/api'

describe('api token handling', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.restoreAllMocks()
  })
  afterEach(() => {
    clearTokens()
  })

  it('stores and reads the access token', () => {
    storeTokens('abc.def.ghi', 'refresh')
    expect(getAccessToken()).toBe('abc.def.ghi')
  })

  it('decodes the role claim from a JWT payload', () => {
    const b64 = btoa(JSON.stringify({ sub: 'bob', role: 'admin' }))
    const token = `header.${b64}.sig`
    const user = decodeUser(token)
    expect(user.username).toBe('bob')
    expect(user.role).toBe('admin')
  })

  it('treats a non-admin role as analyst', () => {
    const b64 = btoa(JSON.stringify({ sub: 'alice', role: 'analyst' }))
    const user = decodeUser(`h.${b64}.s`)
    expect(user.role).toBe('analyst')
  })

  it('rejects a malformed token', () => {
    expect(() => decodeUser('not-a-jwt')).toThrow()
  })
})

describe('api login + requests', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.restoreAllMocks()
  })

  it('login stores tokens and returns the decoded user', async () => {
    const b64 = btoa(JSON.stringify({ sub: 'bob', role: 'analyst' }))
    const access = `h.${b64}.s`
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => ({ access, refresh: 'r' }),
      }),
    )
    const user = await login('bob', 'pw')
    expect(user.username).toBe('bob')
    expect(user.role).toBe('analyst')
    expect(getAccessToken()).toBe(access)
  })

  it('attaches the bearer token on subsequent requests', async () => {
    storeTokens('tok123', 'refresh')
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ total_risk: 5, by_band: {}, top_users: [], top_entities: [], open_alerts: 1, open_incidents: 1 }),
    })
    vi.stubGlobal('fetch', fetchMock)
    await getOverview()
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toContain('/overview')
    expect((init.headers as Headers).get('Authorization')).toBe('Bearer tok123')
  })

  it('throws ApiError with server detail on non-2xx', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: false,
        status: 403,
        json: async () => ({ detail: 'forbidden' }),
      }),
    )
    await expect(getOverview()).rejects.toThrow('forbidden')
  })
})
