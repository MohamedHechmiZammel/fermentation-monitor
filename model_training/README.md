# `model_training/` — offline pipeline for the on-device anomaly detector

Trains, quantizes and validates the tiny autoencoder that flags stuck or
contaminated fermentation directly on the ESP32, and emits
`../firmware/src/model_weights.h`.

**No ML framework.** numpy is the only dependency. The forward pass, backward
pass, Adam update and int8 quantization are all written out by hand — partly
because `CONSTITUTION.md` rules TensorFlow Lite Micro / Edge Impulse out of
scope, and partly because the firmware has to reimplement the forward pass in
fixed-point C, and you cannot port what you cannot read.

```
datagen.py  ->  features.py  ->  train.py  ->  quantize.py  ->  validate.py  ->  export_weights.py
 synthetic      bubble edges     5-3-5 AE      symmetric        threshold &      firmware/src/
 pressure       + 5 features     (float)       int8 + check     N selection      model_weights.h
```

---

## Running it

```bash
cd model_training
pip install -r requirements.txt          # numpy only

python train.py                          # -> model.npz     (~1 min)
python quantize.py --check               # -> quant.npz     (~25 s)
python validate.py                       # -> threshold.json (~25 s), exits 1 on FAIL
python export_weights.py                 # -> ../firmware/src/model_weights.h
```

Order matters: `export_weights.py` refuses to run if `validate.py` reported
FAIL, so a model that does not separate the classes cannot reach the firmware.

`model.npz`, `quant.npz` and `threshold.json` are gitignored intermediates. The
committed artifact is `firmware/src/model_weights.h` — it holds no secrets
(unlike `firmware/src/config.h`) and is meant to be reviewed in diffs.

### Inspecting the pieces

```bash
python datagen.py --profile all                 # summary of one run per profile
python datagen.py --compare-phase-modes         # why sim_sensors.h's fmod is wrong (below)
python features.py --profile stuck --every 100  # feature trajectory for one run
python train.py --epochs 6000 --seeds 0,1,2,3,4,5,6,7
python validate.py --n-windows 5
```

---

## What the pipeline produces

| Constant | Value |
|---|---|
| `AE_RECON_THRESHOLD` | **0.352953** |
| `AE_ANOMALY_N_WINDOWS` | **3** (3 × 2 min = 6 min of confirmation) |
| Feasible threshold band | 0.2176 … 0.5725 (2.63× wide) |
| float ↔ int8 epsilon (normal + stuck) | **0.002008** |

### Why that threshold

`SPEC.md` proposes starting at the 99th percentile of normal reconstruction
error. That percentile is **0.1916**, and it is reported — but it is not
usable, for a reason worth writing down:

> A normal run's reconstruction error is a smooth function of fermentation
> time, so its worst 1 % of windows are **contiguous** — they sit at the very
> start and very end of the curve where the learned manifold is most bent. An
> N-consecutive-windows rule gives no protection against a systematic error
> peak; a threshold at p99 would trip on every normal run. The threshold has to
> clear the normal **maximum**, not the normal p99.

`validate.py` therefore searches explicitly. For each candidate threshold it
counts how many held-out runs of each class raise the N-consecutive flag, and
reports the band that flags every anomalous run and no normal run. At N = 3
that band is 0.2176 … 0.5725; the chosen value is its geometric centre, which
maximises the multiplicative margin on both sides:

* **1.58×** above the worst normal window ever observed (0.2240)
* **1.53×** below the 5th percentile of a stalled run's over-threshold windows (0.5396)

### Why N = 3

On this synthetic data N buys nothing: every N from 1 to 6 is feasible with a
near-identical band, because both anomaly modes stay over threshold for
hundreds of consecutive windows once they start. N = 3 is kept anyway, per
`SPEC.md`, because real hardware has failure modes the simulator does not: an
I2C hiccup, a knocked vessel, or a burst window straddling a baseline reset. At
N = 1 any of those becomes an alert. N = 3 costs four extra minutes of latency
against a stall that takes hours to develop.

### Held-out separation (int8 path, 8 runs per class, seeds 30–37)

| class | p50 | p90 | p99 | max | runs flagged | median latency |
|---|---|---|---|---|---|---|
| normal | 0.0182 | 0.0797 | 0.1916 | 0.224 | **0 / 8** | never |
| stuck | 0.5587 | 0.5827 | 0.5969 | 0.607 | **8 / 8** | 8.8 ferm-hours |
| contamination | 0.5279 | 8.6526 | 9.7875 | 10.284 | **8 / 8** | 1.2 ferm-hours |

Clean separation: every normal window sits below 0.2240 and the sustained
stuck level is ~0.56, a gap of 2.5×. Stuck runs stall at fermentation hour 8
and are flagged at hour 8.8 — a detection latency of under an hour of
fermentation time.

### Quantization fidelity

`python quantize.py --check` compares the float numpy forward pass against the
hand-written int8 fixed-point pass on held-out runs:

