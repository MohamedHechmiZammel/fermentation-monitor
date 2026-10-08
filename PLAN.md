# Fermentation Monitor — Implementation Plan

## Layer Build Order
Build bottom-up: firmware first (generates data), then recorder (consumes MQTT),
then API (serves data), then dashboard (consumes API).

---

## Phase M — Anomaly Detection Model (Training Pipeline)
**Goal:** train, quantize, and validate the on-device autoencoder; emit `firmware/src/model_weights.h`.

### Steps
1. Port `Sim::deltaPa()`'s math to Python (`model_training/datagen.py`): Gaussian envelope, bubble-period formula, 90/10 sawtooth, noise
2. Add `stuck` and `contamination` synthetic anomaly modes with tunable parameters
3. Write a feature extractor (`features.py`) that exactly mirrors the on-device bubble-detection algorithm — training features must equal runtime features
4. Train a 5→3(ReLU)→5 autoencoder on NORMAL-only features with plain numpy; compute per-feature (mean, std) z-score normalization from the same set
5. Hand-derive int8 quantization (weights, biases, input/output scales, symmetric per-tensor) and export the normalization constants alongside them
6. Write `export_weights.py` → emits `firmware/src/model_weights.h` (committed, not a secret)
7. Validate the quantized model against labeled stuck/contaminated runs; pick reconstruction-error threshold (start: ~99th percentile of normal) and N-consecutive-windows (start: N=3)
8. Cross-check the numpy float forward pass against the hand-written int8 fixed-point forward pass (Python-side) before porting to C
9. Write `model_training/README.md`: how to run, chosen threshold/N, and the synthetic-ground-truth limitation

**Validation:** `python model_training/validate.py` prints reconstruction-error percentiles for normal/stuck/contaminated runs with clean separation at the chosen threshold; `python model_training/quantize.py --check` confirms float vs. fixed-point agreement before any C is written.

---

## Phase 1 — Firmware (ESP32)
**Goal:** ESP32 publishes valid JSON to `fermentation/sensor` every 30 s.

### Steps
1. Scaffold PlatformIO project (`firmware/`)
2. Write `config.example.h` with SSID, MQTT broker IP, topic, pins
3. Implement BMP280 read (I2C, Adafruit library)
4. Implement DHT22 read (DHT library)
5. Implement baseline pressure: average 10 readings on boot, store in EEPROM
6. Implement delta_pressure computation
7. Build JSON payload with ArduinoJson
8. Implement MQTT connect + auto-reconnect (QoS 0; PubSubClient does not support QoS 1 publish)
9. Add NTP sync after WiFi connect — timestamps must be Unix epoch, not boot-relative millis
10. Add BOOT-button long-press to reset baseline
11. Implement `bubble_detector.h`: duty-cycled 5–10 Hz burst sampling + drop-edge detection (real + `SIMULATION` paths), as a non-blocking state machine sequenced so it never overlaps the 30 s `publishReading()` BMP280 read
12. Temporarily reconfigure `bmp.setSampling()` to a lighter filter during the burst window, restore `FILTER_X16` after
13. Implement a 2-minute feature-window aggregator producing `[bubble_rate, mean_interval, std_interval, interval_trend, temp_delta]`
14. Implement `autoencoder.h`: apply (mean, std) normalization from `model_weights.h`, then fixed-point int8 matmul + ReLU forward pass
15. Add N-consecutive-window anomaly flag state; threshold/N are hardcoded constants from Phase M
16. Extend `publishReading()` JSON additively with `anomaly`/`recon_error` (sentinel values from boot); bump `StaticJsonDocument<192>` → `<256>`
17. Extend `sim_sensors.h` with discrete bubble events matching `Sim::deltaPa()`'s edges, driven by a new `SIM_PROFILE` build flag
18. Reset bubble-detector/aggregator state whenever `captureBaseline()` runs

**Validation:** Serial monitor shows valid JSON with Unix epoch `ts` + MQTT broker receives on correct topic.
Spot-check: `ts` value should be within a few seconds of `date +%s` on your laptop.
Additionally: serial monitor shows `[bubble] interval=Xs` logs during burst windows, and every published JSON includes `anomaly`/`recon_error` from boot with the existing 5 fields unchanged in shape/order.

---

## Phase 2 — Backend: Recorder
**Goal:** Python subscriber writes every MQTT message to SQLite reliably.

### Steps
1. Create `backend/requirements.txt` (paho-mqtt, sqlite3 stdlib)
2. Write `recorder.py`:
   - Connect to Mosquitto on `localhost:1883`
   - Subscribe to `fermentation/sensor`
   - On message: parse JSON, insert row into `readings` table
   - Create table if not exists on startup
3. Test with `mosquitto_pub` manually injected messages
4. Add a guarded `ALTER TABLE` migration for `anomaly`/`recon_error` columns in `ensure_schema()` — the existing `CREATE TABLE IF NOT EXISTS` silently no-ops against the real, already-existing `fermentation.db`
5. Parse the new fields with `.get()` defaults in `on_message()`

