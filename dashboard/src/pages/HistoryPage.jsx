import { useState, useEffect } from 'react'
import { useAuth } from '../contexts/AuthContext'
import Sidebar from '../components/Sidebar'
import TopBar from '../components/TopBar'
import { ResponsiveContainer, AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ReferenceDot } from 'recharts'

function fmt(ts) {
  const d = new Date(ts * 1000)
  return `${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}`
}

export default function HistoryPage() {
  const { token } = useAuth()
  const [batches, setBatches] = useState([])
  const [selected, setSelected] = useState(null)
  const [readings, setReadings] = useState([])
  const [loading, setLoading] = useState(false)

  const headers = { Authorization: `Bearer ${token}` }

  useEffect(() => {
    fetch('/api/batches', { headers }).then(r => r.json()).then(data => {
      setBatches(data)
      if (data.length > 0) setSelected(data[0].id)
    })
  }, [])

  useEffect(() => {
    if (!selected) return
    setLoading(true)
    fetch(`/api/batches/${selected}/readings?limit=5000`, { headers })
      .then(r => r.json())
      .then(data => setReadings([...data].reverse()))
      .finally(() => setLoading(false))
  }, [selected])

  const selectedBatch = batches.find(b => b.id === selected)
  const anomalies = readings.filter(r => r.anomaly)

  return (
    <div style={{ display: 'flex', height: '100vh', overflow: 'hidden' }}>
      <Sidebar />
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
        <TopBar batchName={selectedBatch?.name} />

        <div style={{ flex: 1, padding: 24, overflow: 'auto', display: 'flex', flexDirection: 'column', gap: 18 }}>
          {/* Batch selector */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <span style={{ fontFamily: 'var(--font-data)', fontSize: 10, letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--text-muted)' }}>Batch</span>
            <select
              value={selected ?? ''}
              onChange={e => setSelected(Number(e.target.value))}
              style={{ background: 'var(--bg-elevated)', border: '1px solid var(--bg-border)', borderRadius: 'var(--r)', padding: '6px 12px', color: 'var(--text-primary)', fontFamily: 'var(--font-ui)', fontSize: 13, outline: 'none', cursor: 'pointer' }}
            >
              {batches.map(b => (
                <option key={b.id} value={b.id}>{b.name} {b.ended_at ? '(ended)' : '● active'}</option>
              ))}
            </select>
            {selectedBatch && (
              <span style={{ fontFamily: 'var(--font-data)', fontSize: 10, color: 'var(--text-muted)' }}>
                {new Date(selectedBatch.started_at * 1000).toLocaleDateString()}
                {selectedBatch.ended_at ? ` → ${new Date(selectedBatch.ended_at * 1000).toLocaleDateString()}` : ' → now'}
              </span>
            )}
          </div>

          {/* Pressure history chart */}
          <div style={{ background: 'var(--bg-surface)', border: '1px solid var(--bg-border)', borderRadius: 'var(--r-lg)', padding: '20px 22px 14px', flex: 1, display: 'flex', flexDirection: 'column' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 16 }}>
              <span style={{ fontFamily: 'var(--font-data)', fontSize: 9, letterSpacing: '0.12em', textTransform: 'uppercase', color: 'var(--text-muted)' }}>
                Full batch · Headspace Pressure Δ ({readings.length} readings)
              </span>
              {anomalies.length > 0 && (
                <span style={{
                  display: 'inline-flex', alignItems: 'center', gap: 5,
                  padding: '2px 8px', borderRadius: 999,
                  fontFamily: 'var(--font-data)', fontSize: 9,
                  letterSpacing: '0.1em', textTransform: 'uppercase',
                  color: 'var(--anomaly)', background: 'var(--anomaly-glow)', border: '1px solid rgba(226,69,90,0.3)',
                }}>
                  <span style={{ width: 5, height: 5, borderRadius: '50%', background: 'var(--anomaly)', display: 'inline-block' }} />
                  {anomalies.length} anomal{anomalies.length === 1 ? 'y' : 'ies'} detected
                </span>
              )}
            </div>
            {loading ? (
              <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-muted)', fontFamily: 'var(--font-data)', fontSize: 12 }}>
                Loading…
              </div>
            ) : (
              <div style={{ flex: 1, minHeight: 300 }}>
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={readings} margin={{ top: 4, right: 0, bottom: 0, left: 0 }}>
                    <defs>
                      <linearGradient id="histGrad" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor="#C8941F" stopOpacity={0.22}/>
                        <stop offset="100%" stopColor="#C8941F" stopOpacity={0}/>
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="" stroke="var(--bg-border)" vertical={false}/>
                    <XAxis dataKey="received_at" type="number" domain={['dataMin','dataMax']} tickFormatter={fmt} tick={{ fontFamily: 'var(--font-data)', fontSize: 8, fill: 'var(--text-muted)' }} axisLine={false} tickLine={false} interval="preserveStartEnd"/>
                    <YAxis tick={{ fontFamily: 'var(--font-data)', fontSize: 8, fill: 'var(--text-muted)' }} axisLine={false} tickLine={false} width={28}/>
                    <Tooltip contentStyle={{ background: 'var(--bg-elevated)', border: '1px solid var(--bg-border)', borderRadius: 6, fontFamily: 'var(--font-data)', fontSize: 11, color: 'var(--text-primary)' }} labelFormatter={fmt}/>
                    <Area type="monotone" dataKey="delta_pa" stroke="#C8941F" strokeWidth={1.8} dot={false} fill="url(#histGrad)" isAnimationActive={false}/>
                    {anomalies.map(r => (
                      <ReferenceDot
                        key={r.received_at}
                        x={r.received_at} y={r.delta_pa}
                        r={5} fill="var(--anomaly)" stroke="var(--bg-surface)" strokeWidth={1.5}
                        ifOverflow="extendDomain"
                      />
                    ))}
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
