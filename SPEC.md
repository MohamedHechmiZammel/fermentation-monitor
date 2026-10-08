# Fermentation Monitor — Specification

## Overview
A self-contained IoT system: one ESP32 node reads sensors and publishes over MQTT; a Python
service subscribes and stores data in SQLite; a FastAPI backend exposes a REST API; a React+Vite
dashboard visualises the fermentation curve in real-time.

---

## Hardware Bill of Materials

| Part              | Role                              | Interface  | Est. Cost |
|-------------------|-----------------------------------|------------|-----------|
| ESP32 DevKit v1   | Microcontroller + WiFi            | —          | $4        |
| BMP280            | Barometric pressure (CO₂ proxy)   | I2C        | $1.50     |
| DHT22             | Ambient temperature + humidity    | 1-wire GPIO| $2        |
| 3–6mm silicone tube | Connect airlock to BMP280 port | —          | $0.50     |
| Airlock (S-type)  | Standard homebrew airlock         | —          | $1        |

**Total BOM cost: ~$9**

---

## Sensor Wiring (ESP32 DevKit v1)

```
BMP280
  SDA  → GPIO 21
  SCL  → GPIO 22
  VCC  → 3.3V
  GND  → GND

DHT22
  DATA → GPIO 4 (with 10kΩ pull-up to 3.3V)
  VCC  → 3.3V
  GND  → GND
```

Physical setup: drill a second 6mm hole in the vessel lid (separate from the airlock hole).
Fit a short silicone tube from this hole → BMP280 sensor port, sealed airtight with food-grade
epoxy or a rubber grommet. BMP280 measures actual vessel headspace overpressure (sawtooth pattern:
slow rise between CO₂ bubbles, sharp drop when a bubble fires through the airlock).
Do NOT tap at the airlock outlet — downstream of the water columns, the signal there is ambient.

---

## Firmware (ESP32 — Arduino / ESP-IDF)

### Language & Framework
- Arduino framework via PlatformIO (C++)
- Libraries: `Adafruit BMP280`, `DHT sensor library`, `PubSubClient` (MQTT)

### Sampling Loop (every 30 s)
1. Read BMP280: pressure (Pa) + temperature (°C)
2. Read DHT22: temperature (°C) + humidity (%RH)
3. Compute `delta_pressure = current_pressure - baseline_pressure` (Pa)
4. Build JSON payload:
```json
{
  "ts": 1720000000,
  "pressure_pa": 101325.4,
  "delta_pressure_pa": 0.82,
  "temp_bmp": 22.1,
  "temp_dht": 22.3,
  "humidity": 58.4
}
```
5. Publish to MQTT topic: `fermentation/sensor`

### Baseline Pressure
- Initialised at boot: average of first 10 readings over 5 min (vessel open, before sealing).
- Updated continuously with an exponential moving average (α = 0.001, τ ≈ 8 h at 30 s interval).
  This filters out slow barometric drift (hours-scale) while preserving fermentation signal (minute-scale).
- Boot value stored in EEPROM; re-seeded on long press of BOOT button.
- Rationale: fixed-boot baseline accumulates hundreds of Pa of barometric drift over multi-day
  fermentation, making the delta signal unusable. Rolling EMA is the correct approach.

### Timestamp Sync (NTP)
- On WiFi connect, call `configTime(0, 0, "pool.ntp.org")` and wait for sync before sampling.
- Payload `ts` field is Unix epoch seconds (not millis since boot).
- If NTP fails to sync within 10 s, log a warning and use relative millis — dashboard uses
  `received_at` (server wall clock) as fallback for time axis.

### WiFi & MQTT
- SSID/password + broker IP stored in `config.h` (not version-controlled)
- Reconnects automatically on disconnect
- QoS 0 for publish (PubSubClient does not support QoS 1 for publish; local WiFi makes this acceptable)

### MQTT Command Subscriber (`fermentation/cmd`)
- Firmware subscribes to `fermentation/cmd` in addition to publishing to `fermentation/sensor`
- Handles incoming JSON commands:
```json
{ "action": "reset_baseline" }
```
- On `reset_baseline`: re-runs the 10-reading boot average, saves new baseline to EEPROM
- This is how `POST /device/reset-baseline` (API) reaches the device remotely

---

## Backend

