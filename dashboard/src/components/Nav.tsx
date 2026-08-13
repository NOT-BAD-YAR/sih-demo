import { NavLink } from 'react-router-dom'
import { useAuth } from '../lib/auth'

interface NavLinkDef {
  to: string
  icon: string
  label: string
  end?: boolean
}

const ANALYST_LINKS: NavLinkDef[] = [
  { to: '/', icon: '▤', label: 'Overview', end: true },
  { to: '/users', icon: '👤', label: 'Users' },
  { to: '/entities', icon: '🖥', label: 'Entities' },
  { to: '/alerts', icon: '⚑', label: 'Alerts' },
  { to: '/incidents', icon: '⚠', label: 'Incidents' },
]

const ADMIN_LINKS: NavLinkDef[] = [{ to: '/admin', icon: '⚙', label: 'Admin' }]

export function Nav() {
  const { user, hasRole } = useAuth()
  const links = [...ANALYST_LINKS, ...(hasRole(['admin']) ? ADMIN_LINKS : [])]

  return (
    <aside className="sidebar">
      <div className="brand">
        <div className="brand-dot">IT</div>
        <div>
          <div className="brand-title">Insider Threat</div>
          <div className="brand-sub">UEBA · SOC Console</div>
        </div>
      </div>

      <div className="nav-section">Operations</div>
      {links.map((l) => (
        <NavLink
          key={l.to}
          to={l.to}
          end={l.end}
          className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}
        >
          <span className="nav-icon">{l.icon}</span>
          {l.label}
        </NavLink>
      ))}

      <div className="sidebar-footer">
        Signed in as <span className="mono">{user?.username}</span> · {user?.role}
      </div>
    </aside>
  )
}
