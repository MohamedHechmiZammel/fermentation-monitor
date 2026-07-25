import { useState } from 'react'
import { useAuth } from '../contexts/AuthContext'
import { useReadings, useSummary, useBatches } from '../hooks/useReadings'
import Sidebar from '../components/Sidebar'
import TopBar from '../components/TopBar'
import PulseGauge from '../components/PulseGauge'
import PressureChart from '../components/PressureChart'
import MetricCard from '../components/MetricCard'

function NewBatchModal({ onClose, onCreated, token }) {
  const [name, setName] = useState('')
  const [loading, setLoading] = useState(false)

  async function submit(e) {
    e.preventDefault()
    setLoading(true)
    try {
      const res = await fetch('/api/batches', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: JSON.stringify({ name }),
      })
      if (res.ok) { onCreated(await res.json()); onClose() }
    } finally { setLoading(false) }
  }

  return (
    <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 100 }}>
      <form onSubmit={submit} style={{ background: 'var(--bg-surface)', border: '1px solid var(--bg-border)', borderRadius: 'var(--r-lg)', padding: '28px 32px', width: 360 }}>
        <div style={{ fontFamily: 'var(--font-display)', fontSize: 20, fontWeight: 600, marginBottom: 20 }}>New Batch</div>
        <label style={{ fontFamily: 'var(--font-data)', fontSize: 10, letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--text-muted)', display: 'block', marginBottom: 7 }}>
          Batch name
        </label>
        <input
          value={name} onChange={e => setName(e.target.value)}
          placeholder="e.g. Wheat Beer #1"
          required autoFocus
          style={{ width: '100%', background: 'var(--bg-elevated)', border: '1px solid var(--bg-border)', borderRadius: 'var(--r)', padding: '10px 14px', color: 'var(--text-primary)', fontFamily: 'var(--font-ui)', fontSize: 14, outline: 'none', marginBottom: 20 }}
        />
        <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
          <button type="button" onClick={onClose} style={{ padding: '8px 16px', background: 'transparent', border: '1px solid var(--bg-border)', borderRadius: 'var(--r)', color: 'var(--text-secondary)', fontFamily: 'var(--font-ui)', fontSize: 13, cursor: 'pointer' }}>Cancel</button>
          <button type="submit" disabled={loading} style={{ padding: '8px 16px', background: 'var(--gold)', border: 'none', borderRadius: 'var(--r)', color: '#0E0D0A', fontFamily: 'var(--font-ui)', fontSize: 13, fontWeight: 600, cursor: 'pointer' }}>Create</button>
        </div>
      </form>
    </div>
  )
}

export default function DashboardPage() {
  const { token, role } = useAuth()
  const readings = useReadings()
  const summary = useSummary()
  const [batches, setBatches] = useBatches()
  const [showModal, setShowModal] = useState(false)

  const latest = readings.at(-1)
  const activeBatch = batches.find(b => !b.ended_at)

  const canCreateBatch = role === 'operator' || role === 'admin'

  return (
    <div style={{ display: 'flex', height: '100vh', overflow: 'hidden' }}>
      <Sidebar />

      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        <TopBar
          batchName={activeBatch?.name}
          activity={summary.activity}
          anomaly={summary.anomaly_active}
          onNewBatch={canCreateBatch ? () => setShowModal(true) : undefined}
        />

        <div style={{ flex: 1, padding: 24, display: 'grid', gridTemplateRows: '1fr auto', gap: 18, overflow: 'hidden' }}>
          {/* Top row */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 280px', gap: 18, overflow: 'hidden' }}>
            <PressureChart readings={readings} />

            {/* Gauge card */}
            <div style={{ background: 'var(--bg-surface)', border: '1px solid var(--bg-border)', borderRadius: 'var(--r-lg)', padding: '22px 24px', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 0 }}>
              <PulseGauge bubbleRate={summary.bubble_rate} state={summary.activity} />

              {/* Gauge stats */}
              <div style={{ marginTop: 24, width: '100%', display: 'flex', flexDirection: 'column', gap: 8 }}>
                {[
                  { label: 'threshold', value: '0.5 Pa' },
                  { label: 'peak rate', value: `${Math.max(summary.bubble_rate, 0)} / 10 min` },
                ].map(({ label, value }) => (
                  <div key={label} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderTop: '1px solid var(--bg-border)', paddingTop: 8 }}>
                    <span style={{ fontFamily: 'var(--font-data)', fontSize: 10, color: 'var(--text-muted)' }}>{label}</span>
                    <span style={{ fontFamily: 'var(--font-data)', fontSize: 11, fontWeight: 500, color: 'var(--text-data)' }}>{value}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>

          {/* Metrics strip */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 14 }}>
            <MetricCard label="Temperature" value={latest ? latest.temp_dht.toFixed(1) : '—'} unit="°C" sub="DHT22 ambient" />
            <MetricCard label="Humidity"    value={latest ? Math.round(latest.humidity)  : '—'} unit="%RH" sub={latest ? `${latest.delta_pa > 0 ? '+' : ''}${(latest.humidity - 50).toFixed(0)}% vs 50%` : undefined} />
            <MetricCard label="Fermenting"  value={summary.duration_s} variant="time" sub="since batch start" />
            <MetricCard label="Last reading" value={latest ? `${Math.round((Date.now()/1000 - latest.received_at))}s ago` : '—'} sub={readings.length ? `${readings.length} readings` : 'waiting for data'} />
          </div>
        </div>
      </div>

      {showModal && (
        <NewBatchModal
          token={token}
          onClose={() => setShowModal(false)}
          onCreated={batch => setBatches(prev => [batch, ...prev])}
        />
      )}
    </div>
  )
}
