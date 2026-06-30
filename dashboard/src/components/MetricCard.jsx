function fmtDuration(s) {
  if (!s) return '—'
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  return `${h}h ${String(m).padStart(2,'0')}m`
}

export default function MetricCard({ label, value, unit, sub, variant = 'default' }) {
  const cardStyle = {
    background: 'var(--bg-surface)', border: '1px solid var(--bg-border)',
    borderRadius: 'var(--r)', padding: '14px 16px',
  }

  const labelStyle = {
    fontFamily: 'var(--font-data)', fontSize: 9, letterSpacing: '0.12em',
    textTransform: 'uppercase', color: 'var(--text-muted)', marginBottom: 7,
  }

  const subStyle = {
    fontFamily: 'var(--font-data)', fontSize: 10, color: 'var(--text-muted)', marginTop: 5,
  }

  if (variant === 'time') {
    return (
      <div style={cardStyle}>
        <div style={labelStyle}>{label}</div>
        <div style={{ fontFamily: 'var(--font-display)', fontStyle: 'italic', fontSize: 22, color: 'var(--gold-bright)' }}>
          {fmtDuration(value)}
        </div>
        {sub && <div style={subStyle}>{sub}</div>}
      </div>
    )
  }

  if (variant === 'status') {
    const alive = value === 'up'
    return (
      <div style={cardStyle}>
        <div style={labelStyle}>{label}</div>
        <div style={{ fontFamily: 'var(--font-data)', fontSize: 12, display: 'flex', alignItems: 'center', gap: 5, color: alive ? 'var(--active)' : 'var(--slow)', lineHeight: 1 }}>
          <span style={{ width: 6, height: 6, borderRadius: '50%', background: alive ? 'var(--active)' : 'var(--slow)', animation: alive ? 'dot-pulse 2s ease-in-out infinite' : 'none' }} />
          {value}
        </div>
        {sub && <div style={subStyle}>{sub}</div>}
      </div>
    )
  }

  return (
    <div style={cardStyle}>
      <div style={labelStyle}>{label}</div>
      <div style={{ fontFamily: 'var(--font-data)', fontSize: 22, fontWeight: 500, color: 'var(--text-primary)', letterSpacing: '-0.03em', lineHeight: 1 }}>
        {value ?? '—'}
        {unit && <span style={{ fontSize: 12, fontWeight: 300, color: 'var(--text-secondary)', marginLeft: 2 }}>{unit}</span>}
      </div>
      {sub && <div style={subStyle}>{sub}</div>}
    </div>
  )
}
