import type { RiskPoint } from '../types'
import { bandFor } from '../lib/bands'

interface Props {
  points: RiskPoint[]
}

export function Timeline({ points }: Props) {
  if (points.length === 0) {
    return <div className="muted">No risk windows stored yet.</div>
  }
  return (
    <div>
      {points.map((p) => (
        <div className="timeline-item" key={p.ts}>
          <span className="timeline-dot" style={{ background: `var(--band-${bandFor(p.risk).toLowerCase()})` }} />
          <div style={{ minWidth: 0 }}>
            <div className="flex-between" style={{ gap: 8 }}>
              <span className="mono faint" style={{ fontSize: 12 }}>
                {p.ts}
              </span>
              <span className="mono" style={{ fontSize: 12 }}>
                {Math.round(p.risk)} / {p.band}
              </span>
            </div>
            <div className="muted mt-4" style={{ fontSize: 12 }}>
              {p.explanation}
            </div>
          </div>
        </div>
      ))}
    </div>
  )
}
