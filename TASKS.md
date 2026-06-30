# Fermentation Monitor — Task List

Legend: [ ] todo  [x] done  [~] in progress
Model:  ⚡ /quick (haiku)   ✦ /task (sonnet)   ◆ /deep (opus)

---

## Phase 1 — Firmware

- [ ] F1  Scaffold PlatformIO project with esp32dev board + Arduino framework                                        ⚡ /quick
- [ ] F2  Write `config.example.h` (SSID, MQTT_HOST, MQTT_PORT, MQTT_TOPIC, BMP_SDA, BMP_SCL, DHT_PIN)             ⚡ /quick
- [ ] F3  Implement BMP280 init + read (pressure + temp) via Adafruit_BMP280                                         ✦ /task
- [ ] F4  Implement DHT22 init + read (temp + humidity)                                                              ✦ /task
- [ ] F5  Implement boot-time baseline: average 10 pressure readings, save to EEPROM (EEPROM.begin() + commit())     ✦ /task
- [ ] F6  Compute delta_pressure = current - baseline; update EMA baseline (α=0.001) for barometric drift            ✦ /task
- [ ] F7  Build JSON payload with ArduinoJson and print to Serial                                                    ✦ /task
- [ ] F8  Implement MQTT connect + auto-reconnect (PubSubClient)                                                     ✦ /task
- [ ] F9  Add NTP sync after WiFi connect: configTime(), wait 10 s; use received_at fallback if sync fails           ✦ /task
- [ ] F10 Publish payload to MQTT topic every 30 s (QoS 0, RETAIN=1, client_id="fermentation-esp32")                ⚡ /quick
- [ ] F11 Subscribe to `fermentation/cmd`; on `{"action":"reset_baseline"}` re-run baseline + save to EEPROM         ✦ /task
- [ ] F12 Add BOOT-button long-press (3 s) on GPIO 0 to reset baseline in EEPROM (local fallback)                   ✦ /task

## Phase B — Broker Configuration

- [ ] B1  Run broker with project config: `mosquitto -c backend/mosquitto.conf -v` — confirm it starts              ⚡ /quick
- [ ] B2  Create persistence directory: `mkdir -p /tmp/mosquitto-fermentation`                                       ⚡ /quick
- [ ] B3  Test retained message: pub with -r flag, subscribe fresh — should receive immediately                      ⚡ /quick
- [ ] B4  Test clean session: stop/start recorder, confirm queued messages delivered on reconnect                    ⚡ /quick
- [ ] B5  (Hardening) Create passwd file: `mosquitto_passwd -c backend/passwd esp32` + `recorder` user              ⚡ /quick
- [ ] B6  (Hardening) Write `backend/acl` with per-user topic restrictions; enable in mosquitto.conf                 ⚡ /quick
- [ ] B7  (Hardening) Generate self-signed TLS certs; configure mosquitto port 8883                                  ✦ /task
- [ ] B8  (Hardening) Update firmware to use `WiFiClientSecure` + embedded CA cert for TLS                          ✦ /task
- [ ] B9  (Hardening) Update recorder paho-mqtt to use `tls_set(ca_cert="backend/certs/ca.crt")`                    ✦ /task
- [ ] B10 (Service) Create systemd user service unit; enable + start; verify status                                  ⚡ /quick

## Phase 2 — Recorder

- [ ] R1  Create `backend/requirements.txt` (paho-mqtt)                                                             ⚡ /quick
- [ ] R2  Write `recorder.py`: connect to Mosquitto, clean_session=False, create readings table                      ✦ /task
- [ ] R3  Subscribe to topic, parse JSON, insert row (map `delta_pressure_pa` → `delta_pa`)                          ✦ /task
- [ ] R4  Add startup log: print broker address, topic, and DB path                                                  ⚡ /quick
- [ ] R5  Test with `mosquitto_pub -t fermentation/sensor -m '{"ts":1,...}'`                                         ⚡ /quick

## Phase 3 — API + RBAC

