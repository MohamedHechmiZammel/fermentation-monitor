#include <Arduino.h>
#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <Adafruit_BMP280.h>
#include <DHT.h>
#include <EEPROM.h>
#include <time.h>
#include "config.h"
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
    raw_pa   = bmp.readPressure();
    temp_bmp = bmp.readTemperature();
    temp_dht = dht.readTemperature();
    humidity = dht.readHumidity();

    if (isnan(temp_dht) || isnan(humidity)) {
        Serial.println("[dht] read failed — skipping publish");
        return;
    }
#endif

    // EMA baseline update (tracks slow barometric drift)
    float delta_pa = raw_pa - baseline_pa;
    baseline_pa = EMA_ALPHA * raw_pa + (1.0f - EMA_ALPHA) * baseline_pa;

    // Build JSON payload
    StaticJsonDocument<192> doc;
    doc["ts"]          = getEpoch();
    doc["pressure_pa"] = raw_pa;
    doc["delta_pa"]    = delta_pa;
    doc["temp_bmp"]    = temp_bmp;
    doc["temp_dht"]    = temp_dht;
    doc["humidity"]    = humidity;

    char buf[192];
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
        bmp.setSampling(Adafruit_BMP280::MODE_NORMAL,
                        Adafruit_BMP280::SAMPLING_X2,
                        Adafruit_BMP280::SAMPLING_X16,
                        Adafruit_BMP280::FILTER_X16,
                        Adafruit_BMP280::STANDBY_MS_500);
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

    // Publish every PUBLISH_INTERVAL ms
    unsigned long now = millis();
    if (now - lastPublish >= PUBLISH_INTERVAL) {
        lastPublish = now;
        if (mqttClient.connected()) {
            publishReading();
        }
    }
}
