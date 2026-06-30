import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../contexts/AuthContext'

export default function LoginPage() {
  const { login } = useAuth()
  const navigate = useNavigate()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  async function handleSubmit(e) {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      await login(username, password)
      navigate('/')
    } catch {
      setError('Invalid username or password')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={{
      minHeight: '100vh', display: 'flex', flexDirection: 'column',
      alignItems: 'center', justifyContent: 'center',
      background: 'radial-gradient(ellipse 60% 50% at 50% 0%, rgba(200,148,31,0.06) 0%, transparent 70%), var(--bg-base)',
    }}>
      {/* Brand */}
      <div style={{ textAlign: 'center', marginBottom: 36 }}>
        <svg style={{ display: 'block', margin: '0 auto 16px', width: 48, height: 48 }} viewBox="0 0 48 48" fill="none">
          <circle cx="24" cy="32" r="12" stroke="#C8941F" strokeWidth="1.5" opacity=".4"/>
          <circle cx="24" cy="26" r="9"  stroke="#C8941F" strokeWidth="1.5" opacity=".6"/>
          <circle cx="24" cy="20" r="6"  stroke="#E8A832" strokeWidth="1.5" opacity=".85"/>
          <circle cx="24" cy="16" r="3"  fill="#E8A832"/>
          <line x1="24" y1="44" x2="24" y2="36" stroke="#C8941F" strokeWidth="1.5" opacity=".4"/>
        </svg>
        <div style={{ fontFamily: 'var(--font-display)', fontSize: 28, fontWeight: 600, letterSpacing: '-0.02em' }}>
          Fermentation <em style={{ fontStyle: 'italic', color: 'var(--gold-bright)' }}>Monitor</em>
        </div>
        <div style={{ fontFamily: 'var(--font-data)', fontSize: 11, letterSpacing: '0.12em', textTransform: 'uppercase', color: 'var(--text-muted)', marginTop: 6 }}>
          Batch intelligence system · v0.1
        </div>
      </div>

      {/* Card */}
      <form onSubmit={handleSubmit} style={{
        width: 360, background: 'var(--bg-surface)',
        border: '1px solid var(--bg-border)', borderRadius: 'var(--r-xl)',
        padding: '36px 32px 28px',
      }}>
        <div style={{ width: 32, height: 1, background: 'var(--bg-border)', margin: '0 auto 28px' }} />

        {['Username', 'Password'].map((label, i) => (
          <div key={label} style={{ marginBottom: 14 }}>
            <label style={{ display: 'block', fontFamily: 'var(--font-data)', fontSize: 10, letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--text-secondary)', marginBottom: 7 }}>
              {label}
            </label>
            <input
              type={i === 1 ? 'password' : 'text'}
              value={i === 0 ? username : password}
              onChange={e => i === 0 ? setUsername(e.target.value) : setPassword(e.target.value)}
              autoComplete={i === 0 ? 'username' : 'current-password'}
              required
              style={{
                width: '100%', background: 'var(--bg-elevated)',
                border: '1px solid var(--bg-border)', borderRadius: 'var(--r)',
                padding: '10px 14px', color: 'var(--text-primary)',
                fontFamily: 'var(--font-ui)', fontSize: 14, outline: 'none',
              }}
            />
          </div>
        ))}

        {error && (
          <p style={{ fontFamily: 'var(--font-data)', fontSize: 11, color: 'var(--slow)', marginTop: 8 }}>
            {error}
          </p>
        )}

        <button type="submit" disabled={loading} style={{
          width: '100%', marginTop: 22, padding: 11,
          background: loading ? 'var(--gold-dim)' : 'var(--gold)',
          border: 'none', borderRadius: 'var(--r)',
          color: '#0E0D0A', fontFamily: 'var(--font-ui)', fontSize: 14,
          fontWeight: 600, cursor: loading ? 'wait' : 'pointer',
        }}>
          {loading ? 'Signing in…' : 'Sign in'}
        </button>
      </form>

      <div style={{ marginTop: 24, fontFamily: 'var(--font-data)', fontSize: 10, letterSpacing: '0.06em', color: 'var(--text-muted)' }}>
        offline-first · zero cloud · open source
      </div>
    </div>
  )
}