- [x] A1  Add all dependencies to `requirements.txt`                                                                ⚡ /quick
- [x] A2  Write `auth.py`: JWT helpers, bcrypt, `require_role()` factory                                            ✦ /task
- [x] A3  Write `users.py`: users table DDL, CRUD, Pydantic models                                                  ✦ /task
- [x] A4  Write `api.py`: all endpoints with role guards, WAL mode, lifespan                                        ✦ /task
- [ ] A5  Copy `.env.example` → `.env`; generate JWT secret with openssl                                             ⚡ /quick
- [x] A6  Write `seed_admin.py`: prompts for password, bcrypt hash, inserts admin user                              ✦ /task
- [ ] A7  Run seed: `python3 backend/seed_admin.py` → confirm user in DB                                             ⚡ /quick
- [ ] A8  Test auth flow: curl POST /auth/token → token                                                              ⚡ /quick
- [ ] A9  Test role enforcement: viewer token on GET /users → 403; admin → 200                                       ⚡ /quick
- [ ] A10 Test `POST /device/reset-baseline` → MQTT message on `fermentation/cmd`                                    ⚡ /quick
- [ ] A11 Test `GET /service/status` → shows recorder lag                                                            ⚡ /quick
- [ ] A12 Test batch flow: POST /batches → GET /batches → PUT /batches/1/end                                         ⚡ /quick

## Phase 4 — Dashboard

- [ ] D0  Write `AuthContext.jsx` + `LoginPage.jsx`: JWT login, localStorage, 401 global handler                     ✦ /task
- [ ] D1  Scaffold Vite + React 18 project in `dashboard/`                                                           ⚡ /quick
- [ ] D2  Install Tailwind CSS + Recharts                                                                            ⚡ /quick
- [ ] D3  Configure Vite proxy: `/api` → `http://localhost:8000`                                                     ⚡ /quick
- [ ] D4  Write `useReadings.js`: seed on mount, incremental poll every 15 s, cap at 2,880 rows                      ✦ /task
- [ ] D5  Write `PressureChart.jsx`: Recharts LineChart, x=time, y=delta_pa                                          ✦ /task
- [ ] D6  Write `TempHumidChart.jsx`: dual-line (temp_bmp, temp_dht) + right-axis humidity                           ✦ /task
- [ ] D7  Write `ActivityBadge.jsx`: fetch /api/summary, colour-coded badge                                          ✦ /task
- [ ] D8  Write `StatusBar.jsx`: elapsed time since first reading                                                    ⚡ /quick
- [ ] D9  Assemble `App.jsx` with responsive layout + routing                                                        ✦ /task
- [ ] D10 Verify live update: chart extends as new rows arrive                                                       ⚡ /quick
- [ ] D11 Write `NavBar.jsx`: role-aware — show "Users" for admin, "Status" for operator+                            ✦ /task
- [ ] D12 Write `UsersPage.jsx` (admin only): user table, create form, deactivate button                             ✦ /task
- [ ] D13 Write `ServiceStatus.jsx` (operator+): recorder up/down + last reading lag                                 ✦ /task
- [ ] D14 Implement History view + batch selector: dropdown, full curve, zoom/pan                                    ✦ /task

## Phase W — Wokwi Simulation Setup

- [ ] W1  Download `wokwi-cli` binary, place in `~/.local/bin/`, chmod +x                                           ⚡ /quick
- [ ] W2  Create Wokwi account + get CI token, set WOKWI_AUTH_TOKEN                                                  ⚡ /quick
- [ ] W3  Verify wokwi.toml, diagram.json, platformio.ini [env:wokwi] are correct                                   ⚡ /quick
- [ ] W4  Build wokwi firmware: `pio run -e wokwi` — confirm firmware.bin exists                                     ⚡ /quick
- [ ] W5  Run simulation: `mosquitto -v &` then `wokwi-cli --net --timeout 120 firmware/`                            ⚡ /quick
- [ ] W6  Confirm MQTT messages arrive on fermentation/sensor every 30 s with valid JSON                             ⚡ /quick
- [ ] W7  Run recorder against live simulation — rows grow in SQLite                                                 ⚡ /quick
- [ ] W8  Verify activity transitions: active → slow → finished over ~35 min                                         ⚡ /quick

## Phase 5 — Integration

- [x] I1  Write `.gitignore`                                                                                         ⚡ /quick
- [ ] I2  Write `README.md` with wiring diagram + startup commands                                                   ✦ /task
- [ ] I3  Full stack smoke test: ESP32 → MQTT → recorder → API → dashboard                                          ✦ /task
- [ ] I4  Calibrate delta_pa threshold with real airlock bubble test                                                 ◆ /deep
- [ ] I5  Start a batch before first real fermentation: `POST /batches {"name":"Test Batch #1"}`                     ⚡ /quick
