#include <Arduino.h>
#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <Adafruit_BMP280.h>
#include <DHT.h>
#include <EEPROM.h>
#include <time.h>
#include "config.h"
#include "model_weights.h"
#include "bubble_detector.h"
#include "autoencoder.h"
#ifdef SIMULATION
#include "sim_sensors.h"
#endif

// ── Constants ─────────────────────────────────────────────
#define EEPROM_SIZE       16
#define EEPROM_ADDR_BASE  0    // float baseline_pa (4 bytes)
#define EEPROM_ADDR_MAGIC 4    // 1 byte: 0xAB if valid
#define EEPROM_MAGIC      0xAB
#define EMA_ALPHA         0.001f
#define PUBLISH_INTERVAL  30000UL   // ms between publishes
#define BOOT_PIN          0          // built-in BOOT button, active LOW
#define BOOT_HOLD_MS      3000UL    // hold duration for baseline reset

// Continuous bubble-detector sampling period, derived from model_weights.h.
// 10 Hz → 100 ms. See the "Sampling" note above serviceSampler().
#define AE_SAMPLE_PERIOD_MS  ((unsigned long)(1000.0f / AE_SAMPLE_RATE_HZ))
// If the sampler falls this far behind (e.g. the 5 s blocking captureBaseline),
// give up on catching up and re-anchor the schedule to now.
#define AE_SAMPLE_RESYNC_MS  (10UL * AE_SAMPLE_PERIOD_MS)

// ── Hardware objects ──────────────────────────────────────
Adafruit_BMP280 bmp;
DHT             dht(DHT_PIN, DHT22);
WiFiClient      wifiClient;
PubSubClient    mqttClient(wifiClient);

// ── State ─────────────────────────────────────────────────
float         baseline_pa       = 101325.0f;
bool          ntpSynced         = false;
unsigned long lastPublish        = 0;
unsigned long lastReconnect      = 0;
unsigned long bootPressedAt      = 0;
bool          bootBaselineDone   = false;

// ── Anomaly-detection state ───────────────────────────────
BubbleDetector    bubbleDet;
FeatureAggregator featAgg;
bool          anomalyFlag        = false;   // published; sentinel false from boot
float         reconError         = 0.0f;    // published; sentinel 0.0 from boot
int           anomalyRun         = 0;       // consecutive over-threshold windows
unsigned long windowsEmitted     = 0;
float         lastRawPa          = 101325.0f;  // newest continuous pressure sample
float         lastTempDht        = 22.0f;      // cached DHT22 temperature
unsigned long nextSampleMs       = 0;
#ifdef SIMULATION
unsigned long simEpochMs         = 0;
bool          simEpochSet        = false;
#endif

void resetAnomalyState();

// ── Baseline capture ──────────────────────────────────────
void captureBaseline() {
    Serial.println("[baseline] Capturing — averaging 10 readings…");
    float sum = 0.0f;
    for (int i = 0; i < 10; i++) {
#ifdef SIMULATION
        sum += Sim::pressurePa(baseline_pa);
#else
        sum += bmp.readPressure();
#endif
        delay(500);
    }
    baseline_pa = sum / 10.0f;

    // Persist to EEPROM
    EEPROM.put(EEPROM_ADDR_BASE, baseline_pa);
    EEPROM.write(EEPROM_ADDR_MAGIC, EEPROM_MAGIC);
    EEPROM.commit();

    Serial.printf("[baseline] Set to %.2f Pa\n", baseline_pa);

    // A baseline step is a step in delta_pa, which the drop-edge detector would
    // read as a bubble (and the half-full interval buffer as a bogus window).
    // Throw the whole feature pipeline away and start again.
    resetAnomalyState();
}

// ── NTP ───────────────────────────────────────────────────
void syncNTP() {
    configTime(0, 0, "pool.ntp.org", "time.nist.gov");
    struct tm t;
    for (int i = 0; i < 20; i++) {   // 10 s timeout
        if (getLocalTime(&t)) { ntpSynced = true; break; }
        delay(500);
    }
    Serial.printf("[ntp] %s\n", ntpSynced ? "synced" : "failed — using millis fallback");
}

unsigned long getEpoch() {
    if (ntpSynced) {
        struct tm t;
        if (getLocalTime(&t)) return (unsigned long)mktime(&t);
    }
    return millis() / 1000UL;
}

// ── MQTT callback ─────────────────────────────────────────
void mqttCallback(char* topic, byte* payload, unsigned int length) {
    StaticJsonDocument<128> doc;
    if (deserializeJson(doc, payload, length) != DeserializationError::Ok) return;
    const char* action = doc["action"];
    if (action && strcmp(action, "reset_baseline") == 0) {
        Serial.println("[cmd] reset_baseline received");
        captureBaseline();
    }
}

