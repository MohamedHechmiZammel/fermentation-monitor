import { NavLink } from 'react-router-dom'
import { useAuth } from '../contexts/AuthContext'

function NavItem({ to, title, children }) {
  return (
    <NavLink to={to} title={title} style={({ isActive }) => ({
      width: 38, height: 38, borderRadius: 'var(--r)',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      color: isActive ? 'var(--gold-bright)' : 'var(--text-muted)',
      background: isActive ? 'var(--gold-glow)' : 'transparent',
      textDecoration: 'none', transition: 'background 0.15s, color 0.15s',
    })}>
      {children}
    </NavLink>
  )
}

export default function Sidebar() {
  const { role } = useAuth()

  return (
    <nav style={{
      width: 60, minHeight: '100vh', background: 'var(--bg-surface)',
      borderRight: '1px solid var(--bg-border)', display: 'flex',
      flexDirection: 'column', alignItems: 'center', padding: '18px 0', gap: 6,
      flexShrink: 0,
    }}>
      {/* Airlock logo mark */}
      <svg style={{ width: 32, height: 32, marginBottom: 14 }} viewBox="0 0 32 32" fill="none">
        <circle cx="16" cy="22" r="7"  stroke="#C8941F" strokeWidth="1.2" opacity=".4"/>
        <circle cx="16" cy="17" r="5"  stroke="#C8941F" strokeWidth="1.2" opacity=".6"/>
        <circle cx="16" cy="13" r="3"  stroke="#E8A832" strokeWidth="1.2" opacity=".9"/>
        <circle cx="16" cy="10" r="1.5" fill="#E8A832"/>
      </svg>

      <div style={{ width: 28, height: 1, background: 'var(--bg-border)', margin: '6px 0' }} />

      <NavItem to="/" title="Dashboard">
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
          <rect x="1" y="1" width="6" height="6" rx="1" stroke="currentColor" strokeWidth="1.3"/>
          <rect x="9" y="1" width="6" height="6" rx="1" stroke="currentColor" strokeWidth="1.3"/>
          <rect x="1" y="9" width="6" height="6" rx="1" stroke="currentColor" strokeWidth="1.3"/>
          <rect x="9" y="9" width="6" height="6" rx="1" stroke="currentColor" strokeWidth="1.3"/>
        </svg>
      </NavItem>

      <NavItem to="/history" title="History">
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
          <polyline points="1,14 5,9 8,11 11,6 15,2" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" strokeLinecap="round"/>
        </svg>
      </NavItem>

      {(role === 'operator' || role === 'admin') && (
        <NavItem to="/status" title="Service Status">
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
            <circle cx="8" cy="8" r="6" stroke="currentColor" strokeWidth="1.3"/>
            <line x1="8" y1="5" x2="8" y2="8" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/>
            <circle cx="8" cy="10.5" r=".8" fill="currentColor"/>
          </svg>
        </NavItem>
      )}

      {role === 'admin' && (
        <NavItem to="/users" title="Users">
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
            <circle cx="8" cy="5" r="3" stroke="currentColor" strokeWidth="1.3"/>
            <path d="M2 14c0-3 2.7-5 6-5s6 2 6 5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/>
          </svg>
        </NavItem>
      )}

      <div style={{ flex: 1 }} />

      <div style={{ width: 30, height: 30, borderRadius: '50%', background: 'var(--bg-elevated)', border: '1px solid var(--bg-border)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: 'var(--font-data)', fontSize: 12, color: 'var(--text-secondary)' }}>
        {role?.[0]?.toUpperCase() ?? '?'}
      </div>
    </nav>
  )
}
