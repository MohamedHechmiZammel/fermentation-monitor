const CIRCUMFERENCE = 408.41 // 2π × 65
const MAX_RATE = 20

const STATE_STYLE = {
  active:   { color: 'var(--active)',   anim: 'gauge-pulse 2.4s ease-in-out infinite' },
  slow:     { color: 'var(--slow)',     anim: 'gauge-pulse-slow 3s ease-in-out infinite' },
  finished: { color: 'var(--finished)', anim: 'none' },
}

export default function PulseGauge({ bubbleRate = 0, state = 'finished' }) {
  const fillPct = Math.min(bubbleRate / MAX_RATE, 1)
  const dashOffset = CIRCUMFERENCE * (1 - fillPct)
  const s = STATE_STYLE[state] ?? STATE_STYLE.finished

  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 24 }}>
      <div style={{ fontFamily: 'var(--font-data)', fontSize: 9, letterSpacing: '0.12em', textTransform: 'uppercase', color: 'var(--text-muted)', alignSelf: 'flex-start' }}>
        Fermentation Pulse
      </div>

      <div style={{ position: 'relative', width: 160, height: 160 }}>
        <svg
          viewBox="0 0 160 160"
          width="160" height="160"
          style={{ transform: 'rotate(-90deg)' }}
        >
          {/* Track */}
          <circle cx="80" cy="80" r="65" fill="none" stroke="var(--bg-elevated)" strokeWidth="10" />
          {/* Fill arc */}
          <circle
            cx="80" cy="80" r="65"
            fill="none"
            stroke={s.color}
            strokeWidth="10"
            strokeLinecap="round"
            strokeDasharray={CIRCUMFERENCE}
            strokeDashoffset={dashOffset}
            style={{ transition: 'stroke-dashoffset 1s ease, stroke 0.5s ease', animation: s.anim }}
          />
        </svg>

        {/* Center label */}
        <div style={{
          position: 'absolute', inset: 0,
          display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
          textAlign: 'center',
        }}>
          <div style={{ fontFamily: 'var(--font-data)', fontSize: 10, letterSpacing: '0.14em', textTransform: 'uppercase', color: s.color, marginBottom: 2 }}>
            {state.toUpperCase()}
          </div>
          <div style={{
            fontFamily: 'var(--font-display)', fontSize: 38, fontWeight: 700,
            lineHeight: 1, letterSpacing: '-0.04em', color: 'var(--text-primary)',
            animation: state === 'active' ? 'number-breathe 2.4s ease-in-out infinite' : 'none',
          }}>
            {bubbleRate}
          </div>
          <div style={{ fontFamily: 'var(--font-data)', fontSize: 9, color: 'var(--text-muted)', letterSpacing: '0.05em', marginTop: 2 }}>
            bubbles / 10 min
          </div>
        </div>
      </div>
    </div>
  )
}
