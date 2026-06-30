# Fermentation Monitor — Implementation Plan

## Layer Build Order
Build bottom-up: firmware first (generates data), then recorder (consumes MQTT),
then API (serves data), then dashboard (consumes API).

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

**Validation:** Serial monitor shows valid JSON with Unix epoch `ts` + MQTT broker receives on correct topic.
Spot-check: `ts` value should be within a few seconds of `date +%s` on your laptop.

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

**Validation:** `sqlite3 data/fermentation.db "SELECT COUNT(*) FROM readings"` grows over time.

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

**Validation:** `curl localhost:8000/readings` returns well-formed JSON array.

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

**Validation:** Dashboard loads, chart animates as new data arrives, badge updates.

---

## Phase 5 — Integration & Polish
1. Add `README.md` with wiring diagram, setup steps, and `mosquitto.conf`
   Note: recorder.py and api.py are two separate processes — README must document running both
   (e.g., two terminal tabs or a Procfile)
2. Add `.gitignore` (config.h, data/, node_modules/, .pio/)
3. Test full stack: ESP32 → MQTT → recorder → API → dashboard
4. Tune `delta_pa` threshold (0.5 Pa default) based on real bubble test with airlock submerged 1 cm

---

## Dependency Map
```
Phase 1 (firmware) ─────────────────────────────┐
Phase 2 (recorder) ← requires Mosquitto running ┤
Phase 3 (API)      ← requires recorder + SQLite  ┤→ Phase 5
Phase 4 (dashboard)← requires API running        ┘
```

Phases 1 and 2 can be developed in parallel (firmware mocked with mosquitto_pub).
