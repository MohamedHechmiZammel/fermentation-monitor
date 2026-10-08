#pragma once

/*
 * model_weights.h -- GENERATED FILE, DO NOT EDIT BY HAND.
 *
 * Produced by model_training/export_weights.py. To change anything here,
 * edit the pipeline and re-run:
 *
 *     cd model_training
 *     python train.py && python quantize.py --check
 *     python validate.py && python export_weights.py
 *
 * Contents: the on-device anomaly detector's feature-extraction constants,
 * z-score normalization constants, symmetric per-tensor int8 autoencoder
 * (5 -> 3 ReLU -> 5), and the alert threshold.
 *
 * GROUND-TRUTH CAVEAT: the stuck/contaminated behaviour these constants were
 * tuned against is entirely synthetic (model_training/datagen.py), invented
 * for this project. It is not derived from any real defective-fermentation
 * dataset. Treat on-device alerts as provisional.
 */

#include <stdint.h>

/* ---------------------------------------------------------------------
 * Feature extraction -- bubble_detector.h MUST match these exactly, or the
 * runtime features will not be drawn from the same distribution as the
 * training features and the thresholds below become meaningless.
 * --------------------------------------------------------------------- */
#define AE_SAMPLE_RATE_HZ        10.0f   /* burst sampling rate            */
#define AE_SMOOTH_N              5      /* boxcar length, samples         */
#define AE_DROP_THRESHOLD_PA     6.0f    /* drop-edge / re-arm hysteresis  */
#define AE_REFRACTORY_S          1.0f    /* min spacing between bubbles    */
#define AE_WINDOW_S              120.0f  /* feature aggregation window     */
#define AE_INTERVAL_BUF_LEN      16     /* rolling interval buffer depth  */
#define AE_MIN_INTERVALS         16     /* emit nothing until buffer full */

/* ---------------------------------------------------------------------
 * Model shape
 * --------------------------------------------------------------------- */
#define AE_N_INPUT               5
#define AE_N_HIDDEN              3
#define AE_INT8_MAX              127

/* feature order: bubble_rate, mean_interval, std_interval, interval_trend, temp_delta */

/* ---------------------------------------------------------------------
 * z-score normalization (learned from the NORMAL training set)
 *     x[i] = clamp((raw[i] - AE_FEAT_MEAN[i]) / AE_FEAT_STD[i],
 *                  -AE_INPUT_CLAMP, +AE_INPUT_CLAMP)
 * --------------------------------------------------------------------- */
static const float AE_FEAT_MEAN[5] = { 2.71958457f, 106.66196526f, 1.67355165f, -1.89083210f, 1.74317135f };
static const float AE_FEAT_STD[5] = { 4.57482599f, 88.76358375f, 1.26087250f, 3.10471305f, 0.87376679f };
#define AE_INPUT_CLAMP           6.0f

/* ---------------------------------------------------------------------
 * Symmetric per-tensor int8 quantization (zero-point == 0)
 * --------------------------------------------------------------------- */
#define AE_INPUT_SCALE           0.0472440945f   /* s_x = AE_INPUT_CLAMP / 127 */
#define AE_W1_SCALE              0.0023190428f
#define AE_HIDDEN_SCALE          0.0098018343f
#define AE_W2_SCALE              0.0255560199f
#define AE_OUTPUT_SCALE          0.0002504959f   /* s_y = s_h * s_w2 */
#define AE_REQUANT_M1            366        /* round((s_x*s_w1/s_h) * 2^shift) */
#define AE_REQUANT_SHIFT         15

static const int8_t AE_W1[5][3] = {
    { -3, -1, 127 },
    { 51, 45, 20 },
    { 7, 40, -21 },
    { 104, -59, -4 },
    { -58, -47, -17 }
};

static const int32_t AE_B1[3] = { 1450, 3961, 1574 };
    /* AE_B1 is pre-scaled into the layer-1 accumulator domain (s_x * s_w1). */

static const int8_t AE_W2[3][5] = {
    { -12, 55, 14, 100, -60 },
    { -16, 88, 80, -97, -87 },
    { 127, 5, -26, -5, -5 }
};

static const int32_t AE_B2[5] = { -1386, -4889, -3338, 2788, 4915 };
    /* AE_B2 is pre-scaled into the layer-2 accumulator domain (s_h * s_w2). */

/* ---------------------------------------------------------------------
 * Alert thresholds -- chosen by model_training/validate.py against held-out
 * synthetic runs (datagen seeds [30, 31, 32, 33, 34, 35, 36, 37]).
 * Feasible threshold band was 0.2176 .. 0.5725; this is its geometric
 * centre. Worst normal window scored 0.2240, so the threshold sits
 * 1.58x above it.
 * --------------------------------------------------------------------- */
#define AE_RECON_THRESHOLD       0.352953f
#define AE_ANOMALY_N_WINDOWS     3        /* 3 x 2 min = 6 min confirmation */

/* ---------------------------------------------------------------------
 * Reference forward pass (mirror of model_training/quantize.py:forward_int8)
 *
 *   int8_t  qx[AE_N_INPUT];
 *   for (i) qx[i] = (int8_t)lrintf(x[i] / AE_INPUT_SCALE);   // x already clamped
 *
 *   int32_t acc1[AE_N_HIDDEN];
 *   for (j) {
 *       acc1[j] = AE_B1[j];
 *       for (i) acc1[j] += (int32_t)qx[i] * AE_W1[i][j];
 *       if (acc1[j] < 0) acc1[j] = 0;                        // ReLU
 *   }
 *
 *   int8_t qh[AE_N_HIDDEN];
 *   for (j) {
 *       int64_t r = ((int64_t)acc1[j] * AE_REQUANT_M1
 *                    + (1LL << (AE_REQUANT_SHIFT - 1))) >> AE_REQUANT_SHIFT;
 *       qh[j] = (int8_t)(r > AE_INT8_MAX ? AE_INT8_MAX : r);  // r >= 0 already
 *   }
 *
 *   float err = 0.0f;
 *   for (i) {
 *       int32_t acc2 = AE_B2[i];
 *       for (j) acc2 += (int32_t)qh[j] * AE_W2[j][i];
 *       float d = (float)acc2 * AE_OUTPUT_SCALE - x[i];
 *       err += d * d;
 *   }
 *   err /= (float)AE_N_INPUT;      // compare against AE_RECON_THRESHOLD
 * --------------------------------------------------------------------- */