// ── MQTT reconnect (non-blocking, max 1 attempt / 5 s) ───
void mqttReconnect() {
    if (millis() - lastReconnect < 5000UL) return;
    lastReconnect = millis();
    if (mqttClient.connect(MQTT_CLIENT_ID)) {
        mqttClient.subscribe(MQTT_CMD_TOPIC);
        Serial.println("[mqtt] connected, subscribed to " MQTT_CMD_TOPIC);
    } else {
        Serial.printf("[mqtt] connect failed, rc=%d\n", mqttClient.state());
    }
}

// ── BOOT button long-press ────────────────────────────────
void checkBootButton() {
    bool pressed = (digitalRead(BOOT_PIN) == LOW);
    if (pressed && bootPressedAt == 0) {
        bootPressedAt   = millis();
        bootBaselineDone = false;
    } else if (!pressed) {
        bootPressedAt   = 0;
        bootBaselineDone = false;
    } else if (pressed && !bootBaselineDone &&
               (millis() - bootPressedAt >= BOOT_HOLD_MS)) {
        captureBaseline();
        bootBaselineDone = true;   // prevent repeated triggers while held
    }
}

// ── Anomaly detection ─────────────────────────────────────
//
// Sampling: the bubble detector is fed a CONTINUOUS AE_SAMPLE_RATE_HZ (10 Hz)
// stream, not the duty-cycled bursts originally sketched in TASKS.md F13.
// model_training/features.py's "DESIGN NOTES FOR THE C PORT" §3 is explicit
// that the reference implementation — and therefore the calibrated threshold —
// assumes a continuous stream: bubble periods run 3–300 s, so any burst short
// enough to be worth duty-cycling drops edges into the gaps and corrupts every
// interval it does measure. 100 ms per BMP280 read is ~0.1 % of an ESP32's
// loop budget, so there is nothing to save. This also removes TASKS.md F21
// entirely: publishReading() consumes the newest sample from this one stream
// instead of issuing its own competing bmp.readPressure().

void resetAnomalyState() {
    bd_reset(&bubbleDet);
    fa_reset(&featAgg);
    anomalyRun     = 0;
    anomalyFlag    = false;
    reconError     = 0.0f;
    windowsEmitted = 0;
    nextSampleMs   = millis() + AE_SAMPLE_PERIOD_MS;
}

void runInference(const float feats[AE_N_INPUT]) {
    float x[AE_N_INPUT];
    ae_normalize(feats, x);
    reconError = ae_recon_error(x);
    windowsEmitted++;

    if (reconError > AE_RECON_THRESHOLD) {
        if (anomalyRun < AE_ANOMALY_N_WINDOWS) anomalyRun++;
        if (anomalyRun >= AE_ANOMALY_N_WINDOWS) anomalyFlag = true;
    } else {
        anomalyRun  = 0;
        anomalyFlag = false;
    }

    Serial.printf("[window] #%lu rate=%.2f/min mean=%.1fs std=%.1fs trend=%.1fs "
                  "dT=%.2fC err=%.4f (thr=%.4f) run=%d/%d anomaly=%d\n",
                  windowsEmitted,
                  feats[FEAT_BUBBLE_RATE], feats[FEAT_MEAN_INTERVAL],
                  feats[FEAT_STD_INTERVAL], feats[FEAT_INTERVAL_TREND],
                  feats[FEAT_TEMP_DELTA], reconError, AE_RECON_THRESHOLD,
                  anomalyRun, AE_ANOMALY_N_WINDOWS, anomalyFlag ? 1 : 0);
}

// Feed one delta-pressure sample (Pa above baseline) + the current temperature
// through detector → aggregator → autoencoder.
void feedSample(float delta_pa, float temp_c) {
    bool   fired, haveInterval;
    double interval_s;
    bd_update(&bubbleDet, delta_pa, &fired, &haveInterval, &interval_s);
    if (fired) {
        fa_push_bubble(&featAgg, haveInterval, interval_s);
        if (haveInterval) {
            Serial.printf("[bubble] interval=%.1fs\n", (float)interval_s);
        } else {
            Serial.println("[bubble] first edge — no interval yet");
        }
    }
    fa_push_temp(&featAgg, temp_c);

    float feats[AE_N_INPUT];
    int   ev = fa_maybe_emit(&featAgg, bubbleDet.t, feats);
    if (ev == FA_EMIT) {
        runInference(feats);
    } else if (ev == FA_TICK_SUPPRESSED) {
        Serial.printf("[window] suppressed — interval buffer %d/%d\n",
                      featAgg.count, AE_MIN_INTERVALS);
    }
}

