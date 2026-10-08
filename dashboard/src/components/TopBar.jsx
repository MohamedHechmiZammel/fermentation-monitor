import { useAuth } from '../contexts/AuthContext'
import ActivityBadge from './ActivityBadge'
import AnomalyBadge from './AnomalyBadge'

export default function TopBar({ batchName, activity, anomaly, onNewBatch }) {
  const { logout } = useAuth()

  return (
    <header style={{
      height: 56, borderBottom: '1px solid var(--bg-border)',
      display: 'flex', alignItems: 'center', padding: '0 24px', gap: 12, flexShrink: 0,
    }}>
      <span style={{ fontFamily: 'var(--font-display)', fontSize: 18, fontWeight: 600, letterSpacing: '-0.01em' }}>
        {batchName
          ? <>{batchName.split('#')[0].trim()} {batchName.includes('#') && <em style={{ fontStyle: 'italic', color: 'var(--gold-bright)' }}>#{batchName.split('#')[1]}</em>}</>
          : <em style={{ fontStyle: 'italic', color: 'var(--text-muted)' }}>No active batch</em>
        }
      </span>

      {activity && (
        <>
          <div style={{ width: 1, height: 16, background: 'var(--bg-border)' }} />
          <ActivityBadge state={activity} />
        </>
      )}

      {anomaly && <AnomalyBadge active={anomaly} />}

      <div style={{ flex: 1 }} />

      <div style={{ display: 'flex', gap: 8 }}>
        <button onClick={logout} style={{
          height: 30, padding: '0 12px', background: 'transparent',
          border: '1px solid var(--bg-border)', borderRadius: 'var(--r-sm)',
          color: 'var(--text-secondary)', fontFamily: 'var(--font-ui)', fontSize: 12,
          cursor: 'pointer',
        }}>
          Sign out
        </button>
        {onNewBatch && (
          <button onClick={onNewBatch} style={{
            height: 30, padding: '0 12px', background: 'var(--gold)',
            border: '1px solid var(--gold)', borderRadius: 'var(--r-sm)',
            color: '#0E0D0A', fontFamily: 'var(--font-ui)', fontSize: 12,
            fontWeight: 600, cursor: 'pointer',
          }}>
            New batch
          </button>
        )}
      </div>
    </header>
  )
}