### Broker
- **Mosquitto 2.x** — config at `backend/mosquitto.conf`
- Listens on `0.0.0.0:1883` (LAN-accessible so ESP32 can reach it over WiFi)

#### Persistence
- `persistence true` — broker survives restarts without losing queued messages
- `autosave_interval 60` — flushes in-memory queue to disk every 60 s

#### Retained Messages
- Firmware publishes with `RETAIN = 1` flag (set in PubSubClient `publish()` call)
- Broker stores the last reading per topic; new subscribers get it immediately on connect
- Eliminates the "wait up to 30 s for first data" problem on dashboard load

#### Clean Session
| Client | Setting | Reason |
|---|---|---|
| ESP32 publisher | `clean_session = true` | Stateless publisher; no queuing needed |
| Python recorder | `clean_session = false` | Broker queues missed messages during brief disconnects; delivered on reconnect |
- Client IDs must be unique and stable: `"fermentation-esp32"`, `"fermentation-recorder"`

#### Service Management
- Development: `mosquitto -c backend/mosquitto.conf -v`
- Production: user-level systemd unit (`~/.config/systemd/user/fermentation-mqtt.service`)
- Commands: `systemctl --user start|stop|restart|status fermentation-mqtt`

#### Hardening (optional — disabled by default in `mosquitto.conf`)

**User Management & Password Auth**
```bash
mosquitto_passwd -c backend/passwd esp32      # create file + esp32 user
mosquitto_passwd    backend/passwd recorder   # add recorder user
```
Enable in `mosquitto.conf`: `password_file`, `allow_anonymous false`

**ACL (Access Control Lists)**
- `backend/acl` restricts publish/subscribe per user:
  - `esp32` → `topic write fermentation/sensor` only
  - `recorder` → `topic read fermentation/sensor` only
- Requires password auth to be active

**TLS (port 8883)**
- Self-signed CA + server cert (free, `openssl` commands documented in `mosquitto.conf`)
- Firmware: `WiFiClientSecure` with embedded CA cert
- Recorder: `paho-mqtt tls_set(ca_cert=...)`
- Adds encryption to all LAN traffic; required if exposing broker beyond localhost

### Data Recorder (Python)
- File: `backend/recorder.py`
- Subscribes to `fermentation/sensor`
- Writes each message to SQLite (`data/fermentation.db`)

#### Schema

**`readings` table**
```sql
CREATE TABLE readings (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  ts           INTEGER NOT NULL,
  received_at  INTEGER NOT NULL,
  pressure_pa  REAL NOT NULL,
  delta_pa     REAL NOT NULL,
  temp_bmp     REAL NOT NULL,
  temp_dht     REAL NOT NULL,
  humidity     REAL NOT NULL,
  batch_id     INTEGER REFERENCES batches(id)  -- NULL until batch tracking enabled
);
CREATE INDEX idx_readings_received_at ON readings(received_at);
```

**`batches` table** — tracks individual fermentation runs
```sql
CREATE TABLE batches (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  name        TEXT NOT NULL,             -- e.g. "Wheat Beer #3"
  started_at  INTEGER NOT NULL,          -- Unix timestamp
  ended_at    INTEGER,                   -- NULL = active batch
  notes       TEXT
);
```

New batch endpoints (admin/operator):
| Method | Path | Role | Description |
|---|---|---|---|
| GET | `/batches` | viewer+ | List all batches |
| POST | `/batches` | operator+ | Start a new batch (marks current time as started_at) |
| PUT | `/batches/{id}/end` | operator+ | Close a batch |
| GET | `/batches/{id}/readings` | viewer+ | Readings scoped to one batch |

### REST API (FastAPI)
- File: `backend/api.py`
- Runs on `localhost:8000`

#### RBAC — Roles
| Role | Permissions |
|---|---|
| `viewer` | Read sensor data and summary |
| `operator` | viewer + trigger device actions (baseline reset, service status) |
| `admin` | operator + user management (create / update / deactivate users) |

Authentication: JWT (HS256). Login at `POST /auth/token` → `Bearer` token in `Authorization` header.
Token duration: 8 hours (configurable in `.env`). Passwords hashed with bcrypt.

