import type { ReactNode } from 'react'

interface Props {
  label: string
  value: ReactNode
  sub?: ReactNode
  accent?: 'ok' | 'warn' | 'danger'
}

export function StatCard({ label, value, sub, accent }: Props) {
  const color =
    accent === 'ok'
      ? 'var(--ok)'
      : accent === 'warn'
        ? 'var(--warn)'
        : accent === 'danger'
          ? 'var(--danger)'
          : undefined
  return (
    <div className="stat-card">
      <div className="stat-label">{label}</div>
      <div className="stat-value" style={color ? { color } : undefined}>
        {value}
      </div>
      {sub && <div className="stat-sub">{sub}</div>}
    </div>
  )
}
