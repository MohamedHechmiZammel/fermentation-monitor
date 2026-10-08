#pragma once
#ifdef SIMULATION

#include <Arduino.h>
#include <math.h>

#include "model_weights.h"

/*
 * Simulated sensor readings for Wokwi CI testing.
 *
 * ── Time domain ───────────────────────────────────────────────────────────
 * Two clocks are in play and they must not be confused:
 *
 *   real time          Wokwi wall clock, what millis() returns.
 *   fermentation time  what a real BMP280 on a real airlock would see;
 *                      bubble periods of 3-300 s, a 120 s feature window,
 *                      a ~35 hour run.
 *
 * They are related by SIM_TIME_SCALE: 1 real minute = 1 fermentation hour
 * (60x), so a full run plays out in ~35 real minutes. The 60x compression is a
 * property of the Wokwi harness ONLY -- it is not part of the signal.
 *
 * Consequence (model_training/README.md §2): the bubble detector needs a
 * CONTINUOUS 10 Hz stream *in fermentation seconds*, so under 60x compression
 * this module must produce 10 * 60 = 600 samples per real second. Samples are
 * therefore pulled explicitly by main.cpp via Sim::advance(), not derived from
 * millis() inside each getter. Sim keeps its own fermentation clock, advanced
 * by exactly one detector sample period per advance() call.
 *
 * ── Signal model ──────────────────────────────────────────────────────────
 * Verbatim port of model_training/datagen.py (which is itself the port of the
 * original Sim::deltaPa()):
 *
 *   activity        = exp(-((t_hours - 24) / 15)^2)   Gaussian, peak @ h24
 *   bubble_period_s = 3 + (1 - activity) * 297        3 s peak, 300 s finish
 *   phase          += dt / bubble_period_s            <-- SEE PHASE FIX BELOW
 *   cycle_pos       = phase - floor(phase)
 *   raw_pa          = cycle_pos < 0.9 ? (cycle_pos/0.9)*80*amplitude : 0
 *   delta_pa        = raw_pa + U(-3, +3)
 *   temp_dht        = 22 + 3  * activity   (quantized to the DHT22's 0.1 C)
 *   humidity        = 58 + 10 * activity   (quantized to 0.1 %)
 *   temp_bmp        = temp_dht - 0.5
 *
 * ── PHASE ACCUMULATOR FIX (model_training/README.md §1) ───────────────────
 * The original code computed the sawtooth phase as
 *
 *     cycle_pos = fmodf(t_sim_s, bubble_period_s) / bubble_period_s;
 *
 * with a TIME-VARYING period. That is not a phase accumulator. Writing
 * g(t) = t / P(t), drop edges occur when frac(g) crosses 0.9, and
 *
 *     g'(t) = 1/P  -  t * P'(t) / P^2
 *
 * The second term dominates once t is large: at fermentation hour 22 the
 * intended period is 8.2 s but the fmod form emits an edge every ~0.56 s, and
 * after ~hour 26 the phase slope goes NEGATIVE, so the sawtooth runs backwards
 * (a slow ramp down with instantaneous jumps up) and contains no bubble drop
 * edges at all. Neither behaviour is physical.
 *
 * The phase is now properly integrated (`_phase += dt / P(t)`), matching
 * datagen.py's default phase_mode="accumulator" -- same envelope, same period
 * formula, same 90/10 sawtooth, same +-3 Pa noise, correct phase. This is
 * required: the anomaly threshold in model_weights.h was calibrated against
 * the accumulator distribution.
 *
 * ── SIM_PROFILE ───────────────────────────────────────────────────────────
 * -D SIM_PROFILE=NORMAL | STUCK | CONTAMINATED (default NORMAL when absent, so
 * builds without the flag behave as before). Profile parameters mirror
 * datagen.py's _normal_trajectory / _stuck_trajectory /
 * _contamination_trajectory exactly.
 *
 * GROUND-TRUTH CAVEAT: STUCK and CONTAMINATED are synthetic inventions of this
 * project (see model_training/README.md "Limitations"), not real defect data.
 */

