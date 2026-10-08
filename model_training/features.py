"""
features.py -- bubble detection + 2-minute feature aggregation.

This file is the NORMATIVE REFERENCE for firmware/src/bubble_detector.h. Every
operation here is deliberately restricted to things that port 1:1 into
fixed-point C on an ESP32:

  * running sum / boxcar average over a fixed-size circular buffer
  * comparisons and subtractions
  * one division by a compile-time constant (buffer length / window length)
  * one sqrtf() for the standard deviation

No regression fits, no filtering libraries, no transcendental functions beyond
sqrt. `interval_trend` is a half-window mean difference, NOT a least-squares
slope -- that is a deliberate design decision (SPEC.md) to keep the C port
trivial. Do not "improve" it into a polyfit.

--------------------------------------------------------------------------
PIPELINE
--------------------------------------------------------------------------
  raw delta_pa @ 10 Hz
        |
        v  BubbleDetector      boxcar(5) -> peak-tracking drop edge + refractory
   bubble intervals (s)
        |
        v  FeatureAggregator   rolling 16-interval buffer, 120 s emit tick
   [bubble_rate, mean_interval, std_interval, interval_trend, temp_delta]

--------------------------------------------------------------------------
DESIGN NOTES FOR THE C PORT
--------------------------------------------------------------------------
1. The interval buffer is a ROLLING buffer of the last 16 intervals that
   persists ACROSS window boundaries; it is not cleared every 120 s. This is
   required: near the start and end of a fermentation the bubble period is
   200-300 s, so a 120 s window contains 0 or 1 bubbles and a per-window
   buffer would make mean/std/trend undefined. Only `bubble_rate` is reset
   per window. SPEC.md's "the window's interval buffer" is read as "the
   interval buffer as it stands at the window tick".

2. `temp_delta` is measured against a boot-time temperature baseline (the
   mean temperature of the first completed window), mirroring how the
   firmware already treats pressure baseline. It is NOT the temperature
   change within the 120 s window -- over 120 fermentation-seconds the
   envelope moves ~0.005 C, far below the DHT22's 0.1 C resolution.

3. Burst sampling: the firmware duty-cycles its 5-10 Hz sampling. The
   intervals measured here assume the detector sees a CONTINUOUS 10 Hz
   stream while a bubble may fire. If bursts are short and widely spaced,
   drop edges land in the gaps and every interval becomes wrong. Either
   sample continuously inside the feature window, or make the burst
   duty-cycle high enough that the longest gap is << the 3 s minimum
   bubble period.

4. Reset: `reset()` on both objects must be called whenever
   `captureBaseline()` runs, or the baseline step reads as a bubble.
"""

from __future__ import annotations

import math

import numpy as np

# --- detector constants (must be mirrored verbatim in bubble_detector.h) -----
SAMPLE_RATE_HZ = 10.0      # firmware burst rate; top of the 5-10 Hz band
SMOOTH_N = 5               # boxcar length, 0.5 s @ 10 Hz
DROP_THRESHOLD_PA = 6.0    # fall below running peak that counts as a bubble,
                           # and the rise needed to re-arm afterwards.
                           # 6.0 Pa == the full +-3 Pa noise band, so no
                           # sequence of pure sensor noise can ever satisfy it
                           # (a boxcar mean of samples drawn from (-3, +3) also
                           # lies in (-3, +3), so its range is strictly < 6).
REFRACTORY_S = 1.0         # min spacing between bubbles (min real period is 3 s)

# detector states
_RISING = 0
_FALLING = 1

# --- aggregator constants ---------------------------------------------------
WINDOW_S = 120.0           # 2-minute feature window
INTERVAL_BUF_LEN = 16      # rolling interval buffer depth
MIN_INTERVALS = 16         # == INTERVAL_BUF_LEN: emit nothing until the interval
                           # buffer is FULL. Windows built from a partially
                           # filled buffer come from a different statistical
                           # population (mean/std/trend of 4 samples vs 16) and
                           # were measured to be the single largest source of
                           # false-positive reconstruction error -- they alone
                           # pushed the normal p99 from 0.22 to 0.31, above the
                           # stuck-profile error. Cost: no anomaly detection for
                           # the first ~1.2 fermentation hours, when the bubble
                           # period is ~275 s and 16 intervals take that long to
                           # accumulate. That is an acceptable blind spot -- a
                           # batch cannot be diagnosed as stuck before it has
                           # had a chance to start.

FEATURE_NAMES = ("bubble_rate", "mean_interval", "std_interval",
                 "interval_trend", "temp_delta")
