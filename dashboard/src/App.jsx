import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { AuthProvider, useAuth } from './contexts/AuthContext'
import LoginPage from './pages/LoginPage'
import DashboardPage from './pages/DashboardPage'
import HistoryPage from './pages/HistoryPage'
import ServiceStatusPage from './pages/ServiceStatusPage'
import UsersPage from './pages/UsersPage'

function RequireAuth({ children }) {
  const { token } = useAuth()
  return token ? children : <Navigate to="/login" replace />
}

function RequireRole({ role, children }) {
  const { role: userRole } = useAuth()
  const allowed = { admin: ['admin'], operator: ['operator', 'admin'], viewer: ['viewer', 'operator', 'admin'] }
  return allowed[role]?.includes(userRole) ? children : <Navigate to="/" replace />
}

function AppRoutes() {
  const { token } = useAuth()
  return (
    <Routes>
      <Route path="/login" element={token ? <Navigate to="/" replace /> : <LoginPage />} />
      <Route path="/" element={<RequireAuth><DashboardPage /></RequireAuth>} />
      <Route path="/history" element={<RequireAuth><HistoryPage /></RequireAuth>} />
      <Route path="/status" element={<RequireAuth><RequireRole role="operator"><ServiceStatusPage /></RequireRole></RequireAuth>} />
      <Route path="/users" element={<RequireAuth><RequireRole role="admin"><UsersPage /></RequireRole></RequireAuth>} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <AppRoutes />
      </AuthProvider>
    </BrowserRouter>
  )
}
