import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { AuthProvider } from '../src/lib/auth'
import { ProtectedRoute } from '../src/features/auth/ProtectedRoute'
import { storeTokens } from '../src/lib/api'

function jwt(role: 'analyst' | 'admin', sub: string) {
  const b64 = btoa(JSON.stringify({ sub, role }))
  return `h.${b64}.s`
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<div>LOGIN PAGE</div>} />
          <Route
            element={
              <ProtectedRoute roles={['analyst', 'admin']}>
                <div>SECURED AREA</div>
              </ProtectedRoute>
            }
          >
            <Route path="/home" element={<div>HOME</div>} />
          </Route>
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  )
}

describe('ProtectedRoute', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('redirects to login when not authenticated', () => {
    renderAt('/home')
    expect(screen.getByText('LOGIN PAGE')).toBeTruthy()
    expect(screen.queryByText('SECURED AREA')).toBeNull()
  })

  it('renders children for an authenticated analyst', () => {
    storeTokens(jwt('analyst', 'bob'), 'r')
    renderAt('/home')
    expect(screen.getByText('SECURED AREA')).toBeTruthy()
  })

  it('shows 403 for a role mismatch', () => {
    storeTokens(jwt('analyst', 'bob'), 'r')
    render(
      <MemoryRouter initialEntries={['/admin']}>
        <AuthProvider>
          <ProtectedRoute roles={['admin']}>
            <div>ADMIN AREA</div>
          </ProtectedRoute>
        </AuthProvider>
      </MemoryRouter>,
    )
    expect(screen.getByText(/403 — Access denied/i)).toBeTruthy()
    expect(screen.queryByText('ADMIN AREA')).toBeNull()
  })
})