// Non-blocking continuous sampler. Called every loop() iteration.
void serviceSampler() {
#ifdef SIMULATION
    // Sim runs in fermentation time (60x real time), so 10 Hz of fermentation
    // sampling is 600 generated samples per real second. Generate whatever is
    // due, capped so one loop() iteration can never hog the CPU.
    if (!simEpochSet) { simEpochMs = millis(); simEpochSet = true; }
    uint32_t due = Sim::samplesDueAt((uint32_t)(millis() - simEpochMs));
    int budget = 4000;
    while (Sim::samplesGenerated() < due && budget-- > 0) {
        Sim::advance();
        lastRawPa   = Sim::pressurePa(baseline_pa);
        lastTempDht = Sim::tempDHT();
        feedSample(Sim::deltaPa(), lastTempDht);
    }
#else
    if ((long)(millis() - nextSampleMs) < 0) return;
    nextSampleMs += AE_SAMPLE_PERIOD_MS;
    if ((long)(millis() - nextSampleMs) > (long)AE_SAMPLE_RESYNC_MS) {
        // Long stall (e.g. blocking Wi-Fi/MQTT reconnect in loop()). The detector
        // clock advances 100 ms per sample, so feeding on would stretch the stalled
        // seconds into the 120 s window. Drop the polluted state and restart clean.
        resetAnomalyState();   // also re-anchors nextSampleMs
    }
    lastRawPa = bmp.readPressure();
    // lastTempDht is refreshed by publishReading(); the DHT22 cannot be polled
    // at 10 Hz (2 s minimum interval, ~250 ms blocking read) and does not need
    // to be — over one 120 s window the thermal envelope moves ~0.005 °C.
    feedSample(lastRawPa - baseline_pa, lastTempDht);
#endif
}

// ── Publish one reading ───────────────────────────────────
void publishReading() {
    // Read sensors
    float raw_pa, temp_bmp, temp_dht, humidity;

#ifdef SIMULATION
    raw_pa   = Sim::pressurePa(baseline_pa);
    temp_bmp = Sim::tempBMP();
    temp_dht = Sim::tempDHT();
    humidity = Sim::humidityDHT();
#else
    // Pressure comes from the continuous 10 Hz sampler — one read stream, so a
    // publish can never interleave with the bubble detector on the I2C bus.
    raw_pa   = lastRawPa;
    temp_bmp = bmp.readTemperature();
    temp_dht = dht.readTemperature();
    humidity = dht.readHumidity();

    if (isnan(temp_dht) || isnan(humidity)) {
        Serial.println("[dht] read failed — skipping publish");
        return;
    }
    lastTempDht = temp_dht;   // feeds the feature aggregator until the next publish
#endif

    // EMA baseline update (tracks slow barometric drift)
    float delta_pa = raw_pa - baseline_pa;
    baseline_pa = EMA_ALPHA * raw_pa + (1.0f - EMA_ALPHA) * baseline_pa;

    // Build JSON payload. `anomaly`/`recon_error` are additive and present from
    // boot with sentinel values (false / 0.0), so the schema shape is constant.
    StaticJsonDocument<256> doc;
    doc["ts"]          = getEpoch();
    doc["pressure_pa"] = raw_pa;
    doc["delta_pa"]    = delta_pa;
    doc["temp_bmp"]    = temp_bmp;
    doc["temp_dht"]    = temp_dht;
    doc["humidity"]    = humidity;
    doc["anomaly"]     = anomalyFlag;
    doc["recon_error"] = reconError;

    char buf[256];
    serializeJson(doc, buf);

    bool ok = mqttClient.publish(MQTT_TOPIC, buf, true);  // RETAIN=true, QoS 0
    Serial.printf("[pub] %s → %s\n", ok ? "OK" : "FAIL", buf);
}

