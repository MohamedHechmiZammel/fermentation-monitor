import { useMemo } from 'react'
import {
  ResponsiveContainer, AreaChart, Area, XAxis, YAxis,
  CartesianGrid, Tooltip,
} from 'recharts'

function fmt(ts) {
  const d = new Date(ts * 1000)
  return `${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}`
}

function CustomTooltip({ active, payload }) {
  if (!active || !payload?.length) return null
  const r = payload[0].payload
  return (
    <div style={{ background: 'var(--bg-elevated)', border: '1px solid var(--bg-border)', borderRadius: 6, padding: '8px 12px' }}>
      <div style={{ fontFamily: 'var(--font-data)', fontSize: 10, color: 'var(--text-muted)', marginBottom: 4 }}>
        {fmt(r.received_at)}
      </div>
      <div style={{ fontFamily: 'var(--font-data)', fontSize: 16, color: 'var(--gold-bright)' }}>
        {r.delta_pa?.toFixed(1)} <span style={{ fontSize: 10, color: 'var(--text-muted)' }}>Pa</span>
      </div>
    </div>
  )
}

export default function PressureChart({ readings }) {
  const latest = readings.at(-1)

  const data = useMemo(() => {
    if (readings.length === 0) return []
    const cutoff = Date.now() / 1000 - 86_400 // last 24h
    return readings.filter(r => r.received_at >= cutoff)
  }, [readings])

  const delta = useMemo(() => {
    if (readings.length < 2) return null
    return readings.at(-1).delta_pa - readings.at(-2).delta_pa
  }, [readings])

  return (
    <div style={{ background: 'var(--bg-surface)', border: '1px solid var(--bg-border)', borderRadius: 'var(--r-lg)', padding: '22px 24px 14px', display: 'flex', flexDirection: 'column', height: '100%' }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', marginBottom: 4 }}>
        <span style={{ fontFamily: 'var(--font-data)', fontSize: 9, letterSpacing: '0.12em', textTransform: 'uppercase', color: 'var(--text-muted)' }}>
          Headspace Pressure Δ
        </span>
        <span style={{ fontFamily: 'var(--font-data)', fontSize: 9, color: 'var(--text-muted)' }}>
          Last 24 h
        </span>
      </div>

      {/* Hero value */}
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 6, marginBottom: 18 }}>
        <span style={{ fontFamily: 'var(--font-data)', fontSize: 28, fontWeight: 500, color: 'var(--gold-bright)', letterSpacing: '-0.03em' }}>
          {latest ? latest.delta_pa.toFixed(1) : '—'}
        </span>
        <span style={{ fontFamily: 'var(--font-data)', fontSize: 11, color: 'var(--text-muted)' }}>
          Pa above baseline
        </span>
        {delta !== null && (
          <span style={{ fontFamily: 'var(--font-data)', fontSize: 11, color: delta >= 0 ? 'var(--active)' : 'var(--slow)', marginLeft: 'auto' }}>
            {delta >= 0 ? '↑' : '↓'} {Math.abs(delta).toFixed(1)} from last
          </span>
        )}
      </div>

      {/* Chart */}
      <div style={{ flex: 1, minHeight: 140 }}>
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={data} margin={{ top: 4, right: 0, bottom: 0, left: 0 }}>
            <defs>
              <linearGradient id="pressureGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%"   stopColor="#C8941F" stopOpacity={0.22} />
                <stop offset="100%" stopColor="#C8941F" stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="" stroke="var(--bg-border)" vertical={false} />
            <XAxis
              dataKey="received_at" type="number"
              domain={['dataMin', 'dataMax']}
              tickFormatter={fmt}
              tick={{ fontFamily: 'var(--font-data)', fontSize: 8, fill: 'var(--text-muted)' }}
              axisLine={false} tickLine={false}
              interval="preserveStartEnd"
            />
            <YAxis
              tick={{ fontFamily: 'var(--font-data)', fontSize: 8, fill: 'var(--text-muted)' }}
              axisLine={false} tickLine={false} width={28}
            />
            <Tooltip content={<CustomTooltip />} />
            <Area
              type="monotone" dataKey="delta_pa"
              stroke="#C8941F" strokeWidth={1.8} dot={false}
              fill="url(#pressureGrad)"
              isAnimationActive={false}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}
