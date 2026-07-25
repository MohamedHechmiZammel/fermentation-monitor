"""
validate.py -- end-to-end validation of the quantized detector, and the script
that picks the two firmware constants: the reconstruction-error THRESHOLD and
the N-consecutive-windows count.

Everything here runs the INT8 FIXED-POINT path, not the float one, because
that is what the ESP32 will execute. Runs are held out: their datagen seeds do
not appear in train.py's training seeds.

--------------------------------------------------------------------------
HOW THE THRESHOLD IS PICKED
--------------------------------------------------------------------------
SPEC.md's starting point is "~99th percentile of normal reconstruction error".
That is reported, but it is NOT usable as-is here, and the reason is worth
recording: a normal run's reconstruction error is a smooth function of
fermentation time, so its top 1% of windows are CONTIGUOUS (they sit at the
very start and the very end of the curve, where the manifold is most bent).
An N-consecutive-windows rule gives no protection against a systematic error
peak -- the run would trip it every time. The threshold therefore has to clear
the normal MAXIMUM, not the normal p99.

So the search is run explicitly: for each candidate threshold, count how many
held-out runs of each class raise the N-consecutive flag. The feasible band is
the set of thresholds that flag every anomalous run and no normal run; the
chosen threshold is the geometric centre of that band, which maximises the
multiplicative margin on both sides.

Usage:
    python validate.py
    python validate.py --n-windows 3 --seeds 30,31,32,33,34
"""

from __future__ import annotations

import argparse
import json
import os

import numpy as np

import train as T
from datagen import PROFILES, generate_run
from features import (INTERVAL_BUF_LEN, WINDOW_S, extract_features)
from quantize import QUANT_PATH, forward_int8

HERE = os.path.dirname(os.path.abspath(__file__))
THRESHOLD_PATH = os.path.join(HERE, "threshold.json")

HELD_OUT_SEEDS = (30, 31, 32, 33, 34, 35, 36, 37)
N_CANDIDATES = (1, 2, 3, 4, 5, 6)
PREFERRED_N = 3        # SPEC.md's starting point: 3 windows ~= 6 minutes


def load_quant(path=QUANT_PATH):
    z = np.load(path)
    qp = {k: z[k] for k in z.files}
    qp["M1"] = int(qp["M1"])
    qp["requant_shift"] = int(qp["requant_shift"])
    qp["s_x"] = float(qp["s_x"])
    qp["s_y"] = float(qp["s_y"])
    qp["input_clamp"] = float(qp["input_clamp"])
    return qp


def run_errors(profile, seed, qp):
    """Reconstruction error per window for one run, via the int8 path."""
    run = generate_run(profile, seed=seed)
    feats, times = extract_features(run)
    x = T.normalize(feats, qp["feat_mean"], qp["feat_std"], qp["input_clamp"])
    y, _, _ = forward_int8(x, qp)
    return T.recon_error(x, y), times


def first_flag_index(err, threshold, n_windows):
    """Index of the window at which N consecutive over-threshold windows complete."""
    streak = 0
    for i, e in enumerate(err):
        streak = streak + 1 if e > threshold else 0
        if streak >= n_windows:
            return i
    return None


