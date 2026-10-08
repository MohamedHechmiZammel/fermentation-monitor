"""
train.py -- hand-rolled 5 -> 3 (ReLU) -> 5 autoencoder, plain numpy.

No ML framework. Forward pass, backward pass and the Adam update are all
written out explicitly below, both because the project forbids framework
dependencies and because the C port has to mirror the forward pass exactly --
it is much easier to port something you wrote by hand.

    x_raw  (5)      [bubble_rate, mean_interval, std_interval,
                     interval_trend, temp_delta]
      |  z-score with per-feature (mean, std) learned from the NORMAL set,
      |  then clamp to +-INPUT_CLAMP sigma
      v
    x      (5)
      |  h = relu(x @ W1 + b1)
      v
    h      (3)      bottleneck
      |  y = h @ W2 + b2                      (linear output)
      v
    y      (5)
    recon_error = mean((x - y)^2)              <- the anomaly score

Trained on NORMAL-profile windows ONLY. The bottleneck can only represent the
low-dimensional manifold a healthy fermentation traces out; stuck and
contaminated windows sit off that manifold and reconstruct badly.

The (mean, std) normalization constants are NOT optional. The five features
are in bubbles/min, seconds, seconds, seconds and degrees C, spanning three
orders of magnitude; without z-scoring, mean_interval alone would dominate the
MSE and the other four features would be invisible to the model.

Usage:
    python train.py                 # train, write model.npz
    python train.py --epochs 6000 --seeds 0,1,2,3,4,5,6,7
"""

from __future__ import annotations

import argparse
import os

import numpy as np

from datagen import DEFAULT_DURATION_H, generate_run
from features import FEATURE_NAMES, N_FEATURES, extract_features

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL_PATH = os.path.join(HERE, "model.npz")

N_HIDDEN = 3
INPUT_CLAMP = 6.0      # z-scored features are clamped to +-6 sigma before the
                       # model sees them. Required: contaminated windows reach
                       # >100 sigma on interval_trend, which no int8 input
                       # quantization can represent. Clamping is applied
                       # identically in the float and fixed-point paths so the
                       # two never disagree. 6 sigma leaves ~1.6x headroom over
                       # the largest |z| any normal training window produces
                       # (3.8), while keeping the int8 input step small
                       # (6/127 = 0.047 sigma).

TRAIN_SEEDS = (0, 1, 2, 3, 4, 5)


# ---------------------------------------------------------------------------
# data
# ---------------------------------------------------------------------------

def collect_features(profile, seeds, duration_h=DEFAULT_DURATION_H, verbose=True):
    """Generate runs and stack their feature windows into one (N, 5) array."""
    chunks = []
    for s in seeds:
        run = generate_run(profile, seed=s, duration_h=duration_h)
        feats, _ = extract_features(run)
        chunks.append(feats)
        if verbose:
            print(f"  {profile}/seed{s}: {len(feats)} windows")
    return np.vstack(chunks)


def normalize(x_raw, mean, std, clamp=INPUT_CLAMP):
    """z-score then clamp. Mirrored verbatim in autoencoder.h."""
    z = (x_raw - mean) / std
    return np.clip(z, -clamp, clamp)


# ---------------------------------------------------------------------------
# model
# ---------------------------------------------------------------------------

def forward(x, W1, b1, W2, b2):
    """Float forward pass. Returns (y, h) -- h kept for the backward pass."""
    z1 = x @ W1 + b1
    h = np.maximum(z1, 0.0)      # ReLU
    y = h @ W2 + b2              # linear output
    return y, h


def recon_error(x, y):
    """Anomaly score: mean squared reconstruction error over the 5 features."""
    d = y - x
    return np.mean(d * d, axis=-1)


