import { useMemo } from 'react'
import { Link } from 'react-router-dom'
import { getOverview } from '../../lib/api'
import { usePolling } from '../../lib/polling'
import { StatCard } from '../../components/StatCard'
import { StateBox } from '../../components/StateBox'
import { RiskBadge } from '../../components/RiskBadge'
import type { Overview as OverviewData, RiskBand } from '../../types'

const REFRESH_MS = 15000

export function Overview() {
  const { data, error, refresh } = usePolling<OverviewData>(getOverview, REFRESH_MS)

  const bands = useMemo(() => {
    if (!data) return []
    return Object.entries(data.by_band)
      .map(([band, count]) => ({ band, count }))
      .sort((a, b) => b.count - a.count)
  }, [data])

  return (
    <>
      <div className="grid grid-4">
        <StatCard
          label="Open incidents"
          value={data?.open_incidents ?? '—'}
          accent={data && data.open_incidents > 0 ? 'danger' : undefined}
          sub={data && data.open_incidents > 0 ? 'require attention' : 'all clear'}
        />
        <StatCard
          label="Open alerts"
          value={data?.open_alerts ?? '—'}
          accent={data && data.open_alerts > 0 ? 'warn' : undefined}
          sub="awaiting review"
        />
        <StatCard
          label="Total open risk"
          value={data != null ? Math.round(data.total_risk) : '—'}
          sub="sum of open incidents"
        />
        <StatCard
          label="Live feed"
          value={<span className="flex" style={{ gap: 6 }}><span className="spinner" /> polling</span>}
          sub="refreshes every 15 s"
        />
      </div>

      {error && (
        <div className="mt-18">
          <StateBox error={error} retry={() => void refresh()} />
        </div>
      )}

      {data && bands.length > 0 && (
        <div className="panel mt-18">
          <div className="panel-title">
            Risk by band
            <span className="muted">open incidents</span>
          </div>
          <div className="flex" style={{ flexWrap: 'wrap' }}>
            {bands.map(({ band, count }) => (
              <div key={band} className="flex" style={{ gap: 8, marginRight: 18 }}>
                <RiskBadge band={band as RiskBand} showValue={false} />
                <span className="mono" style={{ fontSize: 18, fontWeight: 700 }}>
                  {count}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {data && (
        <div className="grid grid-2 mt-18">
          <div className="panel">
            <div className="panel-title">
              Top users at risk
              <Link to="/users" className="muted" style={{ fontSize: 12 }}>
                view all →
              </Link>
            </div>
            <TopTable rows={data.top_users} to={(r) => `/users/${encodeURIComponent(r.entity_ref)}`} />
          </div>
          <div className="panel">
            <div className="panel-title">
              Top entities at risk
              <Link to="/entities" className="muted" style={{ fontSize: 12 }}>
                view all →
              </Link>
            </div>
            <TopTable rows={data.top_entities} to={(r) => `/entities/${encodeURIComponent(r.entity_ref)}`} />
          </div>
        </div>
      )}
    </>
  )
}

function TopTable({ rows, to }: { rows: { entity_ref: string; risk: number }[]; to: (r: { entity_ref: string }) => string }) {
  if (!rows.length) {
    return <div className="muted">No open risk right now.</div>
  }
  return (
    <div className="table-wrap">
      <table>
        <tbody>
          {rows.map((r) => (
            <tr key={r.entity_ref}>
              <td>
                <Link to={to(r)} className="mono">
                  {r.entity_ref}
                </Link>
              </td>
              <td style={{ textAlign: 'right' }}>
                <RiskBadge risk={r.risk} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
