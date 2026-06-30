import { useState, useEffect } from 'react'
import { useAuth } from '../contexts/AuthContext'
import Sidebar from '../components/Sidebar'
import TopBar from '../components/TopBar'

export default function ServiceStatusPage() {
  const { token } = useAuth()
  const [status, setStatus] = useState(null)
  const [resetting, setResetting] = useState(false)
  const [resetMsg, setResetMsg] = useState('')

  const headers = { Authorization: `Bearer ${token}` }

  useEffect(() => {
    async function poll() {
      const res = await fetch('/api/service/status', { headers })
      if (res.ok) setStatus(await res.json())
    }
    poll()
    const id = setInterval(poll, 10_000)
    return () => clearInterval(id)
  }, [token])

  async function resetBaseline() {
    setResetting(true)
    setResetMsg('')
    try {
      const res = await fetch('/api/device/reset-baseline', { method: 'POST', headers })
      setResetMsg(res.ok ? 'Reset command sent to ESP32.' : 'Failed to send command.')
    } catch {
      setResetMsg('Network error.')
    } finally {
      setResetting(false)
    }
  }

  const alive = status?.recorder === 'up'

  return (
    <div style={{ display: 'flex', height: '100vh', overflow: 'hidden' }}>
      <Sidebar />
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
        <TopBar batchName="Service Status" />

        <div style={{ flex: 1, padding: 24, display: 'flex', flexDirection: 'column', gap: 14 }}>
          {/* Recorder status */}
          <div style={{ background: 'var(--bg-surface)', border: '1px solid var(--bg-border)', borderRadius: 'var(--r-lg)', padding: '22px 24px' }}>
            <div style={{ fontFamily: 'var(--font-data)', fontSize: 9, letterSpacing: '0.12em', textTransform: 'uppercase', color: 'var(--text-muted)', marginBottom: 16 }}>Recorder</div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <span style={{ width: 10, height: 10, borderRadius: '50%', background: alive ? 'var(--active)' : 'var(--slow)', animation: alive ? 'dot-pulse 2s ease-in-out infinite' : 'none' }} />
              <span style={{ fontFamily: 'var(--font-display)', fontSize: 24, fontWeight: 600, color: alive ? 'var(--active)' : 'var(--slow)' }}>
                {status ? status.recorder.toUpperCase() : '…'}
              </span>
            </div>
            {status?.last_reading_lag_s != null && (
              <div style={{ marginTop: 12, fontFamily: 'var(--font-data)', fontSize: 11, color: 'var(--text-muted)' }}>
                Last reading: <span style={{ color: 'var(--text-data)' }}>{status.last_reading_lag_s}s ago</span>
                &nbsp;·&nbsp; Broker: <span style={{ color: 'var(--text-data)' }}>{status.broker}</span>
              </div>
            )}
          </div>

          {/* Baseline reset */}
          <div style={{ background: 'var(--bg-surface)', border: '1px solid var(--bg-border)', borderRadius: 'var(--r-lg)', padding: '22px 24px' }}>
            <div style={{ fontFamily: 'var(--font-data)', fontSize: 9, letterSpacing: '0.12em', textTransform: 'uppercase', color: 'var(--text-muted)', marginBottom: 12 }}>Pressure Baseline</div>
            <div style={{ fontFamily: 'var(--font-ui)', fontSize: 13, color: 'var(--text-secondary)', marginBottom: 16 }}>
              Reset the ESP32's rolling EMA baseline. Use when barometric pressure has shifted significantly or after relocating the sensor.
            </div>
            <button
              onClick={resetBaseline} disabled={resetting}
              style={{ padding: '8px 18px', background: 'transparent', border: '1px solid var(--bg-border)', borderRadius: 'var(--r)', color: 'var(--text-primary)', fontFamily: 'var(--font-ui)', fontSize: 13, cursor: resetting ? 'wait' : 'pointer' }}
            >
              {resetting ? 'Sending…' : 'Reset baseline'}
            </button>
            {resetMsg && (
              <div style={{ marginTop: 10, fontFamily: 'var(--font-data)', fontSize: 11, color: 'var(--active)' }}>{resetMsg}</div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