#### Endpoints
| Method | Path | Role | Description |
|--------|------|------|-------------|
| POST | `/auth/token` | public | Login; returns JWT + role |
| GET | `/auth/me` | any | Current user info |
| GET | `/readings` | viewer+ | Last N readings (default 100, max 2000) |
| GET | `/readings?since=<ts>` | viewer+ | All readings after Unix timestamp |
| GET | `/summary` | viewer+ | Activity level, bubble rate, duration |
| GET | `/health` | public | API liveness |
| POST | `/device/reset-baseline` | operator+ | Sends MQTT command to ESP32 to re-seed baseline |
| GET | `/service/status` | operator+ | Recorder heartbeat check, last reading lag |
| GET | `/users` | admin | List all users |
| POST | `/users` | admin | Create user |
| PUT | `/users/{id}` | admin | Update role / password / active state |
| DELETE | `/users/{id}` | admin | Deactivate user (soft delete) |

#### Key files
- `backend/auth.py` — JWT encode/decode, `require_role()` dependency factory, bcrypt helpers
- `backend/users.py` — User CRUD, Pydantic models, `users` table DDL
- `backend/api.py` — Route handlers with `Depends(AnyRole / OperatorPlus / AdminOnly)`
- `backend/.env` — `JWT_SECRET_KEY`, DB path, MQTT config (gitignored)

#### Activity Level Calculation
```
-- "active" / "slow": use last 10 min window (20 readings at 30 s interval)
bubble_rate = count of readings WHERE |delta_pa| > 0.5 AND ts > now()-600

activity = "active"   if bubble_rate > 3      (≥4 non-zero readings in 10 min)
           "slow"     if bubble_rate in 1–3
           "finished" if zero_count_30m == 0   (ALL readings in 30 min below threshold)

-- "finished" uses a separate 30-min window query (60 readings), not the 10-min one
zero_count_30m = count of readings WHERE |delta_pa| > 0.5 AND ts > now()-1800
finished = (zero_count_30m == 0)
```

Note: `delta_pa` here is the SQLite column name. The firmware JSON field is `delta_pressure_pa`;
the recorder must map `delta_pressure_pa` → `delta_pa` on insert.

---

## Frontend (React + Vite)

### Pages / Views
1. **Live Dashboard** (default view)
   - Real-time line chart: `delta_pressure_pa` over time (last 24 h)
   - Temperature and humidity strip charts
   - Activity badge: `ACTIVE` / `SLOW` / `FINISHED` with colour coding
   - "Time fermenting" counter

2. **History View**
   - Full fermentation curve since start
   - Zoom / pan on time axis

### Tech Stack
- React 18 + Vite
- Recharts for charts
- Tailwind CSS for styling (reuse COFAT design tokens where applicable)
- Polling strategy:
  - On mount: call `GET /readings?limit=2000` to seed the chart with recent history
  - Then every 15 s: call `GET /readings?since=<last_ts>` to append new rows only
  - Keep at most 2,880 rows in component state (last 24 h at 30 s interval) to avoid slow renders

---

## Anomaly Detection (On-Device TinyML)

### Approach
Stuck or contaminated fermentation is detected directly on the ESP32 from the CO₂-bubble
pressure sawtooth pattern (see Sensor Wiring), using a hand-rolled tiny autoencoder —
trained offline in Python/numpy, quantized to int8 by hand, forward pass hand-ported to
~20 lines of fixed-point C. No ML framework (TensorFlow Lite Micro, Edge Impulse) ships
on the device; this is a deliberate choice over those alternatives (see Constitution,
Out of Scope) to keep the on-device footprint dependency-free and fully auditable.

### Offline Training Pipeline (`model_training/`)
- Data source: a Python port of `Sim::deltaPa()`'s exact math (Gaussian activity envelope,
  bubble-period formula, 90/10 sawtooth, ±3 Pa noise), extended with `stuck` and
  `contamination` synthetic anomaly modes.
- Feature vector (2-minute aggregation window): `[bubble_rate, mean_interval, std_interval,
  interval_trend, temp_delta]`. `interval_trend` = second-half-mean − first-half-mean of the
  window's interval buffer (cheap, exactly mirrorable in fixed-point C — not a regression slope).
- Model: 5 → 3 (ReLU, bottleneck) → 5 autoencoder, trained on NORMAL-only features.
  Reconstruction error is the anomaly score.
- Quantization: symmetric per-tensor int8 (weights, biases, input/output scales), plus the
  5 per-feature (mean, std) normalization constants used to z-score the raw feature vector
  before it enters the quantized model.
