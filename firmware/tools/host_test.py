#!/usr/bin/env python3
"""Cross-check firmware/src/bubble_detector.h + autoencoder.h against the
Python reference in model_training/.

For each requested profile this script:

  1. generates a datagen run (phase_mode="accumulator", the mode sim_sensors.h
     now implements),
  2. writes its 10 Hz (delta_pa, temp_dht) stream to a CSV,
  3. runs the compiled C host_test over that CSV,
  4. recomputes the same windows with features.py + quantize.py, and
  5. reports the max absolute disagreement on every feature, on the
     reconstruction error, and on the anomaly decision.

Nothing under model_training/ is modified; it is imported read-only.

    python3 host_test.py                 # normal, stuck, contamination
    python3 host_test.py --profile stuck
"""

import argparse
import os
import subprocess
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
FIRMWARE = os.path.dirname(HERE)
REPO = os.path.dirname(FIRMWARE)
sys.path.insert(0, os.path.join(REPO, "model_training"))

import datagen                      # noqa: E402
import features as feat_mod         # noqa: E402
import quantize as qz               # noqa: E402
import train as T                   # noqa: E402

THRESHOLD = 0.352953                # AE_RECON_THRESHOLD in model_weights.h


def build():
    exe = os.path.join(HERE, "host_test")
    cmd = ["gcc", "-std=c99", "-O2", "-Wall", "-Wextra",
           "-I", os.path.join(FIRMWARE, "src"),
           "-o", exe, os.path.join(HERE, "host_test.c"), "-lm"]
    print("$ " + " ".join(cmd))
    subprocess.run(cmd, check=True)
    return exe


def run_profile(exe, profile, seed, duration_h, qp, keep):
    run = datagen.generate_run(profile, duration_h=duration_h, seed=seed)
    delta = run["delta_pa"]
    temp = run["temp_dht"]

    fd, path = tempfile.mkstemp(suffix=".csv", prefix=f"{profile}_")
    os.close(fd)
    with open(path, "w") as fh:
        # %.9g round-trips float32 exactly, so the C side sees the same bits.
        fh.write("\n".join("%.9g,%.9g" % (d, t) for d, t in zip(delta, temp)))
        fh.write("\n")

    proc = subprocess.run([exe, path], capture_output=True, text=True, check=True)
    if not keep:
        os.unlink(path)
    summary = proc.stderr.strip()

    rows = [ln.split(",") for ln in proc.stdout.strip().split("\n")[1:] if ln]
    c_t = np.array([float(r[0]) for r in rows])
    c_feats = np.array([[float(v) for v in r[1:6]] for r in rows])
    c_err = np.array([float(r[6]) for r in rows])

    # --- Python reference ---
    py_feats, py_t = feat_mod.extract_features(run)
    py_x = T.normalize(py_feats, qp["feat_mean"], qp["feat_std"],
                       float(qp["input_clamp"]))
    py_err = qz.recon_error_int8(py_x, qp)

    print(f"\n=== {profile} (seed={seed}, {duration_h:g} h) ===")
    print(f"  C : {summary}")
    print(f"  py: windows={len(py_feats)}")

    ok = True
    if len(py_feats) != len(c_feats):
        print(f"  !! WINDOW COUNT MISMATCH: C={len(c_feats)} py={len(py_feats)}")
        return False
    if len(c_feats) == 0:
        print("  !! no windows emitted")
        return False

    dt_max = np.abs(c_t - py_t).max()
    print(f"  max |dt|            {dt_max:.3e} s")
    ok &= dt_max < 1e-6

    for j, name in enumerate(feat_mod.FEATURE_NAMES):
        d = np.abs(c_feats[:, j] - py_feats[:, j])
        rel = d / np.maximum(np.abs(py_feats[:, j]), 1e-9)
        print(f"  max |d {name:<15}| {d.max():.3e}   (rel {rel.max():.3e})")
        ok &= rel.max() < 1e-5 or d.max() < 1e-5

    derr = np.abs(c_err - py_err)
    print(f"  max |d recon_error| {derr.max():.3e}   "
          f"(mean {derr.mean():.3e})")
    ok &= derr.max() < 1e-4

    flips = np.sum((c_err > THRESHOLD) != (py_err > THRESHOLD))
    print(f"  class flips vs threshold {THRESHOLD}: {flips}")
    ok &= flips == 0

    print(f"  recon_error range   C  [{c_err.min():.4f} .. {c_err.max():.4f}]")
    print(f"                      py [{py_err.min():.4f} .. {py_err.max():.4f}]")
    return bool(ok)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", choices=datagen.PROFILES + ("all",),
                    default="all")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--duration-h", type=float, default=datagen.DEFAULT_DURATION_H)
    ap.add_argument("--keep-csv", action="store_true")
    args = ap.parse_args()

    exe = build()
    qp = dict(np.load(os.path.join(REPO, "model_training", "quant.npz")))

    profiles = datagen.PROFILES if args.profile == "all" else (args.profile,)
    results = {}
    for p in profiles:
        results[p] = run_profile(exe, p, args.seed, args.duration_h, qp,
                                 args.keep_csv)

    print("\n--- summary ---")
    for p, ok in results.items():
        print(f"  {p:<14} {'PASS' if ok else 'FAIL'}")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
