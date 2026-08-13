import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { listIncidents, userRisk, entityRisk, ApiError } from '../../lib/api'
import { StateBox } from '../../components/StateBox'
import { RiskBadge } from '../../components/RiskBadge'
import { WhyFlaggedCard } from '../../components/WhyFlaggedCard'
import { Timeline } from '../../components/Timeline'
import { StatusPill } from '../../components/StatusPill'
import type { IncidentRow, RiskDrillDown } from '../../types'

interface Props {
  kind: 'user' | 'entity'
}

export function EntityInvestigation({ kind }: Props) {
  const { id } = useParams()
  const entityId = id ?? ''

  const [drill, setDrill] = useState<RiskDrillDown | null>(null)
  const [incidents, setIncidents] = useState<IncidentRow[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const fetchFn = kind === 'user' ? userRisk : entityRisk

  useEffect(() => {
    let active = true
    setLoading(true)
    setError(null)
    const load = async () => {
      try {
        const d = await fetchFn(entityId)
        if (!active) return
        setDrill(d)
        const all = await listIncidents()
        if (!active) return
        setIncidents(all.filter((i) => (i.entity_ref ?? '').toLowerCase() === entityId.toLowerCase()))
      } catch (e) {
        if (active) setError(e instanceof ApiError ? e.message : 'Request failed')
      } finally {
        if (active) setLoading(false)
      }
    }
    void load()
    return () => {
      active = false
    }
  }, [entityId, kind, fetchFn])

  const baseline = drill?.baseline_snapshot
  const baselineFeatures = baseline ? Object.entries(baseline) : []
  const latest = drill?.history.at(-1)

  if (error) {
    return <StateBox error={error} retry={() => setLoading(true)} />
  }
  if (loading && !drill) {
    return <StateBox loading />
  }
  if (!drill) return null

  return (
    <>
      <div className="flex-between" style={{ marginBottom: 18 }}>
        <div className="flex" style={{ gap: 12 }}>
          <h2 className="mono" style={{ fontSize: 18 }}>
            {entityId}
          </h2>
          <RiskBadge risk={drill.current.risk} />
          <span className="muted" style={{ fontSize: 12 }}>
            {kind === 'user' ? 'person' : 'entity'}
          </span>
        </div>
      </div>

      <div className="grid grid-3">
        <Step n={1} title="Normal behavior (baseline)">
          {baselineFeatures.length ? (
            <div className="tag-list">
              {baselineFeatures.map(([k, s]) => (
                <span className="tag" key={k}>
                  {k} = {s.mean.toFixed(2)} ± {s.std.toFixed(2)}
                </span>
              ))}
            </div>
          ) : (
            <div className="muted">No individual baseline built yet.</div>
          )}
        </Step>

        <Step n={2} title="Current behavior">
          <div className="stat-value" style={{ fontSize: 22 }}>
            {drill.history.length} window{drill.history.length === 1 ? '' : 's'}
          </div>
          <div className="muted" style={{ fontSize: 12 }}>
            last window: {latest ? latest.ts : 'none'}
          </div>
        </Step>

        <Step n={3} title="Deviation (reason)">
          <div style={{ fontSize: 13 }}>{drill.explanation}</div>
        </Step>
      </div>

      <div className="mt-18">
        <WhyFlaggedCard drill={drill} />
      </div>

      <div className="grid grid-2 mt-18">
        <div className="panel">
          <div className="panel-title">Timeline of risk windows</div>
          <Timeline points={drill.history} />
        </div>
        <div className="panel">
          <div className="panel-title">
            Linked incidents
            <span className="muted">{incidents.length}</span>
          </div>
          {incidents.length === 0 ? (
            <div className="muted">No incidents linked to this entity.</div>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>#</th>
                    <th>Status</th>
                    <th>Severity</th>
                    <th style={{ textAlign: 'right' }}>Risk</th>
                  </tr>
                </thead>
                <tbody>
                  {incidents.map((i) => (
                    <tr key={i.id}>
                      <td>
                        <Link to={`/incidents/${i.id}`} className="mono">
                          #{i.id}
                        </Link>
                      </td>
                      <td>
                        <StatusPill status={i.status} />
                      </td>
                      <td>{i.severity ?? '—'}</td>
                      <td style={{ textAlign: 'right' }}>
                        {i.risk != null ? <RiskBadge risk={i.risk} /> : '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </>
  )
}

function Step({ n, title, children }: { n: number; title: string; children: React.ReactNode }) {
  return (
    <div className="panel">
      <div className="panel-title">
        <span className="faint" style={{ fontSize: 12 }}>
          {n}.
        </span>
        {title}
      </div>
      {children}
    </div>
  )
}
