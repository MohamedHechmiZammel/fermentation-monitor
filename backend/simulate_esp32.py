"""
Simulates ESP32 firmware MQTT output for local testing.
Publishes fermentation/sensor every 30s with a realistic activity curve.
Mirrors the Wokwi sim behaviour (minus the 60× compression).
Run: python3 backend/simulate_esp32.py [--fast]  (--fast = 60× compressed)
"""

import argparse
import json
import math
import random
import time
import os
from dotenv import load_dotenv

load_dotenv()

BROKER  = os.getenv("MQTT_BROKER", "localhost")
PORT    = int(os.getenv("MQTT_PORT", 1883))
TOPIC   = "fermentation/sensor"

try:
    import paho.mqtt.client as mqtt
except ImportError:
    print("pip install paho-mqtt"); raise


def fermentation_curve(elapsed_s: float, total_s: float = 72 * 3600) -> float:
    """Bell-shaped bubble rate (Pa delta). Peaks at 1/3 of total fermentation time."""
    t = elapsed_s / total_s
    peak_t = 0.33
    sigma = 0.18
    base = math.exp(-((t - peak_t) ** 2) / (2 * sigma ** 2)) * 72.0
    noise = math.sin(elapsed_s * 0.07) * 3.5 + math.sin(elapsed_s * 0.19) * 1.8
    return max(0.1, base + noise)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fast", action="store_true", help="60× time compression (Wokwi mode)")
    args = parser.parse_args()

    interval_s   = 0.5 if args.fast else 30
    time_scale   = 60  if args.fast else 1
    baseline_pa  = 101325.0
    start_wall   = time.time()

    client = mqtt.Client(
        client_id="sim-esp32",
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
    )
    client.connect(BROKER, PORT)
    client.loop_start()
    print(f"Simulator connected → {BROKER}:{PORT}  topic={TOPIC}")
    print(f"Mode: {'FAST (60×)' if args.fast else 'REAL-TIME'}  interval={interval_s}s")
    print("Ctrl-C to stop\n")

    elapsed_sim = 0.0
    alpha = 0.001

    try:
        while True:
            raw_pa      = baseline_pa + fermentation_curve(elapsed_sim)
            delta_pa    = raw_pa - baseline_pa
            baseline_pa = alpha * raw_pa + (1 - alpha) * baseline_pa  # EMA drift

            temp_bmp = 22.0 + random.uniform(-0.3, 0.3)
            temp_dht = 21.5 + random.uniform(-0.5, 0.5) + elapsed_sim / (72 * 3600) * 0.8
            humidity = 62.0 + random.uniform(-1, 1) + delta_pa * 0.05

            payload = json.dumps({
                "ts":          int(time.time()),
                "pressure_pa": round(raw_pa, 2),
                "delta_pa":    round(delta_pa, 2),
                "temp_bmp":    round(temp_bmp, 2),
                "temp_dht":    round(temp_dht, 2),
                "humidity":    round(humidity, 2),
            })

            client.publish(TOPIC, payload, qos=0, retain=True)
            wall_elapsed = time.time() - start_wall
            print(f"[{wall_elapsed:6.0f}s wall | {elapsed_sim/3600:5.2f}h sim] "
                  f"Δ={delta_pa:5.1f}Pa  T={temp_dht:.1f}°C  RH={humidity:.0f}%")

            elapsed_sim += interval_s * time_scale
            time.sleep(interval_s)

    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
