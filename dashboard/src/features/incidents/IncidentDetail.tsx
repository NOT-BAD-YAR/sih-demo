import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { Link, useParams } from 'react-router-dom'
import {
  listIncidents,
  patchIncident,
  incidentEvidence,
  incidentActions,
  createAction,
  createNote,
  ApiError,
} from '../../lib/api'
import { StateBox } from '../../components/StateBox'
import { RiskBadge } from '../../components/RiskBadge'
import { StatusPill } from '../../components/StatusPill'
import { EvidenceList } from '../../components/EvidenceList'
import { ActionButtons } from '../../components/ActionButtons'
import { useAuth } from '../../lib/auth'
import type { AnalystAction, IncidentRow, LifecycleStatus, NoteEntry, RawEvent } from '../../types'

const NEXT: Partial<Record<LifecycleStatus, { status: LifecycleStatus; label: string }>> = {
  open: { status: 'assigned', label: 'Assign' },
  assigned: { status: 'investigating', label: 'Investigate' },
  investigating: { status: 'resolved', label: 'Resolve' },
}

export function IncidentDetail() {
  const { id } = useParams()
  const incidentId = Number(id)
  const { user } = useAuth()

  const [incident, setIncident] = useState<IncidentRow | null>(null)
  const [evidence, setEvidence] = useState<RawEvent[]>([])
  const [actions, setActions] = useState<AnalystAction[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [noteText, setNoteText] = useState('')
  const [noteMsg, setNoteMsg] = useState<string | null>(null)

  const loadAll = useCallback(async () => {
    try {
      const all = await listIncidents()
      const inc = all.find((i) => i.id === incidentId)
      if (!inc) throw new ApiError(404, 'Incident not found')
      setIncident(inc)
      const [ev, act] = await Promise.all([incidentEvidence(incidentId), incidentActions(incidentId)])
      setEvidence(ev)
      setActions(act)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Request failed')
    } finally {
      setLoading(false)
    }
  }, [incidentId])

  useEffect(() => {
    setLoading(true)
    void loadAll()
  }, [loadAll])

  async function transition(nextStatus: LifecycleStatus) {
    setBusy(`status:${nextStatus}`)
    try {
      await patchIncident(incidentId, {
        status: nextStatus,
        ...(nextStatus === 'assigned' ? { assignee: user?.username } : {}),
      })
      await loadAll()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Failed')
    } finally {
      setBusy(null)
    }
  }

  async function doAction(action: string) {
    setBusy(`action:${action}`)
    try {
      await createAction(incidentId, action)
      await loadAll()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Failed')
    } finally {
      setBusy(null)
    }
  }

  async function submitNote(e: FormEvent) {
    e.preventDefault()
    if (!noteText.trim()) return
    setBusy('note')
    setNoteMsg(null)
    try {
      await createNote(incidentId, noteText.trim())
      setNoteText('')
      setNoteMsg('Note added')
      await loadAll()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed')
    } finally {
      setBusy(null)
    }
  }

  if (error) {
    return (
      <StateBox
        error={error}
        retry={() => {
          setError(null)
          setLoading(true)
          void loadAll()
        }}
      />
    )
  }
  if (loading && !incident) return <StateBox loading />
  if (!incident) return null

  const notes: NoteEntry[] = incident.notes?.entries ?? []
  const next = NEXT[incident.status]

  return (
    <>
      <div className="flex-between" style={{ marginBottom: 18 }}>
        <div className="flex" style={{ gap: 12, flexWrap: 'wrap' }}>
          <Link to="/incidents" className="muted" style={{ fontSize: 13 }}>
            ← Incidents
          </Link>
          <h2 className="mono" style={{ fontSize: 18 }}>
            #{incident.id}
          </h2>
          <StatusPill status={incident.status} />
          <RiskBadge risk={incident.risk} />
          <span className="muted" style={{ fontSize: 12 }}>
            {incident.severity} severity
          </span>
        </div>
        <div className="flex" style={{ gap: 8 }}>
          {next && (
            <button
              className="btn primary"
              disabled={busy != null}
              onClick={() => void transition(next.status)}
            >
              {busy === `status:${next.status}` ? <span className="spinner" /> : null}
              {next.label}
            </button>
          )}
          {incident.status !== 'false_positive' && incident.status !== 'resolved' && (
            <button
              className="btn danger"
              disabled={busy != null}
              onClick={() => void transition('false_positive')}
            >
              Mark false positive
            </button>
          )}
        </div>
      </div>

      <div className="panel">
        <div className="grid grid-3">
          <div>
            <div className="stat-label">Entity</div>
            <div className="mono mt-4">{incident.entity_ref ?? '—'}</div>
          </div>
          <div>
            <div className="stat-label">Assigned to</div>
            <div className="mono mt-4">{incident.assigned_to ?? 'Unassigned'}</div>
          </div>
          <div>
            <div className="stat-label">Updated by</div>
            <div className="mono mt-4">{incident.updated_by ?? '—'}</div>
          </div>
        </div>
        {incident.entity_chain?.length ? (
          <div className="mt-18">
            <div className="stat-label">Entity chain</div>
            <div className="tag-list mt-4">
              {incident.entity_chain.map((e) => (
                <span className="tag" key={e}>
                  {e}
                </span>
              ))}
            </div>
          </div>
        ) : null}
      </div>

      <div className="grid grid-2 mt-18">
        <div className="panel">
          <div className="panel-title">Simulated response</div>
          <ActionButtons busy={busy?.startsWith('action') ? busy : null} onAction={(a) => void doAction(a)} />
          <div className="panel-title mt-18">Audit trail</div>
          {actions.length === 0 ? (
            <div className="muted">No actions applied yet.</div>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Action</th>
                    <th>Status</th>
                    <th>Actor</th>
                    <th>At</th>
                  </tr>
                </thead>
                <tbody>
                  {actions.map((a) => (
                    <tr key={a.id}>
                      <td className="mono">{a.action}</td>
                      <td>{a.status ?? '—'}</td>
                      <td className="mono faint">{a.actor_user ?? '—'}</td>
                      <td className="faint" style={{ fontSize: 12 }}>
                        {a.created_at ? a.created_at.slice(0, 16).replace('T', ' ') : '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        <div className="panel">
          <div className="panel-title">Analyst notes</div>
          {notes.length ? (
            <div>
              {notes.map((n, idx) => (
                <div className="timeline-item" key={idx}>
                  <span className="timeline-dot" style={{ background: 'var(--accent)' }} />
                  <div style={{ minWidth: 0 }}>
                    <div className="flex" style={{ gap: 8 }}>
                      <span className="mono" style={{ fontSize: 12 }}>
                        {n.by}
                      </span>
                      <span className="faint" style={{ fontSize: 11 }}>
                        {n.at}
                      </span>
                    </div>
                    <div className="muted" style={{ fontSize: 12, marginTop: 2 }}>
                      {n.text}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="muted">No notes yet.</div>
          )}
          <form onSubmit={submitNote} className="mt-18">
            <textarea
              value={noteText}
              onChange={(e) => setNoteText(e.target.value)}
              placeholder="Add an analyst note…"
              aria-label="Add note"
            />
            <div className="flex mt-10" style={{ gap: 8 }}>
              <button type="submit" className="btn primary sm" disabled={busy != null || !noteText.trim()}>
                {busy === 'note' ? <span className="spinner" /> : null}
                Add note
              </button>
              {noteMsg && <span style={{ color: 'var(--ok)', fontSize: 12 }}>{noteMsg}</span>}
            </div>
          </form>
        </div>
      </div>

      <div className="panel mt-18">
        <div className="panel-title">
          Evidence replay
          <span className="muted">{evidence.length} contributing events</span>
        </div>
        <EvidenceList events={evidence} />
      </div>
    </>
  )
}
