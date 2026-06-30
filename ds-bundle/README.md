# Fermentation Monitor — Design System

## Wrapping and setup

No provider wrapper required. All tokens are CSS custom properties on `:root` via `styles.css`.
Import `styles.css` (it pulls in Google Fonts + `_ds_bundle.css`). Every component works with plain CSS classes — no runtime theming.

```jsx
// Any screen — just set the body background
document.body.style.background = 'var(--bg-base)';
// or via Tailwind: bg-[#131310]
```

## Styling idiom — CSS custom properties

This system uses CSS variables, not utility classes. Style all surfaces and text via the tokens below.
**Never hardcode hex values** — always reference a token so the dark aesthetic stays coherent.

| Token | Value | Use |
|---|---|---|
| `--bg-base` | `#131310` | Page background — dark olive-black |
| `--bg-surface` | `#1B1A13` | Card backgrounds |
| `--bg-elevated` | `#222118` | Input fields, inner surfaces |
| `--bg-border` | `#2C2B1D` | All borders and dividers |
| `--gold` | `#C8941F` | Primary accent — CTAs, active data |
| `--gold-bright` | `#E8A832` | Hero numbers, italic highlights |
| `--active` | `#4EB87C` | Fermentation ACTIVE state |
| `--slow` | `#E89040` | Fermentation SLOW state |
| `--finished` | `#5A7A8C` | Fermentation FINISHED state |
| `--text-primary` | `#EDE8D4` | Body text — warm cream |
| `--text-secondary` | `#A09878` | Secondary labels |
| `--text-muted` | `#504E3A` | Timestamps, hints |

## Typography — three roles

| Role | Family | Use |
|---|---|---|
| `--font-display` | Playfair Display, serif | Batch names, large hero numbers, italic accents |
| `--font-data` | DM Mono, monospace | ALL numeric readings, labels, timestamps, badges |
| `--font-ui` | DM Sans, sans-serif | Buttons, nav, body copy |

## Signature element — PulseGauge

The circular fermentation gauge uses SVG stroke-dasharray to fill proportionally to bubble_rate. The CSS animation class `ds-gauge-arc-active` pulses the glow when state is ACTIVE.

```jsx
const fillPct = Math.min(bubbleRate / 20, 1); // 20 = max expected
const dashOffset = 408.41 * (1 - fillPct);    // circumference = 408.41
```

## Component index

| Group | Component | Description |
|---|---|---|
| Foundation | DesignTokens | Full palette + typography reference |
| Layout | LoginPage | Login screen with airlock brand mark |
| Layout | Dashboard | Full instrument panel — sidebar + chart + gauge + metrics |
| Components | PulseGauge | Circular fermentation pulse (signature element) — active/slow/finished |
| Components | PressureChart | 24h headspace Δ pressure line chart with gradient fill |
| Components | MetricCard | Temperature, humidity, time, and recorder status variants |
| Components | ActivityBadge | Active / Slow / Finished pill badges with pulse animation |

## Idiomatic build snippet

```jsx
// Live pressure hero
<div style={{ fontFamily: 'var(--font-data)', fontSize: 96, color: 'var(--gold-bright)', letterSpacing: '-0.04em' }}>
  {delta_pa.toFixed(1)}
  <span style={{ fontSize: 18, color: 'var(--text-muted)', marginLeft: 8 }}>Pa</span>
</div>

// Card surface
<div style={{ background: 'var(--bg-surface)', border: '1px solid var(--bg-border)', borderRadius: 14, padding: '22px 24px' }}>
  ...
</div>

// Activity badge
<span className="ds-badge ds-badge-active">
  <span className="ds-pulse-dot ds-pulse-dot-active" />Active
</span>
```
