import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { listEntities } from '../../lib/api'
import { usePolling } from '../../lib/polling'
import { StateBox } from '../../components/StateBox'
import { RiskBadge } from '../../components/RiskBadge'
import type { EntityKind, EntitySummary } from '../../types'

const REFRESH_MS = 30000

export function EntitiesList() {
  const navigate = useNavigate()
  const [search, setSearch] = useState('')
  const [kind, setKind] = useState<EntityKind | ''>('')

  const { data, error, loading, refresh } = usePolling<EntitySummary[]>(
    () => listEntities(kind || undefined, search || undefined),
    REFRESH_MS,
  )

  useEffect(() => {
    const t = setTimeout(() => void refresh(), 300)
    return () => clearTimeout(t)
  }, [search, kind, refresh])

  return (
    <>
      <div className="flex" style={{ gap: 12, marginBottom: 18 }}>
        <input
          placeholder="Search device / server / app…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          style={{ width: 280 }}
          aria-label="Search entities"
        />
        <select value={kind} onChange={(e) => setKind(e.target.value as EntityKind | '')} aria-label="Filter by kind">
          <option value="">All kinds</option>
          <option value="device">device</option>
          <option value="server">server</option>
          <option value="app">app</option>
        </select>
        <span className="muted" style={{ fontSize: 12 }}>
          {data ? `${data.length} entities` : ''}
        </span>
      </div>

      <div className="panel">
        <div className="panel-title">
          Entities
          <span className="muted">click a row to open Entity Investigation</span>
        </div>
        {error ? (
          <StateBox error={error} retry={() => void refresh()} />
        ) : loading && !data ? (
          <StateBox loading />
        ) : !data?.length ? (
          <StateBox empty emptyText="No entities found." />
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Entity</th>
                  <th>Kind</th>
                  <th>Location</th>
                  <th>IP</th>
                  <th style={{ textAlign: 'right' }}>Risk</th>
                </tr>
              </thead>
              <tbody>
                {data.map((e) => (
                  <tr
                    key={e.entity_id}
                    className="clickable"
                    onClick={() => navigate(`/entities/${encodeURIComponent(e.entity_id)}`)}
                  >
                    <td className="mono">{e.entity_id}</td>
                    <td>
                      <span className="tag">{e.kind}</span>
                    </td>
                    <td>{e.location ?? '—'}</td>
                    <td className="mono faint">{e.ip ?? '—'}</td>
                    <td style={{ textAlign: 'right' }}>
                      {e.risk != null ? <RiskBadge risk={e.risk} /> : <span className="faint">—</span>}
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
