import { bandFor } from '../lib/bands'
import type { RiskBand } from '../types'

interface Props {
  risk?: number | null
  band?: RiskBand
  showValue?: boolean
}

export function RiskBadge({ risk, band, showValue = true }: Props) {
  const resolved = band ?? (risk == null ? 'Low' : bandFor(risk))
  const value = risk == null ? '—' : `${Math.round(risk)}`
  return (
    <span className={`risk-badge band-${resolved} badge-outline ${resolved}`}>
      <span className="dot" />
      {showValue ? `${value} ${resolved}` : resolved}
    </span>
  )
}