```
             set      n    max|dy|   mean|dy|  max|d err|  mean|d err|
          normal   3033   0.055708   0.010746    0.002008     0.000504
           stuck   3033   0.055708   0.012265    0.001748     0.000316
   contamination   3110   2.909907   0.386769    4.604653     0.596901
```

**epsilon = 0.002008** over normal and stuck windows. That is the figure that
constrains the decision: it is 0.57 % of the threshold and ~190× smaller than
the 0.38-wide gap between the two classes, so no normal or stuck window can
change class. A threshold sweep in the `--check` output confirms it — every
class flip is a contamination window.

The much larger all-windows epsilon (4.6) comes entirely from grossly
contaminated windows whose float hidden activations exceed `127 * s_h` and
saturate in the integer path. `s_h` is calibrated on the normal training set,
as it must be — widening it to fit anomalies would coarsen the hidden
resolution exactly where precision is needed. Saturation can only pull an
extreme anomaly score *down*, and it pulls it down to ~10, still ~30× above the
threshold. It cannot cause a missed detection.

The C snippet embedded in `model_weights.h` was compiled and run against the
same feature vectors: it agrees with `quantize.py:forward_int8` to **2.5e-6**
(float32 rounding only) with zero class flips.

---

## Design decisions and deviations

Everything below deviates from, or resolves an ambiguity in, `SPEC.md` /
`PLAN.md` / `TASKS.md`. Each one was forced by a measurement, and the
measurement is given.

### 1. `sim_sensors.h`'s sawtooth phase is wrong; datagen fixes it — firmware must too

`Sim::deltaPa()` computes the sawtooth phase as

```c
cycle_pos = fmodf(t_sim_s, bubble_period_s) / bubble_period_s;
```

with a **time-varying** period. That is not a phase accumulator. Writing
`g(t) = t / P(t)`, drop edges occur when `frac(g)` crosses 0.9, and

```
g'(t) = 1/P  -  t·P'(t)/P²
```

The second term dominates once `t` is large. Measured (`python datagen.py
--compare-phase-modes`):

```
  h0    h2    h4    h6    h8    h10   h12   h14   h16   h18   h20   h22   h24   ...
  13    14    15    16    18    21    27    36    53    90    195   615   1060    <- intended 1/P
  13    14    15    16    18    21    27    36    52    89    195   615   1060    <- accumulator
  13    14    17    21    29    43    68    122   249   617   2083  9560  8010    <- fmod (as written in C)
```

At fermentation hour 22 the intended period is 8.2 s but the fmod formulation
emits an edge every ~0.56 s — 15× too fast. Worse, after ~hour 26 the phase
slope goes **negative**, so the sawtooth runs backwards: a slow ramp *down*
with instantaneous jumps *up*, which contains no bubble drop edges at all.
Neither behaviour is physical, and neither matches the C file's own comment
("3 s at peak activity, 300 s when nearly finished").

`datagen.py` defaults to `phase_mode="accumulator"` (`phase += dt/P(t)`), which
reproduces the *intent* of the C code exactly — same envelope, same period
formula, same 90/10 sawtooth, same ±3 Pa noise — and tracks 1/P to within one
edge per hour. `phase_mode="fmod"` keeps the literal transcription available so
the discrepancy stays inspectable.

> **Action for TASKS.md F19:** `sim_sensors.h` needs the same fix. If the
> firmware keeps the `fmodf` form, its bubble intervals will not come from the
> distribution this model was trained on and the thresholds above are void.

### 2. Time domain is fermentation time, not Wokwi wall-clock

Everything here is in fermentation seconds: bubble intervals of 3–300 s, a
120 s feature window, a 35-hour run. That is what a real BMP280 on a real
airlock sees. `sim_sensors.h`'s 60× compression (1 real minute = 1 fermentation
hour) is a property of the Wokwi harness, not of the signal — under it a 3 s
bubble period becomes 50 ms, which a 10 Hz sampler cannot resolve at all. The
firmware's `SIM_PROFILE` path has to emit discrete bubble events on a timebase
the detector can actually sample.

### 3. The interval buffer rolls across windows, and windows are suppressed until it is full

`SPEC.md` says `interval_trend` is the half-window delta of "the window's
interval buffer". Read literally as *per-window*, that breaks: near the start
and end of a fermentation the bubble period is 200–300 s, so a 120 s window
holds 0 or 1 bubbles and mean/std/trend are undefined.

The aggregator therefore keeps a **rolling 16-interval buffer that persists
across window boundaries**; only `bubble_rate` resets per window. And it emits
**nothing until that buffer is full** (`MIN_INTERVALS == INTERVAL_BUF_LEN ==
16`). The second half of that was not optional — measured, allowing
partially-filled windows pushed the normal p99 from 0.2185 to 0.3134, above the
stuck level of ~0.30, and destroyed the separation outright. Windows built from
4 intervals are simply a different statistical population from windows built
from 16.