N_FEATURES = len(FEATURE_NAMES)


class BubbleDetector:
    """Drop-edge bubble detector -- boxcar + hysteresis peak/trough tracker.

    The pressure stream is boxcar-smoothed, then a two-state machine walks it:

        RISING   peak = max(peak, x). When x has fallen DROP_THRESHOLD_PA below
                 peak, a bubble has fired through the airlock: emit an interval
                 and switch to FALLING.
        FALLING  trough = min(trough, x). Only when x has risen
                 DROP_THRESHOLD_PA back above trough do we re-arm: peak = x,
                 switch to RISING.

    The hysteresis is load-bearing, not decoration. Without it (i.e. simply
    setting peak = x at the moment of firing) the peak is left at a mid-fall
    value; the sawtooth's flat 10 % zero-gap that follows then only needs a
    downward noise excursion to re-trigger, which injected ~30 spurious short
    intervals per normal run and ~90 per stuck run -- enough to wreck
    std_interval and interval_trend for hours of windows at a time.

    With hysteresis and DROP_THRESHOLD_PA == 6.0 (the full +-3 Pa noise band),
    a purely flat noisy stretch can neither fire nor re-arm, so false bubbles
    from sensor noise alone are structurally impossible.
    """

    def __init__(self, sample_rate_hz=SAMPLE_RATE_HZ):
        self.dt = 1.0 / sample_rate_hz
        self.reset()

    def reset(self):
        self._ring = [0.0] * SMOOTH_N
        self._ring_i = 0
        self._ring_sum = 0.0
        self._ring_filled = 0
        self._state = _RISING
        self._peak = 0.0
        self._trough = 0.0
        self._have_peak = False
        self._t = -self.dt
        self._t_last_bubble = 0.0
        self._have_last = False

    def update(self, sample_pa):
        """Feed one raw delta-pressure sample.

        Returns the interval in seconds since the previous bubble when a bubble
        edge is detected and a previous bubble exists, otherwise None. A
        detected first-ever bubble returns None (no interval yet) -- callers
        that need a bubble *count* should use `update_ex`.
        """
        return self.update_ex(sample_pa)[1]

    def update_ex(self, sample_pa):
        """Same as update() but returns (bubble_detected, interval_or_None)."""
        self._t += self.dt

        # --- boxcar (running sum over a circular buffer) ---
        self._ring_sum += sample_pa - self._ring[self._ring_i]
        self._ring[self._ring_i] = sample_pa
        self._ring_i += 1
        if self._ring_i == SMOOTH_N:
            self._ring_i = 0
        if self._ring_filled < SMOOTH_N:
            self._ring_filled += 1
            return (False, None)
        smoothed = self._ring_sum / SMOOTH_N

        # --- hysteresis peak/trough state machine ---
        if not self._have_peak:
            self._peak = smoothed
            self._have_peak = True
            return (False, None)

        if self._state == _FALLING:
            if smoothed < self._trough:
                self._trough = smoothed
            elif (smoothed - self._trough) >= DROP_THRESHOLD_PA:
                self._peak = smoothed
                self._state = _RISING
            return (False, None)

        # _RISING
        if smoothed > self._peak:
            self._peak = smoothed
            return (False, None)
        if (self._peak - smoothed) < DROP_THRESHOLD_PA:
            return (False, None)

        # drop edge
        self._trough = smoothed
        self._state = _FALLING
        if self._have_last and (self._t - self._t_last_bubble) < REFRACTORY_S:
            return (False, None)          # refractory guard (belt-and-braces)
        interval = (self._t - self._t_last_bubble) if self._have_last else None
        self._t_last_bubble = self._t
        self._have_last = True
        return (True, interval)