def train(x, epochs=6000, lr=0.01, batch=256, seed=0, verbose=True):
    """Adam + hand-written backprop on MSE. Returns (W1, b1, W2, b2)."""
    rng = np.random.default_rng(seed)
    n = x.shape[0]

    # He init for the ReLU layer, Xavier-ish for the linear output
    W1 = rng.normal(0.0, np.sqrt(2.0 / N_FEATURES), (N_FEATURES, N_HIDDEN))
    b1 = np.zeros(N_HIDDEN)
    W2 = rng.normal(0.0, np.sqrt(1.0 / N_HIDDEN), (N_HIDDEN, N_FEATURES))
    b2 = np.zeros(N_FEATURES)

    params = [W1, b1, W2, b2]
    m = [np.zeros_like(p) for p in params]
    v = [np.zeros_like(p) for p in params]
    beta1, beta2, eps = 0.9, 0.999, 1e-8
    step = 0

    for ep in range(epochs):
        idx = rng.permutation(n)
        for start in range(0, n, batch):
            xb = x[idx[start:start + batch]]
            bs = xb.shape[0]

            # --- forward ---
            z1 = xb @ W1 + b1
            h = np.maximum(z1, 0.0)
            y = h @ W2 + b2

            # --- backward (MSE = mean over batch and features) ---
            dy = 2.0 * (y - xb) / (bs * N_FEATURES)     # dL/dy
            gW2 = h.T @ dy
            gb2 = dy.sum(axis=0)
            dh = dy @ W2.T
            dz1 = dh * (z1 > 0.0)                        # ReLU gradient
            gW1 = xb.T @ dz1
            gb1 = dz1.sum(axis=0)

            # --- Adam ---
            step += 1
            for i, g in enumerate((gW1, gb1, gW2, gb2)):
                m[i] = beta1 * m[i] + (1 - beta1) * g
                v[i] = beta2 * v[i] + (1 - beta2) * (g * g)
                mhat = m[i] / (1 - beta1 ** step)
                vhat = v[i] / (1 - beta2 ** step)
                params[i] -= lr * mhat / (np.sqrt(vhat) + eps)
            W1, b1, W2, b2 = params

        if verbose and (ep % max(1, epochs // 10) == 0 or ep == epochs - 1):
            y_all, _ = forward(x, W1, b1, W2, b2)
            print(f"    epoch {ep:5d}  mse={recon_error(x, y_all).mean():.6f}")

    return W1, b1, W2, b2


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Train the anomaly autoencoder.")
    ap.add_argument("--epochs", type=int, default=3000)
    ap.add_argument("--lr", type=float, default=0.02)
    ap.add_argument("--batch", type=int, default=128)
    ap.add_argument("--seeds", default=",".join(str(s) for s in TRAIN_SEEDS),
                    help="comma-separated datagen seeds for the NORMAL training runs")
    ap.add_argument("--init-seed", type=int, default=3)
    ap.add_argument("--out", default=MODEL_PATH)
    args = ap.parse_args()

    seeds = [int(s) for s in args.seeds.split(",")]

    print(f"Generating {len(seeds)} NORMAL runs and extracting features...")
    raw = collect_features("normal", seeds)
    print(f"  total {raw.shape[0]} normal training windows\n")

    feat_mean = raw.mean(axis=0)
    feat_std = raw.std(axis=0)
    feat_std = np.where(feat_std < 1e-6, 1.0, feat_std)   # guard flat features

    print("Per-feature normalization constants (from the NORMAL training set):")
    for i, nm in enumerate(FEATURE_NAMES):
        print(f"  {nm:>14}: mean={feat_mean[i]:10.4f}  std={feat_std[i]:10.4f}")

    z_unclamped = (raw - feat_mean) / feat_std
    print(f"\n  max |z| in the training set: {np.abs(z_unclamped).max():.2f} sigma "
          f"(INPUT_CLAMP={INPUT_CLAMP})")
    clipped = (np.abs(z_unclamped) > INPUT_CLAMP).mean()
    print(f"  fraction of training values clamped: {clipped * 100:.3f}%\n")

    x = normalize(raw, feat_mean, feat_std)

    print(f"Training 5 -> {N_HIDDEN} (ReLU) -> 5 autoencoder "
          f"({args.epochs} epochs, lr={args.lr}, batch={args.batch})")
    W1, b1, W2, b2 = train(x, epochs=args.epochs, lr=args.lr,
                           batch=args.batch, seed=args.init_seed)

    y, _ = forward(x, W1, b1, W2, b2)
    err = recon_error(x, y)
    pct = np.percentile(err, [50, 90, 99, 99.9, 100])
    print("\nTraining-set reconstruction error percentiles:")
    for p, v in zip((50, 90, 99, 99.9, 100), pct):
        print(f"  p{p:<5}: {v:.6f}")

    np.savez(args.out,
             W1=W1, b1=b1, W2=W2, b2=b2,
             feat_mean=feat_mean, feat_std=feat_std,
             input_clamp=np.float64(INPUT_CLAMP),
             n_hidden=np.int32(N_HIDDEN),
             train_seeds=np.asarray(seeds),
             train_raw=raw,
             train_err=err)
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
