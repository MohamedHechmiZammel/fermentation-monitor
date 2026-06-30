# Fermentation Monitor

ESP32 + BMP280 + DHT22 → MQTT → FastAPI + SQLite → React dashboard. Tracks CO₂ headspace pressure delta during homebrew fermentation and classifies activity state (active / slow / finished) in real time.

---

## Hardware

| Component | Qty | Notes |
|---|---|---|
| ESP32 DevKit v1 | 1 | Main MCU — WiFi + MQTT |
| BMP280 | 1 | Pressure sensor tapped into vessel headspace via second lid hole |
| DHT22 | 1 | Ambient temperature + humidity |
| 4.7 kΩ resistor | 1 | DHT22 pull-up on data line |
| Silicone tubing | ~15 cm | Connects BMP280 port to vessel headspace |

### Wiring

| ESP32 pin | Connected to | Wire |
|---|---|---|
| GPIO 21 | BMP280 SDA | Green |
| GPIO 22 | BMP280 SCK | Blue |
| GPIO 4  | DHT22 DATA | Orange |
| 3V3 | BMP280 VCC, BMP280 CSB, DHT22 VCC | Red |
| GND | BMP280 GND, BMP280 SDO (→ I2C addr 0x76), DHT22 GND | Black |

BMP280 SDO tied to GND → I2C address `0x76`.

---

## Architecture

```
ESP32 (firmware)
  │  JSON over MQTT QoS 0, RETAIN=1
  ▼
Mosquitto broker (localhost:1883)
  │  paho-mqtt subscribe
  ▼
recorder.py  ──────────────▶  SQLite (WAL mode)
                                  │  sqlite3
                                  ▼
                             FastAPI (:8000)
                                  │  REST / JWT
                                  ▼
                          React Dashboard (:5173)
```

MQTT payload shape:
```json
{"ts": 1700000000, "pressure_pa": 101380.5, "delta_pa": 42.3,
 "temp_bmp": 22.1, "temp_dht": 23.4, "humidity": 63.0}
```

---

## Quick Start

**Prerequisites:** Python 3.10+, Node 18+, PlatformIO CLI, Mosquitto.

```bash
# 1. Clone and install
git clone <repo> && cd fermentation-monitor
python -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
cd dashboard && npm install && cd ..

# 2. Configure
cp backend/.env.example backend/.env
# Edit backend/.env — generate JWT secret:
#   openssl rand -hex 32
cp firmware/src/config.example.h firmware/src/config.h
# Edit firmware/src/config.h — fill SSID, MQTT_HOST

# 3. Start Mosquitto
mosquitto -c backend/mosquitto.conf -v

# 4. Seed admin user, then start backend + recorder
cd backend
python seed_admin.py
uvicorn api:app --reload &
python recorder.py &
cd ..

# 5. Start dashboard
cd dashboard && npm run dev
# Open http://localhost:5173 — sign in as admin
```

---

## Firmware

```bash
# Build and flash to real hardware
pio run -e esp32dev
pio run -e esp32dev -t upload

# Wokwi simulation (runs at 60× time compression)
pio run -e wokwi          # builds with -D SIMULATION
wokwi-cli --net firmware/ # starts sim, publishes to host Mosquitto
```

Config file: `firmware/src/config.h` (copy from `config.example.h`):

```cpp
#define SSID        "your-wifi"
#define WIFI_PASS   "your-password"
#define MQTT_HOST   "192.168.x.x"   // host IP visible from ESP32
#define MQTT_PORT   1883
#define MQTT_TOPIC  "fermentation/sensor"
#define MQTT_CMD_TOPIC "fermentation/cmd"
#define DHT_PIN     4
```

### Baseline reset

Remote (operator+): `POST /device/reset-baseline` → publishes `{"action":"reset_baseline"}` to `fermentation/cmd`.

Local: hold BOOT button (GPIO 0) for 3 s → re-runs boot baseline and saves to EEPROM.

EMA drift correction: `baseline = 0.001 × pressure + 0.999 × baseline` (τ ≈ 8 h). Prevents slow barometric drift from inflating `delta_pa`.

---

## API Reference

Base URL: `http://localhost:8000`. Auth: `Authorization: Bearer <token>` (JWT HS256, 480 min).

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/health` | public | Liveness check |
| POST | `/auth/token` | public | Login (form: username, password) → JWT |
| GET | `/auth/me` | any | Current user info |
| GET | `/readings` | viewer+ | Sensor readings (params: `limit`, `since`) |
| GET | `/summary` | viewer+ | Activity state + bubble rate + duration |
| GET | `/batches` | viewer+ | All batches ordered newest-first |
| POST | `/batches` | operator+ | Start a new batch |
| PUT | `/batches/{id}/end` | operator+ | End an active batch |
| GET | `/batches/{id}/readings` | viewer+ | All readings for a batch |
| GET | `/users` | admin | List all users |
| POST | `/users` | admin | Create user |
| PUT | `/users/{id}` | admin | Update user (role, active) |
| DELETE | `/users/{id}` | admin | Deactivate user |
| POST | `/device/reset-baseline` | operator+ | Send reset command to ESP32 |
| GET | `/service/status` | operator+ | Recorder liveness + MQTT lag |

Get a token:
```bash
curl -X POST http://localhost:8000/auth/token \
  -d "username=admin&password=yourpassword"
```

---

## RBAC Roles

| Role | Read data | Start/end batch | Reset baseline | User mgmt | Service status |
|---|---|---|---|---|---|
| viewer | yes | no | no | no | no |
| operator | yes | yes | yes | no | yes |
| admin | yes | yes | yes | yes | yes |

---

## Activity States

Computed by `GET /summary` from a sliding 10-minute window:

| State | Condition | Meaning |
|---|---|---|
| `active` | > 3 readings with `|delta_pa| > 0.5` in last 10 min | Yeast actively producing CO₂ |
| `slow` | 1–3 readings above threshold in last 10 min | Activity tapering off |
| `finished` | 0 readings above threshold in last 30 min (and >30 min since start) | Fermentation complete |

Threshold `0.5 Pa` calibrated by live bubble test (task I4).

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `JWT_SECRET_KEY` | — | Required. Generate: `openssl rand -hex 32` |
| `JWT_ALGORITHM` | `HS256` | JWT signing algorithm |
| `JWT_EXPIRE_MINUTES` | `480` | Token lifetime (8 h) |
| `DB_PATH` | `data/fermentation.db` | SQLite database path |
| `MQTT_BROKER` | `localhost` | Mosquitto host |
| `MQTT_PORT` | `1883` | Mosquitto port |

---

## Development Notes

- **SQLite WAL mode** — FastAPI and recorder write concurrently without locking.
- **EMA baseline** — α = 0.001, τ ≈ 8 h. Saved to EEPROM on boot and on reset commands.
- **NTP fallback** — If NTP sync fails (Wokwi / offline), `ts` is a relative millis value; `received_at` (server wall clock set by recorder) is always reliable for ordering.
- **QoS 0 + RETAIN=1** — PubSubClient limitation; last reading survives broker restart.
- **clean_session=False** on recorder — durable subscription, no readings lost during recorder restart.
- **DB index** — `CREATE INDEX idx_readings_received_at ON readings(received_at)` for fast time-range queries.
- **Wokwi simulation** — 60× time compression via `Sim::` functions in `sim_sensors.h`. Gaussian activity envelope simulates a full fermentation arc in ~35 min real time.
