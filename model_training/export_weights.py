"""
export_weights.py -- emit firmware/src/model_weights.h.

The header is committed to the repo (unlike config.h, it holds no secrets).
It carries everything the firmware needs to reproduce this pipeline's decision:

  * the bubble-detector / aggregator constants, so the on-device features are
    computed exactly the way the training features were
  * the 5 per-feature (mean, std) z-score normalization constants
  * the symmetric int8 weights, biases, scales and the requantization multiplier
  * the reconstruction-error threshold and the N-consecutive-windows count

Nothing here is runtime-tunable by design: changing the model means a reflash
anyway, so the constants are compile-time.

Usage:
    python export_weights.py                     # after train -> quantize -> validate
    python export_weights.py --out /some/path.h
"""

from __future__ import annotations

import argparse
import json
import os

import numpy as np

import features as F
from quantize import INT8_MAX, QUANT_PATH
from train import MODEL_PATH
from validate import THRESHOLD_PATH

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.abspath(os.path.join(HERE, "..", "firmware", "src", "model_weights.h"))


def _c_float_array(name, values, fmt="%.8ff"):
    body = ", ".join(fmt % v for v in values)
    return f"static const float {name}[{len(values)}] = {{ {body} }};"


def _c_int_array(name, values, ctype):
    body = ", ".join(str(int(v)) for v in values)
    return f"static const {ctype} {name}[{len(values)}] = {{ {body} }};"


def _c_int_matrix(name, mat, ctype):
    rows = ",\n    ".join("{ " + ", ".join(str(int(v)) for v in row) + " }" for row in mat)
    return (f"static const {ctype} {name}[{mat.shape[0]}][{mat.shape[1]}] = {{\n"
            f"    {rows}\n}};")


