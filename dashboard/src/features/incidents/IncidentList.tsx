import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { listIncidents } from '../../lib/api'
import { usePolling } from '../../lib/polling'
import { StateBox } from '../../components/StateBox'
import { RiskBadge } from '../../components/RiskBadge'
import { StatusPill } from '../../components/StatusPill'
import type { IncidentRow, LifecycleStatus } from '../../types'

const REFRESH_MS = 20000

export function IncidentList() {
  const navigate = useNavigate()
  const [statusFilter, setStatusFilter] = useState<'' | LifecycleStatus>('')

  const { data, error, loading, refresh } = usePolling<IncidentRow[]>(
    () => listIncidents(statusFilter || undefined),
    REFRESH_MS,
  )

  useEffect(() => {
    const t = setTimeout(() => void refresh(), 200)
    return () => clearTimeout(t)
  }, [statusFilter, refresh])

  return (
    <>
      <div className="flex" style={{ gap: 12, marginBottom: 18 }}>
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value as '' | LifecycleStatus)}
          aria-label="Filter incidents by status"
        >
          <option value="">All statuses</option>
          <option value="open">open</option>
          <option value="assigned">assigned</option>
          <option value="investigating">investigating</option>
          <option value="resolved">resolved</option>
          <option value="false_positive">false positive</option>
        </select>
        <span className="muted" style={{ fontSize: 12 }}>
          {data ? `${data.length} incidents` : ''}
        </span>
      </div>

      <div className="panel">
        <div className="panel-title">
          Incidents
          <span className="muted">click a row to investigate & respond</span>
        </div>
        {error ? (
          <StateBox error={error} retry={() => void refresh()} />
        ) : loading && !data ? (
          <StateBox loading />
        ) : !data?.length ? (
          <StateBox empty emptyText="No incidents match this filter." />
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>#</th>
                  <th>Entity</th>
                  <th>Severity</th>
                  <th>Status</th>
                  <th>Assigned</th>
                  <th>Chain</th>
                  <th style={{ textAlign: 'right' }}>Risk</th>
                </tr>
              </thead>
              <tbody>
                {data.map((i) => (
                  <tr
                    key={i.id}
                    className="clickable"
                    onClick={() => navigate(`/incidents/${i.id}`)}
                  >
                    <td className="mono faint">#{i.id}</td>
                    <td className="mono">{i.entity_ref ?? '—'}</td>
                    <td>{i.severity ?? '—'}</td>
                    <td>
                      <StatusPill status={i.status} />
                    </td>
                    <td className="mono faint">{i.assigned_to ?? '—'}</td>
                    <td className="mono faint">{(i.entity_chain?.length ?? 0)}</td>
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
    </>
  )
}
