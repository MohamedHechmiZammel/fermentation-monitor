"""
Fermentation Monitor — FastAPI REST backend.

Permission matrix:
    GET  /health          — public
    POST /auth/token      — public
    GET  /auth/me         — any authenticated role
    GET  /readings        — viewer, operator, admin
    GET  /summary         — viewer, operator, admin
    GET  /users           — admin
    POST /users           — admin
    PUT  /users/{id}      — admin
    DELETE /users/{id}    — admin
    POST /device/reset-baseline — operator, admin
    GET  /service/status  — operator, admin
"""

import os
import sqlite3
import time
import subprocess
from contextlib import asynccontextmanager
from typing import Annotated, Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordRequestForm
from dotenv import load_dotenv

from auth import AnyRole, AdminOnly, OperatorPlus, create_access_token
from users import (
    UserCreate, UserUpdate, UserOut,
    authenticate_user, create_user, delete_user,
    ensure_users_table, list_users, update_user,
)

load_dotenv()
DB_PATH = os.getenv("DB_PATH", "data/fermentation.db")


# ── Database ──────────────────────────────────────────────────────────────────

def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

@asynccontextmanager
async def lifespan(app: FastAPI):
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    conn = get_db()
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

    # Guarded migration: CREATE TABLE IF NOT EXISTS above silently no-ops against a
    # pre-existing readings table, so add the anomaly-detection columns by hand if missing.
    existing_cols = {row["name"] for row in conn.execute("PRAGMA table_info(readings)")}
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

    ensure_users_table(conn)
    conn.commit()
    conn.close()
    yield


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(title="Fermentation Monitor API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Auth endpoints (public) ───────────────────────────────────────────────────

@app.post("/auth/token")
async def login(form: Annotated[OAuth2PasswordRequestForm, Depends()]):
    conn = get_db()
    try:
        user = authenticate_user(conn, form.username, form.password)
    finally:
        conn.close()
    token = create_access_token(user["username"], user["role"])
    return {"access_token": token, "token_type": "bearer", "role": user["role"]}


@app.get("/auth/me")
async def me(current_user: Annotated[dict, Depends(AnyRole)]):
    return {"username": current_user["sub"], "role": current_user["role"]}


# ── Sensor data endpoints (viewer+) ──────────────────────────────────────────

@app.get("/readings")
async def get_readings(
    limit: int = Query(default=100, le=5000),
    since: Optional[int] = Query(default=None, description="Unix timestamp"),
    _user: Annotated[dict, Depends(AnyRole)] = None,
):
    conn = get_db()
    try:
        if since is not None:
            rows = conn.execute(
                "SELECT * FROM readings WHERE received_at > ? ORDER BY received_at LIMIT ?",
                (since, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM readings ORDER BY received_at DESC LIMIT ?", (limit,)
            ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


@app.get("/summary")
async def get_summary(_user: Annotated[dict, Depends(AnyRole)] = None):
    conn = get_db()
    try:
        now = int(time.time())

        # Active/slow: 10-min window
        bubble_rate = conn.execute(
            "SELECT COUNT(*) FROM readings WHERE ABS(delta_pa) > 0.5 AND received_at > ?",
            (now - 600,),
        ).fetchone()[0]

        # Finished: zero active readings in 30-min window
        active_in_30m = conn.execute(
            "SELECT COUNT(*) FROM readings WHERE ABS(delta_pa) > 0.5 AND received_at > ?",
            (now - 1800,),
        ).fetchone()[0]

        # Duration since first reading
        first = conn.execute("SELECT MIN(received_at) FROM readings").fetchone()[0]
        duration_s = (now - first) if first else 0

        # Latest anomaly state
        latest = conn.execute(
            "SELECT anomaly, recon_error FROM readings ORDER BY received_at DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()

    if active_in_30m == 0 and duration_s > 1800:
        activity = "finished"
    elif bubble_rate > 3:
        activity = "active"
    elif bubble_rate >= 1:
        activity = "slow"
    else:
        activity = "finished"

    anomaly_active = bool(latest["anomaly"]) if latest else False
    recon_error = latest["recon_error"] if latest else None

    return {
        "activity": activity,
        "bubble_rate": bubble_rate,
        "duration_s": duration_s,
        "anomaly_active": anomaly_active,
        "recon_error": recon_error,
    }


@app.get("/health")
async def health():
    return {"status": "ok", "ts": int(time.time())}


# ── Device action endpoints (operator+) ──────────────────────────────────────

@app.post("/device/reset-baseline")
async def reset_baseline(_user: Annotated[dict, Depends(OperatorPlus)] = None):
    """
    Publishes an MQTT command to the ESP32 to reset its pressure baseline.
    The firmware listens on fermentation/cmd and handles the 'reset_baseline' action.
    """
    import paho.mqtt.publish as publish
    try:
        publish.single(
            topic="fermentation/cmd",
            payload='{"action":"reset_baseline"}',
            hostname=os.getenv("MQTT_BROKER", "localhost"),
            port=int(os.getenv("MQTT_PORT", 1883)),
        )
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"MQTT publish failed: {e}")
    return {"status": "command_sent"}


@app.get("/service/status")
async def service_status(_user: Annotated[dict, Depends(OperatorPlus)] = None):
    """Reports whether recorder is alive by checking recency of last DB write."""
    conn = get_db()
    try:
        last_ts = conn.execute("SELECT MAX(received_at) FROM readings").fetchone()[0]
    finally:
        conn.close()
    now = int(time.time())
    lag_s = (now - last_ts) if last_ts else None
    recorder_alive = lag_s is not None and lag_s < 120  # missed < 4 samples
    return {
        "recorder": "up" if recorder_alive else "down",
        "last_reading_lag_s": lag_s,
        "broker": os.getenv("MQTT_BROKER", "localhost"),
    }


# ── Batch endpoints (viewer+ read, operator+ write) ──────────────────────────

from pydantic import BaseModel as PydanticBase

class BatchCreate(PydanticBase):
    name: str
    notes: Optional[str] = None

@app.get("/batches")
async def get_batches(_user: Annotated[dict, Depends(AnyRole)] = None):
    conn = get_db()
    try:
        rows = conn.execute("SELECT * FROM batches ORDER BY started_at DESC").fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]

@app.post("/batches", status_code=201)
async def start_batch(data: BatchCreate, _user: Annotated[dict, Depends(OperatorPlus)] = None):
    conn = get_db()
    try:
        cur = conn.execute(
            "INSERT INTO batches (name, started_at, notes) VALUES (?,?,?)",
            (data.name, int(time.time()), data.notes),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM batches WHERE id=?", (cur.lastrowid,)).fetchone()
    finally:
        conn.close()
    return dict(row)

@app.put("/batches/{batch_id}/end")
async def end_batch(batch_id: int, _user: Annotated[dict, Depends(OperatorPlus)] = None):
    conn = get_db()
    try:
        result = conn.execute(
            "UPDATE batches SET ended_at=? WHERE id=? AND ended_at IS NULL",
            (int(time.time()), batch_id),
        )
        conn.commit()
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Batch not found or already ended")
        return {"status": "ended"}
    finally:
        conn.close()

@app.get("/batches/{batch_id}/readings")
async def get_batch_readings(
    batch_id: int,
    limit: int = Query(default=2000, le=5000),
    _user: Annotated[dict, Depends(AnyRole)] = None,
):
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM readings WHERE batch_id=? ORDER BY received_at LIMIT ?",
            (batch_id, limit),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


# ── User management endpoints (admin only) ────────────────────────────────────

@app.get("/users", response_model=list[UserOut])
async def get_users(_user: Annotated[dict, Depends(AdminOnly)] = None):
    conn = get_db()
    try:
        return list_users(conn)
    finally:
        conn.close()


@app.post("/users", response_model=UserOut, status_code=201)
async def add_user(data: UserCreate, _user: Annotated[dict, Depends(AdminOnly)] = None):
    conn = get_db()
    try:
        return create_user(conn, data)
    finally:
        conn.close()


@app.put("/users/{user_id}", response_model=UserOut)
async def edit_user(
    user_id: int, data: UserUpdate, _user: Annotated[dict, Depends(AdminOnly)] = None
):
    conn = get_db()
    try:
        return update_user(conn, user_id, data)
    finally:
        conn.close()


@app.delete("/users/{user_id}", status_code=204)
async def remove_user(user_id: int, _user: Annotated[dict, Depends(AdminOnly)] = None):
    conn = get_db()
    try:
        delete_user(conn, user_id)
    finally:
        conn.close()
