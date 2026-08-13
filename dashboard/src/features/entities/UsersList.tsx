import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { listUsers } from '../../lib/api'
import { usePolling } from '../../lib/polling'
import { StateBox } from '../../components/StateBox'
import { RiskBadge } from '../../components/RiskBadge'
import type { UserSummary } from '../../types'

const REFRESH_MS = 30000

export function UsersList() {
  const navigate = useNavigate()
  const [search, setSearch] = useState('')
  const [dept, setDept] = useState('')

  const { data, error, loading, refresh } = usePolling<UserSummary[]>(
    () => listUsers(search || undefined, dept || undefined),
    REFRESH_MS,
  )

  useEffect(() => {
    const t = setTimeout(() => void refresh(), 300)
    return () => clearTimeout(t)
  }, [search, dept, refresh])

  const departments = data ? [...new Set(data.map((u) => u.department))].sort() : []

  return (
    <>
      <div className="flex" style={{ gap: 12, marginBottom: 18 }}>
        <input
          placeholder="Search by name or employee id…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          style={{ width: 280 }}
          aria-label="Search users"
        />
        <select value={dept} onChange={(e) => setDept(e.target.value)} aria-label="Filter by department">
          <option value="">All departments</option>
          {departments.map((d) => (
            <option key={d} value={d}>
              {d}
            </option>
          ))}
        </select>
        <span className="muted" style={{ fontSize: 12 }}>
          {data ? `${data.length} people` : ''}
        </span>
      </div>

      <div className="panel">
        <div className="panel-title">
          People
          <span className="muted">click a row to open Entity Investigation</span>
        </div>
        {error ? (
          <StateBox error={error} retry={() => void refresh()} />
        ) : loading && !data ? (
          <StateBox loading />
        ) : !data?.length ? (
          <StateBox empty emptyText="No users found." />
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Employee</th>
                  <th>Department</th>
                  <th>Role</th>
                  <th>Sensitivity</th>
                  <th>Office</th>
                  <th>Last activity</th>
                  <th style={{ textAlign: 'right' }}>Risk</th>
                </tr>
              </thead>
              <tbody>
                {data.map((u) => (
                  <tr
                    key={u.emp_id}
                    className="clickable"
                    onClick={() => navigate(`/users/${encodeURIComponent(u.emp_id)}`)}
                  >
                    <td>
                      <div>{u.name}</div>
                      <div className="faint mono" style={{ fontSize: 11 }}>
                        {u.emp_id}
                      </div>
                    </td>
                    <td>{u.department}</td>
                    <td>{u.role ?? '—'}</td>
                    <td>{u.sensitivity_tier ?? '—'}</td>
                    <td>{u.office_geo ?? '—'}</td>
                    <td className="faint" style={{ fontSize: 12 }}>
                      {u.last_activity_at ? u.last_activity_at.slice(0, 16).replace('T', ' ') : '—'}
                    </td>
                    <td style={{ textAlign: 'right' }}>
                      {u.risk != null ? <RiskBadge risk={u.risk} /> : <span className="faint">—</span>}
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
