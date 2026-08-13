const ACTIONS: Array<{ key: string; label: string }> = [
  { key: 'force_mfa', label: 'Force MFA' },
  { key: 'revoke_session', label: 'Revoke session' },
  { key: 'restrict_access', label: 'Restrict access' },
  { key: 'isolate_device', label: 'Isolate device' },
  { key: 'notify_manager', label: 'Notify manager' },
  { key: 'investigate', label: 'Investigate' },
]

interface Props {
  busy?: string | null
  onAction: (action: string) => void
}

export function ActionButtons({ busy, onAction }: Props) {
  return (
    <div className="flex" style={{ flexWrap: 'wrap' }}>
      {ACTIONS.map((a) => (
        <button
          key={a.key}
          className="btn sm"
          disabled={busy != null}
          onClick={() => onAction(a.key)}
        >
          {busy === a.key ? <span className="spinner" /> : null}
          {a.label}
        </button>
      ))}
    </div>
  )
}