/* ── profile selection ───────────────────────────────────── */
#define SIM_PROFILE_NORMAL        0
#define SIM_PROFILE_STUCK         1
#define SIM_PROFILE_CONTAMINATED  2

#ifndef SIM_PROFILE
#define SIM_PROFILE NORMAL
#endif

#define _SIM_CAT2(a, b) a##b
#define _SIM_CAT(a, b)  _SIM_CAT2(a, b)
#define SIM_PROFILE_ID  _SIM_CAT(SIM_PROFILE_, SIM_PROFILE)

namespace Sim {

/* ── constants (verbatim from datagen.py) ────────────────── */
static const float ENVELOPE_PEAK_H     = 24.0f;
static const float ENVELOPE_WIDTH_H    = 15.0f;
static const float PERIOD_MIN_S        = 3.0f;
static const float PERIOD_SPAN_S       = 297.0f;
static const float SAWTOOTH_RISE_FRAC  = 0.9f;
static const float AMPLITUDE_PA        = 80.0f;
static const float TEMP_BASE_C         = 22.0f;
static const float TEMP_RISE_C         = 3.0f;
static const float HUMID_BASE_PCT      = 58.0f;
static const float HUMID_RISE_PCT      = 10.0f;
static const float TEMP_BMP_OFFSET_C   = -0.5f;
static const float DHT_RESOLUTION_C    = 0.1f;

/* stuck profile */
static const float STALL_HOUR          = 8.0f;
static const float STALL_DECAY_PER_H   = 0.004f;
/* contamination profile */
static const float SEG_MIN_H           = 0.3f;
static const float SEG_MAX_H           = 1.5f;
static const float LOG_PERIOD_SPAN     = 1.6f;
static const float AMP_LO              = 0.35f;
static const float AMP_HI              = 1.30f;

/* 1 real minute == 1 fermentation hour */
static const float  SIM_TIME_SCALE     = 60.0f;
/* fermentation seconds per generated sample */
static const double SIM_DT_S           = 1.0 / (double)AE_SAMPLE_RATE_HZ;
/* generated samples per real millisecond */
static const double SIM_SAMPLES_PER_MS = (double)AE_SAMPLE_RATE_HZ *
                                         (double)SIM_TIME_SCALE / 1000.0;

/* ── state ───────────────────────────────────────────────── */
static uint32_t _nsamples  = 0;      /* samples generated since boot        */
static double   _phase     = 0.0;    /* sawtooth phase accumulator          */
static float    _delta_pa  = 0.0f;   /* most recent sample                  */
static float    _activity  = 0.0f;   /* drives temperature / humidity       */
#if SIM_PROFILE_ID == SIM_PROFILE_CONTAMINATED
/* contamination segment state */
static float    _seg_end_h    = 0.0f;
static float    _period_mult  = 1.0f;
static float    _amp_mult     = 1.0f;

static inline float _urandf(float lo, float hi) {
    return lo + (hi - lo) * ((float)random(0, 1000000) / 1000000.0f);
}
#endif

static inline float _activityEnvelope(float t_hours) {
    float u = (t_hours - ENVELOPE_PEAK_H) / ENVELOPE_WIDTH_H;
    return expf(-(u * u));
}

static inline float _bubblePeriodS(float activity) {
    return PERIOD_MIN_S + (1.0f - activity) * PERIOD_SPAN_S;
}

static inline float _clampf(float v, float lo, float hi) {
    return v < lo ? lo : (v > hi ? hi : v);
}

/* Fermentation seconds elapsed in the simulated run. */
static inline double simSeconds() {
    return (double)_nsamples * SIM_DT_S;
}

/* Generate exactly one delta-pressure sample, advancing the sim clock by one
 * detector sample period (0.1 fermentation seconds). */
static inline void advance() {
    float t_h = (float)(simSeconds() / 3600.0);

    float activity, period, amplitude;

#if SIM_PROFILE_ID == SIM_PROFILE_STUCK
    /* Activity envelope freezes at STALL_HOUR, then decays slowly. */
    float frozen = (t_h < STALL_HOUR) ? t_h : STALL_HOUR;
    float past   = (t_h > STALL_HOUR) ? (t_h - STALL_HOUR) : 0.0f;
    activity  = _activityEnvelope(frozen) * expf(-STALL_DECAY_PER_H * past);
    period    = _bubblePeriodS(activity);
    amplitude = activity;

#elif SIM_PROFILE_ID == SIM_PROFILE_CONTAMINATED
    /* Piecewise-random multiplier on the bubble period, redrawn every
     * 0.3-1.5 fermentation hours. */
    if (t_h >= _seg_end_h) {
        _seg_end_h  += _urandf(SEG_MIN_H, SEG_MAX_H);
        _period_mult = expf(_urandf(-LOG_PERIOD_SPAN, LOG_PERIOD_SPAN));
        _amp_mult    = _urandf(AMP_LO, AMP_HI);
    }
    float base = _activityEnvelope(t_h);
    period     = _clampf(_bubblePeriodS(base) * _period_mult,
                         PERIOD_MIN_S, PERIOD_SPAN_S + PERIOD_MIN_S + 100.0f);
    amplitude  = _clampf(base * _amp_mult, 0.0f, 1.0f);
    activity   = amplitude;   /* thermal envelope follows the erratic amplitude */

#else /* SIM_PROFILE_NORMAL */
    activity  = _activityEnvelope(t_h);
    period    = _bubblePeriodS(activity);
    amplitude = activity;
#endif

    /* proper phase integration -- see PHASE ACCUMULATOR FIX above */
    _phase += SIM_DT_S / (double)period;
    _phase -= floor(_phase);
    float cycle_pos = (float)_phase;

    /* Sawtooth: 90 % linear rise, 10 % sharp drop to zero. */
    float raw = (cycle_pos < SAWTOOTH_RISE_FRAC)
                    ? (cycle_pos / SAWTOOTH_RISE_FRAC) * AMPLITUDE_PA * amplitude
                    : 0.0f;

    /* ±3 Pa sensor noise */
    float noise = (float)(random(-300, 300)) / 100.0f;

    _delta_pa = raw + noise;
    _activity = activity;
    _nsamples++;
}

/* Number of samples that should have been generated by real time `now_ms`,
 * counting from the epoch at which sampling started. */
static inline uint32_t samplesDueAt(uint32_t elapsed_ms) {
    return (uint32_t)((double)elapsed_ms * SIM_SAMPLES_PER_MS);
}

static inline uint32_t samplesGenerated() { return _nsamples; }

/* ── sensor getters (return the most recently generated sample) ─────────── */

// Simulated delta_pressure in Pa (vessel headspace above baseline).
inline float deltaPa() { return _delta_pa; }

// Ambient temperature: rises ~3°C during peak fermentation (yeast heat).
inline float tempDHT() {
    float t = TEMP_BASE_C + TEMP_RISE_C * _activity;
    return roundf(t / DHT_RESOLUTION_C) * DHT_RESOLUTION_C;
}

inline float humidityDHT() {
    float h = HUMID_BASE_PCT + HUMID_RISE_PCT * _activity;
    return roundf(h / DHT_RESOLUTION_C) * DHT_RESOLUTION_C;
}

// BMP280 also reads temperature (used as cross-check in real hardware).
inline float tempBMP() {
    return tempDHT() + TEMP_BMP_OFFSET_C;  // BMP280 runs slightly cooler
}

// Absolute pressure (baseline + delta), so delta computation in main.cpp works unchanged.
inline float pressurePa(float baseline_pa) {
    return baseline_pa + _delta_pa;
}

}  // namespace Sim

#endif  // SIMULATION