// ── setup ─────────────────────────────────────────────────
void setup() {
    Serial.begin(115200);
    delay(200);
    Serial.println("\n=== Fermentation Monitor ===");

    // EEPROM
    EEPROM.begin(EEPROM_SIZE);

    // Anomaly detector (must exist before captureBaseline() can reset it)
    bd_init(&bubbleDet, AE_SAMPLE_RATE_HZ);
    fa_reset(&featAgg);

    // BOOT button
    pinMode(BOOT_PIN, INPUT_PULLUP);

    // BMP280 — I2C on SDA=21, SCL=22; address 0x76 (SDO→GND)
    Wire.begin(21, 22);
    if (!bmp.begin(0x76)) {
        Serial.println("[bmp280] init FAILED — check wiring");
#ifndef SIMULATION
        while (true) delay(1000);
#endif
    } else {
        // ONE sampling configuration serves both consumers (see the note above
        // serviceSampler() — with continuous sampling there is no burst window
        // to swap filters in and out of):
        //
        //   STANDBY_MS_1  — the old STANDBY_MS_500 caps the output data rate at
        //                   ~2 Hz, so a 10 Hz sampler would re-read stale values
        //                   and see the drop edge as a 5-sample plateau. At 0.5 ms
        //                   standby with x16/x2 oversampling the ODR is ~23 Hz.
        //   FILTER_X2     — FILTER_X16 needs ~22 samples (~1 s) to settle, which
        //                   smears the sawtooth's drop (0.3 s at the 3 s minimum
        //                   period) away entirely. x2 settles in ~2 samples
        //                   (~90 ms), well inside both the drop and the detector's
        //                   own 0.5 s boxcar, while still trimming ADC noise: with
        //                   pressure oversampling x16 the residual is ≲1 Pa RMS,
        //                   comfortably inside the ±3 Pa band that
        //                   AE_DROP_THRESHOLD_PA = 6.0 Pa was sized against.
        //
        // The 30 s EMA baseline is unaffected: EMA_ALPHA = 0.001 over 30 s samples
        // is an ~8 h time constant, so ~1 Pa of extra per-sample noise on a
        // 101325 Pa absolute reading is invisible to baseline_pa, and delta_pa's
        // signal swing is ~80 Pa.
        bmp.setSampling(Adafruit_BMP280::MODE_NORMAL,
                        Adafruit_BMP280::SAMPLING_X2,
                        Adafruit_BMP280::SAMPLING_X16,
                        Adafruit_BMP280::FILTER_X2,
                        Adafruit_BMP280::STANDBY_MS_1);
        Serial.println("[bmp280] OK");
    }

    // DHT22
    dht.begin();
    Serial.println("[dht22] OK");

    // WiFi
    Serial.printf("[wifi] connecting to %s…", WIFI_SSID);
    WiFi.begin(WIFI_SSID, WIFI_PASS);
    for (int i = 0; i < 40 && WiFi.status() != WL_CONNECTED; i++) {
        delay(500);
        Serial.print(".");
    }
    Serial.println();
    if (WiFi.status() != WL_CONNECTED) {
        Serial.println("[wifi] FAILED — check credentials");
        while (true) delay(1000);
    }
    Serial.printf("[wifi] connected, IP=%s\n", WiFi.localIP().toString().c_str());

    // NTP
    syncNTP();

    // MQTT
    mqttClient.setServer(MQTT_HOST, MQTT_PORT);
    mqttClient.setCallback(mqttCallback);
    mqttClient.setKeepAlive(60);
    mqttReconnect();

    // Baseline: load from EEPROM or capture fresh
    if (EEPROM.read(EEPROM_ADDR_MAGIC) == EEPROM_MAGIC) {
        EEPROM.get(EEPROM_ADDR_BASE, baseline_pa);
        Serial.printf("[baseline] Loaded from EEPROM: %.2f Pa\n", baseline_pa);
    } else {
        captureBaseline();
    }
    // Prime what the continuous sampler and the first feature window depend on.
    // lastTempDht in particular sets the aggregator's boot temperature baseline;
    // leaving it at its placeholder until the first publish would bias
    // temp_delta by (30 s / 120 s) x (placeholder - ambient).
#ifdef SIMULATION
    lastRawPa = baseline_pa;
#else
    lastRawPa = bmp.readPressure();
    float t0  = dht.readTemperature();
    if (!isnan(t0)) lastTempDht = t0;
#endif

    // Start the continuous sampler cleanly, whichever branch ran above.
    resetAnomalyState();

    Serial.printf("[ae] %.1f Hz continuous sampling, %.0f s windows, "
                  "threshold=%.4f, N=%d\n",
                  AE_SAMPLE_RATE_HZ, AE_WINDOW_S, AE_RECON_THRESHOLD,
                  AE_ANOMALY_N_WINDOWS);
    Serial.println("[setup] done — entering loop");
}

// ── loop ──────────────────────────────────────────────────
void loop() {
    // MQTT processing
    if (mqttClient.connected()) {
        mqttClient.loop();
    } else {
        // WiFi reconnect if dropped
        if (WiFi.status() != WL_CONNECTED) {
            Serial.println("[wifi] reconnecting…");
            WiFi.reconnect();
            delay(2000);
        }
        mqttReconnect();
    }

    // BOOT button long-press check
    checkBootButton();

    // Continuous 10 Hz bubble-detector sampling (non-blocking)
    serviceSampler();

    // Publish every PUBLISH_INTERVAL ms
    unsigned long now = millis();
    if (now - lastPublish >= PUBLISH_INTERVAL) {
        lastPublish = now;
        if (mqttClient.connected()) {
            publishReading();
        }
    }
}
