#pragma once

/*
 * bubble_detector.h -- CO2 bubble drop-edge detection + 2-minute feature
 * aggregation.
 *
 * This is a 1:1 port of model_training/features.py (classes BubbleDetector and
 * FeatureAggregator), which is the NORMATIVE REFERENCE. Every constant comes
 * from model_weights.h; nothing here may be "improved", because the anomaly
 * threshold in model_weights.h was calibrated against exactly this behaviour.
 *
 * Notable properties that are load-bearing (see model_training/README.md
 * "Design decisions and deviations"):
 *
 *   * The peak/trough tracker is a two-state HYSTERESIS machine, not a plain
 *     running peak + refractory guard. Without hysteresis the boxcar leaves the
 *     peak at a mid-fall value and the sawtooth's flat 10 % gap re-triggers on
 *     noise (~26 spurious intervals per normal run, measured).
 *   * The interval buffer is a ROLLING buffer that persists ACROSS window
 *     boundaries. Only bubble_rate resets per window.
 *   * Nothing is emitted until the interval buffer is FULL
 *     (AE_MIN_INTERVALS == AE_INTERVAL_BUF_LEN). Partially-filled windows are a
 *     different statistical population and were measured to destroy the
 *     normal/stuck separation outright.
 *   * temp_delta is measured against a boot-time temperature baseline (the mean
 *     temperature of the first completed window), not within the window.
 *
 * Time base: the detector keeps its OWN clock, advanced by exactly `dt` per
 * sample, exactly as features.py does. It is therefore agnostic to whether a
 * sample arrives at real-time 10 Hz (hardware) or is generated faster than
 * real time (SIMULATION's 60x fermentation-time compression) -- the intervals
 * it reports are always in the timebase the samples were generated on.
 *
 * `double` is used for the clock and the statistics accumulators so the
 * arithmetic is bit-comparable with the float64 Python reference. At 10 Hz the
 * software-float cost on an ESP32 is irrelevant.
 *
 * Header-only, no Arduino dependency -- so it can be compiled and diffed
 * against the Python reference on a host (see tools/host_test.c).
 */

#include <math.h>
#include <stdbool.h>
#include <stdint.h>

#include "model_weights.h"

/* ── detector states ─────────────────────────────────────── */
#define BD_RISING   0
#define BD_FALLING  1

/* ── fa_maybe_emit() return codes ────────────────────────── */
#define FA_NO_TICK          0   /* window boundary not reached yet          */
#define FA_TICK_SUPPRESSED  1   /* window closed, interval buffer not full  */
#define FA_EMIT             2   /* window closed, feature vector written    */

/* feature vector indices (order fixed by model_weights.h) */
#define FEAT_BUBBLE_RATE     0
#define FEAT_MEAN_INTERVAL   1
#define FEAT_STD_INTERVAL    2
#define FEAT_INTERVAL_TREND  3
#define FEAT_TEMP_DELTA      4

/* ─────────────────────────────────────────────────────────────
 * BubbleDetector -- boxcar + hysteresis peak/trough state machine
 * ───────────────────────────────────────────────────────────── */
typedef struct {
    double dt;                      /* seconds per sample                   */

    float  ring[AE_SMOOTH_N];       /* boxcar circular buffer               */
    int    ring_i;
    double ring_sum;                /* running sum (avoids O(N) per sample) */
    int    ring_filled;

    int    state;                   /* BD_RISING / BD_FALLING               */
    double peak;
    double trough;
    bool   have_peak;

    double t;                       /* detector clock, seconds              */
    double t_last_bubble;
    bool   have_last;
} BubbleDetector;

static inline void bd_reset(BubbleDetector *d) {
    for (int i = 0; i < AE_SMOOTH_N; i++) d->ring[i] = 0.0f;
    d->ring_i        = 0;
    d->ring_sum      = 0.0;
    d->ring_filled   = 0;
    d->state         = BD_RISING;
    d->peak          = 0.0;
    d->trough        = 0.0;
    d->have_peak     = false;
    d->t             = -d->dt;      /* first update() lands on t == 0       */
    d->t_last_bubble = 0.0;
    d->have_last     = false;
}

static inline void bd_init(BubbleDetector *d, float sample_rate_hz) {
    d->dt = 1.0 / (double)sample_rate_hz;
    bd_reset(d);
}

/* Feed one raw delta-pressure sample.
 *
 * Sets *fired true when a bubble drop edge is detected. When a previous bubble
 * exists, *have_interval is set true and *interval_s holds the seconds since
 * it. The first-ever bubble fires with *have_interval == false.
 *
 * Mirrors features.py:BubbleDetector.update_ex() line for line.             */
static inline void bd_update(BubbleDetector *d, float sample_pa,
                             bool *fired, bool *have_interval,
                             double *interval_s) {
    *fired         = false;
    *have_interval = false;
    *interval_s    = 0.0;

    d->t += d->dt;

    /* --- boxcar (running sum over a circular buffer) --- */
    d->ring_sum += (double)sample_pa - (double)d->ring[d->ring_i];
    d->ring[d->ring_i] = sample_pa;
    d->ring_i++;
    if (d->ring_i == AE_SMOOTH_N) d->ring_i = 0;
    if (d->ring_filled < AE_SMOOTH_N) {
        d->ring_filled++;
        return;
    }
    double smoothed = d->ring_sum / (double)AE_SMOOTH_N;

    /* --- hysteresis peak/trough state machine --- */
    if (!d->have_peak) {
        d->peak      = smoothed;
        d->have_peak = true;
        return;
    }

    if (d->state == BD_FALLING) {
        if (smoothed < d->trough) {
            d->trough = smoothed;
        } else if ((smoothed - d->trough) >= (double)AE_DROP_THRESHOLD_PA) {
            d->peak  = smoothed;         /* re-arm */
            d->state = BD_RISING;
        }
        return;
    }

    /* BD_RISING */
    if (smoothed > d->peak) {
        d->peak = smoothed;
        return;
    }
    if ((d->peak - smoothed) < (double)AE_DROP_THRESHOLD_PA) {
        return;
    }

    /* drop edge */
    d->trough = smoothed;
    d->state  = BD_FALLING;
    if (d->have_last && (d->t - d->t_last_bubble) < (double)AE_REFRACTORY_S) {
        return;                          /* refractory guard (belt-and-braces) */
    }
    if (d->have_last) {
        *have_interval = true;
        *interval_s    = d->t - d->t_last_bubble;
    }
    d->t_last_bubble = d->t;
    d->have_last     = true;
    *fired           = true;
}

