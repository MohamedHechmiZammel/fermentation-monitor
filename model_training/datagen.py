"""
datagen.py -- synthetic fermentation signal generator.

Python port of firmware/src/sim_sensors.h (`Sim::deltaPa`, `Sim::tempDHT`,
`Sim::humidityDHT`, `Sim::_activityEnvelope`), extended with two synthetic
anomaly profiles used as ground truth for the on-device anomaly detector.

--------------------------------------------------------------------------
TIME DOMAIN
--------------------------------------------------------------------------
Everything in this module is expressed in *fermentation time*:

    t_hours        fermentation hours (0 .. ~35 for a full run)
    t_sim_s        fermentation seconds = t_hours * 3600

`sim_sensors.h` maps 1 real minute -> 1 fermentation hour (60x compression)
so a full run is ~35 real minutes inside Wokwi. Feature extraction, the
2-minute aggregation window, and the bubble intervals are all defined in
fermentation seconds, because that is what a real BMP280 on a real airlock
would see. The 60x mapping is a property of the Wokwi harness only.

--------------------------------------------------------------------------
PORTED MATH (identical to sim_sensors.h)
--------------------------------------------------------------------------
    activity           = exp(-((t_hours - 24) / 15)^2)      Gaussian, peak @ h24, FWHM ~25 h
    bubble_period_s    = 3 + (1 - activity) * 297           3 s at peak, 300 s near finish
    cycle_pos          = <sawtooth phase in [0,1)>
    raw_pa             = cycle_pos < 0.9 ? (cycle_pos/0.9)*80*activity : 0
    delta_pa           = raw_pa + U(-3, +3)                 +-3 Pa BMP280 noise
    temp_dht           = 22 + 3  * activity
    humidity           = 58 + 10 * activity
    temp_bmp           = temp_dht - 0.5

--------------------------------------------------------------------------
PHASE MODE -- deliberate, documented deviation
--------------------------------------------------------------------------
The C code computes the sawtooth phase as

    cycle_pos = fmodf(t_sim_s, bubble_period_s) / bubble_period_s

with a *time-varying* period. That is not a phase accumulator: writing
g(t) = t / P(t), drop edges occur whenever frac(g) crosses 0.9, and

    g'(t) = 1/P  -  t * P'(t) / P^2

The second term dominates once t is large, so the observed bubble rate does
NOT equal 1/P. Concretely, at fermentation hour 22 the intended period is
8.2 s but the fmod formulation emits a drop edge every ~0.56 s; and on the
falling limb (t > ~26 h, P' > 0) g'(t) goes negative, so the sawtooth runs
*backwards* -- a slow ramp DOWN with instantaneous jumps UP, i.e. no bubble
drop edges at all. Neither behaviour is physical and neither matches the
code's own comment ("3 s at peak activity, 300 s when nearly finished").

`phase_mode="accumulator"` (the DEFAULT) integrates the phase properly:

    phase += dt / P(t);   cycle_pos = phase - floor(phase)

which reproduces the *intent* of the C code exactly (same envelope, same
period formula, same 90/10 sawtooth, same noise) and yields the documented
3 s..300 s bubble periods.

`phase_mode="fmod"` is the literal transcription of the C line, kept so the
discrepancy stays inspectable (`python datagen.py --compare-phase-modes`).

>>> ACTION FOR THE FIRMWARE PORT (TASKS.md F19): sim_sensors.h must be fixed
>>> to use an accumulated phase, otherwise the on-device features will not
>>> match the features this model was trained on.
"""

from __future__ import annotations

import argparse
import math

import numpy as np

# --- constants ported verbatim from sim_sensors.h ---------------------------
ENVELOPE_PEAK_H = 24.0     # Gaussian centre (fermentation hours)
ENVELOPE_WIDTH_H = 15.0    # Gaussian width  -> FWHM ~25 h
PERIOD_MIN_S = 3.0         # bubble period at activity == 1
PERIOD_SPAN_S = 297.0      # + (1 - activity) * 297  -> 300 s at activity == 0
SAWTOOTH_RISE_FRAC = 0.9   # 90 % rise, 10 % drop
AMPLITUDE_PA = 80.0        # peak-to-peak sawtooth height at activity == 1
NOISE_PA = 3.0             # +-3 Pa BMP280 noise floor
TEMP_BASE_C = 22.0
TEMP_RISE_C = 3.0
HUMID_BASE_PCT = 58.0
HUMID_RISE_PCT = 10.0
TEMP_BMP_OFFSET_C = -0.5

