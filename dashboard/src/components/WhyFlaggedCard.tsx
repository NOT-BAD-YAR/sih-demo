import type { RiskDrillDown } from '../types'
import { RiskBadge } from './RiskBadge'
import { StateBox } from './StateBox'

interface Props {
  drill: RiskDrillDown
  loading?: boolean
  error?: string | null
}

const FEATURE_LABELS: Record<string, string> = {
  volume: 'volume',
  event_count: 'event count',
  active_hours_frac: 'active hours',
  location_count: 'locations',
  location_dist_km: 'location distance',
  new_peer_count: 'new peers',
  fail_rate: 'fail rate',
}

export function WhyFlaggedCard({ drill, loading, error }: Props) {
  if (loading || error) {
    return <StateBox loading={loading} error={error} />
  }
  const latest = drill.history.at(-1)
  const baseline = drill.baseline_snapshot
  const features = baseline ? Object.entries(baseline).slice(0, 7) : []

  return (
    <div className="panel">
      <div className="panel-title">
        Why flagged?
        <RiskBadge risk={drill.current.risk} />
      </div>
      <div className="muted" style={{ fontSize: 12 }}>
        {drill.entity_ref}
      </div>
      <p style={{ margin: '12px 0 4px' }}>{drill.explanation}</p>
      {latest && (
        <div className="faint mt-10" style={{ fontSize: 12 }}>
          Last window ({latest.ts}): <span className="mono">{latest.explanation}</span>
        </div>
      )}
      {features.length > 0 && (
        <>
          <div className="panel-title mt-18">Normal behavior (baseline snapshot)</div>
          <div className="tag-list">
            {features.map(([key, stats]) => (
              <span className="tag" key={key}>
                {FEATURE_LABELS[key] ?? key} ≈ {stats.mean.toFixed(2)} ± {stats.std.toFixed(2)}
                <span className="faint"> ({stats.count})</span>
              </span>
            ))}
          </div>
        </>
      )}
    </div>
  )
}