class FeatureAggregator:
    """Turns bubble intervals + temperature into a feature vector every 120 s."""

    def __init__(self, window_s=WINDOW_S, buf_len=INTERVAL_BUF_LEN):
        self.window_s = window_s
        self.buf_len = buf_len
        self.reset()

    def reset(self):
        self._buf = [0.0] * self.buf_len
        self._head = 0          # next write slot
        self._count = 0         # entries filled, saturates at buf_len
        self._window_bubbles = 0
        self._temp_sum = 0.0
        self._temp_n = 0
        self._temp_baseline = None
        self._next_tick_s = self.window_s

    def push_bubble(self, interval_s):
        """Register one detected bubble; `interval_s` may be None (first ever)."""
        self._window_bubbles += 1
        if interval_s is None:
            return
        self._buf[self._head] = interval_s
        self._head += 1
        if self._head == self.buf_len:
            self._head = 0
        if self._count < self.buf_len:
            self._count += 1

    def push_temp(self, temp_c):
        self._temp_sum += temp_c
        self._temp_n += 1

    def _ordered(self):
        """Buffer contents oldest -> newest."""
        start = self._head - self._count
        return [self._buf[(start + k) % self.buf_len] for k in range(self._count)]

    def maybe_emit(self, t_s):
        """Call once per sample with the current time. Returns a 5-vector or None."""
        if t_s < self._next_tick_s:
            return None
        self._next_tick_s += self.window_s

        mean_temp = (self._temp_sum / self._temp_n) if self._temp_n else 0.0
        self._temp_sum = 0.0
        self._temp_n = 0
        if self._temp_baseline is None:
            self._temp_baseline = mean_temp

        bubbles = self._window_bubbles
        self._window_bubbles = 0

        if self._count < MIN_INTERVALS:
            return None

        vals = self._ordered()
        n = self._count

        # f1: bubbles per minute over the just-closed window
        bubble_rate = bubbles / (self.window_s / 60.0)

        # f2/f3: mean and population std of the rolling interval buffer
        total = 0.0
        for v in vals:
            total += v
        mean_interval = total / n
        sq = 0.0
        for v in vals:
            d = v - mean_interval
            sq += d * d
        std_interval = math.sqrt(sq / n)

        # f4: half-buffer mean difference (NOT a regression slope -- see header)
        half = n // 2
        old_sum = 0.0
        for k in range(half):
            old_sum += vals[k]
        new_sum = 0.0
        for k in range(n - half, n):
            new_sum += vals[k]
        interval_trend = (new_sum - old_sum) / half

        # f5: temperature rise above the boot-time baseline
        temp_delta = mean_temp - self._temp_baseline

        return (bubble_rate, mean_interval, std_interval, interval_trend, temp_delta)


def extract_features(run):
    """Run the detector + aggregator over a datagen run.

    Returns (features, window_t_s):
        features    float64 array, shape (n_windows, 5)
        window_t_s  fermentation-second timestamp of each window's close
    """
    sample_rate = run["sample_rate_hz"]
    dt = 1.0 / sample_rate
    pressure = run["delta_pa"].tolist()
    temps = run["temp_dht"].tolist()

    det = BubbleDetector(sample_rate)
    agg = FeatureAggregator()

    rows = []
    times = []
    t = 0.0
    det_update = det.update_ex
    agg_push_bubble = agg.push_bubble
    agg_push_temp = agg.push_temp
    agg_emit = agg.maybe_emit

    for i in range(len(pressure)):
        fired, interval = det_update(pressure[i])
        if fired:
            agg_push_bubble(interval)
        agg_push_temp(temps[i])
        out = agg_emit(t)
        if out is not None:
            rows.append(out)
            times.append(t)
        t += dt

    if not rows:
        return np.zeros((0, N_FEATURES)), np.zeros(0)
    return np.asarray(rows, dtype=np.float64), np.asarray(times, dtype=np.float64)


def features_for(profile="normal", seed=0, duration_h=None, **kw):
    """Convenience: generate a run and extract its features in one call."""
    from datagen import DEFAULT_DURATION_H, generate_run
    run = generate_run(profile, seed=seed,
                       duration_h=DEFAULT_DURATION_H if duration_h is None else duration_h,
                       **kw)
    return extract_features(run)


def _main():
    import argparse
    import time as _time
    from datagen import PROFILES

    ap = argparse.ArgumentParser(description="Inspect extracted features.")
    ap.add_argument("--profile", choices=PROFILES, default="normal")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--every", type=int, default=50, help="print every Nth window")
    args = ap.parse_args()

    t0 = _time.time()
    feats, times = features_for(args.profile, seed=args.seed)
    dur = _time.time() - t0

    print(f"profile={args.profile} seed={args.seed}  "
          f"{feats.shape[0]} windows in {dur:.1f}s\n")
    print(f"{'t_h':>6} " + " ".join(f"{n:>14}" for n in FEATURE_NAMES))
    for i in range(0, len(feats), args.every):
        print(f"{times[i]/3600:6.2f} " + " ".join(f"{v:14.3f}" for v in feats[i]))
    print("\nper-feature min / mean / max:")
    for j, n in enumerate(FEATURE_NAMES):
        c = feats[:, j]
        print(f"  {n:>14}: {c.min():10.3f} {c.mean():10.3f} {c.max():10.3f}")


if __name__ == "__main__":
    _main()
