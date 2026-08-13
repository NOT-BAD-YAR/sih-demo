// Risk band mapping (mirrors analytics.risk.band_of: <25 Low, <50 Medium,
// <75 High, else Critical).

import type { RiskBand } from '../types'

export const BAND_LOW_MAX = 25
export const BAND_HIGH = 50
export const BAND_CRITICAL = 75

export function bandFor(risk: number, high = BAND_HIGH, critical = BAND_CRITICAL): RiskBand {
  if (risk < BAND_LOW_MAX) return 'Low'
  if (risk < high) return 'Medium'
  if (risk < critical) return 'High'
  return 'Critical'
}