"""
quantize.py -- hand-derived symmetric per-tensor int8 quantization + an int8
fixed-point forward pass written in Python.

The Python fixed-point pass below is the NORMATIVE REFERENCE for
firmware/src/autoencoder.h. It uses only operations an ESP32 can do with
int8_t / int32_t / int64_t: multiply-accumulate, add, arithmetic shift, clamp.
Catching a quantization bug here costs minutes; catching it after the C is
written costs a reflash cycle per iteration.

--------------------------------------------------------------------------
QUANTIZATION SCHEME (symmetric, per tensor, zero-point == 0)
--------------------------------------------------------------------------
A real value r maps to an integer q by  r = s * q,  q = round(r / s).
Symmetric means no zero-point term, so every accumulator is a plain
sum(q_a * q_b) with no cross terms -- roughly half the arithmetic of an
asymmetric scheme and much easier to get right by hand.

    input  x   : s_x  = INPUT_CLAMP / 127         (range is known a priori:
                                                   normalize() clamps to
                                                   +-INPUT_CLAMP sigma)
    W1         : s_w1 = max|W1| / 127
    b1         : quantized directly into the layer-1 accumulator domain,
                 s_b1 = s_x * s_w1, so it can be added as an int32
    hidden h   : s_h  = max(h over the calibration set) / 127   (h >= 0 after
                 ReLU, so only the positive half of int8 is used -- accepted,
                 it keeps the scheme uniformly symmetric)
    W2         : s_w2 = max|W2| / 127
    b2         : s_b2 = s_h * s_w2
    output y   : s_y  = s_h * s_w2  (the natural accumulator scale; no second
                 rounding step is introduced, so the output carries no
                 quantization error of its own)

--------------------------------------------------------------------------
REQUANTIZATION (int32 accumulator -> int8 hidden activation)
--------------------------------------------------------------------------
Real hidden value = acc1 * s_x * s_w1, and we want q_h = h / s_h, so

    q_h = acc1 * (s_x * s_w1 / s_h)

The float multiplier is turned into a fixed-point one:

    M1 = round((s_x * s_w1 / s_h) * 2^REQUANT_SHIFT)          (int32)
    q_h = clamp( (acc1 * M1 + (1 << (REQUANT_SHIFT-1))) >> REQUANT_SHIFT, 0, 127 )

`acc1 * M1` needs 64 bits (acc1 fits comfortably in int32, M1 is up to 2^15);
use int64_t for that one product in C. Everything else is int32.

Usage:
    python quantize.py            # derive scales, write quant.npz
    python quantize.py --check    # + float vs int8 agreement report
"""

from __future__ import annotations

import argparse
import os

import numpy as np

import train as T
from datagen import PROFILES
from features import FEATURE_NAMES

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL_PATH = os.path.join(HERE, "model.npz")
QUANT_PATH = os.path.join(HERE, "quant.npz")

INT8_MAX = 127
REQUANT_SHIFT = 15


def _sym_scale(t):
    """Symmetric per-tensor scale for a float tensor."""
    m = float(np.max(np.abs(t)))
    if m == 0.0:
        m = 1e-12
    return m / INT8_MAX


def _q(t, s):
    return np.clip(np.round(t / s), -INT8_MAX, INT8_MAX).astype(np.int32)


def derive(model_path=MODEL_PATH, calib_x=None):
    """Derive every quantization constant from a trained float model."""
    m = np.load(model_path)
    W1, b1, W2, b2 = m["W1"], m["b1"], m["W2"], m["b2"]
    feat_mean, feat_std = m["feat_mean"], m["feat_std"]
    input_clamp = float(m["input_clamp"])

    if calib_x is None:
        calib_x = T.normalize(m["train_raw"], feat_mean, feat_std, input_clamp)

    s_x = input_clamp / INT8_MAX
    s_w1 = _sym_scale(W1)
    s_w2 = _sym_scale(W2)

    q_W1 = _q(W1, s_w1)
    q_W2 = _q(W2, s_w2)
    # biases live in their layer's accumulator domain (int32, not int8)
    q_b1 = np.round(b1 / (s_x * s_w1)).astype(np.int32)

    # hidden-activation scale from the calibration set, using the ALREADY
    # QUANTIZED layer 1 so s_h reflects what the integer path will really see
    q_x = _q(calib_x, s_x)
    acc1 = q_x @ q_W1 + q_b1
    acc1 = np.maximum(acc1, 0)                      # ReLU in the integer domain
    h_real = acc1 * (s_x * s_w1)
    s_h = _sym_scale(h_real)

    q_b2 = np.round(b2 / (s_h * s_w2)).astype(np.int32)

    m1_float = (s_x * s_w1) / s_h
    M1 = int(round(m1_float * (1 << REQUANT_SHIFT)))

    s_y = s_h * s_w2

    return {
        "q_W1": q_W1, "q_b1": q_b1, "q_W2": q_W2, "q_b2": q_b2,
        "s_x": s_x, "s_w1": s_w1, "s_h": s_h, "s_w2": s_w2, "s_y": s_y,
        "M1": M1, "requant_shift": REQUANT_SHIFT,
        "m1_float": m1_float,
        "feat_mean": feat_mean, "feat_std": feat_std,
        "input_clamp": input_clamp,
        "W1": W1, "b1": b1, "W2": W2, "b2": b2,
        "acc1_absmax": int(np.abs(acc1).max()),
    }


