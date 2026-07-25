export default function AnomalyBadge({ active = false }) {
  if (!active) return null

  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 5,
      padding: '3px 10px', borderRadius: 999,
      fontFamily: 'var(--font-data)', fontSize: 10,
      letterSpacing: '0.1em', textTransform: 'uppercase',
      color: 'var(--anomaly)', background: 'var(--anomaly-glow)', border: '1px solid rgba(226,69,90,0.3)',
    }}>
      <span style={{
        width: 6, height: 6, borderRadius: '50%',
        background: 'var(--anomaly)', display: 'inline-block',
        animation: 'dot-pulse 1.2s ease-in-out infinite',
      }} />
      Anomaly
    </span>
  )
}
