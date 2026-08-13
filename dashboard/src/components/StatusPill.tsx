import type { LifecycleStatus } from '../types'

const LABELS: Record<LifecycleStatus, string> = {
  open: 'Open',
  assigned: 'Assigned',
  investigating: 'Investigating',
  resolved: 'Resolved',
  false_positive: 'False positive',
}

export function StatusPill({ status }: { status: LifecycleStatus }) {
  return <span className={`status-pill ${status}`}>{LABELS[status] ?? status}</span>
}
