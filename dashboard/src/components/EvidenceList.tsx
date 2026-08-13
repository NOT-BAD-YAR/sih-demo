import type { RawEvent } from '../types'
import { StateBox } from './StateBox'

interface Props {
  events: RawEvent[]
  loading?: boolean
  error?: string | null
}

function summary(e: RawEvent): string {
  const parts: string[] = []
  if (e.actor) parts.push(`actor ${e.actor}`)
  if (e.source_entity) parts.push(`source ${e.source_entity}`)
  if (e.target_entity) parts.push(`target ${e.target_entity}`)
  if (e.peer_entity) parts.push(`peer ${e.peer_entity}`)
  if (e.ip) parts.push(`ip ${e.ip}`)
  if (e.file_path) parts.push(e.file_path)
  return parts.join(' · ') || '—'
}

export function EvidenceList({ events, loading, error }: Props) {
  if (loading || error) {
    return <StateBox loading={loading} error={error} />
  }
  if (events.length === 0) {
    return <div className="muted">No contributing events recorded.</div>
  }
  return (
    <div>
      {events.map((e) => (
        <div className="timeline-item" key={e.event_id}>
          <span
            className="timeline-dot"
            style={{
              background:
                e.severity === 'critical' || e.severity === 'high'
                  ? 'var(--danger)'
                  : e.severity === 'medium'
                    ? 'var(--warn)'
                    : 'var(--accent)',
            }}
          />
          <div style={{ minWidth: 0 }}>
            <div className="flex" style={{ gap: 8, flexWrap: 'wrap' }}>
              <span className="mono" style={{ fontSize: 12 }}>
                {e.event_type}
              </span>
              <span className="tag">{e.entity_id ?? e.entity_type}</span>
              <span className="mono faint" style={{ fontSize: 11 }}>
                {e.ts}
              </span>
            </div>
            <div className="muted mt-4" style={{ fontSize: 12 }}>
              {summary(e)}
            </div>
            <div className="faint mono" style={{ fontSize: 11, marginTop: 2 }}>
              {e.event_id}
            </div>
          </div>
        </div>
      ))}
    </div>
  )
}
