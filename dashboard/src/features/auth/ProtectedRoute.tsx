import { Navigate, useLocation } from 'react-router-dom'
import type { ReactNode } from 'react'
import { useAuth } from '../../lib/auth'
import type { Role } from '../../types'

interface Props {
  children: ReactNode
  roles?: Role[]
}

export function ProtectedRoute({ children, roles }: Props) {
  const { user } = useAuth()
  const loc = useLocation()

  if (!user) {
    return <Navigate to="/login" state={{ from: loc.pathname }} replace />
  }
  if (roles && !roles.includes(user.role)) {
    return (
      <div className="state-box error" style={{ margin: 40 }}>
        <div style={{ fontSize: 18 }}>403 — Access denied</div>
        <div className="muted mt-10">
          This area requires the <b>{roles.join(' / ')}</b> role. Your account is <b>{user.role}</b>.
        </div>
      </div>
    )
  }
  return <>{children}</>
}