# --- generator defaults -----------------------------------------------------
DEFAULT_DURATION_H = 35.0    # ~35 real minutes under the 60x Wokwi mapping
DEFAULT_SAMPLE_RATE_HZ = 10.0  # top of the firmware's 5-10 Hz burst-sampling band
DHT_NOISE_C = 0.3            # DHT22 accuracy is +-0.5 C; 0.3 C 1-sigma
DHT_RESOLUTION_C = 0.1       # DHT22 reports 0.1 C steps
DHT_HUMID_RESOLUTION_PCT = 0.1

PROFILES = ("normal", "stuck", "contamination")


def activity_envelope(t_hours):
    """Gaussian activity envelope. Verbatim port of Sim::_activityEnvelope()."""
    return np.exp(-(((t_hours - ENVELOPE_PEAK_H) / ENVELOPE_WIDTH_H) ** 2))


def bubble_period_s(activity):
    """Verbatim port: 3 s at peak activity, 300 s near finish."""
    return PERIOD_MIN_S + (1.0 - activity) * PERIOD_SPAN_S


# ---------------------------------------------------------------------------
# Profile -> (activity, period, amplitude) trajectories
# ---------------------------------------------------------------------------

def _normal_trajectory(t_hours, rng, params):
    activity = activity_envelope(t_hours)
    return activity, bubble_period_s(activity), activity


def _stuck_trajectory(t_hours, rng, params):
    """Stuck fermentation: the activity envelope stalls and never recovers.

    The envelope tracks the normal Gaussian until `stall_hour`, then freezes
    at that value for the rest of the run. `stall_hour=0` gives the "never
    rises at all" variant. The bubble period therefore stays long and flat
    instead of collapsing toward 3 s at hour 24.
    """
    stall_hour = params.get("stall_hour", 8.0)
    frozen = np.minimum(t_hours, stall_hour)
    activity = activity_envelope(frozen)
    # a little slow decay after the stall: a stuck batch degasses slightly
    decay = params.get("stall_decay_per_h", 0.004)
    past = np.maximum(t_hours - stall_hour, 0.0)
    activity = activity * np.exp(-decay * past)
    return activity, bubble_period_s(activity), activity


def _contamination_trajectory(t_hours, rng, params):
    """Contamination: erratic, non-monotonic bubble-period jumps.

    A piecewise-constant random multiplier is applied to the bubble period
    (and, more weakly, to the sawtooth amplitude and the thermal envelope).
    Segment boundaries and multipliers are redrawn every 0.3-1.5 fermentation
    hours, so the period wanders up and down instead of tracing the smooth
    Gaussian-driven curve a healthy batch follows.
    """
    seg_min = params.get("segment_min_h", 0.3)
    seg_max = params.get("segment_max_h", 1.5)
    log_span = params.get("log_period_span", 1.6)   # exp(+-1.6) -> 0.20x .. 4.95x
    amp_lo = params.get("amp_lo", 0.35)
    amp_hi = params.get("amp_hi", 1.30)

    total_h = float(t_hours[-1])
    edges = [0.0]
    while edges[-1] < total_h:
        edges.append(edges[-1] + rng.uniform(seg_min, seg_max))
    edges = np.asarray(edges)
    n_seg = len(edges) - 1

    period_mult = np.exp(rng.uniform(-log_span, log_span, size=n_seg))
    amp_mult = rng.uniform(amp_lo, amp_hi, size=n_seg)

    idx = np.clip(np.searchsorted(edges, t_hours, side="right") - 1, 0, n_seg - 1)

    base_activity = activity_envelope(t_hours)
    period = np.clip(bubble_period_s(base_activity) * period_mult[idx],
                     PERIOD_MIN_S, PERIOD_SPAN_S + PERIOD_MIN_S + 100.0)
    amplitude = np.clip(base_activity * amp_mult[idx], 0.0, 1.0)
    # thermal/humidity envelope follows the erratic amplitude, not the Gaussian
    return amplitude, period, amplitude


