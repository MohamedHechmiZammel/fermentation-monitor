"""
Fermentation Monitor — MQTT → SQLite recorder.

Subscribes to fermentation/sensor, writes readings to SQLite.
Run: python3 recorder.py
"""

import json
import os
import sqlite3
import time

import paho.mqtt.client as mqtt
from dotenv import load_dotenv

load_dotenv()

DB_PATH     = os.getenv("DB_PATH",     "data/fermentation.db")
MQTT_BROKER = os.getenv("MQTT_BROKER", "localhost")
MQTT_PORT   = int(os.getenv("MQTT_PORT", 1883))
MQTT_TOPIC  = "fermentation/sensor"


# ── Database ───────────────────────────────────────────────────────────────────

def open_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def ensure_schema():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    conn = open_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS batches (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT NOT NULL,
            started_at INTEGER NOT NULL,
            ended_at   INTEGER,
            notes      TEXT
        );

        CREATE TABLE IF NOT EXISTS readings (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ts          INTEGER NOT NULL,
            received_at INTEGER NOT NULL,
            pressure_pa REAL NOT NULL,
            delta_pa    REAL NOT NULL,
            temp_bmp    REAL NOT NULL,
            temp_dht    REAL NOT NULL,
            humidity    REAL NOT NULL,
            batch_id    INTEGER REFERENCES batches(id)
        );

        CREATE INDEX IF NOT EXISTS idx_readings_received_at ON readings(received_at);
    """)

    # Guarded migration: `anomaly`/`recon_error` were added after the original
    # CREATE TABLE shipped, so existing databases need an explicit ALTER TABLE —
    # CREATE TABLE IF NOT EXISTS silently no-ops on a table that already exists.
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(readings)")}
    if "anomaly" not in existing_cols:
        try:
            conn.execute("ALTER TABLE readings ADD COLUMN anomaly INTEGER")
        except sqlite3.OperationalError as e:
            # Another process added it between PRAGMA and ALTER — fine.
            if "duplicate column name" not in str(e):
                raise
    if "recon_error" not in existing_cols:
        try:
            conn.execute("ALTER TABLE readings ADD COLUMN recon_error REAL")
        except sqlite3.OperationalError as e:
            # Another process added it between PRAGMA and ALTER — fine.
            if "duplicate column name" not in str(e):
                raise

    conn.commit()
    conn.close()


def active_batch_id(conn: sqlite3.Connection):
    row = conn.execute(
        "SELECT id FROM batches WHERE ended_at IS NULL ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    return row[0] if row else None


# ── MQTT callbacks ─────────────────────────────────────────────────────────────

def on_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        print(f"[recorder] connected to {MQTT_BROKER}:{MQTT_PORT}")
        client.subscribe(MQTT_TOPIC, qos=1)
        print(f"[recorder] subscribed to {MQTT_TOPIC}")
    else:
        print(f"[recorder] connection failed rc={rc}")


def on_message(client, userdata, msg):
    received_at = int(time.time())
    try:
        payload = json.loads(msg.payload.decode())
        ts          = int(payload["ts"])
        pressure_pa = float(payload["pressure_pa"])
        delta_pa    = float(payload["delta_pa"])
        temp_bmp    = float(payload["temp_bmp"])
        temp_dht    = float(payload["temp_dht"])
        humidity    = float(payload["humidity"])
        # Additive TinyML anomaly-detection fields — optional/new, so use .get()
        # with sentinel defaults instead of bracket access, since older firmware
        # (or the transition period before it lands) won't send them.
        anomaly     = bool(payload.get("anomaly", False))
        recon_error = float(payload.get("recon_error", 0.0))
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        print(f"[recorder] malformed payload: {e} — {msg.payload!r}")
        return

    conn = open_db()
    try:
        batch_id = active_batch_id(conn)
        conn.execute(
            "INSERT INTO readings "
            "(ts, received_at, pressure_pa, delta_pa, temp_bmp, temp_dht, humidity, batch_id, anomaly, recon_error) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (ts, received_at, pressure_pa, delta_pa, temp_bmp, temp_dht, humidity, batch_id, anomaly, recon_error),
        )
        conn.commit()
    finally:
        conn.close()

    print(f"[{received_at}] delta={delta_pa:.2f}Pa temp={temp_dht:.1f}°C batch={batch_id}")


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    ensure_schema()
    print(f"[recorder] broker={MQTT_BROKER}:{MQTT_PORT}  topic={MQTT_TOPIC}  db={DB_PATH}")

    client = mqtt.Client(
        client_id="fermentation-recorder",
        protocol=mqtt.MQTTv5,
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
    )
    client.on_connect = on_connect
    client.on_message = on_message

    # clean_start=False = durable session (MQTTv5 equivalent of clean_session=False)
    client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60, clean_start=False)
    client.loop_forever()


if __name__ == "__main__":
    main()
