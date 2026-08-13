// Shared API contract types (mirror the Phase 6 FastAPI wire shapes).

export type Role = 'analyst' | 'admin'
export type RiskBand = 'Low' | 'Medium' | 'High' | 'Critical'
export type LifecycleStatus =
  | 'open'
  | 'assigned'
  | 'investigating'
  | 'resolved'
  | 'false_positive'
export type EntityKind = 'device' | 'server' | 'app'

export const RISK_BANDS: RiskBand[] = ['Low', 'Medium', 'High', 'Critical']

export interface LoginResponse {
  access: string
  refresh: string
}

export interface AuthUser {
  username: string
  role: Role
}

export interface TopRisk {
  entity_ref: string
  risk: number
  severity?: string | null
}

export interface Overview {
  total_risk: number
  by_band: Record<string, number>
  top_users: TopRisk[]
  top_entities: TopRisk[]
  open_alerts: number
  open_incidents: number
}

export interface UserSummary {
  id: number
  emp_id: string
  name: string
  department: string
  peer_group_id: number | null
  role: string | null
  sensitivity_tier: string | null
  primary_device_id: string | null
  office_geo: string | null
  last_activity_at: string | null
  created_at: string
  risk: number | null
}

export interface EntitySummary {
  id: number
  entity_id: string
  kind: EntityKind
  owner_user_id: number | null
  location: string | null
  ip: string | null
  risk: number | null
}

export interface RiskPoint {
  ts: string
  risk: number
  band: RiskBand
  explanation: string
}

export interface BaselineStats {
  count: number
  mean: number
  std: number
  min: number
  max: number
}

export interface RiskDrillDown {
  entity_ref: string
  current: { risk: number; band: RiskBand }
  history: RiskPoint[]
  explanation: string
  baseline_snapshot: Record<string, BaselineStats> | null
}

export interface AlertRow {
  id: number
  entity_ref?: string | null
  severity?: string | null
  risk?: number | null
  status: LifecycleStatus
  evidence_refs?: string[] | null
  created_at?: string | null
  updated_at?: string | null
  assigned_to?: string | null
  updated_by?: string | null
}

export interface NoteEntry {
  at: string
  by: string
  text: string
}

export interface IncidentRow {
  id: number
  entity_ref?: string | null
  severity?: string | null
  risk?: number | null
  status: LifecycleStatus
  entity_chain?: string[] | null
  related_alert_ids?: number[] | null
  evidence_refs?: string[] | null
  notes?: { entries?: NoteEntry[] } | null
  created_at?: string | null
  updated_at?: string | null
  assigned_to?: string | null
  updated_by?: string | null
}

export interface RawEvent {
  event_id: string
  ts: string
  event_type: string
  entity_type?: string | null
  entity_id?: string | null
  user_id?: string | null
  severity?: string | null
  actor?: string | null
  source_entity?: string | null
  target_entity?: string | null
  peer_entity?: string | null
  ip?: string | null
  file_path?: string | null
  bytes?: number | null
  [key: string]: unknown
}

export interface AnalystAction {
  id: number
  incident_id?: number | null
  action: string
  actor_user?: string | null
  impact?: unknown
  created_at?: string | null
  status?: string | null
  simulated_state?: unknown
}

export interface AdminAccount {
  id: number
  username: string
  role: Role
  disabled: boolean
}

export interface ThresholdsResponse {
  settings: Record<string, number>
}
