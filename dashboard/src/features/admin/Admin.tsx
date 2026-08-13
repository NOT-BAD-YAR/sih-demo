import { useEffect, useState, type FormEvent } from 'react'
import { adminCreateUser, adminListUsers, getThresholds, putThresholds, ApiError } from '../../lib/api'
import { StateBox } from '../../components/StateBox'
import type { AdminAccount, ThresholdsResponse } from '../../types'

type Tab = 'users' | 'thresholds'

const FIELD_KEYS: Record<string, { key: string; label: string }> = {
  RULE_VOLUME_K: { key: 'k', label: 'Volume rule k (k for z-score)' },
  DORMANCY_DAYS: { key: 'dormancy_days', label: 'Dormancy days' },
  RISK_BAND_CRITICAL: { key: 'band_critical', label: 'Critical risk band threshold' },
}

export function Admin() {
  const [tab, setTab] = useState<Tab>('users')

  return (
    <>
      <div className="flex" style={{ gap: 8, marginBottom: 18 }}>
        <button className={`btn${tab === 'users' ? ' primary' : ''}`} onClick={() => setTab('users')}>
          Manage users
        </button>
        <button className={`btn${tab === 'thresholds' ? ' primary' : ''}`} onClick={() => setTab('thresholds')}>
          Thresholds
        </button>
      </div>
      {tab === 'users' ? <ManageUsers /> : <Thresholds />}
    </>
  )
}

function ManageUsers() {
  const [users, setUsers] = useState<AdminAccount[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [msg, setMsg] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const [username, setUsername] = useState('')
  const [role, setRole] = useState<'analyst' | 'admin'>('analyst')
  const [password, setPassword] = useState('')

  async function load() {
    try {
      setUsers(await adminListUsers())
      setError(null)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Failed')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void load()
  }, [])

  async function create(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setMsg(null)
    setError(null)
    try {
      await adminCreateUser(username.trim(), role, password)
      setUsername('')
      setPassword('')
      setMsg(`Account ${username.trim()} created`)
      await load()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="grid grid-2">
      <div className="panel">
        <div className="panel-title">Accounts</div>
        {error ? <StateBox error={error} retry={() => void load()} /> : loading ? <StateBox loading /> : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Username</th>
                  <th>Role</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {users.map((u) => (
                  <tr key={u.id}>
                    <td className="mono">{u.username}</td>
                    <td>
                      <span className={`role-pill${u.role === 'admin' ? ' admin' : ''}`}>{u.role}</span>
                    </td>
                    <td>{u.disabled ? 'disabled' : 'active'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="panel">
        <div className="panel-title">Create analyst account</div>
        <form onSubmit={create}>
          <div className="field">
            <label htmlFor="new-user">Username</label>
            <input id="new-user" value={username} onChange={(e) => setUsername(e.target.value)} required />
          </div>
          <div className="field">
            <label htmlFor="new-role">Role</label>
            <select id="new-role" value={role} onChange={(e) => setRole(e.target.value as 'analyst' | 'admin')}>
              <option value="analyst">analyst</option>
              <option value="admin">admin</option>
            </select>
          </div>
          <div className="field">
            <label htmlFor="new-password">Password (min 4 chars)</label>
            <input
              id="new-password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              minLength={4}
            />
          </div>
          <button type="submit" className="btn primary" disabled={busy}>
            {busy ? <span className="spinner" /> : null}
            Create
          </button>
          {msg && <div style={{ color: 'var(--ok)', fontSize: 12, marginTop: 10 }}>{msg}</div>}
        </form>
      </div>
    </div>
  )
}

function Thresholds() {
  const [settings, setSettings] = useState<ThresholdsResponse['settings'] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [msg, setMsg] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [values, setValues] = useState<Record<string, string>>({})

  async function load() {
    try {
      const r = await getThresholds()
      setSettings(r.settings)
      setValues(Object.fromEntries(Object.entries(r.settings).map(([k, v]) => [k, String(v)])))
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Failed')
    }
  }

  useEffect(() => {
    void load()
  }, [])

  async function save(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setMsg(null)
    try {
      const patch: Record<string, number> = {}
      for (const k of Object.keys(values)) {
        const num = Number(values[k])
        if (!Number.isNaN(num)) patch[FIELD_KEYS[k]?.key ?? k] = num
      }
      const r = await putThresholds(patch)
      setSettings(r.settings)
      setMsg('Thresholds updated')
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="panel">
      <div className="panel-title">Engine thresholds</div>
      {error ? <StateBox error={error} /> : !settings ? (
        <StateBox loading />
      ) : (
        <form onSubmit={save} style={{ maxWidth: 460 }}>
          {Object.keys(FIELD_KEYS).map((key) => (
            <div className="field" key={key}>
              <label htmlFor={key}>{FIELD_KEYS[key].label}</label>
              <input
                id={key}
                type="number"
                value={values[key] ?? ''}
                onChange={(e) => setValues((v) => ({ ...v, [key]: e.target.value }))}
              />
            </div>
          ))}
          <button type="submit" className="btn primary" disabled={busy}>
            {busy ? <span className="spinner" /> : null}
            Save thresholds
          </button>
          {msg && <div style={{ color: 'var(--ok)', fontSize: 12, marginTop: 10 }}>{msg}</div>}
        </form>
      )}
    </div>
  )
}
