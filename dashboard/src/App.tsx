import { Route, Routes } from 'react-router-dom'
import { AuthProvider } from './lib/auth'
import { Layout } from './components/Layout'
import { ProtectedRoute } from './features/auth/ProtectedRoute'
import { Login } from './features/auth/Login'
import { Overview } from './features/overview/Overview'
import { UsersList } from './features/entities/UsersList'
import { EntitiesList } from './features/entities/EntitiesList'
import { EntityInvestigation } from './features/investigate/EntityInvestigation'
import { AlertQueue } from './features/alerts/AlertQueue'
import { IncidentList } from './features/incidents/IncidentList'
import { IncidentDetail } from './features/incidents/IncidentDetail'
import { Admin } from './features/admin/Admin'

export default function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route
          element={
            <ProtectedRoute>
              <Layout />
            </ProtectedRoute>
          }
        >
          <Route index element={<Overview />} />
          <Route path="/users" element={<UsersList />} />
          <Route path="/users/:id" element={<EntityInvestigation kind="user" />} />
          <Route path="/entities" element={<EntitiesList />} />
          <Route path="/entities/:id" element={<EntityInvestigation kind="entity" />} />
          <Route path="/alerts" element={<AlertQueue />} />
          <Route path="/incidents" element={<IncidentList />} />
          <Route path="/incidents/:id" element={<IncidentDetail />} />
          <Route
            path="/admin"
            element={
              <ProtectedRoute roles={['admin']}>
                <Admin />
              </ProtectedRoute>
            }
          />
        </Route>
      </Routes>
    </AuthProvider>
  )
}