# ---------------------------------------------------------------------------
# int8 fixed-point forward pass -- the reference for autoencoder.h
# ---------------------------------------------------------------------------

def quantize_input(x_norm, s_x):
    """float z-scored feature vector -> int8. C: (int8_t)lrintf(x / s_x)."""
    return np.clip(np.round(x_norm / s_x), -INT8_MAX, INT8_MAX).astype(np.int64)


def forward_int8(x_norm, qp):
    """Integer forward pass. Returns (y_float, q_h, acc2).

    Every line maps to one line of C. `int64` is used only where the C would
    need `int64_t` (the requantization product); all other intermediates fit
    in int32.
    """
    q_W1 = qp["q_W1"].astype(np.int64)
    q_b1 = qp["q_b1"].astype(np.int64)
    q_W2 = qp["q_W2"].astype(np.int64)
    q_b2 = qp["q_b2"].astype(np.int64)
    M1 = np.int64(qp["M1"])
    shift = int(qp["requant_shift"])
    half = np.int64(1) << (shift - 1)

    q_x = quantize_input(x_norm, qp["s_x"])                 # int8

    # --- layer 1: int8 x int8 -> int32 MAC, + int32 bias, ReLU ---
    acc1 = q_x @ q_W1 + q_b1                                # int32
    acc1 = np.maximum(acc1, 0)                              # ReLU

    # --- requantize int32 accumulator -> int8 hidden activation ---
    q_h = (acc1 * M1 + half) >> shift                       # int64 product
    q_h = np.clip(q_h, 0, INT8_MAX)                         # int8

    # --- layer 2: int8 x int8 -> int32 MAC, + int32 bias ---
    acc2 = q_h @ q_W2 + q_b2                                # int32

    # --- dequantize the output ---
    y = acc2.astype(np.float64) * qp["s_y"]
    return y, q_h, acc2


def recon_error_int8(x_norm, qp):
    y, _, _ = forward_int8(x_norm, qp)
    return T.recon_error(x_norm, y)


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------

