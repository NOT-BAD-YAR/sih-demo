// Auth context: user identity, login/logout, role guards.

import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react'
import * as api from './api'
import type { AuthUser, Role } from '../types'

interface AuthContextValue {
  user: AuthUser | null
  login: (username: string, password: string) => Promise<void>
  logout: () => void
  hasRole: (roles: Role[]) => boolean
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(() => {
    const token = api.getAccessToken()
    if (!token) return null
    try {
      return api.decodeUser(token)
    } catch {
      api.clearTokens()
      return null
    }
  })

  const login = useCallback(async (username: string, password: string) => {
    const u = await api.login(username, password)
    setUser(u)
  }, [])

  const logout = useCallback(() => {
    api.logout()
    setUser(null)
  }, [])

  const hasRole = useCallback(
    (roles: Role[]) => (user ? roles.includes(user.role) : false),
    [user],
  )

  const value = useMemo(
    () => ({ user, login, logout, hasRole }),
    [user, login, logout, hasRole],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}