_TRAJECTORIES = {
    "normal": _normal_trajectory,
    "stuck": _stuck_trajectory,
    "contamination": _contamination_trajectory,
}


# ---------------------------------------------------------------------------
# Sawtooth phase
# ---------------------------------------------------------------------------

def _cycle_pos_accumulator(t_sim_s, period, dt):
    """Proper phase integration: phase += dt / P(t). See module docstring."""
    phase = np.cumsum(dt / period)
    return phase - np.floor(phase)


def _cycle_pos_fmod(t_sim_s, period, dt):
    """Literal transcription of `fmodf(t_sim_s, bubble_period_s) / bubble_period_s`."""
    return np.fmod(t_sim_s, period) / period


# ---------------------------------------------------------------------------
# Run generation
# ---------------------------------------------------------------------------

def generate_run(profile="normal",
                 duration_h=DEFAULT_DURATION_H,
                 sample_rate_hz=DEFAULT_SAMPLE_RATE_HZ,
                 seed=0,
                 phase_mode="accumulator",
                 params=None):
    """Generate one synthetic fermentation run.

    Returns a dict of equal-length float32 arrays sampled at `sample_rate_hz`
    in fermentation seconds:

        t_s        fermentation seconds
        delta_pa   headspace pressure above baseline (what the firmware's
                   bubble detector consumes)
        temp_dht   DHT22 ambient temperature, C
        temp_bmp   BMP280 temperature, C
        humidity   DHT22 relative humidity, %

    plus scalar/array metadata: profile, activity, period_s, sample_rate_hz.
    """
    if profile not in _TRAJECTORIES:
        raise ValueError(f"unknown profile {profile!r}; expected one of {PROFILES}")
    if phase_mode not in ("accumulator", "fmod"):
        raise ValueError("phase_mode must be 'accumulator' or 'fmod'")

    rng = np.random.default_rng(seed)
    params = dict(params or {})

    dt = 1.0 / sample_rate_hz
    n = int(round(duration_h * 3600.0 / dt))
    t_sim_s = np.arange(n, dtype=np.float64) * dt
    t_hours = t_sim_s / 3600.0

    activity, period, amplitude = _TRAJECTORIES[profile](t_hours, rng, params)
    activity = np.broadcast_to(np.asarray(activity, dtype=np.float64), t_hours.shape)
    period = np.broadcast_to(np.asarray(period, dtype=np.float64), t_hours.shape)
    amplitude = np.broadcast_to(np.asarray(amplitude, dtype=np.float64), t_hours.shape)

    if phase_mode == "accumulator":
        cycle_pos = _cycle_pos_accumulator(t_sim_s, period, dt)
    else:
        cycle_pos = _cycle_pos_fmod(t_sim_s, period, dt)

    # Sawtooth: 90 % linear rise, 10 % sharp drop to zero.
    raw = np.where(cycle_pos < SAWTOOTH_RISE_FRAC,
                   (cycle_pos / SAWTOOTH_RISE_FRAC) * AMPLITUDE_PA * amplitude,
                   0.0)
    delta_pa = raw + rng.uniform(-NOISE_PA, NOISE_PA, size=n)

    temp_dht = TEMP_BASE_C + TEMP_RISE_C * activity
    temp_dht = temp_dht + rng.normal(0.0, DHT_NOISE_C, size=n)
    temp_dht = np.round(temp_dht / DHT_RESOLUTION_C) * DHT_RESOLUTION_C

    humidity = HUMID_BASE_PCT + HUMID_RISE_PCT * activity
    humidity = humidity + rng.normal(0.0, DHT_NOISE_C, size=n)
    humidity = np.round(humidity / DHT_HUMID_RESOLUTION_PCT) * DHT_HUMID_RESOLUTION_PCT

    return {
        "profile": profile,
        "seed": seed,
        "sample_rate_hz": float(sample_rate_hz),
        "duration_h": float(duration_h),
        "phase_mode": phase_mode,
        "t_s": t_sim_s.astype(np.float32),
        "delta_pa": delta_pa.astype(np.float32),
        "temp_dht": temp_dht.astype(np.float32),
        "temp_bmp": (temp_dht + TEMP_BMP_OFFSET_C).astype(np.float32),
        "humidity": humidity.astype(np.float32),
        "activity": activity.astype(np.float32),
        "period_s": period.astype(np.float32),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _describe(run):
    d = run["delta_pa"]
    print(f"profile={run['profile']:<14} seed={run['seed']:<3} "
          f"phase={run['phase_mode']:<11} "
          f"n={len(d):>9,}  "
          f"delta_pa[min/mean/max]={d.min():7.2f}/{d.mean():6.2f}/{d.max():7.2f}  "
          f"temp[{run['temp_dht'].min():.1f}..{run['temp_dht'].max():.1f}]C  "
          f"period_s[{run['period_s'].min():.1f}..{run['period_s'].max():.1f}]")


def _compare_phase_modes():
    """Show why the literal fmod transcription of sim_sensors.h is unusable."""
    print("Sawtooth cycles per fermentation hour: intended (1/P) vs the two phase modes\n")
    dt = 1.0 / DEFAULT_SAMPLE_RATE_HZ
    run = generate_run("normal", duration_h=35.0, seed=1)
    t = run["t_s"].astype(np.float64)
    period = run["period_s"].astype(np.float64)
    hours = (t / 3600.0).astype(int)

    intended = np.bincount(hours, weights=dt / period, minlength=35)[:35]
    rows = [("intended 1/P", intended)]
    for mode, fn in (("accumulator", _cycle_pos_accumulator), ("fmod", _cycle_pos_fmod)):
        cp = fn(t, period, dt)
        d = np.diff(cp)
        wraps = np.bincount(hours[1:][np.abs(d) > 0.5], minlength=35)[:35]
        rows.append((mode, wraps.astype(float)))
        if mode == "fmod":
            slope = np.array([np.median(d[(hours[1:] == h) & (np.abs(d) < 0.5)])
                              for h in range(35)])

    hdr = "  " + " ".join(f"h{h:<4d}"[:5] for h in range(0, 35, 2))
    print(hdr)
    for name, vals in rows:
        print(f"  {' '.join(f'{v:<5.0f}' for v in vals[::2])}   <- {name}")
    print("\n  phase slope sign (fmod), every 2 h:")
    print("  " + " ".join(("+" if s > 0 else "-") + "    " for s in slope[::2]))
    print("\n  Reading: the accumulator tracks 1/P exactly. The fmod transcription")
    print("  overshoots massively on the rising limb (~6x the intended cycle rate")
    print("  around hour 22) and its phase slope turns NEGATIVE after ~hour 26, so")
    print("  the sawtooth runs backwards -- a slow ramp DOWN with instantaneous")
    print("  jumps UP, i.e. no bubble drop edges at all on the falling limb.")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", choices=PROFILES + ("all",), default="all")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--duration-h", type=float, default=DEFAULT_DURATION_H)
    ap.add_argument("--sample-rate-hz", type=float, default=DEFAULT_SAMPLE_RATE_HZ)
    ap.add_argument("--phase-mode", choices=("accumulator", "fmod"), default="accumulator")
    ap.add_argument("--compare-phase-modes", action="store_true",
                    help="show why the literal fmod port of sim_sensors.h is unusable")
    ap.add_argument("--save", metavar="NPZ", help="write the generated run(s) to an .npz")
    args = ap.parse_args()

    if args.compare_phase_modes:
        _compare_phase_modes()
        return

    profiles = PROFILES if args.profile == "all" else (args.profile,)
    out = {}
    for p in profiles:
        run = generate_run(p, duration_h=args.duration_h,
                           sample_rate_hz=args.sample_rate_hz,
                           seed=args.seed, phase_mode=args.phase_mode)
        _describe(run)
        if args.save:
            for k in ("t_s", "delta_pa", "temp_dht", "humidity"):
                out[f"{p}_{k}"] = run[k]
    if args.save:
        np.savez_compressed(args.save, **out)
        print(f"\nwrote {args.save}")


if __name__ == "__main__":
    main()
