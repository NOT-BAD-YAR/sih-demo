import { useState, type FormEvent } from 'react'
import { Navigate, useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from '../../lib/auth'
import { ApiError } from '../../lib/api'

export function Login() {
  const { user, login } = useAuth()
  const navigate = useNavigate()
  const loc = useLocation()
  const from = (loc.state as { from?: string } | null)?.from ?? '/'

  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  if (user) return <Navigate to={from} replace />

  async function submit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    setBusy(true)
    try {
      await login(username.trim(), password)
      navigate(from, { replace: true })
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Login failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div
      style={{
        minHeight: '100vh',
        display: 'grid',
        placeItems: 'center',
        background:
          'radial-gradient(900px 500px at 70% -10%, rgba(25,118,210,0.10), transparent 60%), var(--bg)',
      }}
    >
      <form
        onSubmit={submit}
        className="panel"
        style={{ width: 360, padding: 28 }}
      >
        <div className="brand" style={{ padding: '0 0 16px' }}>
          <div className="brand-dot">IT</div>
          <div>
            <div className="brand-title">Insider Threat UEBA</div>
            <div className="brand-sub">SOC Console</div>
          </div>
        </div>

        <div className="field">
          <label htmlFor="username">Username</label>
          <input
            id="username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="username"
            placeholder="analyst"
            required
          />
        </div>

        <div className="field">
          <label htmlFor="password">Password</label>
          <input
            id="password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            placeholder="••••••••"
            required
          />
        </div>

        {error && (
          <div className="state-box error" role="alert" style={{ padding: 12 }}>
            {error}
          </div>
        )}

        <button type="submit" className="btn primary" disabled={busy} style={{ width: '100%', justifyContent: 'center', marginTop: 6 }}>
          {busy ? <span className="spinner" /> : null}
          {busy ? 'Signing in…' : 'Sign in'}
        </button>

        <div className="faint" style={{ fontSize: 12, marginTop: 18, textAlign: 'center' }}>
          Demo accounts: <span className="mono">analyst / analyst</span> · <span className="mono">admin / admin</span>
        </div>
      </form>
    </div>
  )
}