- Output artifact: `firmware/src/model_weights.h` — committed (not a secret), containing the
  quantized weights/biases and the normalization constants.
- Threshold/N: reconstruction-error threshold (~99th percentile of normal) and N-consecutive-
  windows (~3, ≈6 min) are chosen empirically against labeled synthetic runs, then hardcoded
  as firmware constants — a reflash is already required to update the model, so no runtime-
  tunable path is needed.

### On-Device Inference (`firmware/src/`)
- `bubble_detector.h`: duty-cycled 5–10 Hz burst sampling to catch the sawtooth's sharp drop
  (the existing 30 s sampling loop aliases it away). BMP280 temporarily switches to a lighter
  filter during the burst window and restores `FILTER_X16` after. Runs as a non-blocking state
  machine, matching the existing `loop()` style. Sequenced so a burst never overlaps the
  existing 30 s `publishReading()` BMP280 read (shared I2C bus). Bubble-detector/aggregator
  state resets whenever `captureBaseline()` runs (BOOT-button or MQTT `reset_baseline`), since
  a baseline jump mid-window would otherwise read as a false anomaly.
- Feature aggregator: builds the 5-value feature vector every 2 minutes.
- `autoencoder.h`: applies the (mean, std) normalization constants, then the fixed-point int8
  matmul + ReLU forward pass, using `model_weights.h`.
- Anomaly flag: reconstruction error above threshold for N consecutive windows.
- MQTT payload: `anomaly` (bool) and `recon_error` (float) added additively to the existing
  JSON published every 30 s, present from boot with sentinel values (`false`/`0.0`) so the
  schema shape is constant over time, not just additive.

### Backend Persistence & API
- `readings` table gains `anomaly` and `recon_error` columns via a guarded `ALTER TABLE`
  (the table already exists via `CREATE TABLE IF NOT EXISTS`, which would silently skip new
  columns — both `recorder.py::ensure_schema()` and `api.py::lifespan()` need the identical
  migration).
- `GET /readings` and `GET /batches/{id}/readings` expose the new fields automatically via
  `SELECT *`. `/summary` does not carry anomaly data unless explicitly extended.

### Frontend
- New `--anomaly` / `--anomaly-glow` CSS custom properties in `dashboard/src/index.css`
  (the dashboard's actual token source of truth — not `ds-bundle/`, which isn't imported by
  the app), matching the existing `--active`/`--active-glow` naming pattern.
- `AnomalyBadge.jsx`: a **separate** indicator shown alongside `ActivityBadge`, not folded
  into its state enum — a fermentation can be simultaneously `active` and anomalous (e.g.
  early contamination during otherwise-normal activity).

### Limitations
The `stuck` and `contamination` ground truth used to train and validate the model is
entirely synthetic — invented for this project, not derived from real fermentation defect
data. Real-world detection accuracy is unvalidated until tested against an actual stuck or
contaminated batch; treat on-device alerts as provisional until then.

---

## Project Structure

```
fermentation-monitor/
├── CONSTITUTION.md
├── SPEC.md
├── PLAN.md
├── TASKS.md
├── .gitignore
├── firmware/
│   ├── platformio.ini        ✓ created
│   ├── wokwi.toml            ✓ created
│   ├── diagram.json          ✓ created
│   └── src/
│       ├── main.cpp
│       ├── sim_sensors.h     ✓ created
│       ├── bubble_detector.h
│       ├── autoencoder.h
│       ├── model_weights.h   # generated by model_training/export_weights.py, committed
│       ├── config.h          # gitignored
│       └── config.example.h
├── model_training/
│   ├── datagen.py
│   ├── features.py
│   ├── train.py
│   ├── quantize.py
│   ├── export_weights.py
│   ├── validate.py
│   ├── requirements.txt
│   └── README.md
├── backend/
│   ├── requirements.txt      ✓ created
│   ├── mosquitto.conf        ✓ created
│   ├── auth.py               ✓ created
│   ├── users.py              ✓ created
│   ├── api.py                ✓ created
│   ├── recorder.py
│   ├── seed_admin.py
│   ├── .env.example          ✓ created
│   ├── .env                  # gitignored
│   ├── passwd                # gitignored (mosquitto users)
│   ├── acl                   # gitignored (mosquitto ACL)
│   ├── certs/                # gitignored (TLS)
│   └── data/                 # gitignored (SQLite)
└── dashboard/
    ├── package.json
    ├── vite.config.js
    └── src/
        ├── App.jsx
        ├── contexts/
        │   └── AuthContext.jsx
        ├── pages/
        │   ├── LoginPage.jsx
        │   ├── DashboardPage.jsx
        │   ├── HistoryPage.jsx
        │   └── UsersPage.jsx        ← admin only
        ├── components/
        │   ├── ActivityBadge.jsx
        │   ├── AnomalyBadge.jsx
        │   ├── PressureChart.jsx
        │   ├── TempHumidChart.jsx
        │   ├── StatusBar.jsx
        │   ├── ServiceStatus.jsx    ← operator+
        │   └── NavBar.jsx           ← role-aware
        └── hooks/
            ├── useReadings.js
            └── useSummary.js
```