Cost: no anomaly detection for the first ~1.2 fermentation hours, while 16
intervals at ~275 s accumulate. Acceptable — a batch cannot be diagnosed as
stuck before it has had a chance to start.

### 4. `temp_delta` is measured against a boot baseline, not within the window

Over 120 fermentation-seconds the thermal envelope moves ~0.005 °C, far below
the DHT22's 0.1 °C resolution, so a within-window temperature delta is pure
noise. `temp_delta` is instead the window's mean temperature minus a boot-time
temperature baseline (the mean of the first completed window), mirroring how
the firmware already treats the pressure baseline.

### 5. The bubble detector needs hysteresis, not just a refractory period

The first implementation tracked a running peak, fired when the signal fell
`DROP_THRESHOLD_PA` below it, and used a refractory period to suppress
double-counting. That is wrong in a way that is easy to miss: the boxcar
spreads one pressure collapse over `SMOOTH_N` samples, so the peak gets reset
to a **mid-fall** value; the sawtooth's flat 10 % zero-gap that follows then
only needs a downward noise excursion to re-trigger.

Measured on normal seed 0: 26 spurious short intervals per run (and 92 per
stuck run), each one splitting a real 200 s interval into two and polluting the
next 16 windows through the rolling buffer. `std_interval` reached 92 s where
the true value was ~3 s.

The detector is now a two-state hysteresis machine — RISING tracks the peak and
fires on a `DROP_THRESHOLD_PA` fall; FALLING tracks the trough and only re-arms
after a `DROP_THRESHOLD_PA` rise. With `DROP_THRESHOLD_PA = 6.0` (the full
±3 Pa noise band — a boxcar mean of samples drawn from (−3, +3) also lies in
(−3, +3), so its range is strictly < 6), a flat noisy stretch can neither fire
nor re-arm, and false bubbles from sensor noise alone are *structurally*
impossible. After the fix, detected bubble counts match the generator's true
cycle counts exactly (5516 / 5516 on normal, 581 / 581 on stuck).

### 6. The 5→3→5 architecture is PCA-3, and that shaped the rest of the pipeline

Worth knowing before anyone tries to "improve" the training: with a 3-unit ReLU
bottleneck and a 5-dimensional input whose intrinsic linear dimension is 4, the
optimizer converges to the linear PCA-3 solution every time. The training set's
covariance eigenvalues are `[3.8253, 0.6948, 0.3303, 0.1504, 0.0001]`, so the
PCA-3 residual MSE is `(0.1504 + 0.0001) / 5 = 0.030097`. Every restart tried —
36 of them across init seeds, bias-init scales and learning-rate schedules —
converged to 0.0302 ± 0.0005. The reason is structural: to beat PCA the model
would have to switch a ReLU unit off over part of the data, but with only 3
units and rank 3 needed everywhere, there is no spare capacity for kinks.

So the separation had to come from the feature side, not from model capacity —
which is what §3 and §5 are about. Do not expect longer training or a better
optimizer to help.

### 7. Inputs are clamped to ±6 σ

Contaminated windows reach >100 σ on `interval_trend`, which no int8 input
quantization can represent. `normalize()` clamps the z-scored vector to ±6 σ
and the clamp is applied identically in the float and fixed-point paths, so the
two never disagree because of it. 6 σ leaves ~1.6× headroom over the largest
|z| any normal training window produces (3.8) while keeping the int8 input step
at 0.047 σ.

---

## Limitations

**The ground truth here is entirely synthetic.** The `stuck` and
`contamination` profiles in `datagen.py` were invented for this project. They
are not derived from, calibrated against, or validated on any real dataset of
stuck or contaminated fermentation. Specifically:

* `stuck` is modelled as the Gaussian activity envelope freezing at
  fermentation hour 8 and decaying slowly. Real stalls have many causes
  (temperature crash, nutrient deficiency, high gravity, yeast flocculating
  early) and there is no reason to believe they all present as a frozen
  envelope.
* `contamination` is modelled as a piecewise-random multiplier on the bubble
  period, redrawn every 0.3–1.5 hours. Real contamination (lacto, pedio, wild
  yeast, acetobacter) produces gas at rates and in patterns this crude model
  does not attempt to reproduce, and some contaminations produce a *faster*
  and perfectly smooth curve that this detector would call healthy.
* The sensor model is the simulator's: uniform ±3 Pa pressure noise, Gaussian
  DHT22 noise at 0.1 °C resolution. It has no barometric drift, no temperature
  cross-sensitivity, no airlock water-level change, no vessel disturbance.

The reported numbers — 0/8 false positives, 8/8 detections, a 2.63× feasible
threshold band — describe how well the model separates *these synthetic
profiles*, and nothing more. They are evidence that the pipeline works end to
end and that the quantization is sound; they are **not** evidence of real-world
detection accuracy.

Real-world accuracy is unvalidated until the detector is run against an actual
stuck or contaminated batch. Until then, treat every on-device alert as
provisional, and expect the threshold to need recalibration against real data.