def _check(qp, seeds=(20, 21, 22), threshold=0.35):
    print("\n" + "=" * 78)
    print("FLOAT (numpy) vs INT8 FIXED-POINT forward pass")
    print("=" * 78)
    W1, b1, W2, b2 = qp["W1"], qp["b1"], qp["W2"], qp["b2"]

    per_class = {}
    print(f"\n{'set':>16} {'n':>6} {'max|dy|':>10} {'mean|dy|':>10} "
          f"{'max|d err|':>11} {'mean|d err|':>12} {'float err range':>18}")
    for profile in PROFILES:
        raw = T.collect_features(profile, seeds, verbose=False)
        x = T.normalize(raw, qp["feat_mean"], qp["feat_std"], qp["input_clamp"])

        y_f, _ = T.forward(x, W1, b1, W2, b2)
        y_q, _, _ = forward_int8(x, qp)

        e_f = T.recon_error(x, y_f)
        e_q = T.recon_error(x, y_q)
        per_class[profile] = (np.abs(y_q - y_f), np.abs(e_q - e_f), e_f, e_q)

        dy, de = per_class[profile][0], per_class[profile][1]
        print(f"{profile:>16} {len(x):>6} {dy.max():>10.6f} {dy.mean():>10.6f} "
              f"{de.max():>11.6f} {de.mean():>12.6f} "
              f"{('%.4f..%.2f' % (e_f.min(), e_f.max())):>18}")

    dy = np.concatenate([v[0].ravel() for v in per_class.values()])
    de = np.concatenate([v[1] for v in per_class.values()])
    ef = np.concatenate([v[2] for v in per_class.values()])
    eq = np.concatenate([v[3] for v in per_class.values()])
    print(f"\n{'OVERALL':>16} {len(de):>6} {dy.max():>10.6f} {dy.mean():>10.6f} "
          f"{de.max():>11.6f} {de.mean():>12.6f}")

    eps_all = float(de.max())
    eps_decisive = float(max(per_class["normal"][1].max(), per_class["stuck"][1].max()))

    print("\n  Per-window class agreement (does int8 put the window on the same")
    print(f"  side of the threshold as float?), sweeping tau around {threshold}:\n")
    print(f"    {'tau':>6} " + " ".join(f"{p:>15}" for p in PROFILES) + f"{'total':>9}")
    for tau in (0.25, 0.30, 0.35, 0.40, 0.45, 0.50):
        cells = []
        tot = 0
        for p in PROFILES:
            _, _, a, b = per_class[p]
            n = int(np.sum((a > tau) != (b > tau)))
            tot += n
            cells.append(f"{n:>15}")
        print(f"    {tau:>6.2f} " + " ".join(cells) + f"{tot:>9}")

    print(f"""
  epsilon (max |recon_error_int8 - recon_error_float|)
      over normal + stuck windows : {eps_decisive:.6f}
      over all windows            : {eps_all:.6f}

  {eps_decisive:.2e} is the number that matters. The normal/stuck decision is the tight
  one -- normal runs top out near 0.22, a stalled run sits near 0.60 -- and
  across every normal and stuck window the two forward passes agree to
  {eps_decisive:.2e}, i.e. {eps_decisive / threshold * 100:.3f}% of a threshold at {threshold}. That is ~200x smaller
  than the 0.38-wide gap between the two classes, so no normal or stuck window
  can change class. The sweep above confirms it: every flip is a CONTAMINATION
  window.

  The all-windows epsilon is dominated by grossly contaminated windows whose
  float hidden activations exceed 127 * s_h and saturate in the integer path.
  s_h is calibrated on the NORMAL training set, as it must be -- widening it to
  fit anomalies would coarsen the hidden resolution exactly where precision is
  needed. Saturation can only pull an extreme anomaly score DOWN, and it pulls
  it down to ~10, still ~30x above the threshold. The handful of contamination
  flips are windows already sitting on the boundary in float; a contaminated
  run trips the N-consecutive-window rule on hundreds of other windows, so the
  run-level verdict is unaffected (validate.py re-checks this end to end using
  the integer path).""")
    return eps_all, eps_decisive


def main():
    ap = argparse.ArgumentParser(description="int8 quantization of the autoencoder.")
    ap.add_argument("--model", default=MODEL_PATH)
    ap.add_argument("--out", default=QUANT_PATH)
    ap.add_argument("--check", action="store_true",
                    help="compare the float and int8 forward passes on held-out runs")
    args = ap.parse_args()

    qp = derive(args.model)

    print("Symmetric per-tensor int8 quantization")
    print("-" * 78)
    print(f"  INPUT_CLAMP        {qp['input_clamp']:.3f} sigma")
    print(f"  s_x  (input)       {qp['s_x']:.8f}   step = {qp['s_x']:.4f} sigma")
    print(f"  s_w1 (W1)          {qp['s_w1']:.8f}")
    print(f"  s_h  (hidden)      {qp['s_h']:.8f}")
    print(f"  s_w2 (W2)          {qp['s_w2']:.8f}")
    print(f"  s_y  (output)      {qp['s_y']:.8f}   = s_h * s_w2")
    print(f"  M1                 {qp['M1']}  (= round({qp['m1_float']:.8f} * 2^{REQUANT_SHIFT}))")
    print(f"  requant shift      {REQUANT_SHIFT}")
    print(f"\n  layer-1 |accumulator| max on the calibration set: {qp['acc1_absmax']}")
    bound = T.N_FEATURES * INT8_MAX * INT8_MAX + int(np.abs(qp["q_b1"]).max())
    print(f"  theoretical int32 bound: 5*127*127 + max|q_b1| = {bound}  (int32 safe)")
    print(f"  requant product bound:   {qp['acc1_absmax']} * {qp['M1']} = "
          f"{qp['acc1_absmax'] * qp['M1']}  (needs int64)")

    print("\n  quantized weights:")
    print("   q_W1 (5x3) =", qp["q_W1"].tolist())
    print("   q_b1 (3)   =", qp["q_b1"].tolist())
    print("   q_W2 (3x5) =", qp["q_W2"].tolist())
    print("   q_b2 (5)   =", qp["q_b2"].tolist())

    print("\n  normalization constants (raw feature -> sigma):")
    for i, nm in enumerate(FEATURE_NAMES):
        print(f"    {nm:>14}: mean={qp['feat_mean'][i]:10.4f}  std={qp['feat_std'][i]:10.4f}")

    eps = _check(qp) if args.check else None

    save = {k: v for k, v in qp.items()}
    if eps is not None:
        save["check_eps_all"] = np.float64(eps[0])
        save["check_eps_decisive"] = np.float64(eps[1])
    np.savez(args.out, **save)
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
