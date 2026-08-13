import { Outlet, useLocation } from 'react-router-dom'
import { Nav } from './Nav'
import { useAuth } from '../lib/auth'

const TITLES: Record<string, { title: string; subtitle: string }> = {
  '/': { title: 'Overview', subtitle: 'Live risk posture across the estate' },
  '/users': { title: 'Users', subtitle: 'People and their current risk' },
  '/entities': { title: 'Entities', subtitle: 'Devices, servers and applications' },
  '/alerts': { title: 'Alert Queue', subtitle: 'Review, assign and close alerts' },
  '/incidents': { title: 'Incidents', subtitle: 'Investigate and respond' },
  '/admin': { title: 'Administration', subtitle: 'Manage analysts and thresholds' },
}

export function Layout() {
  const { user, logout } = useAuth()
  const loc = useLocation()
  const meta = TITLES[loc.pathname] ?? {
    title: 'Entity Investigation',
    subtitle: 'Drill into a single identity',
  }

  return (
    <div className="app-shell">
      <Nav />
      <div className="app-main">
        <header className="topbar">
          <div>
            <div className="page-title">{meta.title}</div>
            <div className="page-subtitle">{meta.subtitle}</div>
          </div>
          <div className="topbar-user">
            <span className={`role-pill${user?.role === 'admin' ? ' admin' : ''}`}>
              {user?.role}
            </span>
            <span className="muted mono">{user?.username}</span>
            <button className="logout-btn" onClick={logout}>
              Sign out
            </button>
          </div>
        </header>
        <main className="app-content">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