---

## Wokwi CLI Simulation

### Purpose
Run the full firmware in simulation before any physical hardware is assembled.
The Wokwi + PlatformIO combination allows validating the MQTT payload format, baseline
EMA logic, timestamp handling, and JSON serialisation against the real backend stack.

### Setup (one-time)
```bash
# 1. Download wokwi-cli binary (Rust, no pip/npm needed)
#    Visit: https://github.com/wokwi/wokwi-cli/releases/latest
#    Download wokwi-cli-x86_64-unknown-linux-musl, chmod +x, move to ~/.local/bin/

# 2. Get free auth token: https://wokwi.com/dashboard/ci
export WOKWI_AUTH_TOKEN=<your_token>

# 3. Build the wokwi simulation firmware
cd firmware
pio run -e wokwi

# 4. Start Mosquitto on host (required before simulation)
mosquitto -v

# 5. Run simulation with virtual network (host reachable at 10.0.0.2)
wokwi-cli --net --timeout 120 .
```

### Simulation Mode (`-D SIMULATION` build flag)
When `[env:wokwi]` is selected, PlatformIO sets `-D SIMULATION`.
The firmware `sim_sensors.h` module replaces all BMP280/DHT22 I2C reads with
generated data:

| What it simulates | How |
|---|---|
| CO₂ pressure curve | Sawtooth wave, Gaussian activity envelope, peak at sim-hour 24 |
| Time compression | 1 real minute = 1 fermentation hour (60×); full run = ~35 min |
| Sensor noise | ±3 Pa random jitter on pressure, matching real BMP280 noise floor |
| Temperature | Rises 3°C during peak activity (yeast exothermic) |
| Humidity | Rises 10% during peak (CO₂ carries moisture) |
| Anomaly scenario | `SIM_PROFILE=NORMAL\|STUCK\|CONTAMINATED` build flag selects the fermentation curve shape, so anomaly detection can be exercised end-to-end without physical hardware |

### Why BMP280 static values don't matter
Wokwi's `wokwi-bmp280` component has static `pressure` attributes in `diagram.json`.
In `SIMULATION` mode, the firmware bypasses all I2C reads and calls `Sim::pressurePa()`
instead. The component still appears in the diagram for documentation purposes.

### MQTT in Wokwi (`--net` flag)
`wokwi-cli --net` creates a virtual network where the host machine is at `10.0.0.2`.
The wokwi build environment sets `MQTT_HOST = "10.0.0.2"` via build flag so the
simulated ESP32 reaches the host's Mosquitto broker at port 1883.

### NTP in Wokwi
`pool.ntp.org` will not resolve in Wokwi's virtual network. The firmware NTP-failure
fallback (use `received_at` for time axis) activates automatically in simulation.

### What simulation validates
- JSON payload is well-formed and fields match recorder's expected keys
- Baseline EMA updates correctly over time
- MQTT reconnect logic fires when connection drops
- Backend `activity` classification transitions: `active` → `slow` → `finished`
- Dashboard chart correctly extends as new rows arrive

---

## Non-Functional Requirements
- SQLite DB must not exceed 50 MB for a 2-week fermentation (30 s interval = ~40 k rows ≈ 4 MB)
- API response < 200 ms for last-100-readings query
- Dashboard loads in < 2 s on localhost
- Firmware binary < 1 MB (well within ESP32 4 MB flash)
