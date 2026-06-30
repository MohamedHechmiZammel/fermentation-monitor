const STATE = {
  active:   { label: 'Active',   color: 'var(--active)',   bg: 'var(--active-glow)',   border: 'rgba(78,184,124,0.3)',  anim: '2.4s' },
  slow:     { label: 'Slow',     color: 'var(--slow)',     bg: 'var(--slow-glow)',     border: 'rgba(232,144,64,0.3)', anim: '3.5s' },
  finished: { label: 'Finished', color: 'var(--finished)', bg: 'rgba(90,122,140,0.15)', border: 'rgba(90,122,140,0.3)', anim: null  },
}

export default function ActivityBadge({ state = 'finished' }) {
  const s = STATE[state] ?? STATE.finished

  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 5,
      padding: '3px 10px', borderRadius: 999,
      fontFamily: 'var(--font-data)', fontSize: 10,
      letterSpacing: '0.1em', textTransform: 'uppercase',
      color: s.color, background: s.bg, border: `1px solid ${s.border}`,
    }}>
      <span style={{
        width: 6, height: 6, borderRadius: '50%',
        background: s.color, display: 'inline-block',
        animation: s.anim ? `dot-pulse ${s.anim} ease-in-out infinite` : 'none',
      }} />
      {s.label}
    </span>
  )
}