/* ─────────────────────────────────────────────────────────────
 * FeatureAggregator -- rolling interval buffer + AE_WINDOW_S emit tick
 * ───────────────────────────────────────────────────────────── */
typedef struct {
    double   buf[AE_INTERVAL_BUF_LEN];   /* rolling interval buffer, seconds */
    int      head;                       /* next write slot                  */
    int      count;                      /* filled entries, saturates        */

    uint32_t window_bubbles;

    double   temp_sum;
    uint32_t temp_n;
    double   temp_baseline;
    bool     have_temp_baseline;

    double   next_tick_s;
} FeatureAggregator;

static inline void fa_reset(FeatureAggregator *a) {
    for (int i = 0; i < AE_INTERVAL_BUF_LEN; i++) a->buf[i] = 0.0;
    a->head               = 0;
    a->count              = 0;
    a->window_bubbles     = 0;
    a->temp_sum           = 0.0;
    a->temp_n             = 0;
    a->temp_baseline      = 0.0;
    a->have_temp_baseline = false;
    a->next_tick_s        = (double)AE_WINDOW_S;
}

/* Register one detected bubble. `have_interval` is false for the first ever. */
static inline void fa_push_bubble(FeatureAggregator *a, bool have_interval,
                                  double interval_s) {
    a->window_bubbles++;
    if (!have_interval) return;
    a->buf[a->head] = interval_s;
    a->head++;
    if (a->head == AE_INTERVAL_BUF_LEN) a->head = 0;
    if (a->count < AE_INTERVAL_BUF_LEN) a->count++;
}

static inline void fa_push_temp(FeatureAggregator *a, float temp_c) {
    a->temp_sum += (double)temp_c;
    a->temp_n++;
}

/* Call once per sample with the detector clock. Returns FA_NO_TICK,
 * FA_TICK_SUPPRESSED or FA_EMIT; on FA_EMIT `out[5]` holds
 * [bubble_rate, mean_interval, std_interval, interval_trend, temp_delta].
 *
 * Mirrors features.py:FeatureAggregator.maybe_emit() line for line.         */
static inline int fa_maybe_emit(FeatureAggregator *a, double t_s,
                                float out[AE_N_INPUT]) {
    if (t_s < a->next_tick_s) return FA_NO_TICK;
    a->next_tick_s += (double)AE_WINDOW_S;

    double mean_temp = (a->temp_n != 0) ? (a->temp_sum / (double)a->temp_n) : 0.0;
    a->temp_sum = 0.0;
    a->temp_n   = 0;
    if (!a->have_temp_baseline) {
        a->temp_baseline      = mean_temp;
        a->have_temp_baseline = true;
    }

    uint32_t bubbles  = a->window_bubbles;
    a->window_bubbles = 0;

    if (a->count < AE_MIN_INTERVALS) return FA_TICK_SUPPRESSED;

    int n = a->count;

    /* buffer contents oldest -> newest */
    double vals[AE_INTERVAL_BUF_LEN];
    int start = a->head - n;
    for (int k = 0; k < n; k++) {
        int idx = start + k;
        while (idx < 0) idx += AE_INTERVAL_BUF_LEN;
        vals[k] = a->buf[idx % AE_INTERVAL_BUF_LEN];
    }

    /* f1: bubbles per minute over the just-closed window */
    double bubble_rate = (double)bubbles / ((double)AE_WINDOW_S / 60.0);

    /* f2/f3: mean and population std of the rolling interval buffer */
    double total = 0.0;
    for (int k = 0; k < n; k++) total += vals[k];
    double mean_interval = total / (double)n;
    double sq = 0.0;
    for (int k = 0; k < n; k++) {
        double dv = vals[k] - mean_interval;
        sq += dv * dv;
    }
    double std_interval = sqrt(sq / (double)n);

    /* f4: half-buffer mean difference (NOT a regression slope) */
    int half = n / 2;
    double old_sum = 0.0;
    for (int k = 0; k < half; k++) old_sum += vals[k];
    double new_sum = 0.0;
    for (int k = n - half; k < n; k++) new_sum += vals[k];
    double interval_trend = (new_sum - old_sum) / (double)half;

    /* f5: temperature rise above the boot-time baseline */
    double temp_delta = mean_temp - a->temp_baseline;

    out[FEAT_BUBBLE_RATE]    = (float)bubble_rate;
    out[FEAT_MEAN_INTERVAL]  = (float)mean_interval;
    out[FEAT_STD_INTERVAL]   = (float)std_interval;
    out[FEAT_INTERVAL_TREND] = (float)interval_trend;
    out[FEAT_TEMP_DELTA]     = (float)temp_delta;
    return FA_EMIT;
}