def build_header(qp, thr):
    fm = qp["feat_mean"]
    fs = qp["feat_std"]
    names = ", ".join(F.FEATURE_NAMES)

    lines = [
        "#pragma once",
        "",
        "/*",
        " * model_weights.h -- GENERATED FILE, DO NOT EDIT BY HAND.",
        " *",
        " * Produced by model_training/export_weights.py. To change anything here,",
        " * edit the pipeline and re-run:",
        " *",
        " *     cd model_training",
        " *     python train.py && python quantize.py --check",
        " *     python validate.py && python export_weights.py",
        " *",
        " * Contents: the on-device anomaly detector's feature-extraction constants,",
        " * z-score normalization constants, symmetric per-tensor int8 autoencoder",
        " * (5 -> 3 ReLU -> 5), and the alert threshold.",
        " *",
        " * GROUND-TRUTH CAVEAT: the stuck/contaminated behaviour these constants were",
        " * tuned against is entirely synthetic (model_training/datagen.py), invented",
        " * for this project. It is not derived from any real defective-fermentation",
        " * dataset. Treat on-device alerts as provisional.",
        " */",
        "",
        "#include <stdint.h>",
        "",
        "/* ---------------------------------------------------------------------",
        " * Feature extraction -- bubble_detector.h MUST match these exactly, or the",
        " * runtime features will not be drawn from the same distribution as the",
        " * training features and the thresholds below become meaningless.",
        " * --------------------------------------------------------------------- */",
        f"#define AE_SAMPLE_RATE_HZ        {F.SAMPLE_RATE_HZ:.1f}f   /* burst sampling rate            */",
        f"#define AE_SMOOTH_N              {F.SMOOTH_N}      /* boxcar length, samples         */",
        f"#define AE_DROP_THRESHOLD_PA     {F.DROP_THRESHOLD_PA:.1f}f    /* drop-edge / re-arm hysteresis  */",
        f"#define AE_REFRACTORY_S          {F.REFRACTORY_S:.1f}f    /* min spacing between bubbles    */",
        f"#define AE_WINDOW_S              {F.WINDOW_S:.1f}f  /* feature aggregation window     */",
        f"#define AE_INTERVAL_BUF_LEN      {F.INTERVAL_BUF_LEN}     /* rolling interval buffer depth  */",
        f"#define AE_MIN_INTERVALS         {F.MIN_INTERVALS}     /* emit nothing until buffer full */",
        "",
        "/* ---------------------------------------------------------------------",
        " * Model shape",
        " * --------------------------------------------------------------------- */",
        f"#define AE_N_INPUT               {F.N_FEATURES}",
        f"#define AE_N_HIDDEN              {qp['q_W1'].shape[1]}",
        f"#define AE_INT8_MAX              {INT8_MAX}",
        "",
        f"/* feature order: {names} */",
        "",
        "/* ---------------------------------------------------------------------",
        " * z-score normalization (learned from the NORMAL training set)",
        " *     x[i] = clamp((raw[i] - AE_FEAT_MEAN[i]) / AE_FEAT_STD[i],",
        " *                  -AE_INPUT_CLAMP, +AE_INPUT_CLAMP)",
        " * --------------------------------------------------------------------- */",
        _c_float_array("AE_FEAT_MEAN", fm),
        _c_float_array("AE_FEAT_STD", fs),
        f"#define AE_INPUT_CLAMP           {qp['input_clamp']:.1f}f",
        "",
        "/* ---------------------------------------------------------------------",
        " * Symmetric per-tensor int8 quantization (zero-point == 0)",
        " * --------------------------------------------------------------------- */",
        f"#define AE_INPUT_SCALE           {float(qp['s_x']):.10f}f   /* s_x = AE_INPUT_CLAMP / 127 */",
        f"#define AE_W1_SCALE              {float(qp['s_w1']):.10f}f",
        f"#define AE_HIDDEN_SCALE          {float(qp['s_h']):.10f}f",
        f"#define AE_W2_SCALE              {float(qp['s_w2']):.10f}f",
        f"#define AE_OUTPUT_SCALE          {float(qp['s_y']):.10f}f   /* s_y = s_h * s_w2 */",
        f"#define AE_REQUANT_M1            {int(qp['M1'])}        /* round((s_x*s_w1/s_h) * 2^shift) */",
        f"#define AE_REQUANT_SHIFT         {int(qp['requant_shift'])}",
        "",
        _c_int_matrix("AE_W1", qp["q_W1"], "int8_t"),
        "",
        _c_int_array("AE_B1", qp["q_b1"], "int32_t"),
        "    /* AE_B1 is pre-scaled into the layer-1 accumulator domain (s_x * s_w1). */",
        "",
        _c_int_matrix("AE_W2", qp["q_W2"], "int8_t"),
        "",
        _c_int_array("AE_B2", qp["q_b2"], "int32_t"),
        "    /* AE_B2 is pre-scaled into the layer-2 accumulator domain (s_h * s_w2). */",
        "",
        "/* ---------------------------------------------------------------------",
        " * Alert thresholds -- chosen by model_training/validate.py against held-out",
        f" * synthetic runs (datagen seeds {thr['held_out_seeds']}).",
        f" * Feasible threshold band was {thr['feasible_low']:.4f} .. {thr['feasible_high']:.4f}; this is its geometric",
        f" * centre. Worst normal window scored {thr['normal_max']:.4f}, so the threshold sits",
        f" * {thr['threshold'] / thr['normal_max']:.2f}x above it.",
        " * --------------------------------------------------------------------- */",
        f"#define AE_RECON_THRESHOLD       {thr['threshold']:.6f}f",
        f"#define AE_ANOMALY_N_WINDOWS     {thr['n_windows']}        "
        f"/* {thr['n_windows']} x {F.WINDOW_S / 60:.0f} min = {thr['n_windows'] * F.WINDOW_S / 60:.0f} min confirmation */",
        "",
        "/* ---------------------------------------------------------------------",
        " * Reference forward pass (mirror of model_training/quantize.py:forward_int8)",
        " *",
        " *   int8_t  qx[AE_N_INPUT];",
        " *   for (i) qx[i] = (int8_t)lrintf(x[i] / AE_INPUT_SCALE);   // x already clamped",
        " *",
        " *   int32_t acc1[AE_N_HIDDEN];",
        " *   for (j) {",
        " *       acc1[j] = AE_B1[j];",
        " *       for (i) acc1[j] += (int32_t)qx[i] * AE_W1[i][j];",
        " *       if (acc1[j] < 0) acc1[j] = 0;                        // ReLU",
        " *   }",
        " *",
        " *   int8_t qh[AE_N_HIDDEN];",
        " *   for (j) {",
        " *       int64_t r = ((int64_t)acc1[j] * AE_REQUANT_M1",
        " *                    + (1LL << (AE_REQUANT_SHIFT - 1))) >> AE_REQUANT_SHIFT;",
        " *       qh[j] = (int8_t)(r > AE_INT8_MAX ? AE_INT8_MAX : r);  // r >= 0 already",
        " *   }",
        " *",
        " *   float err = 0.0f;",
        " *   for (i) {",
        " *       int32_t acc2 = AE_B2[i];",
        " *       for (j) acc2 += (int32_t)qh[j] * AE_W2[j][i];",
        " *       float d = (float)acc2 * AE_OUTPUT_SCALE - x[i];",
        " *       err += d * d;",
        " *   }",
        " *   err /= (float)AE_N_INPUT;      // compare against AE_RECON_THRESHOLD",
        " * --------------------------------------------------------------------- */",
        "",
    ]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="Emit firmware/src/model_weights.h")
    ap.add_argument("--quant", default=QUANT_PATH)
    ap.add_argument("--threshold", default=THRESHOLD_PATH)
    ap.add_argument("--out", default=DEFAULT_OUT)
    args = ap.parse_args()

    for p, what, how in ((MODEL_PATH, "model.npz", "python train.py"),
                         (args.quant, "quant.npz", "python quantize.py --check"),
                         (args.threshold, "threshold.json", "python validate.py")):
        if not os.path.exists(p):
            raise SystemExit(f"missing {what} -- run `{how}` first")

    z = np.load(args.quant)
    qp = {k: z[k] for k in z.files}
    with open(args.threshold) as f:
        thr = json.load(f)

    if not thr.get("pass"):
        raise SystemExit("threshold.json reports FAIL -- refusing to export a model "
                         "that does not separate the classes")

    header = build_header(qp, thr)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as f:
        f.write(header)

    print(f"wrote {args.out}  ({len(header.splitlines())} lines)")
    print(f"  threshold = {thr['threshold']:.6f}   N = {thr['n_windows']}")
    print(f"  int8 weights: W1 {qp['q_W1'].shape}, W2 {qp['q_W2'].shape}")


if __name__ == "__main__":
    main()
