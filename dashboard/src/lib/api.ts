// Typed API client for the Phase 6 FastAPI service.
// Attaches the JWT, transparently refreshes on 401, maps errors to messages.

import type {
  AdminAccount,
  AlertRow,
  AnalystAction,
  AuthUser,
  EntityKind,
  EntitySummary,
  IncidentRow,
  LoginResponse,
  NoteEntry,
  Overview,
  RawEvent,
  RiskDrillDown,
  ThresholdsResponse,
  UserSummary,
} from '../types'

export const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? '/api'

const ACCESS_KEY = 'ueba.access'
const REFRESH_KEY = 'ueba.refresh'

export class ApiError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
    this.name = 'ApiError'
  }
}

export function getAccessToken(): string | null {
  return localStorage.getItem(ACCESS_KEY)
}

export function getRefreshToken(): string | null {
  return localStorage.getItem(REFRESH_KEY)
}

export function storeTokens(access: string, refresh: string): void {
  localStorage.setItem(ACCESS_KEY, access)
  localStorage.setItem(REFRESH_KEY, refresh)
}

export function clearTokens(): void {
  localStorage.removeItem(ACCESS_KEY)
  localStorage.removeItem(REFRESH_KEY)
}

async function request<T>(path: string, init: RequestInit = {}, retry = true): Promise<T> {
  const headers = new Headers(init.headers)
  headers.set('Content-Type', 'application/json')
  const token = getAccessToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)

  const res = await fetch(`${API_BASE}${path}`, { ...init, headers })

  if (res.status === 401 && retry && getRefreshToken()) {
    const refreshed = await tryRefresh()
    if (refreshed) return request<T>(path, init, false)
    clearTokens()
    throw new ApiError(401, 'Session expired — please sign in again')
  }

  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = (await res.json()) as { detail?: string | unknown }
      if (typeof body.detail === 'string') detail = body.detail
      else if (Array.isArray(body.detail)) detail = 'Invalid request'
    } catch {
      /* non-JSON body */
    }
    throw new ApiError(res.status, detail)
  }

  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

let refreshing: Promise<boolean> | null = null

async function tryRefresh(): Promise<boolean> {
  if (!refreshing) {
    refreshing = doRefresh().finally(() => {
      refreshing = null
    })
  }
  return refreshing
}

async function doRefresh(): Promise<boolean> {
  const refresh = getRefreshToken()
  if (!refresh) return false
  try {
    const res = await fetch(`${API_BASE}/auth/refresh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh }),
    })
    if (!res.ok) return false
    const body = (await res.json()) as { access: string }
    storeTokens(body.access, refresh)
    return true
  } catch {
    return false
  }
}

export function decodeUser(access: string): AuthUser {
  const parts = access.split('.')
  if (parts.length !== 3) throw new ApiError(401, 'Malformed token')
  const payload = JSON.parse(
    atob(parts[1].replace(/-/g, '+').replace(/_/g, '/').padEnd(parts[1].length + ((4 - (parts[1].length % 4)) % 4), '=')),
  ) as { sub?: string; role?: string }
  const role = payload.role === 'admin' ? 'admin' : 'analyst'
  return { username: payload.sub ?? 'unknown', role }
}

// --- session ---------------------------------------------------------------

export async function login(username: string, password: string): Promise<AuthUser> {
  const body = await request<LoginResponse>('/auth/login', {
    method: 'POST',
    body: JSON.stringify({ username, password }),
  }, false)
  storeTokens(body.access, body.refresh)
  return decodeUser(body.access)
}

export function logout(): void {
  clearTokens()
}

// --- overview / entities ----------------------------------------------------

export async function getOverview(): Promise<Overview> {
  return request<Overview>('/overview')
}

export async function listUsers(search?: string, dept?: string): Promise<UserSummary[]> {
  const q = new URLSearchParams()
  if (search) q.set('search', search)
  if (dept) q.set('dept', dept)
  const s = q.toString()
  return request<UserSummary[]>(`/users${s ? `?${s}` : ''}`)
}

export async function listEntities(kind?: EntityKind, search?: string): Promise<EntitySummary[]> {
  const q = new URLSearchParams()
  if (kind) q.set('kind', kind)
  if (search) q.set('search', search)
  const s = q.toString()
  return request<EntitySummary[]>(`/entities${s ? `?${s}` : ''}`)
}

export async function userRisk(entityId: string): Promise<RiskDrillDown> {
  return request<RiskDrillDown>(`/users/${encodeURIComponent(entityId)}/risk`)
}

export async function entityRisk(entityId: string): Promise<RiskDrillDown> {
  return request<RiskDrillDown>(`/entities/${encodeURIComponent(entityId)}/risk`)
}

// --- alerts ----------------------------------------------------------------

export async function listAlerts(status?: string, band?: string): Promise<AlertRow[]> {
  const q = new URLSearchParams()
  if (status) q.set('status', status)
  if (band) q.set('band', band)
  const s = q.toString()
  return request<AlertRow[]>(`/alerts${s ? `?${s}` : ''}`)
}

export async function patchAlert(id: number, patch: { status?: string; assignee?: string }): Promise<AlertRow> {
  return request<AlertRow>(`/alerts/${id}`, { method: 'PATCH', body: JSON.stringify(patch) })
}

// --- incidents -------------------------------------------------------------

export async function listIncidents(status?: string, assignee?: string): Promise<IncidentRow[]> {
  const q = new URLSearchParams()
  if (status) q.set('status', status)
  if (assignee) q.set('assignee', assignee)
  const s = q.toString()
  return request<IncidentRow[]>(`/incidents${s ? `?${s}` : ''}`)
}

export async function patchIncident(id: number, patch: { status?: string; assignee?: string }): Promise<IncidentRow> {
  return request<IncidentRow>(`/incidents/${id}`, { method: 'PATCH', body: JSON.stringify(patch) })
}

export async function incidentEvidence(id: number): Promise<RawEvent[]> {
  return request<RawEvent[]>(`/incidents/${id}/evidence`)
}

export async function incidentActions(id: number): Promise<AnalystAction[]> {
  return request<AnalystAction[]>(`/incidents/${id}/actions`)
}

export async function createAction(id: number, action: string): Promise<AnalystAction> {
  return request<AnalystAction>(`/incidents/${id}/actions`, { method: 'POST', body: JSON.stringify({ action }) })
}

export async function createNote(id: number, text: string): Promise<{ incident_id: number; note: NoteEntry }> {
  return request<{ incident_id: number; note: NoteEntry }>(`/incidents/${id}/notes`, {
    method: 'POST',
    body: JSON.stringify({ text }),
  })
}

// --- admin ----------------------------------------------------------------

export async function adminListUsers(): Promise<AdminAccount[]> {
  return request<AdminAccount[]>('/admin/users')
}

export async function adminCreateUser(username: string, role: 'analyst' | 'admin', password: string): Promise<AdminAccount> {
  return request<AdminAccount>('/admin/users', {
    method: 'POST',
    body: JSON.stringify({ username, role, password }),
  })
}

export async function getThresholds(): Promise<ThresholdsResponse> {
  return request<ThresholdsResponse>('/admin/thresholds')
}

export async function putThresholds(patch: {
  k?: number
  dormancy_days?: number
  band_critical?: number
}): Promise<ThresholdsResponse> {
  return request<ThresholdsResponse>('/admin/thresholds', { method: 'PUT', body: JSON.stringify(patch) })
}
