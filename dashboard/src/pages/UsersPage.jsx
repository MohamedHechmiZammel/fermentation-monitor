import { useState, useEffect } from 'react'
import { useAuth } from '../contexts/AuthContext'
import Sidebar from '../components/Sidebar'
import TopBar from '../components/TopBar'

const ROLES = ['viewer', 'operator', 'admin']

export default function UsersPage() {
  const { token } = useAuth()
  const [users, setUsers] = useState([])
  const [form, setForm] = useState({ username: '', password: '', role: 'viewer' })
  const [error, setError] = useState('')

  const headers = { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }

  useEffect(() => {
    fetch('/api/users', { headers }).then(r => r.json()).then(setUsers)
  }, [token])

  async function createUser(e) {
    e.preventDefault()
    setError('')
    const res = await fetch('/api/users', { method: 'POST', headers, body: JSON.stringify(form) })
    if (res.ok) {
      const newUser = await res.json()
      setUsers(prev => [...prev, newUser])
      setForm({ username: '', password: '', role: 'viewer' })
    } else {
      const data = await res.json()
      setError(data.detail || 'Failed to create user')
    }
  }

  async function deactivate(id) {
    const res = await fetch(`/api/users/${id}`, { method: 'PUT', headers, body: JSON.stringify({ is_active: false }) })
    if (res.ok) setUsers(prev => prev.map(u => u.id === id ? { ...u, is_active: false } : u))
  }

  const roleColor = { admin: 'var(--gold-bright)', operator: 'var(--active)', viewer: 'var(--text-secondary)' }

  return (
    <div style={{ display: 'flex', height: '100vh', overflow: 'hidden' }}>
      <Sidebar />
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
        <TopBar batchName="User Management" />

        <div style={{ flex: 1, padding: 24, overflow: 'auto', display: 'flex', flexDirection: 'column', gap: 18 }}>
          {/* User table */}
          <div style={{ background: 'var(--bg-surface)', border: '1px solid var(--bg-border)', borderRadius: 'var(--r-lg)', overflow: 'hidden' }}>
            <div style={{ padding: '16px 20px', borderBottom: '1px solid var(--bg-border)', fontFamily: 'var(--font-data)', fontSize: 9, letterSpacing: '0.12em', textTransform: 'uppercase', color: 'var(--text-muted)' }}>
              Users ({users.length})
            </div>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr>
                  {['Username', 'Role', 'Status', ''].map(h => (
                    <th key={h} style={{ textAlign: 'left', padding: '10px 20px', fontFamily: 'var(--font-data)', fontSize: 9, letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--text-muted)', borderBottom: '1px solid var(--bg-border)' }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {users.map(u => (
                  <tr key={u.id} style={{ borderBottom: '1px solid var(--bg-border)' }}>
                    <td style={{ padding: '12px 20px', fontFamily: 'var(--font-ui)', color: 'var(--text-primary)' }}>{u.username}</td>
                    <td style={{ padding: '12px 20px', fontFamily: 'var(--font-data)', fontSize: 11, color: roleColor[u.role] ?? 'var(--text-secondary)' }}>{u.role}</td>
                    <td style={{ padding: '12px 20px', fontFamily: 'var(--font-data)', fontSize: 11, color: u.is_active ? 'var(--active)' : 'var(--text-muted)' }}>
                      {u.is_active ? 'active' : 'inactive'}
                    </td>
                    <td style={{ padding: '12px 20px' }}>
                      {u.is_active && (
                        <button onClick={() => deactivate(u.id)} style={{ padding: '4px 10px', background: 'transparent', border: '1px solid var(--bg-border)', borderRadius: 'var(--r-sm)', color: 'var(--text-secondary)', fontFamily: 'var(--font-ui)', fontSize: 11, cursor: 'pointer' }}>
                          Deactivate
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Create user form */}
          <div style={{ background: 'var(--bg-surface)', border: '1px solid var(--bg-border)', borderRadius: 'var(--r-lg)', padding: '22px 24px' }}>
            <div style={{ fontFamily: 'var(--font-data)', fontSize: 9, letterSpacing: '0.12em', textTransform: 'uppercase', color: 'var(--text-muted)', marginBottom: 16 }}>Create User</div>
            <form onSubmit={createUser} style={{ display: 'flex', gap: 10, alignItems: 'flex-end', flexWrap: 'wrap' }}>
              {[
                { label: 'Username', key: 'username', type: 'text' },
                { label: 'Password', key: 'password', type: 'password' },
              ].map(({ label, key, type }) => (
                <div key={key}>
                  <div style={{ fontFamily: 'var(--font-data)', fontSize: 9, letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--text-muted)', marginBottom: 6 }}>{label}</div>
                  <input
                    type={type} value={form[key]} required
                    onChange={e => setForm(p => ({ ...p, [key]: e.target.value }))}
                    style={{ background: 'var(--bg-elevated)', border: '1px solid var(--bg-border)', borderRadius: 'var(--r)', padding: '8px 12px', color: 'var(--text-primary)', fontFamily: 'var(--font-ui)', fontSize: 13, outline: 'none', width: 160 }}
                  />
                </div>
              ))}
              <div>
                <div style={{ fontFamily: 'var(--font-data)', fontSize: 9, letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--text-muted)', marginBottom: 6 }}>Role</div>
                <select value={form.role} onChange={e => setForm(p => ({ ...p, role: e.target.value }))}
                  style={{ background: 'var(--bg-elevated)', border: '1px solid var(--bg-border)', borderRadius: 'var(--r)', padding: '8px 12px', color: 'var(--text-primary)', fontFamily: 'var(--font-ui)', fontSize: 13, outline: 'none', cursor: 'pointer' }}>
                  {ROLES.map(r => <option key={r} value={r}>{r}</option>)}
                </select>
              </div>
              <button type="submit" style={{ padding: '8px 18px', background: 'var(--gold)', border: 'none', borderRadius: 'var(--r)', color: '#0E0D0A', fontFamily: 'var(--font-ui)', fontSize: 13, fontWeight: 600, cursor: 'pointer', height: 36 }}>
                Create
              </button>
            </form>
            {error && <div style={{ marginTop: 10, fontFamily: 'var(--font-data)', fontSize: 11, color: 'var(--slow)' }}>{error}</div>}
          </div>
        </div>
      </div>
    </div>
  )
}
