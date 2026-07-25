#pragma once
/*
 * Minimal Arduino.h stand-in so firmware/src/sim_sensors.h can be compiled and
 * exercised on a host (tools/sim_test.cpp). It provides only what that header
 * touches: the integer types and Arduino's random()/millis().
 *
 * random() here is a plain xorshift32 rather than the ESP32's esp_random().
 * The simulator only uses it for ±3 Pa uniform sensor noise and (for the
 * CONTAMINATED profile) the segment draws, so the *distribution* is what
 * matters, not the exact stream.
 */

#include <math.h>
#include <stdint.h>
#include <stdlib.h>

static uint32_t _shim_rng_state = 0x1234567u;

static inline uint32_t _shim_rand32(void) {
    uint32_t x = _shim_rng_state;
    x ^= x << 13;
    x ^= x >> 17;
    x ^= x << 5;
    _shim_rng_state = x;
    return x;
}

static inline void randomSeed(uint32_t s) { _shim_rng_state = s ? s : 1u; }

static inline long random(long howbig) {
    if (howbig <= 0) return 0;
    return (long)(_shim_rand32() % (uint32_t)howbig);
}

static inline long random(long howsmall, long howbig) {
    if (howsmall >= howbig) return howsmall;
    return random(howbig - howsmall) + howsmall;
}

static inline uint32_t millis(void) { return 0; }