**Validation:** `sqlite3 data/fermentation.db "SELECT COUNT(*) FROM readings"` grows over time.
Additionally: against the **existing** `backend/data/fermentation.db`, `sqlite3 backend/data/fermentation.db ".schema readings"` shows the new columns after restart, old rows unaffected.

---

## Phase 3 — Backend: API
**Goal:** FastAPI exposes clean REST endpoints consumed by dashboard.

### Steps
1. Add FastAPI + uvicorn to `requirements.txt`
2. Write `api.py`:
   - `GET /readings` with `limit` + `since` query params
   - `GET /summary` — computes activity level in Python
   - `GET /health`
3. Add CORS middleware (allow `localhost:5173`)
4. Test all endpoints with `curl`
5. Mirror the identical `ALTER TABLE` migration in `lifespan()` (schema DDL is duplicated between `recorder.py` and `api.py`)
6. (Optional) Extend `/summary` with `anomaly_active`/latest `recon_error`

**Validation:** `curl localhost:8000/readings` returns well-formed JSON array.
Additionally: `curl localhost:8000/readings?limit=1` includes `anomaly`/`recon_error` keys, no other shape change.

---

## Phase 4 — Dashboard (React + Vite)
**Goal:** Live dashboard shows fermentation curve refreshed every 15 s.

### Steps
1. Scaffold Vite project (`dashboard/`) with React 18 + Tailwind
2. Write `useReadings.js` hook — polls `/readings?since=<last_ts>` every 15 s, accumulates data
3. Write `PressureChart.jsx` — Recharts LineChart of `delta_pa` over time
4. Write `TempHumidChart.jsx` — dual-line chart for temp (BMP + DHT) and humidity
5. Write `ActivityBadge.jsx` — colour-coded badge from `/summary`
6. Write `StatusBar.jsx` — elapsed time counter
7. Assemble `App.jsx` — layout with all components
8. Wire Vite proxy to API (`/api → localhost:8000`)
9. Add `--anomaly`/`--anomaly-glow` tokens to `index.css` `:root`, matching the existing `--active`/`--active-glow` pattern
10. Build `AnomalyBadge.jsx` as a **separate** indicator (not folded into `ActivityBadge`'s state enum)
11. In `DashboardPage.jsx`, derive `anomaly` from `readings.at(-1)?.anomaly` (`/summary` doesn't carry it) and pass into `TopBar`; render `AnomalyBadge` alongside `ActivityBadge`
12. Add anomaly markers to `HistoryPage`'s chart

**Validation:** Dashboard loads, chart animates as new data arrives, badge updates.
Additionally: `mosquitto_pub -t fermentation/sensor -m '{...,"anomaly":true,"recon_error":4.2}' -r` lights up `AnomalyBadge` within one poll cycle, without touching `ActivityBadge`'s existing rendering.

---

## Phase 5 — Integration & Polish
1. Add `README.md` with wiring diagram, setup steps, and `mosquitto.conf`
   Note: recorder.py and api.py are two separate processes — README must document running both
   (e.g., two terminal tabs or a Procfile)
2. Add `.gitignore` (config.h, data/, node_modules/, .pio/)
3. Test full stack: ESP32 → MQTT → recorder → API → dashboard
4. Tune `delta_pa` threshold (0.5 Pa default) based on real bubble test with airlock submerged 1 cm
5. Full-stack smoke test including anomaly fields, ESP32→MQTT→recorder→API→dashboard
6. Document the synthetic-ground-truth limitation prominently in README
7. Write a top-level architecture write-up explaining the hand-rolled-vs-TFLM/Edge-Impulse
   decision and the bubble-detection → feature-extraction → quantized-autoencoder pipeline —
   this is the artifact that demonstrates the technical depth the feature was built for

---

## Dependency Map
```
Phase M (training pipeline, pure Python) ──produces model_weights.h──┐
Phase 1 (firmware) ─────────────────────────────────────────────────┤
Phase 2 (recorder) ← requires Mosquitto running                     ┤
Phase 3 (API)      ← requires recorder + SQLite                     ┤→ Phase 5
Phase 4 (dashboard)← requires API running                           ┘
Phase W (Wokwi)    ← requires Phase 1's SIM_PROFILE                 ┘
```

Phase M has no dependency on anything else and can be built and iterated on entirely in
parallel with Phases 2–4, but must complete before Phase 1's anomaly-detection steps
(11–18) since they consume `model_weights.h`. Phases 1 and 2 can otherwise be developed in
parallel (firmware mocked with mosquitto_pub), as can Phase 3. Phase 4 depends on Phase 3's
fields existing in `/readings`. Phase W depends on Phase 1's `SIM_PROFILE` flag and firmware
anomaly logic.
