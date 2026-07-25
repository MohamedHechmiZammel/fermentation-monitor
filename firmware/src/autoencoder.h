#pragma once

/*
 * autoencoder.h -- fixed-point int8 forward pass of the 5 -> 3 (ReLU) -> 5
 * anomaly autoencoder, plus the z-score normalization that feeds it.
 *
 * This is a near-verbatim transcription of the reference pseudocode in the
 * final comment block of model_weights.h, which is itself a mirror of
 * model_training/quantize.py:forward_int8. Do not restructure it: the
 * threshold in model_weights.h is only meaningful for this exact arithmetic.
 *
 * Everything is int32/int64 MACs plus a handful of floats; no float matmul, no
 * transcendental functions, no dynamic allocation. Cost per window is
 * 5*3 + 3*5 = 30 multiply-accumulates, i.e. microseconds.
 */

#include <math.h>
#include <stdint.h>

#include "model_weights.h"

/* z-score then clamp -- mirrors model_training/train.py:normalize().
 *
 * The clamp is applied identically in the float and fixed-point paths, so the
 * two never disagree because of it. +-6 sigma leaves ~1.6x headroom over the
 * largest |z| any normal training window produces (3.8) while keeping the int8
 * input step at 0.047 sigma.                                                */
static inline void ae_normalize(const float raw[AE_N_INPUT],
                                float x[AE_N_INPUT]) {
    for (int i = 0; i < AE_N_INPUT; i++) {
        float z = (raw[i] - AE_FEAT_MEAN[i]) / AE_FEAT_STD[i];
        if (z >  AE_INPUT_CLAMP) z =  AE_INPUT_CLAMP;
        if (z < -AE_INPUT_CLAMP) z = -AE_INPUT_CLAMP;
        x[i] = z;
    }
}

/* Reconstruction error of an ALREADY normalized+clamped input vector.
 * Compare against AE_RECON_THRESHOLD.                                       */
static inline float ae_recon_error(const float x[AE_N_INPUT]) {
    int8_t qx[AE_N_INPUT];
    for (int i = 0; i < AE_N_INPUT; i++) {
        qx[i] = (int8_t)lrintf(x[i] / AE_INPUT_SCALE);
    }

    int32_t acc1[AE_N_HIDDEN];
    for (int j = 0; j < AE_N_HIDDEN; j++) {
        acc1[j] = AE_B1[j];
        for (int i = 0; i < AE_N_INPUT; i++) {
            acc1[j] += (int32_t)qx[i] * (int32_t)AE_W1[i][j];
        }
        if (acc1[j] < 0) acc1[j] = 0;                     /* ReLU */
    }

    int8_t qh[AE_N_HIDDEN];
    for (int j = 0; j < AE_N_HIDDEN; j++) {
        int64_t r = ((int64_t)acc1[j] * (int64_t)AE_REQUANT_M1
                     + (1LL << (AE_REQUANT_SHIFT - 1))) >> AE_REQUANT_SHIFT;
        qh[j] = (int8_t)(r > AE_INT8_MAX ? AE_INT8_MAX : r);  /* r >= 0 already */
    }

    float err = 0.0f;
    for (int i = 0; i < AE_N_INPUT; i++) {
        int32_t acc2 = AE_B2[i];
        for (int j = 0; j < AE_N_HIDDEN; j++) {
            acc2 += (int32_t)qh[j] * (int32_t)AE_W2[j][i];
        }
        float d = (float)acc2 * AE_OUTPUT_SCALE - x[i];
        err += d * d;
    }
    return err / (float)AE_N_INPUT;
}

/* Convenience: raw feature vector -> anomaly score. */
static inline float ae_score(const float raw[AE_N_INPUT]) {
    float x[AE_N_INPUT];
    ae_normalize(raw, x);
    return ae_recon_error(x);
}
