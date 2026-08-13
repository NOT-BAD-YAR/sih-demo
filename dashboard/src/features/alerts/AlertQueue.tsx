import { useEffect, useState } from 'react'
import { patchAlert, listAlerts } from '../../lib/api'
import { usePolling } from '../../lib/polling'
import { useAuth } from '../../lib/auth'
import { StateBox } from '../../components/StateBox'
import { RiskBadge } from '../../components/RiskBadge'
import { StatusPill } from '../../components/StatusPill'
import type { AlertRow, LifecycleStatus } from '../../types'

const REFRESH_MS = 20000

const NEXT: Partial<Record<LifecycleStatus, { status: LifecycleStatus; label: string }>> = {
  open: { status: 'assigned', label: 'Assign' },
  assigned: { status: 'investigating', label: 'Investigate' },
  investigating: { status: 'resolved', label: 'Resolve' },
}

export function AlertQueue() {
  const { user } = useAuth()
  const [statusFilter, setStatusFilter] = useState<'' | LifecycleStatus>('')
  const [busy, setBusy] = useState<number | null>(null)
  const [flash, setFlash] = useState<{ id: number; msg: string } | null>(null)

  const { data, error, loading, refresh } = usePolling<AlertRow[]>(
    () => listAlerts(statusFilter || undefined),
    REFRESH_MS,
  )

  useEffect(() => {
    const t = setTimeout(() => void refresh(), 200)
    return () => clearTimeout(t)
  }, [statusFilter, refresh])

  async function advance(alert: AlertRow) {
    const next = NEXT[alert.status]
    if (!next) return
    setBusy(alert.id)
    try {
      await patchAlert(alert.id, {
        status: next.status,
        ...(next.status === 'assigned' ? { assignee: user?.username } : {}),
      })
      setFlash({ id: alert.id, msg: `${alert.entity_ref} → ${next.status}` })
      await refresh()
    } finally {
      setBusy(null)
    }
  }

  async function markFalsePositive(alert: AlertRow) {
    setBusy(alert.id)
    try {
      await patchAlert(alert.id, { status: 'false_positive' })
      setFlash({ id: alert.id, msg: `${alert.entity_ref} marked false positive` })
      await refresh()
    } finally {
      setBusy(null)
    }
  }

  const open = data?.filter((a) => !['resolved', 'false_positive'].includes(a.status)) ?? []
  const closed = data?.filter((a) => ['resolved', 'false_positive'].includes(a.status)) ?? []

  return (
    <>
      <div className="flex" style={{ gap: 12, marginBottom: 18 }}>
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value as '' | LifecycleStatus)}
          aria-label="Filter alerts by status"
        >
          <option value="">All statuses</option>
          <option value="open">open</option>
          <option value="assigned">assigned</option>
          <option value="investigating">investigating</option>
          <option value="resolved">resolved</option>
          <option value="false_positive">false positive</option>
        </select>
        <span className="muted" style={{ fontSize: 12 }}>
          {open.length} open · {closed.length} closed
        </span>
      </div>

      <div className="panel">
        <div className="panel-title">Alert queue</div>
        {error ? (
          <StateBox error={error} retry={() => void refresh()} />
        ) : loading && !data ? (
          <StateBox loading />
        ) : !data?.length ? (
          <StateBox empty emptyText="No alerts match this filter." />
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
                  <th style={{ textAlign: 'right' }}>Risk</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {data.map((a) => (
                  <tr key={a.id}>
                    <td className="mono faint">#{a.id}</td>
                    <td className="mono">{a.entity_ref ?? '—'}</td>
                    <td>{a.severity ?? '—'}</td>
                    <td>
                      <StatusPill status={a.status} />
                    </td>
                    <td className="mono faint">{a.assigned_to ?? '—'}</td>
                    <td style={{ textAlign: 'right' }}>
                      {a.risk != null ? <RiskBadge risk={a.risk} /> : '—'}
                    </td>
                    <td>
                      <div className="flex" style={{ gap: 6 }}>
                        {NEXT[a.status] && (
                          <button
                            className="btn sm"
                            disabled={busy != null}
                            onClick={() => void advance(a)}
                          >
                            {NEXT[a.status]!.label}
                          </button>
                        )}
                        {a.status !== 'false_positive' && a.status !== 'resolved' && (
                          <button
                            className="btn sm danger"
                            disabled={busy != null}
                            onClick={() => void markFalsePositive(a)}
                          >
                            False positive
                          </button>
                        )}
                      </div>
                      {flash?.id === a.id && (
                        <div className="ok mt-4" style={{ color: 'var(--ok)', fontSize: 11 }}>
                          {flash.msg}
                        </div>
                      )}
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