def main():
    ap = argparse.ArgumentParser(description="Validate the quantized anomaly detector.")
    ap.add_argument("--quant", default=QUANT_PATH)
    ap.add_argument("--seeds", default=",".join(str(s) for s in HELD_OUT_SEEDS))
    ap.add_argument("--n-windows", type=int, default=None,
                    help="force N instead of selecting it")
    ap.add_argument("--out", default=THRESHOLD_PATH)
    args = ap.parse_args()

    seeds = [int(s) for s in args.seeds.split(",")]
    qp = load_quant(args.quant)

    print("=" * 78)
    print("HELD-OUT VALIDATION -- int8 fixed-point path")
    print("=" * 78)
    print(f"  held-out datagen seeds : {seeds}")
    print(f"  feature window         : {WINDOW_S:.0f} s   "
          f"(interval buffer {INTERVAL_BUF_LEN})")

    errs = {}
    for profile in PROFILES:
        per_run = []
        for s in seeds:
            e, t = run_errors(profile, s, qp)
            per_run.append((e, t))
        errs[profile] = per_run
        print(f"  {profile:>14}: {len(per_run)} runs x "
              f"{len(per_run[0][0])} windows")

    # ---------------- percentiles ----------------
    print("\n" + "-" * 78)
    print("RECONSTRUCTION-ERROR PERCENTILES PER CLASS (all held-out windows)")
    print("-" * 78)
    print(f"{'class':>16} {'n':>7} " + " ".join(f"{'p'+str(p):>9}"
          for p in (1, 25, 50, 75, 90, 99)) + f"{'max':>10}")
    flat = {}
    for profile in PROFILES:
        e = np.concatenate([r[0] for r in errs[profile]])
        flat[profile] = e
        q = np.percentile(e, [1, 25, 50, 75, 90, 99])
        print(f"{profile:>16} {len(e):>7} " + " ".join(f"{v:>9.4f}" for v in q)
              + f"{e.max():>10.3f}")

    normal_p99 = float(np.percentile(flat["normal"], 99))
    normal_max = float(flat["normal"].max())
    print(f"\n  normal p99 (SPEC's starting point) : {normal_p99:.4f}")
    print(f"  normal max                         : {normal_max:.4f}")

    # ---------------- threshold / N search ----------------
    print("\n" + "-" * 78)
    print("RUN-LEVEL FLAGGING vs THRESHOLD and N   (runs flagged / runs tested)")
    print("-" * 78)
    grid = np.unique(np.round(np.geomspace(0.05, 5.0, 120), 4))

    def flagged(profile, tau, n):
        return sum(first_flag_index(e, tau, n) is not None for e, _ in errs[profile])

    n_list = [args.n_windows] if args.n_windows else list(N_CANDIDATES)
    feasible = {}
    for n in n_list:
        ok = [tau for tau in grid
              if flagged("normal", tau, n) == 0
              and flagged("stuck", tau, n) == len(seeds)
              and flagged("contamination", tau, n) == len(seeds)]
        feasible[n] = (min(ok), max(ok)) if ok else None
        if ok:
            print(f"  N={n}: feasible thresholds {min(ok):.4f} .. {max(ok):.4f}  "
                  f"(multiplicative width {max(ok)/min(ok):.2f}x)")
        else:
            print(f"  N={n}: NO feasible threshold")

    usable = {n: b for n, b in feasible.items() if b}
    if not usable:
        print("\n  *** FAIL: no (threshold, N) pair separates the classes. ***")
        raise SystemExit(1)

    # N selection. On this synthetic data every N from 1 to 6 is feasible with
    # a near-identical threshold band, because both anomaly modes stay
    # over-threshold for hundreds of consecutive windows once they start -- so
    # N buys nothing HERE. It is kept at SPEC.md's PREFERRED_N anyway: on real
    # hardware a single window can be corrupted by an I2C hiccup, a knocked
    # vessel, or a burst window that straddles a baseline reset, and N=1 would
    # turn any of those into an alert. N=3 costs 4 extra minutes of latency
    # against a stall that takes hours to develop. If PREFERRED_N is ever
    # infeasible, fall back to the smallest N that works.
    chosen_n = args.n_windows or (PREFERRED_N if PREFERRED_N in usable
                                  else min(usable))
    lo, hi = usable[chosen_n]
    chosen_thr = float(np.sqrt(lo * hi))         # geometric centre = max margin

    print("\n" + "-" * 78)
    print("CHOSEN CONSTANTS")
    print("-" * 78)
    print(f"  AE_RECON_THRESHOLD    = {chosen_thr:.4f}")
    print(f"  AE_ANOMALY_N_WINDOWS  = {chosen_n}   "
          f"({chosen_n} x {WINDOW_S/60:.0f} min = {chosen_n * WINDOW_S/60:.0f} min of confirmation)")
    print(f"  feasible band         : {lo:.4f} .. {hi:.4f}")
    print(f"  margin below          : {chosen_thr / normal_max:.2f}x the worst normal window")

    # ---------------- separation report ----------------
    print("\n" + "-" * 78)
    print("SEPARATION AT THE CHOSEN THRESHOLD")
    print("-" * 78)
    print(f"{'class':>16} {'runs flagged':>14} {'windows > thr':>16} "
          f"{'median flag latency':>21}")
    verdicts = {}
    for profile in PROFILES:
        n_flag = flagged(profile, chosen_thr, chosen_n)
        frac = float(np.mean(flat[profile] > chosen_thr)) * 100
        lat = []
        for e, t in errs[profile]:
            i = first_flag_index(e, chosen_thr, chosen_n)
            if i is not None:
                lat.append(t[i] / 3600.0)
        lat_s = f"{np.median(lat):.1f} ferm-hours" if lat else "never"
        verdicts[profile] = n_flag
        print(f"{profile:>16} {f'{n_flag}/{len(seeds)}':>14} {frac:>15.2f}% {lat_s:>21}")

    ok = (verdicts["normal"] == 0
          and verdicts["stuck"] == len(seeds)
          and verdicts["contamination"] == len(seeds))

    print("\n  Worst normal window : "
          f"{normal_max:.4f}    (threshold is {chosen_thr/normal_max:.2f}x higher)")
    sustained = np.percentile(flat["stuck"][flat["stuck"] > chosen_thr], 5) \
        if np.any(flat["stuck"] > chosen_thr) else float("nan")
    print(f"  Stuck, p5 of its over-threshold windows : {sustained:.4f}"
          f"    ({sustained/chosen_thr:.2f}x the threshold)")

    print("\n" + "=" * 78)
    if ok:
        print("PASS -- clean separation. Every stuck and contaminated run raises the")
        print(f"        flag, no normal run ever does, and the feasible threshold band")
        print(f"        spans {hi/lo:.2f}x, so the choice is not knife-edge.")
    else:
        print("FAIL -- no clean separation at the chosen threshold.")
    print("=" * 78)

    with open(args.out, "w") as f:
        json.dump({
            "threshold": chosen_thr,
            "n_windows": chosen_n,
            "feasible_low": lo,
            "feasible_high": hi,
            "normal_p99": normal_p99,
            "normal_max": normal_max,
            "held_out_seeds": seeds,
            "pass": bool(ok),
        }, f, indent=2)
    print(f"\nwrote {args.out}")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
