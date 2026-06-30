#pragma once
// Copy this file to config.h and fill in your values.
// config.h is gitignored — never commit it.

#define WIFI_SSID       "your-ssid"
#define WIFI_PASS       "your-password"

#define MQTT_HOST       "192.168.1.100"
#define MQTT_PORT       1883
#define MQTT_TOPIC      "fermentation/sensor"
#define MQTT_CMD_TOPIC  "fermentation/cmd"
#define MQTT_CLIENT_ID  "fermentation-esp32"

// Pins — match diagram.json wiring
#define DHT_PIN         4    // esp:4 → dht:SDA (orange wire)
// BMP280 uses default I2C: SDA=21, SCL=22; SDO→GND → address 0x76
